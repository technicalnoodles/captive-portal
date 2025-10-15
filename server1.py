#!/usr/bin/env python3
import json
import os
import socket
import time
import logging
import threading
from datetime import datetime
from flask import Flask, request, send_file, make_response, redirect, g
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


def _client_ip() -> str:
    return (request.access_route[0] if request.access_route else request.remote_addr) or ""


def _xff_client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else "")


# Optional: MongoDB setup
mongo_client = None
mongo_coll = None
if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        db = mongo_client.get_database(MONGO_DB) if MONGO_DB else mongo_client.get_default_database()
        if db is None:
            db = mongo_client["captive"]
        mongo_coll = db[MONGO_COLLECTION]
        mongo_client.admin.command("ping")
        app.logger.info("[mongo] connected")
    except Exception as e:
        app.logger.error(f"[mongo] connection error: {e}")
        mongo_client = None
        mongo_coll = None


def _log_to_mongo(event: str):
    if mongo_coll is None:
        return
    doc = {
        "ts": datetime.utcnow(),
        "event": event,
        "ip": _client_ip(),
        "xff_ip": _xff_client_ip(),
        "ua": request.headers.get("User-Agent", ""),
        "path": request.full_path if request.query_string else request.path,
    }

    def _write():
        try:
            mongo_coll.insert_one(doc)
        except Exception:
            pass

    threading.Thread(target=_write, daemon=True).start()


@app.before_request
def _start_timer():
    g._t = time.time()


@app.after_request
def _log_request(resp):
    try:
        ms = int((time.time() - getattr(g, "_t", time.time())) * 1000)
    except Exception:
        ms = None
    entry = {
        "method": request.method,
        "path": request.full_path if request.query_string else request.path,
        "status": resp.status_code,
        "ms": ms,
        "clientIP": _client_ip(),
        "ua": request.headers.get("User-Agent", ""),
    }
    line = json.dumps(entry)
    if request.method == "GET":
        app.logger.info(line)
        _log_to_mongo("request")
    if resp.status_code >= 400:
        app.logger.error(line)
    return resp


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
    return no_store(resp)


@app.get(PORTAL_PATH)
def portal_page():
    file_path = os.path.join(ROOT_DIR, "index.html")
    if not os.path.exists(file_path):
        return ("index.html not found", 404)
    resp = make_response(send_file(file_path))
    return no_store(resp)


@app.get("/")
def root_index():
    return no_store(redirect(PORTAL_PATH, code=302))


@app.post(ACCEPT_PATH)
def accept_terms():
    client_ip = _client_ip()
    ACCEPTED.add(client_ip)
    resp = make_response("", 204)
    _log_to_mongo("accept")
    return no_store(resp)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Captive Portal server (Flask) running on http://0.0.0.0:{port}")
    print(f"API: {API_PATH}")
    print(f"Portal: {PORTAL_PATH}")
    print(f"Accept endpoint: {ACCEPT_PATH}")
    app.run(host="0.0.0.0", port=port)
