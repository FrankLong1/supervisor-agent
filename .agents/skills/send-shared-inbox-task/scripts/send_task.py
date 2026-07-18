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
    "research@alice": {
        "owner": "alice@",
        "agent_id": "7a3fa6fa-2f49-42c9-bb6a-d4a9eafed720",
    },
    "helper@bob": {
        "owner": "frank@",
        "agent_id": "fa212e75-7581-457b-a918-4ac8bc617bbc",
    },
}
ALIASES = {
    "alice": "research@alice",
    "alice@": "research@alice",
    "frank": "helper@bob",
    "frank@": "helper@bob",
    "bob": "helper@bob",
}


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Resolve and queue a shared inbox TASK_PROPOSAL"
    )
    command.add_argument("--to", required=True, choices=tuple(ALIASES))
    command.add_argument("--subject", required=True)
    body = command.add_mutually_exclusive_group(required=True)
    body.add_argument("--body-text")
    body.add_argument("--body-file", type=Path)
    command.add_argument("--idempotency-key")
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


def resolve(sender: str, target_alias: str) -> str:
    sender = sender.strip().casefold()
    if sender not in DIRECTORY:
        raise ValueError(
            "SUPERVISOR_INBOX_AGENT_ADDRESS must be research@alice or helper@bob"
        )
    recipient = ALIASES[target_alias.casefold()]
    if recipient == sender:
        raise ValueError("refusing to queue a shared inbox task to the sender itself")
    return recipient


def stable_key(sender: str, recipient: str, subject: str, body: str) -> str:
    canonical = json.dumps(
        {
            "sender": sender,
            "recipient": recipient,
            "subject": subject,
            "body": body,
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
        sender = os.environ.get("SUPERVISOR_INBOX_AGENT_ADDRESS", "").strip().casefold()
        recipient = resolve(sender, args.to)
        subject = args.subject.strip()
        if not subject or len(subject) > 500:
            raise ValueError("task subject must be between 1 and 500 characters")
        body = read_body(args)
        key = args.idempotency_key or stable_key(sender, recipient, subject, body)
        if not key or len(key) > 200:
            raise ValueError("idempotency key must be between 1 and 200 characters")
        projection = {
            "sender_address": sender,
            "sender_owner": DIRECTORY[sender.casefold()]["owner"],
            "recipient_address": recipient,
            "recipient_owner": DIRECTORY[recipient]["owner"],
            "recipient_agent_id": DIRECTORY[recipient]["agent_id"],
            "kind": "TASK_PROPOSAL",
            "subject": subject,
            "body_bytes": len(body.encode("utf-8")),
            "idempotency_key": key,
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
                recipient,
                "--subject",
                subject,
                "--body-file",
                body_file.name,
                "--idempotency-key",
                key,
            ]
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
