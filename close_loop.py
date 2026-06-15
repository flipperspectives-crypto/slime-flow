#!/usr/bin/env python3
"""
CLOSE LOOP — APSA project health monitor + observer triage engine.
Port 8084. Monitors repos, polls observer, creates kanban cards, resolves stale problems.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, os, time, subprocess, threading, urllib.request
from datetime import datetime

PORT = 8084
HOME = os.path.expanduser("~")
OBSERVER_URL = "http://127.0.0.1:8082"
POLL_INTERVAL = 300  # match observer's CACHE_TTL

# Score tracking: {problem_id: [generation, score, generation, score, ...]}
score_history = {}
# Kanban cards already created (don't duplicate)
kanban_created = set()
# Action log
action_log = []

def check_projects():
    """Check all known APSA/project repos for health."""
    projects = []
    known = [
        ("slime-flow", f"{HOME}/slime-flow"),
        ("VEILPIERCER", f"{HOME}/VEILPIERCER"),
        ("godlike4", f"{HOME}/godlike4"),
        ("minmty", f"{HOME}/minmty"),
        ("apsa-builds", f"{HOME}/apsa-builds"),
    ]
    for name, path in known:
        status = {"name": name, "path": path, "exists": os.path.isdir(path)}
        if os.path.isdir(path):
            try:
                r = subprocess.run(
                    ["git", "-C", path, "status", "--porcelain"],
                    capture_output=True, text=True, timeout=5
                )
                dirty = len(r.stdout.strip().split("\n")) if r.stdout.strip() else 0
                status["dirty_files"] = dirty
                r = subprocess.run(
                    ["git", "-C", path, "log", "-1", "--format=%h %s %ar"],
                    capture_output=True, text=True, timeout=5
                )
                status["last_commit"] = r.stdout.strip()
                r = subprocess.run(
                    ["git", "-C", path, "branch", "--show-current"],
                    capture_output=True, text=True, timeout=5
                )
                status["branch"] = r.stdout.strip()
                r = subprocess.run(
                    ["find", path, "-name", "*.py", "-o", "-name", "*.html", "-o", "-name", "*.jl"],
                    capture_output=True, text=True, timeout=5
                )
                status["code_files"] = len([l for l in r.stdout.split("\n") if l])
                status["healthy"] = True
            except Exception as e:
                status["healthy"] = False
                status["error"] = str(e)[:80]
        else:
            status["healthy"] = False
            status["error"] = "not found"
        projects.append(status)
    return projects


def relevance_tag(title):
    """Assign a relevance tag based on keywords in the title."""
    title_lower = title.lower()
    tags = []
    if any(k in title_lower for k in ["ai", "model", "llm", "gpt", "transformer", "neural"]):
        tags.append("ai")
    if any(k in title_lower for k in ["security", "vuln", "exploit", "cve", "hack", "breach"]):
        tags.append("security")
    if any(k in title_lower for k in ["crypto", "blockchain", "solana", "defi", "token"]):
        tags.append("crypto")
    if any(k in title_lower for k in ["dev", "tool", "cli", "api", "sdk", "library", "framework"]):
        tags.append("devtools")
    if any(k in title_lower for k in ["startup", "acqui", "funding", "ipo", "revenue"]):
        tags.append("business")
    if any(k in title_lower for k in ["rust", "python", "go", "c++", "emacs", "vim"]):
        tags.append("lang")
    return tags if tags else ["general"]


def create_kanban_card(problem):
    """Create a Hermes kanban card for a high-score problem."""
    title = problem["title"][:80]
    url = problem.get("url", "")
    score = problem.get("score", 0)
    source = problem.get("source", "?")
    tags = relevance_tag(title)

    body = f"Source: {source} | Score: {score}\nURL: {url}\nRelevance: {', '.join(tags)}"

    try:
        r = subprocess.run(
            ["hermes", "kanban", "create", title,
             "--assignee", "default",
             "--body", body,
             "--json"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "HOME": HOME}
        )
        if r.returncode == 0:
            result = json.loads(r.stdout)
            task_id = result.get("id", result.get("task_id", "?"))
            action_log.append({
                "action": "kanban_created",
                "problem_id": problem.get("id", "?"),
                "title": title[:60],
                "tags": tags,
                "task_id": task_id,
                "time": datetime.now().isoformat()
            })
            return task_id
        else:
            action_log.append({
                "action": "kanban_failed",
                "problem_id": problem.get("id", "?"),
                "title": title[:60],
                "error": r.stderr[:200],
                "time": datetime.now().isoformat()
            })
    except Exception as e:
        action_log.append({
            "action": "kanban_error",
            "problem_id": problem.get("id", "?"),
            "title": title[:60],
            "error": str(e)[:200],
            "time": datetime.now().isoformat()
        })
    return None


def resolve_problem(problem_id):
    """Tell observer to resolve (drop) a problem."""
    try:
        data = json.dumps({"id": problem_id}).encode()
        req = urllib.request.Request(
            f"{OBSERVER_URL}/resolve",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        action_log.append({
            "action": "resolved",
            "problem_id": problem_id,
            "result": result,
            "time": datetime.now().isoformat()
        })
        return result.get("ok", False)
    except Exception as e:
        action_log.append({
            "action": "resolve_failed",
            "problem_id": problem_id,
            "error": str(e)[:200],
            "time": datetime.now().isoformat()
        })
    return False


def triage_loop():
    """Background thread: poll observer, track scores, act on problems."""
    global score_history, kanban_created
    last_gen = -1

    while True:
        try:
            # Fetch current problems from observer
            req = urllib.request.Request(f"{OBSERVER_URL}/problems")
            data = json.loads(urllib.request.urlopen(req, timeout=10).read())
            problems = data.get("problems", [])
            gen = data.get("gen", 0)

            if gen != last_gen and gen > 0:
                last_gen = gen
                current_ids = set()

                for p in problems:
                    pid = p.get("id")
                    if not pid or pid.startswith("filter-"):
                        continue
                    score = p.get("score", 0)
                    current_ids.add(pid)

                    # Track score history: append [gen, score]
                    if pid not in score_history:
                        score_history[pid] = []
                    score_history[pid].append((gen, score))

                    # --- KANBAN: create card for HN items above score 40 ---
                    if score > 40 and p.get("source") == "HN" and pid not in kanban_created:
                        tid = create_kanban_card(p)
                        if tid:
                            kanban_created.add(pid)

                    # --- STALE DETECTION: 3 identical scores = auto-resolve ---
                    history = score_history[pid]
                    if len(history) >= 3:
                        last_three = [s for _, s in history[-3:]]
                        if len(set(last_three)) == 1:  # all 3 scores identical
                            if resolve_problem(pid):
                                # Clean up tracking
                                if pid in score_history:
                                    del score_history[pid]
                                kanban_created.discard(pid)

                # Clean up history for problems no longer in feed
                stale_ids = set(score_history.keys()) - current_ids
                for sid in stale_ids:
                    del score_history[sid]

        except Exception:
            pass

        time.sleep(POLL_INTERVAL)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            projects = check_projects()
            healthy = sum(1 for p in projects if p.get("healthy"))
            self.send_json({
                "service": "Close Loop",
                "total": len(projects),
                "healthy": healthy,
                "tracked_problems": len(score_history),
                "kanban_created": len(kanban_created),
                "actions": len(action_log),
            })
        elif self.path == "/projects":
            projects = check_projects()
            healthy = sum(1 for p in projects if p.get("healthy"))
            self.send_json({"projects": projects, "total": len(projects), "healthy": healthy})
        elif self.path == "/health":
            projects = check_projects()
            healthy = sum(1 for p in projects if p.get("healthy"))
            self.send_json({
                "status": "ok",
                "total": len(projects),
                "healthy": healthy,
                "tracked": len(score_history),
                "kanban_cards": len(kanban_created),
                "time": datetime.now().isoformat()
            })
        elif self.path == "/actions":
            self.send_json({"actions": action_log[-50:], "total": len(action_log)})
        elif self.path == "/tracked":
            tracked = {}
            for pid, history in score_history.items():
                tracked[pid] = {
                    "scores": [s for _, s in history],
                    "generations": [g for g, _ in history],
                    "count": len(history),
                    "kanban_created": pid in kanban_created,
                }
            self.send_json({"tracked": tracked, "count": len(tracked)})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/force-triage":
            """Fast-forward: simulate 3 identical scores for all tracked problems, then run triage."""
            global score_history, kanban_created
            try:
                # Seed each tracked problem with 2 extra identical-score entries
                for pid, history in list(score_history.items()):
                    if len(history) >= 1:
                        last_gen, last_score = history[-1]
                        # Add 2 more identical entries (simulating 2 more fetches with same score)
                        history.append((last_gen + 1, last_score))
                        history.append((last_gen + 2, last_score))

                # Now run the stale detection logic
                resolved = []
                for pid, history in list(score_history.items()):
                    if len(history) >= 3:
                        last_three = [s for _, s in history[-3:]]
                        if len(set(last_three)) == 1:
                            if resolve_problem(pid):
                                resolved.append(pid)
                                del score_history[pid]
                                kanban_created.discard(pid)

                self.send_json({
                    "simulated": True,
                    "resolved": resolved,
                    "count": len(resolved),
                    "remaining_tracked": len(score_history),
                })
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        else:
            self.send_json({"error": "not found"}, 404)

    def send_json(self, data, code=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    import socket as _socket
    threading.Thread(target=triage_loop, daemon=True).start()
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.socket.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    print(f"Close Loop on :{PORT}")
    server.serve_forever()
