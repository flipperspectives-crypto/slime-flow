#!/usr/bin/env python3
"""
OBSERVER — HN + GitHub problem tracker with smarter scoring.
Port 8082. Polls HN top stories, GitHub trending, scores urgency/relevance.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, time, threading, urllib.request, re, os, glob
from datetime import datetime, timezone

PORT = 8082
CACHE_TTL = 300  # 5 min

problems = []
last_fetch = 0

# Repos to scan for dependency manifest
DEP_REPOS = [
    os.path.expanduser("~/slime-flow"),
    os.path.expanduser("~/VEILPIERCER"),
    os.path.expanduser("~/godlike4"),
    os.path.expanduser("~/minmty"),
    os.path.expanduser("~/apsa-builds"),
]

DEP_MANIFEST_CACHE = {"packages": set(), "built_at": 0}

def build_dependency_manifest():
    """Scan all project repos for known dependency files, extract package names.
    Returns a set of lowercased package identifiers for fast lookup."""
    packages = set()
    # Dependency file patterns -> extractor
    scanners = [
        # package.json: "name" field + dependencies/devDependencies keys
        ("package.json", lambda content: _extract_npm_packages(content)),
        # requirements.txt: one package per line (strip versions)
        ("requirements.txt", lambda content: _extract_pip_packages(content)),
        # go.mod: module + require lines
        ("go.mod", lambda content: _extract_go_packages(content)),
        # Cargo.toml: [dependencies] sections
        ("Cargo.toml", lambda content: _extract_cargo_packages(content)),
        # pyproject.toml: [project] dependencies
        ("pyproject.toml", lambda content: _extract_pip_packages(content)),
    ]

    for repo in DEP_REPOS:
        if not os.path.isdir(repo):
            continue
        for pattern, extractor in scanners:
            for filepath in glob.glob(os.path.join(repo, "**", pattern), recursive=True):
                # Skip node_modules, .git, __pycache__
                if any(skip in filepath for skip in ["node_modules", ".git", "__pycache__", "venv"]):
                    continue
                try:
                    with open(filepath) as f:
                        content = f.read()
                    packages.update(extractor(content))
                except:
                    pass

    # Also scan for Python imports in our own code (self-referential)
    for repo in DEP_REPOS:
        if not os.path.isdir(repo):
            continue
        for pyfile in glob.glob(os.path.join(repo, "**", "*.py"), recursive=True):
            if any(skip in pyfile for skip in ["__pycache__", ".git", "node_modules"]):
                continue
            try:
                with open(pyfile) as f:
                    for line in f:
                        # import X or from X import Y
                        m = re.match(r'^(?:from|import)\s+(\S+)', line)
                        if m:
                            packages.add(m.group(1).lower())
            except:
                pass

    DEP_MANIFEST_CACHE["packages"] = packages
    DEP_MANIFEST_CACHE["built_at"] = time.time()
    return packages

def _extract_npm_packages(content):
    """Extract npm package names from package.json."""
    pkgs = set()
    try:
        data = json.loads(content)
        for section in ["dependencies", "devDependencies", "peerDependencies"]:
            deps = data.get(section, {})
            if isinstance(deps, dict):
                for name in deps:
                    pkgs.add(name.lower())
                    # Also add unscoped name (e.g. "@angular/core" -> "angular")
                    if name.startswith("@"):
                        pkgs.add(name.split("/")[-1].lower())
    except:
        pass
    return pkgs

def _extract_pip_packages(content):
    """Extract pip package names from requirements.txt or pyproject.toml."""
    pkgs = set()
    for line in content.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            # Strip version specifiers: "pkg==1.0" -> "pkg", "pkg>=1.0,<2.0" -> "pkg"
            match = re.match(r'^([a-zA-Z0-9_.-]+)', line)
            if match:
                pkgs.add(match.group(1).lower())
    return pkgs

def _extract_go_packages(content):
    """Extract Go package names from go.mod."""
    pkgs = set()
    for line in content.split("\n"):
        line = line.strip()
        # module github.com/org/repo
        if line.startswith("module "):
            pkgs.add(line.split()[1].lower())
            pkgs.add(line.split()[1].split("/")[-1].lower())
        # require github.com/org/repo v1.2.3
        if line.startswith("require ") and len(line.split()) >= 2:
            pkg = line.split()[1]
            pkgs.add(pkg.lower())
            pkgs.add(pkg.split("/")[-1].lower())
    return pkgs

def _extract_cargo_packages(content):
    """Extract Rust crate names from Cargo.toml."""
    pkgs = set()
    in_deps = False
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("[dependencies"):
            in_deps = True
            continue
        if line.startswith("[") and in_deps:
            in_deps = False
            continue
        if in_deps and "=" in line:
            name = line.split("=")[0].strip().strip('"').lower()
            pkgs.add(name)
    return pkgs

def is_package_in_stack(advisory):
    """Check if a GitHub advisory affects any package used in our repos."""
    # Rebuild manifest every hour
    if time.time() - DEP_MANIFEST_CACHE.get("built_at", 0) > 3600:
        build_dependency_manifest()

    packages = DEP_MANIFEST_CACHE.get("packages", set())
    if not packages:
        return False  # If manifest failed to build, keep advisory (conservative)

    # Extract affected package names from advisory
    vulns = advisory.get("vulnerabilities", [])
    for vuln in vulns:
        pkg = vuln.get("package", {})
        pkg_name = pkg.get("name", "").lower()
        if not pkg_name:
            continue
        # Direct match
        if pkg_name in packages:
            return True
        # Check last segment (e.g. "github.com/filebrowser/filebrowser/v2" -> "filebrowser")
        last_seg = pkg_name.rsplit("/", 1)[-1].lower()
        if last_seg in packages:
            return True
    return False

def fetch_hn():
    """Fetch top HN stories and extract problem/solution posts."""
    items = []
    try:
        # Top stories
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers={"User-Agent": "Observer/1.0"}
        )
        ids = json.loads(urllib.request.urlopen(req, timeout=10).read())[:30]
        
        for sid in ids[:15]:
            try:
                req = urllib.request.Request(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                    headers={"User-Agent": "Observer/1.0"}
                )
                item = json.loads(urllib.request.urlopen(req, timeout=5).read())
                title = item.get("title", "")
                url = item.get("url", f"https://news.ycombinator.com/item?id={sid}")
                score = item.get("score", 0)
                
                # Score: higher = more urgent/interesting
                prob_keywords = ["bug", "vulnerability", "outage", "breach", "exploit", "leak",
                                "crash", "failure", "attack", "hack", "zero-day", "CVE",
                                "deprecated", "breaking", "recall", "lawsuit", "ban"]
                urgency = sum(1 for k in prob_keywords if k.lower() in title.lower()) * 15
                base_score = min(score / 10, 30)
                final_score = base_score + urgency + (20 if score > 200 else 0)
                
                items.append({
                    "source": "HN",
                    "title": title,
                    "url": url,
                    "score": round(final_score, 1),
                    "points": score,
                    "time": datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc).isoformat()
                })
            except:
                continue
    except Exception as e:
        items.append({"source": "HN", "title": f"[Fetch error: {str(e)[:60]}]", "url": "", "score": 0, "points": 0, "time": ""})
    return items

def fetch_github():
    """Fetch GitHub security advisories, filtered to only packages in our stack."""
    items = []
    filtered = 0
    try:
        # GitHub security advisories
        req = urllib.request.Request(
            "https://api.github.com/advisories?per_page=10",
            headers={"User-Agent": "Observer/1.0", "Accept": "application/vnd.github+json"}
        )
        advisories = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for adv in advisories[:10]:
            # Filter: only include advisories affecting packages we actually use
            if not is_package_in_stack(adv):
                filtered += 1
                continue
            severity = adv.get("severity", "medium")
            sev_score = {"critical": 40, "high": 30, "medium": 15, "low": 5}.get(severity, 10)
            items.append({
                "source": "GH",
                "title": adv.get("summary", "?")[:120],
                "url": adv.get("html_url", ""),
                "score": sev_score + 5,
                "points": 0,
                "severity": severity,
                "time": adv.get("published_at", "")
            })
        if filtered:
            # Filter summary for /problems only — dropped from /top to keep counts clean
            items.append({
                "source": "GH",
                "title": f"[{filtered} advisories filtered — not in our dependency stack]",
                "url": "",
                "score": -1,  # negative score = hidden from /top, visible in /problems
                "points": 0,
                "severity": "info",
                "time": ""
            })
    except Exception as e:
        items.append({"source": "GH", "title": f"[Fetch error: {str(e)[:60]}]", "url": "", "score": 0, "points": 0, "severity": "?", "time": ""})
    return items

def refresh():
    global problems, last_fetch
    while True:
        try:
            items = fetch_hn() + fetch_github()
            items.sort(key=lambda x: x["score"], reverse=True)
            problems = items
            last_fetch = time.time()
        except:
            pass
        time.sleep(CACHE_TTL)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_json({"service": "Observer", "problems": len(problems), "last_fetch": last_fetch})
        elif self.path == "/problems":
            self.send_json({"problems": problems, "total": len(problems), "updated": last_fetch})
        elif self.path == "/top":
            # Only show real problems (score >= 0), drop filter metadata lines
            real = [p for p in problems if p["score"] >= 0]
            self.send_json({"problems": real[:10]})
        elif self.path == "/health":
            real_count = sum(1 for p in problems if p["score"] >= 0)
            self.send_json({"status": "ok", "problems": real_count, "age": time.time() - last_fetch})
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
    threading.Thread(target=refresh, daemon=True).start()
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Observer on :{PORT}")
    server.serve_forever()
