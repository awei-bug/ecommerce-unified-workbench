"""Test-only local proxy that can fail the next ingest request."""
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

lock = threading.Lock()
fail_next = False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global fail_next
        if self.path == '/test/fail-next':
            with lock:
                fail_next = True
            self.send_response(200)
            self.end_headers()
            return
        self.forward()

    def do_POST(self):
        global fail_next
        with lock:
            fail = self.path == '/hub/ingest' and fail_next
            if fail:
                fail_next = False
        if fail:
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error":"simulated outage"}')
            return
        self.forward()

    def forward(self):
        size = int(self.headers.get('Content-Length', '0'))
        data = self.rfile.read(size) if size else None
        headers = {key: value for key, value in self.headers.items() if key.lower() in ('content-type', 'x-hub-token', 'x-hub-tenant')}
        request = urllib.request.Request(os.environ['CHAIN_REAL_HUB'] + self.path, data=data, headers=headers, method=self.command)
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            self.send_response(response.status)
            self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)


ThreadingHTTPServer(('127.0.0.1', int(os.environ['CHAIN_HUB_PROXY_PORT'])), Handler).serve_forever()
