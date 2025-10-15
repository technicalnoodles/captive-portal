#!/usr/bin/env python3
import json
import os
import socket
import time
import threading
from datetime import datetime
from flask import Flask, request, send_file, make_response, g, redirect
from werkzeug.middleware.proxy_fix import ProxyFix
from pymongo import MongoClient

# In-memory acceptance state keyed by client IP
ACCEPTED = set()

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PORTAL_PATH = "/portal"  # human-facing portal UI path
API_PATH = "/.well-known/captive-portal"  # RFC 8908 recommended well-known path
ACCEPT_PATH = "/accept"
MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB = os.environ.get("MONGO_DB")
MONGO_COLLECTION = os.environ.get("MONGO_COLLECTION", "portal_requests")


def portal_url_from_host(host_header: str) -> str:
    scheme = "http"
    host = host_header or f"{socket.gethostbyname(socket.gethostname())}:8000"
    return f"{scheme}://{host}{PORTAL_PATH}"


def no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


app = Flask(__name__)
# If behind a reverse proxy, trust the first X-Forwarded-For hop
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# Optional: MongoDB setup
mongo_client = None
mongo_coll = None
if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        db = mongo_client.get_database(MONGO_DB) if MONGO_DB else mongo_client.get_default_database()
        if db is None:
            # Fallback: if URI has no default DB and MONGO_DB not set, use 'captive'
            db = mongo_client["captive"]
        mongo_coll = db[MONGO_COLLECTION]
        # quick ping
        mongo_client.admin.command("ping")
        print("[mongo] connected")
    except Exception as e:
        print(f"[mongo] connection error: {e}")
        mongo_client = None
        mongo_coll = None


def _client_ip() -> str:
    return (request.access_route[0] if request.access_route else request.remote_addr) or ""


@app.before_request
def _start_timer():
    g._start_ts = time.time()


def _log_request(event: str | None = None, extra: dict | None = None):
    if not mongo_coll:
        return
    # Build log entry
    start = getattr(g, "_start_ts", time.time())
    xff = request.headers.get("X-Forwarded-For", "")
    entry = {
        "ts": datetime.utcnow(),
        "method": request.method,
        "path": request.full_path if request.query_string else request.path,
        "clientIP": _client_ip(),
        "xff": xff,
        "xffClientIP": (xff.split(",")[0].strip() if xff else ""),
        "host": request.headers.get("Host", ""),
        "ua": request.headers.get("User-Agent", ""),
        "referer": request.headers.get("Referer", ""),
        "status": getattr(g, "_status_code", None),
        "ms": int((time.time() - start) * 1000),
    }
    if event:
        entry["event"] = event
    if extra:
        entry.update(extra)

    def _write():
        try:
            mongo_coll.insert_one(entry)
        except Exception:
            pass

    threading.Thread(target=_write, daemon=True).start()


@app.get(API_PATH)
def captive_portal_api():
    client_ip = _client_ip()
    captive = client_ip not in ACCEPTED
    payload = {
        "captive": captive,
        "user-portal-url": portal_url_from_host(request.headers.get("Host")),
    }
    resp = make_response(json.dumps(payload), 200)
    resp.mimetype = "application/captive+json"
    g._status_code = 200
    return no_store(resp)


@app.get(PORTAL_PATH)
def portal_page():
    file_path = os.path.join(ROOT_DIR, "index.html")
    if not os.path.exists(file_path):
        g._status_code = 404
        return ("index.html not found", 404)
    resp = make_response(send_file(file_path))
    g._status_code = 200
    return no_store(resp)


@app.get("/")
def root_index():
    # Redirect root to the portal path for convenience
    g._status_code = 302
    return no_store(redirect(PORTAL_PATH, code=302))


@app.post(ACCEPT_PATH)
def accept_terms():
    client_ip = _client_ip()
    ACCEPTED.add(client_ip)
    resp = make_response("", 204)
    g._status_code = 204
    return no_store(resp)


@app.after_request
def _after(resp):
    # Store status for logger and submit log asynchronously
    try:
        g._status_code = resp.status_code
    except Exception:
        pass
    # Mark accept explicitly
    event = "accept" if request.path == ACCEPT_PATH and request.method == "POST" else None
    _log_request(event=event)
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Captive Portal server (Flask) running on http://0.0.0.0:{port}")
    print(f"API: {API_PATH}")
    print(f"Portal: {PORTAL_PATH}")
    print(f"Accept endpoint: {ACCEPT_PATH}")
    app.run(host="0.0.0.0", port=port)
