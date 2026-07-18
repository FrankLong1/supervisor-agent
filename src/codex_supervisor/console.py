from __future__ import annotations

import html
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .codex import AppServerClient
from .state import read_human_review_queue


STYLESHEET = b"""body{font:16px/1.55 system-ui,sans-serif;max-width:900px;margin:2.5rem auto;padding:0 1.25rem;background:#f7f8fa;color:#19212b}h1{margin-bottom:0}h2{margin:2.5rem 0 1rem;font-size:1.15rem}.muted{color:#687382;font-size:.875rem}.work-list,.review-list{display:grid;gap:.85rem}.work,.review{background:#fff;border:1px solid #dde2e8;border-radius:10px;padding:1rem 1.15rem;box-shadow:0 1px 2px #19212b0a}.work{display:flex;justify-content:space-between;gap:1rem}.state,.meta{color:#687382;font-size:.875rem}.task-label,.reason-label{margin:0 0 .2rem;font-size:.8rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#596575}.review h3{margin:0;font-size:1.12rem}.reason-label{margin-top:1rem}.reason-points{margin:.35rem 0 0;padding-left:1.25rem}.reason-points>li{padding:.22rem 0}.reason-points strong{font-weight:750;color:#111820}.reason-points ul{margin:.2rem 0 .3rem;padding-left:1.15rem}.reason-points ul li{padding:.12rem 0}.meta{margin:.75rem 0 0}.empty{color:#687382;font-style:italic}"""


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


def _reason_points_html(reason: str) -> str:
    """Make the durable free-text reason scannable without changing its content."""
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", reason.strip()) if sentence.strip()]
    status = sentences[0] if sentences else "No stop reason was recorded."
    action_index = next((index for index, sentence in enumerate(sentences[1:], 1) if sentence.startswith(("What remains", "To unblock", "The human", "Codex is blocked", "Whether to", "Until "))), None)
    if action_index is not None:
        context = " ".join(sentences[1:action_index]) or "No separate supporting context was recorded."
        next_step = " ".join(sentences[action_index:])
    elif len(sentences) >= 3:
        context = " ".join(sentences[1:-1])
        next_step = sentences[-1]
    elif len(sentences) == 2:
        context, next_step = sentences[1], "No separate next step was recorded."
    else:
        context, next_step = "No separate supporting context was recorded.", "Review this task and decide how to proceed."
    points = (("Status", status), ("What Fable established", context), ("Your next step", next_step))
    return "<ul class=\"reason-points\">" + "".join(
        f"<li><strong>{html.escape(label)}:</strong>{_sub_points_html(content)}</li>" for label, content in points
    ) + "</ul>"


def _sub_points_html(content: str) -> str:
    """Bound each section to four short, readable supporting points."""
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", content) if sentence.strip()]
    points = [_truncate_point(sentence) for sentence in sentences[:4]] or ["No additional detail was recorded."]
    return "<ul>" + "".join(f"<li>{html.escape(point)}</li>" for point in points) + "</ul>"


def _truncate_point(point: str, limit: int = 240) -> str:
    if len(point) <= limit:
        return point
    shortened = point[:limit - 1].rsplit(" ", 1)[0].rstrip()
    return f"{shortened or point[:limit - 1].rstrip()}…"


def render_html(state_path: Path, socket_path: Path) -> str:
    rows = read_human_review_queue(state_path)
    active_rows = _active_work(state_path, socket_path, {row["thread_id"] for row in rows})
    active_body = "".join(
        f"<article class=\"work\"><span>{html.escape(item['title'])}</span>"
        f"<span class=\"state\">{html.escape(item['state'])}</span></article>"
        for item in active_rows
    ) or "<p class=\"empty\">Nothing is currently in progress.</p>"
    body = "".join(
        "<article class=\"review\">"
        "<p class=\"task-label\">Task</p>"
        f"<h3>{html.escape(str(row['title']) or 'Untitled task')}</h3>"
        "<p class=\"reason-label\">Why Fable stopped</p>"
        f"{_reason_points_html(str(row['reason']))}"
        f"<p class=\"meta\">Fable turns: {row['fable_turn_count'] if row['fable_turn_count'] is not None else 'not recorded'}"
        f" &middot; marked {html.escape(str(row['marked_at']))}</p>"
        "</article>"
        for row in rows
    ) or "<p class=\"empty\">Nothing currently needs human review.</p>"
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><meta http-equiv=\"refresh\" content=\"15\">
<title>Fable human review queue</title>
<link rel="stylesheet" href="/styles.css">
</head><body><h1>Fable supervisor</h1><p><small>Read-only. Refreshes every 15 seconds.</small></p>
<h2>Fable is still juggling</h2><section class="work-list">{active_body}</section>
<h2>Needs your review</h2><section class="review-list">{body}</section></body></html>"""


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
