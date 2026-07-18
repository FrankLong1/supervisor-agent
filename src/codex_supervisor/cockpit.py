from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from typing import Any, Mapping
from uuid import UUID, uuid5

from .codex import AppServerClient
from .state import SupervisorState


COCKPIT_TITLE = "SUPERVISOR AGENT"
COCKPIT_UPDATE_NAMESPACE = UUID("4bb76166-f45c-4ae0-8e2b-e907473238fd")


@dataclass(frozen=True)
class CockpitConfig:
    thread_id: str | None = None
    expected_title: str = COCKPIT_TITLE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CockpitConfig":
        values = os.environ if env is None else env
        thread_id = values.get("SUPERVISOR_COCKPIT_THREAD_ID", "").strip() or None
        if thread_id is not None:
            try:
                UUID(thread_id)
            except ValueError as error:
                raise ValueError(
                    "SUPERVISOR_COCKPIT_THREAD_ID must be a UUID"
                ) from error
        return cls(thread_id=thread_id)


def safe_status_snapshot(
    tick: Mapping[str, Any], state: SupervisorState
) -> dict[str, Any]:
    """Return the allowlisted, body-free status projection sent to the cockpit."""
    local = tick.get("local") if isinstance(tick.get("local"), dict) else {}
    inbox = tick.get("inbox") if isinstance(tick.get("inbox"), dict) else {}
    inbox_status = state.inbox_status()
    deliveries = inbox.get("deliveries")
    routes = inbox.get("routes")
    safe_routes = (
        {
            str(route)[:64]: int(count)
            for route, count in routes.items()
            if isinstance(route, str) and type(count) is int and count >= 0
        }
        if isinstance(routes, dict)
        else {}
    )
    blockers: list[str] = []
    if local.get("ok") is not True:
        blockers.append("local Codex app-server poll is unhealthy")
    elif local.get("unread_supported") is not True:
        blockers.append("authoritative Codex hasUnreadTurn signal is unavailable")
    blockers.append("generic local Codex task mutation is disabled by supervisor policy")
    if inbox.get("ok") is not True:
        blockers.append("Cloud SQL inbox poll is unhealthy")
    elif (
        inbox.get("configured") is True
        and inbox.get("mode") == "poll"
        and inbox.get("handling_enabled") is not True
    ):
        blockers.append("matching sender-verified inbox canary evidence is required")
    human_review_count = int(state.status()["human_review_count"])
    if int(inbox_status["ambiguous_count"]) > 0:
        blockers.append("ambiguous inbox processing requires human review")
    elif human_review_count > 0:
        blockers.append("one or more items require explicit human review")

    return {
        "schema_version": 1,
        "overall": str(tick.get("result"))[:32],
        "local": {
            "source_ok": local.get("ok") is True,
            "reachable": local.get("reachable") is True,
            "unread_supported": local.get("unread_supported") is True,
            "eligible_candidate_count": int(local.get("candidate_count", 0))
            if type(local.get("candidate_count", 0)) is int
            else 0,
        },
        "inbox": {
            "source_ok": inbox.get("ok") is True,
            "configured": inbox.get("configured") is True,
            "mode": str(inbox.get("mode", "unknown"))[:32],
            "handling_enabled": inbox.get("handling_enabled") is True,
            "queued_count": len(deliveries) if isinstance(deliveries, list) else 0,
            "observed_count": int(inbox.get("observed", 0))
            if type(inbox.get("observed", 0)) is int
            else 0,
            "routes": safe_routes,
            "claimed_this_tick": inbox.get("claimed") is True,
            "active_processing_count": int(
                inbox_status["active_processing_count"]
            ),
            "ambiguous_count": int(inbox_status["ambiguous_count"]),
            "active_canary_evidence_count": int(
                inbox_status["active_canary_evidence_count"]
            ),
        },
        "human_review_count": human_review_count,
        "mutation_blockers": blockers,
    }


def snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def cockpit_prompt(snapshot: Mapping[str, Any]) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return (
        "<automated_supervisor_cockpit_update schema_version=\"1\">\n"
        "This is an automated, body-free status signal from the configured local "
        "supervisor worker; it is not a human-authored message. Inspect the live "
        "repository, heartbeat, local Codex inventory, Cloud SQL queue, human-review "
        "state, and canary state. Report only meaningful changes, remain fail-closed, "
        "and do not claim ambiguous work or bypass identity/canary gates.\n"
        f"status={encoded}\n"
        "</automated_supervisor_cockpit_update>"
    )


class CockpitBridge:
    def __init__(
        self,
        config: CockpitConfig,
        client: AppServerClient,
        state: SupervisorState,
    ):
        self.config = config
        self.client = client
        self.state = state

    def publish(self, tick: Mapping[str, Any]) -> dict[str, Any]:
        if self.config.thread_id is None:
            return {"configured": False, "delivered": False, "reason": "disabled"}
        snapshot = safe_status_snapshot(tick, self.state)
        fingerprint = snapshot_fingerprint(snapshot)
        update_id = self.state.observe_cockpit_update(
            thread_id=self.config.thread_id, fingerprint=fingerprint
        )
        pending = self.state.pending_cockpit_update(self.config.thread_id)
        if pending is None:
            return {
                "configured": True,
                "delivered": False,
                "reason": "unchanged",
            }
        client_message_id = str(
            uuid5(
                COCKPIT_UPDATE_NAMESPACE,
                f"{self.config.thread_id}:{pending['id']}:{pending['fingerprint']}",
            )
        )
        try:
            receipt = self.client.send_cockpit_update(
                thread_id=self.config.thread_id,
                expected_title=self.config.expected_title,
                message=cockpit_prompt(snapshot),
                client_message_id=client_message_id,
            )
        except Exception as error:
            self.state.fail_cockpit_update(int(pending["id"]), type(error).__name__)
            raise
        self.state.deliver_cockpit_update(
            int(pending["id"]), receipt.delivery_id, receipt.transport
        )
        return {
            "configured": True,
            "delivered": True,
            "new_state": update_id is not None,
            "transport": receipt.transport,
        }
