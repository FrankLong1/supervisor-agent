from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from .models import TaskContext, ThreadCandidate, text_from_item


class AppServerError(RuntimeError): pass


class AppServerClient:
    """Same-host newline-delimited JSON-RPC client for the Codex app-server."""
    def __init__(self, socket_path: str | Path, timeout_seconds: float = 10):
        self.socket_path, self.timeout_seconds, self._request_id = str(socket_path), timeout_seconds, 0

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        message = json.dumps({"jsonrpc":"2.0", "id":self._request_id, "method":method, "params":params}) + "\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout_seconds); connection.connect(self.socket_path); connection.sendall(message.encode())
            buffer = b""
            while b"\n" not in buffer:
                chunk = connection.recv(65536)
                if not chunk: raise AppServerError("app-server closed JSON-RPC connection")
                buffer += chunk
        response = json.loads(buffer.split(b"\n", 1)[0])
        if "error" in response: raise AppServerError(str(response["error"]))
        return response.get("result", {})

    def list_unarchived_threads(self) -> list[dict]:
        return list(self.request("thread/list", {"archived": False, "useStateDbOnly": True}).get("data", []))

    def read_context(self, candidate: ThreadCandidate, message_limit: int = 5) -> TaskContext:
        turns = self.request("thread/read", {"threadId": candidate.thread_id, "includeTurns": True}).get("thread", {}).get("turns", [])
        texts = [text for turn in turns[-2:] for item in turn.get("items", []) if (text := text_from_item(item))]
        return TaskContext(candidate, tuple(texts[-message_limit:]), texts[-1] if texts else None)

    def send_reply(self, candidate: ThreadCandidate, reply: str) -> None:
        # Idle threads must use resume/start flow; steer is valid only for active turns.
        input_items = [{"type": "text", "text": reply}]
        if candidate.active_turn_id:
            self.request("turn/steer", {"threadId": candidate.thread_id, "expectedTurnId": candidate.active_turn_id, "input": input_items})
            return
        # Resume establishes/rejoins the thread. TurnStartParams then needs only
        # the thread ID and input; never pass a guessed resume-result field.
        self.request("thread/resume", {"threadId": candidate.thread_id})
        self.request("turn/start", {"threadId": candidate.thread_id, "input": input_items})
