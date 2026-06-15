#!/usr/bin/env python3
"""
CLOSE LOOP — APSA project health monitor.
Port 8084. Checks deployed projects, reports status.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, os, time, subprocess
from datetime import datetime

PORT = 8084
HOME = os.path.expanduser("~")

def check_projects():
    """Check all known APSA/project repos for health."""
    projects = []
    
    # Known projects
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
                # Git status
                r = subprocess.run(
                    ["git", "-C", path, "status", "--porcelain"],
                    capture_output=True, text=True, timeout=5
                )
                dirty = len(r.stdout.strip().split("\n")) if r.stdout.strip() else 0
                status["dirty_files"] = dirty
                
                # Last commit
                r = subprocess.run(
                    ["git", "-C", path, "log", "-1", "--format=%h %s %ar"],
                    capture_output=True, text=True, timeout=5
                )
                status["last_commit"] = r.stdout.strip()
                
                # Branch
                r = subprocess.run(
                    ["git", "-C", path, "branch", "--show-current"],
                    capture_output=True, text=True, timeout=5
                )
                status["branch"] = r.stdout.strip()
                
                # File count / line count
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

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        projects = check_projects()
        healthy = sum(1 for p in projects if p.get("healthy"))
        
        if self.path == "/":
            self.send_json({"service": "Close Loop", "total": len(projects), "healthy": healthy})
        elif self.path == "/projects":
            self.send_json({"projects": projects, "total": len(projects), "healthy": healthy})
        elif self.path == "/health":
            self.send_json({"status": "ok", "total": len(projects), "healthy": healthy, "time": datetime.now().isoformat()})
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
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Close Loop on :{PORT}")
    server.serve_forever()
