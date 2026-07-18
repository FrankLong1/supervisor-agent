from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
import re
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID


class InboxMode(StrEnum):
    DISABLED = "disabled"
    DRY_RUN = "dry-run"
    ONE_SHOT = "one-shot"
    POLL = "poll"


@dataclass(frozen=True)
class InboxConfig:
    mode: InboxMode = InboxMode.DISABLED
    dsn: str | None = None
    instance_id: str | None = None
    poll_seconds: int = 10
    claim_seconds: int = 120
    agent_id: str | None = None
    agent_address: str | None = None
    schema: str = "public"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "InboxConfig":
        source = os.environ if env is None else env
        try:
            mode = InboxMode(source.get("SUPERVISOR_INBOX_MODE", "disabled"))
            poll = int(source.get("SUPERVISOR_INBOX_POLL_SECONDS", "10"))
            claim = int(source.get("SUPERVISOR_INBOX_CLAIM_SECONDS", "120"))
        except (ValueError, TypeError) as error:
            raise ValueError("malformed supervisor inbox configuration") from error
        config = cls(
            mode=mode,
            dsn=source.get("SUPERVISOR_INBOX_DSN") or None,
            instance_id=source.get("SUPERVISOR_INBOX_INSTANCE_ID") or None,
            poll_seconds=poll,
            claim_seconds=claim,
            agent_id=source.get("SUPERVISOR_INBOX_AGENT_ID") or None,
            agent_address=source.get("SUPERVISOR_INBOX_AGENT_ADDRESS") or None,
            schema=source.get("SUPERVISOR_INBOX_SCHEMA", "public"),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not 5 <= self.poll_seconds <= 300:
            raise ValueError("SUPERVISOR_INBOX_POLL_SECONDS must be between 5 and 300")
        if not 5 <= self.claim_seconds <= 900:
            raise ValueError("SUPERVISOR_INBOX_CLAIM_SECONDS must be between 5 and 900")
        if self.mode is InboxMode.DISABLED:
            return
        if not self.dsn or not self.instance_id or not self.instance_id.strip():
            raise ValueError("enabled inbox mode requires DSN and stable instance ID")
        if len(self.instance_id.encode("utf-8")) > 200:
            raise ValueError("inbox instance ID is too long")
        if self.agent_id:
            try:
                UUID(self.agent_id)
            except ValueError as error:
                raise ValueError("SUPERVISOR_INBOX_AGENT_ID must be a UUID") from error
        if self.agent_address and len(self.agent_address.encode("utf-8")) > 512:
            raise ValueError("SUPERVISOR_INBOX_AGENT_ADDRESS is too long")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.schema):
            raise ValueError("SUPERVISOR_INBOX_SCHEMA must be a PostgreSQL identifier")

    def require_live_identity(self) -> None:
        if self.mode not in {InboxMode.ONE_SHOT, InboxMode.POLL}:
            raise ValueError("live inbox mutation requires one-shot mode")
        if not self.agent_id:
            raise ValueError("live inbox mutation requires SUPERVISOR_INBOX_AGENT_ID")

    @property
    def masked_target(self) -> str | None:
        if not self.dsn:
            return None
        try:
            parsed = urlsplit(self.dsn)
            if parsed.scheme and parsed.hostname:
                port = f":{parsed.port}" if parsed.port else ""
                return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"
        except ValueError:
            pass
        redacted = re.sub(
            r"(?i)(password|pass|token|access_token)\s*=\s*\S+", r"\1=***", self.dsn
        )
        redacted = re.sub(r"//[^/@\s]+@", "//***@", redacted)
        return redacted[:512]
