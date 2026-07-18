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
from uuid import UUID


ACTORS = {
    "alice": {
        "principal": "alice@gravitationalventures.com",
        "address_env": "SUPERVISOR_INBOX_ALICE_AGENT_ADDRESS",
        "id_env": "SUPERVISOR_INBOX_ALICE_AGENT_ID",
    },
    "frank": {
        "principal": "frank@gravitationalventures.com",
        "address_env": "SUPERVISOR_INBOX_FRANK_AGENT_ADDRESS",
        "id_env": "SUPERVISOR_INBOX_FRANK_AGENT_ID",
    },
}
ALIASES = {
    "alice": "alice",
    "alice@": "alice",
    "alice@gravitationalventures.com": "alice",
    "frank": "frank",
    "frank@": "frank",
    "frank@gravitationalventures.com": "frank",
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


def configured_directory(env: dict[str, str]) -> dict[str, dict[str, str]]:
    directory: dict[str, dict[str, str]] = {}
    for name, actor in ACTORS.items():
        address = env.get(actor["address_env"], "").strip().casefold()
        agent_id = env.get(actor["id_env"], "").strip()
        if not address or not agent_id:
            raise ValueError(
                f"{actor['address_env']} and {actor['id_env']} must be configured"
            )
        try:
            UUID(agent_id)
        except ValueError as error:
            raise ValueError(f"{actor['id_env']} must be a UUID") from error
        directory[name] = {
            "principal": actor["principal"],
            "address": address,
            "agent_id": agent_id,
        }
    if directory["alice"]["address"] == directory["frank"]["address"]:
        raise ValueError("Alice and Frank agent addresses must be distinct")
    if directory["alice"]["agent_id"] == directory["frank"]["agent_id"]:
        raise ValueError("Alice and Frank agent IDs must be distinct")
    return directory


def resolve(
    sender_address: str,
    sender_agent_id: str,
    target_alias: str,
    directory: dict[str, dict[str, str]],
) -> tuple[str, dict[str, str]]:
    sender_address = sender_address.strip().casefold()
    senders = [
        name
        for name, actor in directory.items()
        if actor["address"] == sender_address and actor["agent_id"] == sender_agent_id
    ]
    if len(senders) != 1:
        raise ValueError(
            "configured sender address and agent ID do not identify Alice or Frank"
        )
    sender = senders[0]
    recipient_name = ALIASES[target_alias.casefold()]
    if recipient_name == sender:
        raise ValueError("refusing to queue a shared inbox task to the sender itself")
    return sender, directory[recipient_name]


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
        directory = configured_directory(dict(os.environ))
        sender_address = os.environ.get("SUPERVISOR_INBOX_AGENT_ADDRESS", "")
        sender_agent_id = os.environ.get("SUPERVISOR_INBOX_AGENT_ID", "")
        sender, recipient = resolve(
            sender_address, sender_agent_id, args.to, directory
        )
        subject = args.subject.strip()
        if not subject or len(subject) > 500:
            raise ValueError("task subject must be between 1 and 500 characters")
        body = read_body(args)
        key = args.idempotency_key or stable_key(
            directory[sender]["address"], recipient["address"], subject, body
        )
        if not key or len(key) > 200:
            raise ValueError("idempotency key must be between 1 and 200 characters")
        projection = {
            "sender_address": directory[sender]["address"],
            "sender_principal": directory[sender]["principal"],
            "recipient_address": recipient["address"],
            "recipient_principal": recipient["principal"],
            "recipient_agent_id": recipient["agent_id"],
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
                recipient["address"],
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
