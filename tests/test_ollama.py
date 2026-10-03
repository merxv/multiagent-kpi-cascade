"""OllamaProvider против поддельного HTTP-сервера Ollama (настоящая Ollama не нужна)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from llm.client import LLMConfigError, OllamaProvider


class FakeOllama(BaseHTTPRequestHandler):
    requests: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append(body)
        if body["model"] != "qwen2.5:7b":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "model not found"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"message": {"content": '{"ok": true}'},
                                     "prompt_eval_count": 120, "eval_count": 7}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_ollama_success_sends_context_size(server):
    FakeOllama.requests = []
    resp = OllamaProvider("qwen2.5:7b", server, timeout=5, num_ctx=16384).complete("sys", "user", "agent", 100)
    assert resp.text == '{"ok": true}' and resp.tokens_in == 120 and resp.tokens_out == 7
    sent = FakeOllama.requests[0]
    assert sent["options"] == {"num_predict": 100, "num_ctx": 16384}
    assert sent["format"] == "json"


def test_ollama_model_not_pulled(server):
    with pytest.raises(LLMConfigError, match="ollama pull llama-unknown"):
        OllamaProvider("llama-unknown", server, timeout=5, num_ctx=4096).complete("s", "u", "a", 10)


def test_ollama_not_running():
    # Свободный порт, на котором никто не слушает
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    port = srv.server_port
    srv.server_close()
    with pytest.raises(LLMConfigError, match="Ollama не запущена"):
        OllamaProvider("qwen2.5:7b", f"http://127.0.0.1:{port}", timeout=5, num_ctx=4096).complete("s", "u", "a", 10)
