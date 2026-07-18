from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from typing import Any

from .models import (
    ClaimedEnvelopeValidationError,
    InboxClaim,
    InboxEnvelope,
    InboxOutcome,
    InboxValidationError,
    MessageKind,
    SendReceipt,
)


ADAPTER_IDENTITY = "postgres-stored-functions/v0"
CONTRACT_VERSION = "cloud-sql-agent-inbox-v0"


class PostgresInboxAdapter:
    """Typed client for only the v0 public stored-function surface."""

    adapter_identity = ADAPTER_IDENTITY
    contract_version = CONTRACT_VERSION

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        connect: Callable[..., Any] | None = None,
    ):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        if not schema.replace("_", "a").isalnum() or schema[0].isdigit():
            raise ValueError("invalid PostgreSQL schema")
        if connect is None:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as error:
                raise RuntimeError(
                    "install the 'inbox' extra to use PostgreSQL"
                ) from error

            def connect(value):
                return psycopg.connect(value, row_factory=dict_row)

        self._connection = connect(dsn)
        self._schema = schema

    def close(self) -> None:
        self._connection.close()

    def _query(self, sql: str, parameters: tuple[Any, ...]) -> list[Mapping[str, Any]]:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                description = getattr(cursor, "description", None)
                if not description:
                    rows: list[Mapping[str, Any]] = []
                else:
                    raw_rows = cursor.fetchall()
                    if raw_rows and not isinstance(raw_rows[0], Mapping):
                        names = [
                            item.name if hasattr(item, "name") else item[0]
                            for item in description
                        ]
                        rows = [dict(zip(names, row, strict=True)) for row in raw_rows]
                    else:
                        rows = list(raw_rows)
            self._connection.commit()
            return rows
        except Exception:
            self._connection.rollback()
            raise

    def _function(self, name: str) -> str:
        return f'"{self._schema}"."{name}"'

    def authenticated_identity(self) -> str:
        rows = self._query("SELECT session_user AS authenticated_identity", ())
        if (
            len(rows) != 1
            or not isinstance(rows[0].get("authenticated_identity"), str)
            or not rows[0]["authenticated_identity"].strip()
        ):
            raise RuntimeError("database session identity is not attributable")
        return str(rows[0]["authenticated_identity"])

    def list_deliveries(
        self, limit: int, status: str | None = None
    ) -> tuple[InboxEnvelope, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("delivery list limit must be between 1 and 100")
        rows = self._query(
            f"SELECT * FROM {self._function('inbox_list_deliveries')}(%s, %s, %s)",
            (limit, None, status),
        )
        return tuple(InboxEnvelope.from_row(row) for row in rows)

    def claim_next(
        self,
        instance_id: str,
        lease_seconds: int,
        recipient_agent_id: str | None = None,
    ) -> InboxClaim | None:
        rows = self._query(
            f"SELECT * FROM {self._function('inbox_claim_next_delivery')}(%s, %s, %s)",
            (instance_id, lease_seconds, recipient_agent_id),
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError("claim function returned more than one delivery")
        try:
            return InboxClaim.from_row(rows[0])
        except InboxValidationError as error:
            raise ClaimedEnvelopeValidationError.from_row(rows[0], error) from error

    @staticmethod
    def _boolean_result(rows: list[Mapping[str, Any]], function_name: str) -> bool:
        if len(rows) != 1 or len(rows[0]) != 1:
            raise RuntimeError(f"{function_name} returned an unexpected shape")
        value = next(iter(rows[0].values()))
        if not isinstance(value, bool):
            raise RuntimeError(f"{function_name} did not return boolean")
        return value

    def mark_received(self, delivery_id: str, instance_id: str) -> bool:
        rows = self._query(
            f"SELECT {self._function('inbox_mark_received')}(%s, %s)",
            (delivery_id, instance_id),
        )
        return self._boolean_result(rows, "inbox_mark_received")

    def complete(
        self,
        delivery_id: str,
        instance_id: str,
        outcome: InboxOutcome,
        detail: Mapping[str, Any],
    ) -> bool:
        encoded = json.dumps(detail, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 4096:
            raise ValueError("completion detail exceeds 4096 bytes")
        rows = self._query(
            f"SELECT {self._function('inbox_complete_delivery')}(%s, %s, %s, %s::jsonb)",
            (delivery_id, instance_id, outcome.value, encoded),
        )
        return self._boolean_result(rows, "inbox_complete_delivery")

    def send_message(
        self,
        *,
        sender_agent_id: str,
        recipient_address: str,
        kind: MessageKind,
        subject: str,
        body_text: str,
        body_json: Mapping[str, Any],
        idempotency_key: str,
        thread_id: str | None = None,
        reply_to_message_id: str | None = None,
        expires_at=None,
    ) -> SendReceipt:
        rows = self._query(
            f"SELECT * FROM {self._function('inbox_send_message')}(%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)",
            (
                sender_agent_id,
                recipient_address,
                kind.value,
                subject,
                body_text,
                json.dumps(body_json, sort_keys=True, separators=(",", ":")),
                idempotency_key,
                thread_id,
                reply_to_message_id,
                expires_at,
            ),
        )
        if len(rows) != 1:
            raise RuntimeError("inbox_send_message returned an unexpected row count")
        return SendReceipt.from_row(rows[0])
