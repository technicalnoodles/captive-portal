#!/usr/bin/env python3
import json
import os
import socket
from http.server import SimpleHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# In-memory acceptance state keyed by client IP
ACCEPTED = set()

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PORTAL_PATH = "/portal"  # human-facing portal UI path
API_PATH = "/.well-known/captive-portal"  # RFC 8908 recommended well-known path
ACCEPT_PATH = "/accept"


def portal_url_from_host(host_header: str) -> str:
    scheme = "http"
    host = host_header or f"{socket.gethostbyname(socket.gethostname())}:8000"
    return f"{scheme}://{host}{PORTAL_PATH}"


class CaptivePortalHandler(SimpleHTTPRequestHandler):
    def _client_ip(self):
        # REMOTE_ADDR equivalent
        return self.client_address[0]

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/captive+json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _no_content(self, code=204):
        self.send_response(code)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == API_PATH:
            # RFC 8908 Captive Portal API response
            client_ip = self._client_ip()
            captive = client_ip not in ACCEPTED
            payload = {
                "captive": captive,
                "user-portal-url": portal_url_from_host(self.headers.get("Host")),
            }
            return self._json(payload)

        if path == PORTAL_PATH:
            # Serve the portal UI (index.html)
            return self._serve_index()

        # Fall back to static file serving from ROOT_DIR
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == ACCEPT_PATH:
            # Mark client as accepted
            client_ip = self._client_ip()
            ACCEPTED.add(client_ip)
            return self._no_content(204)

        self.send_error(404, "Not Found")

    def _serve_index(self):
        try:
            file_path = os.path.join(ROOT_DIR, "index.html")
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404, "index.html not found")


def run(addr="0.0.0.0", port=8000):
    os.chdir(ROOT_DIR)
    with HTTPServer((addr, port), CaptivePortalHandler) as httpd:
        print(f"Captive Portal server running on http://{addr}:{port}")
        print(f"API: {API_PATH}")
        print(f"Portal: {PORTAL_PATH}")
        print(f"Accept endpoint: {ACCEPT_PATH}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    run(port=port)
