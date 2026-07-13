from __future__ import annotations

import html
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .codex import AppServerClient
from .state import read_human_review_queue


STYLESHEET = b"""body{font:16px system-ui;margin:2rem;color:#19212b}table{border-collapse:collapse;width:100%}th,td{padding:.7rem;text-align:left;border-bottom:1px solid #d9dee5;vertical-align:top}th{color:#5c6673}a{color:#0866d8}small{color:#5c6673}code{white-space:nowrap}"""


def _active_work(state_path: Path, socket_path: Path, reviewed_ids: set[str]) -> list[dict[str, str]]:
    try:
        threads = AppServerClient(socket_path).list_unarchived_threads()
        titles = {str(item["id"]): str(item.get("name") or item.get("title") or "Untitled task") for item in threads}
        active = [
            {"title": titles[str(item["id"])], "thread_id": str(item["id"]), "state": "active"}
            for item in threads if item.get("status", {}).get("type") == "active"
        ]
        active.extend(
            {"title": titles[str(item["id"])], "thread_id": str(item["id"]), "state": "queued for Fable"}
            for item in threads
            if item.get("status", {}).get("type") == "idle" and str(item["id"]) not in reviewed_ids
        )
        import sqlite3
        db = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
        try:
            claims = db.execute("SELECT thread_id,status FROM supervisor_delivery_claims WHERE status != 'CONFIRMED'").fetchall()
        finally:
            db.close()
        known = {item["thread_id"] for item in active}
        active.extend(
            {"title": titles.get(str(thread_id), "Untitled task"), "thread_id": str(thread_id), "state": f"delivery {status.lower()}"}
            for thread_id, status in claims if str(thread_id) not in known
        )
        return active
    except Exception:
        return []


def render_html(state_path: Path, socket_path: Path) -> str:
    rows = read_human_review_queue(state_path)
    active_rows = _active_work(state_path, socket_path, {row["thread_id"] for row in rows})
    active_body = "".join(
        "<tr>"
        f"<td>{html.escape(item['title'])}</td><td>{html.escape(item['state'])}</td>"
        f"<td><code>{html.escape(item['thread_id'])}</code></td></tr>"
        for item in active_rows
    ) or "<tr><td colspan=\"3\">Nothing is currently in progress.</td></tr>"
    body = "".join(
        "<tr>"
        f"<td>{html.escape(row['marked_at'])}</td>"
        f"<td>{html.escape(row['title'] or 'Untitled task')}</td>"
        f"<td>{html.escape(row['reason'])}</td>"
        f"<td><code>{html.escape(row['thread_id'])}</code></td>"
        "</tr>"
        for row in rows
    ) or "<tr><td colspan=\"4\">Nothing currently needs human review.</td></tr>"
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><meta http-equiv=\"refresh\" content=\"15\">
<title>Fable human review queue</title>
<link rel="stylesheet" href="/styles.css">
</head><body><h1>Fable supervisor</h1><p><small>Read-only. Refreshes every 15 seconds.</small></p>
<h2>Fable is still juggling</h2><table><thead><tr><th>Task</th><th>State</th><th>Task ID</th></tr></thead><tbody>{active_body}</tbody></table>
<h2>Needs your review</h2><table><thead><tr><th>Marked</th><th>Task</th><th>Why Fable stopped</th><th>Task ID</th></tr></thead><tbody>{body}</tbody></table></body></html>"""


def serve(state_path: Path, socket_path: Path, port: int, open_browser: bool = False) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/styles.css":
                self.send_response(200)
                self.send_header("Content-Type", "text/css; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; base-uri 'none'; form-action 'none'")
                self.send_header("Content-Length", str(len(STYLESHEET)))
                self.end_headers(); self.wfile.write(STYLESHEET); return
            if self.path not in {"/", "/index.html"}:
                self.send_error(404); return
            content = render_html(state_path, socket_path).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; base-uri 'none'; form-action 'none'")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers(); self.wfile.write(content)
        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(url, flush=True)
    if open_browser:
        threading.Timer(0.1, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
