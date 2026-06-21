"""HTTP layer: routing, JSON IO and static file serving (standard library only).

Run with:  python -m backend.server
The same server hosts the JSON API and the frontend, so the whole app starts
with a single command and no build step.
"""
import json
import mimetypes
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config
from .app import App

APP: App  # set in main()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- helpers -------------------------------------------------------------
    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, rel_path: str):
        # default document
        if rel_path in ("", "/"):
            rel_path = "index.html"
        rel_path = rel_path.lstrip("/")
        target = (config.FRONTEND_DIR / rel_path).resolve()
        # prevent path traversal outside the frontend directory
        if config.FRONTEND_DIR.resolve() not in target.parents and \
                target != config.FRONTEND_DIR.resolve():
            self.send_error(403, "Forbidden")
            return
        if not target.is_file():
            self.send_error(404, "Not Found")
            return
        ctype, _ = mimetypes.guess_type(str(target))
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- routing -------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        q = self._query()
        try:
            if path == "/suggest":
                prefix = q.get("q", [""])[0]
                mode = q.get("mode", ["count"])[0]
                limit = int(q.get("limit", [config.SUGGEST_LIMIT])[0])
                self._send_json(APP.suggest(prefix, mode, limit))
            elif path == "/trending":
                limit = int(q.get("limit", [config.SUGGEST_LIMIT])[0])
                self._send_json(APP.trending_now(limit))
            elif path == "/cache/debug":
                prefix = q.get("prefix", [""])[0]
                mode = q.get("mode", ["count"])[0]
                self._send_json(APP.cache_debug(prefix, mode))
            elif path == "/metrics":
                self._send_json(APP.metrics_report())
            elif path == "/healthz":
                self._send_json({"status": "ok"})
            elif path == "/api":
                self._send_json(_API_DOC)
            else:
                self._send_static(path)
        except Exception as exc:  # never leak a stack trace to the client
            self._send_json({"error": str(exc)}, status=500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/search":
                body = self._read_json_body()
                query = body.get("query", "")
                self._send_json(APP.search(query))
            else:
                self._send_json({"error": "not found"}, status=404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def do_OPTIONS(self):  # CORS preflight (handy if UI is served elsewhere)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, fmt, *args):  # quieter console; comment out to debug
        return


_API_DOC = {
    "GET /suggest?q=<prefix>&mode=count|trending&limit=10":
        "Up to 10 prefix-matching suggestions sorted by count (or trending).",
    "POST /search {query}": "Records the query (batched) and returns 'Searched'.",
    "GET /trending?limit=10": "Recency-aware trending suggestions.",
    "GET /cache/debug?prefix=<p>&mode=count":
        "Which cache node owns the prefix (consistent hashing) + HIT/MISS.",
    "GET /metrics": "Latency p50/p95/p99, cache hit rate, batch write reduction.",
    "GET /healthz": "Liveness probe.",
}


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Same as ThreadingHTTPServer but doesn't dump a traceback when a client
    closes the connection early (common with browsers / keep-alive probes)."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError)):
            return  # harmless: the client went away
        super().handle_error(request, client_address)


def main():
    global APP
    # line-buffer stdout so startup/flush logs appear promptly in any terminal
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    APP = App()
    APP.startup()
    httpd = QuietThreadingHTTPServer((config.HOST, config.PORT), Handler)

    def _graceful(signum, frame):
        print("\n[server] shutting down ...")
        APP.shutdown()
        httpd.shutdown()

    signal.signal(signal.SIGINT, _graceful)
    try:
        signal.signal(signal.SIGTERM, _graceful)
    except (ValueError, AttributeError):
        pass

    url = f"http://{config.HOST}:{config.PORT}"
    print(f"[server] listening on {url}  (open this in your browser)")
    try:
        httpd.serve_forever()
    finally:
        APP.shutdown()


if __name__ == "__main__":
    main()
