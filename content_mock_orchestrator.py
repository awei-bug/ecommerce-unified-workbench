"""Test-only orchestrator: simulate completion without invoking a model."""
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path != "/internal/batch-jobs/items":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        def callback():
            time.sleep(0.5)
            event = {"jobItemId": body["jobItemId"], "status": "succeeded", "outputs": [{"outputType": "marketing_copy", "outputData": {"title": "测试标题", "body": "生成正文，仅供联调"}}]}
            req = urllib.request.Request(os.environ["CHAIN_CONTENT_API"] + "/internal/batch-items/events", data=json.dumps(event).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                print(response.read().decode(), flush=True)
        threading.Thread(target=callback, daemon=True).start()
        payload = b'{"data":{"accepted":true}}'
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


ThreadingHTTPServer(("127.0.0.1", int(os.environ["CHAIN_MOCK_PORT"])), Handler).serve_forever()
