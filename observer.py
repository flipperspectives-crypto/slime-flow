#!/usr/bin/env python3
"""
OBSERVER — HN + GitHub problem tracker with smarter scoring.
Port 8082. Polls HN top stories, GitHub trending, scores urgency/relevance.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, time, threading, urllib.request, re
from datetime import datetime, timezone

PORT = 8082
CACHE_TTL = 300  # 5 min

problems = []
last_fetch = 0

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
    """Fetch GitHub trending / security advisories."""
    items = []
    try:
        # GitHub security advisories
        req = urllib.request.Request(
            "https://api.github.com/advisories?per_page=10",
            headers={"User-Agent": "Observer/1.0", "Accept": "application/vnd.github+json"}
        )
        advisories = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for adv in advisories[:10]:
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
            self.send_json({"problems": problems[:10]})
        elif self.path == "/health":
            self.send_json({"status": "ok", "problems": len(problems), "age": time.time() - last_fetch})
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
