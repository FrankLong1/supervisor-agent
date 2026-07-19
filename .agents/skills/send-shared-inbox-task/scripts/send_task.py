#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


DIRECTORY = {
    "alice@gravitationalventures.com": {
        "principal": "alice@gravitationalventures.com",
        "agent_id": "7a3fa6fa-2f49-42c9-bb6a-d4a9eafed720",
    },
    "frank@gravitationalventures.com": {
        "principal": "frank@gravitationalventures.com",
        "agent_id": "fa212e75-7581-457b-a918-4ac8bc617bbc",
    },
}


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Resolve and queue a shared inbox TASK_PROPOSAL"
    )
    command.add_argument("--to", required=True, choices=tuple(DIRECTORY))
    command.add_argument("--subject", required=True)
    body = command.add_mutually_exclusive_group(required=True)
    body.add_argument("--body-text")
    body.add_argument("--body-file", type=Path)
    command.add_argument("--idempotency-key")
    command.add_argument("--workspace-key")
    command.add_argument("--source-codex-thread-id")
    command.add_argument("--expected-result")
    command.add_argument("--state-path", type=Path)
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--supervisor-bin")
    return command


def read_body(args: argparse.Namespace) -> str:
    try:
        value = (
            args.body_file.read_text(encoding="utf-8")
            if args.body_file
            else args.body_text
        )
    except (OSError, UnicodeError) as error:
        raise ValueError("task body file is not readable UTF-8") from error
    if not value or not value.strip():
        raise ValueError("task body must be non-empty")
    if len(value.encode("utf-8")) > 65536:
        raise ValueError("task body exceeds 65536 bytes")
    return value


def resolve(
    sender_address: str,
    sender_agent_id: str,
    recipient_address: str,
) -> tuple[str, dict[str, str]]:
    sender_address = sender_address.strip().casefold()
    sender = DIRECTORY.get(sender_address)
    if sender is None or sender["agent_id"] != sender_agent_id:
        raise ValueError(
            "configured sender address and agent ID do not identify Alice or Frank"
        )
    recipient_address = recipient_address.strip().casefold()
    recipient = DIRECTORY[recipient_address]
    if recipient_address == sender_address:
        raise ValueError("refusing to queue a shared inbox task to the sender itself")
    return sender_address, recipient


def stable_key(
    sender: str, recipient: str, subject: str, body: str,
    workspace_key: str = "supervisor-agent",
) -> str:
    canonical = json.dumps(
        {
            "sender": sender,
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "workspace_key": workspace_key,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "skill-task:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def supervisor_command(explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "supervisor"]
    supervisor = shutil.which("supervisor")
    if supervisor:
        return [supervisor]
    raise ValueError("neither uv nor the supervisor executable is available")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        sender_address = os.environ.get("SUPERVISOR_INBOX_AGENT_ADDRESS", "")
        sender_agent_id = os.environ.get("SUPERVISOR_INBOX_AGENT_ID", "")
        sender, recipient = resolve(sender_address, sender_agent_id, args.to)
        subject = args.subject.strip()
        if not subject or len(subject) > 500:
            raise ValueError("task subject must be between 1 and 500 characters")
        body = read_body(args)
        workspace_key = (
            args.workspace_key
            or os.environ.get("SUPERVISOR_INBOX_WORKSPACE_KEY")
            or "supervisor-agent"
        ).strip()
        if not workspace_key or len(workspace_key.encode("utf-8")) > 64:
            raise ValueError("workspace key must be between 1 and 64 bytes")
        source_codex_thread_id = (
            args.source_codex_thread_id
            or os.environ.get("CODEX_THREAD_ID")
            or None
        )
        key = args.idempotency_key or stable_key(
            sender, recipient["principal"], subject, body, workspace_key
        )
        if not key or len(key) > 200:
            raise ValueError("idempotency key must be between 1 and 200 characters")
        projection = {
            "sender_address": sender,
            "sender_principal": DIRECTORY[sender]["principal"],
            "recipient_address": recipient["principal"],
            "recipient_principal": recipient["principal"],
            "recipient_agent_id": recipient["agent_id"],
            "kind": "TASK_PROPOSAL",
            "subject": subject,
            "body_bytes": len(body.encode("utf-8")),
            "idempotency_key": key,
            "workspace_key": workspace_key,
            "source_codex_thread_id": source_codex_thread_id,
        }
        if args.dry_run:
            print(json.dumps({"dry_run": True, **projection}, sort_keys=True))
            return 0
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="shared-inbox-task-", delete=True
        ) as body_file:
            os.chmod(body_file.name, 0o600)
            body_file.write(body)
            body_file.flush()
            command = [
                *supervisor_command(args.supervisor_bin),
                "inbox",
                "send-task",
                "--recipient-address",
                recipient["principal"],
                "--subject",
                subject,
                "--body-file",
                body_file.name,
                "--idempotency-key",
                key,
                "--workspace-key",
                workspace_key,
            ]
            if source_codex_thread_id:
                command.extend(("--source-codex-thread-id", source_codex_thread_id))
            if args.expected_result:
                command.extend(("--expected-result", args.expected_result))
            if args.state_path:
                command.extend(("--state-path", str(args.state_path)))
            return subprocess.run(command, check=False).returncode
    except ValueError as error:
        print(
            json.dumps({"queued": False, "error": str(error)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
