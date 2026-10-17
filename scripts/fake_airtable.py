"""Tiny fake Airtable API for the demo script: records POSTs, always returns 200.

Usage: python scripts/fake_airtable.py [requests.jsonl]
Prints the chosen port on stdout, then serves until killed.
"""

from __future__ import annotations

import http.server
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/airtable-requests.jsonl"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server API
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"path": self.path, "records": len(body["records"])}) + "\n")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
