"""Isolated Ollama protocol double: deterministic embeddings, request-derived citations.

This service is for generated test data only; it does not assess model quality.
"""
import hashlib
import json
import math
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def embedding(value: str) -> list[float]:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    vector = [(byte - 127.5) / 127.5 for byte in digest]
    norm = math.sqrt(sum(item * item for item in vector))
    return [item / norm for item in vector]


def response(messages: list[dict]) -> str:
    text = "\n".join(str(message.get("content", "")) for message in messages)
    # Context lines come from production's anchor formatter, not from this stub.
    matches = re.findall(r"(?m)^\[([A-Za-z0-9_-]{8,})\][^\n]*\n([^\n]+)", text)
    aggregate = re.findall(r"(?m)^- \[([A-Za-z0-9_-]{8,})(?:,[^\]]*)?\]", text)
    ids = [matches[0][0]] if matches else aggregate[:1]
    quote = matches[0][1][:80] if matches else ""
    claim = {"text": "测试生成的占位结论", "anchor_ids": ids}
    if quote:
        claim["quote"] = quote
    payload = {
        "overview": claim,
        "key_points": [claim] if ids else [],
        "sections": [{"title": "测试章节", "summary": "测试摘要", "anchor_ids": ids,
                      "claims": [claim]}] if ids else [],
        "terms": [], "open_questions": [], "relations": [],
    }
    return json.dumps(payload, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b'{"status":"ok"}\n'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 20 * 1024 * 1024:
            self.send_error(413)
            return
        try:
            request = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            self.send_error(400)
            return
        delay = float(os.environ.get("NB_PROVIDER_DELAY_SECONDS", "0"))
        if delay:
            time.sleep(delay)
        mode = os.environ.get("NB_PROVIDER_MODE", "normal")
        if mode == "disconnect":
            self.close_connection = True
            return
        if self.path in ("/api/embed", "/api/embeddings"):
            inputs = request.get("input", request.get("prompt", ""))
            values = inputs if isinstance(inputs, list) else [inputs]
            vectors = [embedding(str(value)) for value in values]
            result = {"model": request.get("model", "test"), "embeddings": vectors}
            if self.path == "/api/embeddings":
                result["embedding"] = vectors[0]
        elif self.path == "/api/chat":
            content = response(request.get("messages", []))
            if mode == "invalid-json":
                content = "{invalid"
            elif mode == "invalid-reference":
                content = json.dumps({"overview": {"text": "invalid", "anchor_ids": ["outside"]}})
            if request.get("stream"):
                # Ollama wire format: newline-delimited JSON chunks terminated
                # by a final done=true object. This makes the app's real
                # astream path observable (first answer token is a real chunk).
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                interval = float(os.environ.get(
                    "NB_PROVIDER_TOKEN_INTERVAL_SECONDS", "0.002"))
                for offset in range(0, len(content), 8):
                    chunk = {
                        "model": request.get("model", "test"),
                        "created_at": "2026-01-01T00:00:00Z",
                        "message": {"role": "assistant",
                                    "content": content[offset:offset + 8]},
                        "done": False,
                    }
                    self.wfile.write(
                        (json.dumps(chunk, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                    if interval > 0:
                        time.sleep(interval)
                final = {
                    "model": request.get("model", "test"),
                    "created_at": "2026-01-01T00:00:00Z",
                    "message": {"role": "assistant", "content": ""},
                    "done": True, "done_reason": "stop",
                    "total_duration": 1, "eval_count": 1,
                }
                self.wfile.write(
                    (json.dumps(final, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
                return
            result = {"model": request.get("model", "test"), "created_at": "2026-01-01T00:00:00Z",
                      "message": {"role": "assistant", "content": content}, "done": True,
                      "done_reason": "stop", "total_duration": 1, "eval_count": 1}
        else:
            self.send_error(404)
            return
        body = (json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson" if request.get("stream") else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Avoid logging request content and generated documents.
        pass


class BoundedProviderServer(ThreadingHTTPServer):
    """Keep burst accepts off the tiny default backlog and bound thread stacks."""
    request_queue_size = 128
    daemon_threads = True

    def __init__(self, address, handler, max_active=16):
        import threading
        self._slots = threading.BoundedSemaphore(max_active)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        self._slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


if __name__ == "__main__":
    BoundedProviderServer(("0.0.0.0", 11434), Handler).serve_forever()
