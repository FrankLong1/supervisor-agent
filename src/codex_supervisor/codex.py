from __future__ import annotations

import json
import base64
import os
import socket
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import (
    CockpitDeliveryReceipt,
    DeliveryReceipt,
    TaskContext,
    ThreadCandidate,
    text_from_item,
)


class AppServerError(RuntimeError):
    pass


class AppServerClient:
    """App-server client using its documented local WebSocket transport."""
    unread_supported = False

    def __init__(self, socket_path: str | Path, timeout_seconds: float = 10):
        self.socket_path, self.timeout_seconds, self._request_id = str(socket_path), timeout_seconds, 0

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        with self._connect() as connection:
            self._send(connection, {"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "codex-unread-task-supervisor", "version": "0.1.0"}}})
            self._read_response(connection, 0)
            self._send(connection, {"method": "initialized"})
            self._send(connection, {"id": self._request_id, "method": method, "params": params})
            response = self._read_response(connection, self._request_id)
        if "error" in response:
            raise AppServerError(str(response["error"]))
        return response.get("result", {})

    def _connect(self) -> socket.socket:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(self.timeout_seconds)
            connection.connect(self.socket_path)
            key = base64.b64encode(os.urandom(16)).decode()
            request = (
                "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                f"Sec-WebSocket-Key: {key}\r\n\r\n"
            )
            connection.sendall(request.encode())
            headers = self._read_http_headers(connection)
            if not headers.startswith("HTTP/1.1 101"):
                raise AppServerError(f"app-server WebSocket upgrade failed: {headers.splitlines()[0] if headers else 'empty response'}")
            return connection
        except Exception:
            connection.close()
            raise

    @staticmethod
    def _read_http_headers(connection: socket.socket) -> str:
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = connection.recv(4096)
            if not chunk:
                raise AppServerError("app-server closed during WebSocket upgrade")
            buffer += chunk
            if len(buffer) > 65536:
                raise AppServerError("oversized WebSocket upgrade response")
        return buffer.decode("ascii", errors="replace")

    @staticmethod
    def _send(connection: socket.socket, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":")).encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        elif len(payload) <= 0xFFFF:
            header.extend([0x80 | 126, *len(payload).to_bytes(2, "big")])
        else:
            raise AppServerError("app-server request frame is too large")
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        connection.sendall(bytes(header) + mask + masked)

    @staticmethod
    def _read_response(connection: socket.socket, request_id: int) -> dict[str, Any]:
        while True:
            opcode, payload = AppServerClient._read_frame(connection)
            if opcode == 0x9:
                connection.sendall(bytes([0x8A, len(payload)]) + payload)
                continue
            if opcode == 0x8:
                raise AppServerError("app-server closed WebSocket connection")
            if opcode != 0x1:
                continue
            response = json.loads(payload)
            if response.get("id") == request_id:
                return response

    @staticmethod
    def _read_frame(connection: socket.socket) -> tuple[int, str]:
        first, second = AppServerClient._read_exact(connection, 2)
        opcode, size = first & 0x0F, second & 0x7F
        if size == 126:
            size = int.from_bytes(AppServerClient._read_exact(connection, 2), "big")
        elif size == 127:
            size = int.from_bytes(AppServerClient._read_exact(connection, 8), "big")
        masked = bool(second & 0x80)
        mask = AppServerClient._read_exact(connection, 4) if masked else None
        data = AppServerClient._read_exact(connection, size)
        if mask is not None:
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        return opcode, data.decode()

    @staticmethod
    def _read_exact(connection: socket.socket, size: int) -> bytes:
        chunks = b""
        while len(chunks) < size:
            chunk = connection.recv(size - len(chunks))
            if not chunk:
                raise AppServerError("app-server closed WebSocket connection")
            chunks += chunk
        return chunks

    def list_unarchived_threads(self) -> list[dict]:
        return list(
            self.request(
                "thread/list",
                {"archived": False, "limit": 100, "useStateDbOnly": True},
            ).get("data", [])
        )

    @staticmethod
    def _turn_id(result: dict[str, Any]) -> str | None:
        turn = result.get("turn")
        value = turn.get("id") if isinstance(turn, dict) else None
        value = value or result.get("turnId") or result.get("id")
        return str(value) if value else None

    def send_cockpit_update(
        self,
        *,
        thread_id: str,
        expected_title: str,
        message: str,
        client_message_id: str,
    ) -> CockpitDeliveryReceipt:
        """Send one explicitly automated update to an exact unarchived cockpit."""
        matches = [
            thread
            for thread in self.list_unarchived_threads()
            if thread.get("id") == thread_id
        ]
        if len(matches) != 1:
            raise AppServerError("configured cockpit is not exactly one unarchived task")
        thread = matches[0]
        if thread.get("name") != expected_title:
            raise AppServerError("configured cockpit title does not match")
        status = thread.get("status")
        status_type = status.get("type") if isinstance(status, dict) else None
        input_items = [{"type": "text", "text": message}]
        if status_type == "active":
            context = self.request(
                "thread/read", {"threadId": thread_id, "includeTurns": True}
            ).get("thread", {})
            turns = context.get("turns", []) if isinstance(context, dict) else []
            active_turn_id = next(
                (
                    str(turn["id"])
                    for turn in reversed(turns)
                    if isinstance(turn, dict)
                    and turn.get("status") == "inProgress"
                    and turn.get("id")
                ),
                None,
            )
            if active_turn_id is None:
                raise AppServerError("active cockpit has no verifiable in-progress turn")
            result = self.request(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "expectedTurnId": active_turn_id,
                    "clientUserMessageId": client_message_id,
                    "input": input_items,
                },
            )
            return CockpitDeliveryReceipt(self._turn_id(result), "turn/steer")
        if status_type != "idle":
            raise AppServerError("configured cockpit is neither idle nor active")
        self.request("thread/resume", {"threadId": thread_id})
        result = self.request(
            "turn/start",
            {
                "threadId": thread_id,
                "clientUserMessageId": client_message_id,
                "input": input_items,
            },
        )
        return CockpitDeliveryReceipt(self._turn_id(result), "turn/start")

    def read_context(self, candidate: ThreadCandidate, message_limit: int = 5) -> TaskContext:
        turns = self.request("thread/read", {"threadId": candidate.thread_id, "includeTurns": True}).get("thread", {}).get("turns", [])
        texts = [text for turn in turns[-2:] for item in turn.get("items", []) if (text := text_from_item(item))]
        active_turn_id = next((str(turn["id"]) for turn in reversed(turns) if turn.get("status") == "inProgress"), None)
        return TaskContext(replace(candidate, active_turn_id=active_turn_id), tuple(texts[-message_limit:]), texts[-1] if texts else None)

    def send_reply(self, candidate: ThreadCandidate, reply: str) -> DeliveryReceipt:
        # Idle threads must use resume/start flow; steer is valid only for active turns.
        input_items = [{"type": "text", "text": reply}]
        if candidate.active_turn_id:
            result = self.request("turn/steer", {"threadId": candidate.thread_id, "expectedTurnId": candidate.active_turn_id, "input": input_items})
            return DeliveryReceipt(True, str(result.get("turnId") or result.get("id") or "") or None)
        # Resume establishes/rejoins the thread. TurnStartParams then needs only
        # the thread ID and input; never pass a guessed resume-result field.
        self.request("thread/resume", {"threadId": candidate.thread_id})
        result = self.request("turn/start", {"threadId": candidate.thread_id, "input": input_items})
        return DeliveryReceipt(True, str(result.get("turnId") or result.get("id") or "") or None)
