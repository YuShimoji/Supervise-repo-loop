#!/usr/bin/env python3
"""Deterministic state and binding engine for supervise-repo-loop v2."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlparse


SCHEMA_VERSION = 2
SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINDINGS = SKILL_ROOT / "state" / "bindings.v2.json"
DEFAULT_HOSTS = SKILL_ROOT / "state" / "hosts.v2.json"
DEFAULT_ADAPTER = SKILL_ROOT / "state" / "adapter-snapshot.v1.json"
DEFAULT_COORDINATOR_STATE = SKILL_ROOT / "state" / "coordinator-state.v2.json"
DEFAULT_COORDINATOR_EVENTS = SKILL_ROOT / "state" / "events"
# Keep the installed filename stable while migrating its contents to schema v2.
# Coordinator state already points at this path, so changing the filename would
# strand the live waiting route during deployment.
DEFAULT_SCHEDULER_STATE = SKILL_ROOT / "state" / "coordinator-scheduler.v1.json"
LEGACY_SCHEDULER_STATE = DEFAULT_SCHEDULER_STATE
DEFAULT_MISSIONS = SKILL_ROOT / "state" / "missions"
DEFAULT_TERMINALS = SKILL_ROOT / "state" / "terminal_packets"
DEFAULT_NOTIFICATION_LEDGER = SKILL_ROOT / "state" / "notification-ledger.v2.json"
DEFAULT_PORTFOLIO_JSON = SKILL_ROOT / "state" / "coordinator-current-status.v1.json"
DEFAULT_PORTFOLIO_MARKDOWN = SKILL_ROOT / "state" / "coordinator-current-status.md"
PROTOCOL_PATH = SKILL_ROOT / "references" / "protocol-v2.md"

COORDINATOR_PROMPT_THIS_REPOSITORY = (
    "Use $supervise-repo-loop for this repository until terminal state."
)
COORDINATOR_PROMPT_NEXT_ACTIONABLE = (
    "Use $supervise-repo-loop for the next actionable registered repository "
    "until terminal state."
)
USER_VISIBLE_CODEX_ENTRY_POINTS = 1
SCHEDULER_STATE_VERSION = 2
DEFAULT_COORDINATOR_CONCURRENCY_LIMIT = 3
MAX_COORDINATOR_WAIT_TARGETS = 8

MODES = {"coordinator", "worker", "single-thread", "binding-repair"}
TERMINAL_STATES = {
    "COMPLETE",
    "USER_DECISION",
    "USER_ACTION",
    "BLOCKED",
    "SAFETY_CEILING",
}
EXTERNAL_EFFECT_NAMES = (
    "transport",
    "recipient_open",
    "upload",
    "publication",
    "release",
)
EXTERNAL_EFFECT_STATES = {
    "not_required",
    "pending",
    "complete",
    "blocked",
    "unverified",
}
MISSION_STATUSES = {
    "pending",
    "running",
    "complete",
    "blocked",
    "rejected",
    "superseded",
}
REVIEW_STATUSES = {
    "not_required",
    "pending",
    "accepted",
    "bounded_repair",
    "rejected",
}
REVIEW_GATES = {"none", "required"}
REVIEW_DEPTHS = {"light", "standard", "deep"}
USER_RESPONSE_ADJUDICATION_STATE = (
    "SUPERVISOR_USER_RESPONSE_ADJUDICATION_REQUESTED"
)
EXACT_OUTBOUND_WAIT_STATES = {
    "SUPERVISOR_WORK_ORDER_REQUESTED",
    "WORKER_DISPATCHED",
    "SUPERVISOR_ADJUDICATION_REQUESTED",
    USER_RESPONSE_ADJUDICATION_STATE,
}
WORKER_REPORT_FIELDS = (
    "repository_id",
    "mission_id",
    "attempt_id",
    "worker_task_id",
    "host_id",
    "result_classification",
    "active_artifact",
    "verification_summary",
    "deviations",
    "bounded_blocker",
    "suggested_decision_type",
    "git_state",
    "external_effect_state",
    "full_worker_report",
)

PORTFOLIO_STAGE_ORDER = (
    "MISSION",
    "WORK_ORDER",
    "WORKER",
    "WORKER_REPORT",
    "SUPERVISOR",
    "VERDICT",
    "NEXT_ROUTE",
)
PORTFOLIO_PROJECT_STATES = {
    "RUNNING",
    "READY",
    "WAITING_USER",
    "WAITING_EXTERNAL",
    "SYSTEM_BLOCKED",
    "MISSION_COMPLETE_NEXT_UNSELECTED",
    "PARKED_BY_POLICY",
    "PROJECT_COMPLETE",
}
BLOCKED_CONTRACT_VERSION = 2
BLOCKED_CONTRACT_REQUIRED_FIELDS = (
    "blocker_id",
    "introduced_by",
    "requirement",
    "rationale",
    "qualifies_when",
    "does_not_qualify",
    "diagnostics_completed",
    "owner",
    "next_permitted_probe",
    "retry_policy",
    "input_route",
    "baseline_observation_fingerprint",
)
BLOCKED_CONTRACT_REVISION_EVENT_KINDS = {
    "SUPERVISOR_PROJECT_QUESTION_VERDICT",
    "SUPERVISOR_DIRECTION_UPDATE_VERDICT",
    "SUPERVISOR_USER_RESPONSE_VERDICT",
    "SUPERVISOR_VERDICT_RECEIVED",
}
PROTOCOL_HANDOFF_ACTION_KINDS = {
    "route_user_response",
    "await_supervisor_verdict",
    "await_supervisor_work_order",
    "await_worker_result",
    "return_worker_result",
    "probe_authorized_runtime_repair",
    "return_authorized_runtime_recovery_result",
}

AUTHORIZED_RUNTIME_HANDLER_ID = "codex_windows_deny_read_acl_state_v1"
AUTHORIZED_RUNTIME_HANDLER_VERSION = 1
AUTHORIZED_RUNTIME_TARGET_PRE_SIZE = 22
AUTHORIZED_RUNTIME_TARGET_PRE_SHA256 = (
    "6a4875ddaceaa91fb3369f0f6d962f77442daf1b1d97733457d12bcabdf79441"
)
AUTHORIZED_RUNTIME_REPAIR_EXECUTION_SURFACE = "runtime_owner_maintenance"
AUTHORIZED_RUNTIME_PROBE_EXECUTION_SURFACE = "restricted_workspace_write"
AUTHORIZED_RUNTIME_DISALLOWED_PROBE_SURFACE = "danger-full-access"
AUTHORIZED_RUNTIME_REPAIR_EXECUTOR_AUTHORITY = (
    "runtime-owner maintenance surface on the same Thank host"
)
AUTHORIZED_RUNTIME_PHASES = {
    "AUTHORIZED",
    "EFFECT_INTENT",
    "EFFECT_PREPARED",
    "REPAIR_PREPARED",
    "ROLLBACK_REQUIRED",
    "RESULT_READY",
    "COMPLETE",
}
AUTHORIZED_RUNTIME_LOCAL_ACTION_KINDS = {
    "execute_authorized_runtime_repair",
    "rollback_authorized_runtime_repair",
}
AUTHORIZED_RUNTIME_EXTERNAL_ACTION_KINDS = {
    "probe_authorized_runtime_repair",
    "return_authorized_runtime_recovery_result",
}
AUTHORIZED_RUNTIME_ACTION_KINDS = (
    AUTHORIZED_RUNTIME_LOCAL_ACTION_KINDS
    | AUTHORIZED_RUNTIME_EXTERNAL_ACTION_KINDS
)
ROUTE_OBSERVER_KINDS = {"codex_wait", "chatgpt_poll"}


DEFAULT_CODEX_DENY_READ_STATE_PATH = Path(
    r"C:\Users\thank\.codex\.sandbox\deny_read_acl_state.json"
)
AUTHORIZED_RUNTIME_RECEIPT_RELATIVE_PATH = (
    Path("production_pilots")
    / "factory_canaries"
    / "food_expiry_labels_001"
    / "auto_video_runs"
    / "japanese_final_form_candidate_v1_thank_recovery"
    / "_evidence"
    / "codex_sandbox_recovery_v1"
    / "recovery_receipt.json"
)


class ProtocolError(RuntimeError):
    """A fail-closed protocol violation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_hash(value: Any) -> str:
    """Hash a JSON-compatible value without depending on formatting."""
    return sha256_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ProtocolError(f"JSON object required: {path}")
    return value


def atomic_write_json(path: Path | str, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_write_text(path: Path | str, value: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_write_bytes(path: Path | str, value: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _normalized_absolute_path(path: Path | str) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))


def validate_blocked_contract(contract: dict[str, Any]) -> None:
    if not isinstance(contract, dict) or not contract:
        raise ProtocolError("BLOCKED requires a non-empty recovery contract")
    missing = [
        field
        for field in BLOCKED_CONTRACT_REQUIRED_FIELDS
        if field not in contract
    ]
    if missing:
        raise ProtocolError(
            "BLOCKED recovery contract missing: " + ", ".join(missing)
        )
    if contract.get("contract_version") != BLOCKED_CONTRACT_VERSION:
        raise ProtocolError(
            f"BLOCKED recovery contract_version must be {BLOCKED_CONTRACT_VERSION}"
        )
    for field in (
        "blocker_id",
        "requirement",
        "rationale",
        "owner",
        "next_permitted_probe",
        "retry_policy",
    ):
        if not isinstance(contract.get(field), str) or not contract[field].strip():
            raise ProtocolError(f"BLOCKED recovery contract {field} is required")
    introduced_by = contract.get("introduced_by")
    if not isinstance(introduced_by, dict):
        raise ProtocolError("BLOCKED introduced_by must be an object")
    for field in ("event", "at", "evidence"):
        if not isinstance(introduced_by.get(field), str) or not introduced_by[
            field
        ].strip():
            raise ProtocolError(f"BLOCKED introduced_by.{field} is required")
    for field in ("qualifies_when", "does_not_qualify", "diagnostics_completed"):
        values = contract.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(item, str) or not item.strip() for item in values)
        ):
            raise ProtocolError(f"BLOCKED recovery contract {field} must be non-empty")
    input_route = contract.get("input_route")
    if not isinstance(input_route, dict):
        raise ProtocolError("BLOCKED input_route must be an object")
    for field in ("destination", "format"):
        if not isinstance(input_route.get(field), str) or not input_route[
            field
        ].strip():
            raise ProtocolError(f"BLOCKED input_route.{field} is required")
    fingerprint = str(contract.get("baseline_observation_fingerprint") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint):
        raise ProtocolError(
            "BLOCKED baseline_observation_fingerprint must be SHA-256"
        )
    supersedes = contract.get("supersedes_contract_fingerprint")
    if supersedes is not None and not re.fullmatch(
        r"[0-9a-fA-F]{64}", str(supersedes)
    ):
        raise ProtocolError(
            "BLOCKED supersedes_contract_fingerprint must be SHA-256"
        )


def blocked_contract_issues(contract: Any) -> list[str]:
    try:
        validate_blocked_contract(contract)
    except ProtocolError as exc:
        return [str(exc)]
    return []


def _blocked_contract_introduced_at(contract: dict[str, Any]) -> datetime:
    raw = str(contract.get("introduced_by", {}).get("at") or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError("BLOCKED introduced_by.at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ProtocolError("BLOCKED introduced_by.at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_blocked_contract_revision_authority(
    mission: dict[str, Any], contract: dict[str, Any]
) -> None:
    authority = contract.get("revision_authority")
    if not isinstance(authority, dict):
        raise ProtocolError(
            "BLOCKED contract revision requires exact revision_authority"
        )
    required = (
        "event_kind",
        "event_id",
        "repository_id",
        "mission_id",
        "attempt_id",
        "supervisor_thread_id",
        "evidence_sha256",
    )
    missing = [field for field in required if authority.get(field) in (None, "")]
    if missing:
        raise ProtocolError(
            "BLOCKED revision_authority missing: " + ", ".join(missing)
        )
    event_kind = str(authority["event_kind"])
    if event_kind not in BLOCKED_CONTRACT_REVISION_EVENT_KINDS:
        raise ProtocolError("BLOCKED contract revision event_kind is not allowed")
    if contract["introduced_by"]["event"] != event_kind:
        raise ProtocolError("BLOCKED revision event_kind does not match origin")
    for field in (
        "repository_id",
        "mission_id",
        "attempt_id",
        "supervisor_thread_id",
    ):
        if authority[field] != mission.get(field):
            raise ProtocolError(
                f"BLOCKED revision_authority.{field} does not match Mission"
            )
    event_id = str(authority["event_id"])
    evidence_sha256 = str(authority["evidence_sha256"])
    if not re.fullmatch(r"[0-9a-fA-F]{64}", event_id):
        raise ProtocolError("BLOCKED revision_authority.event_id must be SHA-256")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", evidence_sha256):
        raise ProtocolError(
            "BLOCKED revision_authority.evidence_sha256 must be SHA-256"
        )
    if contract["baseline_observation_fingerprint"] != event_id:
        raise ProtocolError("BLOCKED revision baseline must equal exact event_id")
    evidence_path = Path(contract["introduced_by"]["evidence"])
    if not evidence_path.is_file():
        raise ProtocolError("BLOCKED revision evidence file is missing")
    if sha256_file(evidence_path) != evidence_sha256:
        raise ProtocolError("BLOCKED revision evidence SHA-256 mismatch")
    evidence = load_json(evidence_path)
    evidence_checks = {
        "event_kind": event_kind,
        "event_id": authority["event_id"],
        "repository_id": mission.get("repository_id"),
        "mission_id": mission.get("mission_id"),
        "attempt_id": mission.get("attempt_id"),
        "supervisor_thread_id": mission.get("supervisor_thread_id"),
    }
    for field, expected in evidence_checks.items():
        if evidence.get(field) != expected:
            raise ProtocolError(
                f"BLOCKED revision evidence {field} does not match exact authority"
            )


def blocked_contract_replay_location(
    mission: dict[str, Any], contract: dict[str, Any]
) -> str | None:
    if mission.get("blocked_contract") == contract:
        return "current"
    candidate_fingerprint = canonical_json_hash(contract)
    for entry in mission.get("blocked_contract_history", []):
        if (
            isinstance(entry, dict)
            and entry.get("contract_fingerprint") == candidate_fingerprint
            and entry.get("contract") == contract
        ):
            return "history"
    return None


def is_blocked_contract_authority_enrichment(
    existing: Any, candidate: Any
) -> bool:
    if not isinstance(existing, dict) or not isinstance(candidate, dict):
        return False
    if existing.get("revision_authority") is not None:
        return False
    if not isinstance(candidate.get("revision_authority"), dict):
        return False
    without_authority = copy.deepcopy(candidate)
    without_authority.pop("revision_authority", None)
    return without_authority == existing


def validate_blocked_contract_history(mission: dict[str, Any]) -> None:
    history = mission.get("blocked_contract_history", [])
    if not isinstance(history, list):
        raise ProtocolError("blocked_contract_history must be an array")
    current = mission.get("blocked_contract")
    if current is not None:
        validate_blocked_contract(current)
    if not history:
        if isinstance(current, dict) and current.get(
            "supersedes_contract_fingerprint"
        ) is not None:
            raise ProtocolError(
                "initial BLOCKED contract cannot name a superseded contract"
            )
        return
    if not isinstance(current, dict):
        raise ProtocolError("blocked contract history requires a current contract")
    seen: set[str] = set()
    previous_superseded_at: datetime | None = None
    for index, entry in enumerate(history):
        if not isinstance(entry, dict):
            raise ProtocolError("blocked_contract_history entries must be objects")
        contract = entry.get("contract")
        validate_blocked_contract(contract)
        contract_fingerprint = str(entry.get("contract_fingerprint") or "")
        superseded_by = str(
            entry.get("superseded_by_contract_fingerprint") or ""
        )
        if contract_fingerprint != canonical_json_hash(contract):
            raise ProtocolError("blocked contract history fingerprint mismatch")
        if contract_fingerprint in seen:
            raise ProtocolError("blocked contract history fingerprints must be unique")
        seen.add(contract_fingerprint)
        superseded_at = str(entry.get("superseded_at") or "")
        try:
            parsed = datetime.fromisoformat(superseded_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProtocolError(
                "blocked contract history superseded_at must be ISO-8601"
            ) from exc
        if parsed.tzinfo is None:
            raise ProtocolError(
                "blocked contract history superseded_at must include a timezone"
            )
        parsed = parsed.astimezone(timezone.utc)
        if previous_superseded_at is not None and parsed < previous_superseded_at:
            raise ProtocolError("blocked contract history time order is invalid")
        previous_superseded_at = parsed
        next_contract = (
            history[index + 1].get("contract")
            if index + 1 < len(history)
            and isinstance(history[index + 1], dict)
            else current
        )
        validate_blocked_contract(next_contract)
        _validate_blocked_contract_revision_authority(mission, next_contract)
        next_fingerprint = canonical_json_hash(next_contract)
        if superseded_by != next_fingerprint:
            raise ProtocolError("blocked contract history successor link mismatch")
        if (
            next_contract.get("supersedes_contract_fingerprint")
            != contract_fingerprint
        ):
            raise ProtocolError("blocked contract history predecessor link mismatch")
        if _blocked_contract_introduced_at(next_contract) <= _blocked_contract_introduced_at(
            contract
        ):
            raise ProtocolError("blocked contract history semantic order is invalid")
    if canonical_json_hash(current) in seen:
        raise ProtocolError("current blocked contract duplicates its history")


def enrich_current_blocked_contract_authority(
    mission: dict[str, Any], contract: dict[str, Any]
) -> dict[str, Any]:
    existing = mission.get("blocked_contract")
    if not is_blocked_contract_authority_enrichment(existing, contract):
        raise ProtocolError(
            "BLOCKED contract authority enrichment must change only revision_authority"
        )
    history = mission.get("blocked_contract_history")
    if not isinstance(history, list) or not history:
        raise ProtocolError(
            "BLOCKED contract authority enrichment requires revision history"
        )
    _validate_blocked_contract_revision_authority(mission, contract)
    existing_fingerprint = canonical_json_hash(existing)
    if history[-1].get("superseded_by_contract_fingerprint") != existing_fingerprint:
        raise ProtocolError(
            "BLOCKED contract authority enrichment history link mismatch"
        )
    enriched_fingerprint = canonical_json_hash(contract)
    mission["blocked_contract"] = copy.deepcopy(contract)
    history[-1]["superseded_by_contract_fingerprint"] = enriched_fingerprint
    now = utc_now()
    mission.setdefault("events", []).append(
        {
            "at": now,
            "state": "BLOCKED_CONTRACT_AUTHORITY_ENRICHED",
            "details": {
                "blocker_id": contract["blocker_id"],
                "prior_contract_fingerprint": existing_fingerprint,
                "contract_fingerprint": enriched_fingerprint,
                "revision_event_id": contract["revision_authority"]["event_id"],
            },
        }
    )
    mission["updated_at"] = now
    validate_mission(mission)
    return mission


def record_blocked_contract(
    mission: dict[str, Any], contract: dict[str, Any]
) -> dict[str, Any]:
    if mission.get("state") != "BLOCKED":
        raise ProtocolError("recovery contract repair requires a BLOCKED Mission")
    validate_blocked_contract(contract)
    existing = mission.get("blocked_contract")
    if blocked_contract_replay_location(mission, contract) is not None:
        return mission
    revision = bool(existing and not blocked_contract_issues(existing))
    existing_fingerprint: str | None = None
    if revision:
        existing_fingerprint = canonical_json_hash(existing)
        if contract.get("supersedes_contract_fingerprint") != existing_fingerprint:
            raise ProtocolError(
                "BLOCKED contract revision must name the current contract fingerprint"
            )
        _validate_blocked_contract_revision_authority(mission, contract)
        if _blocked_contract_introduced_at(contract) <= _blocked_contract_introduced_at(
            existing
        ):
            raise ProtocolError(
                "BLOCKED contract revision must be newer than the current contract"
            )
        if contract["introduced_by"]["evidence"] == existing["introduced_by"][
            "evidence"
        ]:
            raise ProtocolError(
                "BLOCKED contract revision requires distinct Supervisor evidence"
            )
        successor_fingerprint = canonical_json_hash(contract)
        mission.setdefault("blocked_contract_history", []).append(
            {
                "contract": copy.deepcopy(existing),
                "contract_fingerprint": existing_fingerprint,
                "superseded_at": utc_now(),
                "superseded_by_contract_fingerprint": successor_fingerprint,
            }
        )
    mission["blocked_contract"] = copy.deepcopy(contract)
    now = utc_now()
    mission.setdefault("events", []).append(
        {
            "at": now,
            "state": (
                "BLOCKED_CONTRACT_REVISED"
                if revision
                else "BLOCKED_CONTRACT_RECORDED"
            ),
            "details": {
                "blocker_id": contract["blocker_id"],
                "contract_fingerprint": canonical_json_hash(contract),
                "superseded_contract_fingerprint": existing_fingerprint,
            },
        }
    )
    mission["updated_at"] = now
    validate_blocked_contract_history(mission)
    return mission


@contextmanager
def exclusive_mission_file_lock(
    mission_path: Path | str, timeout_seconds: float = 10.0
) -> Iterable[None]:
    target = Path(mission_path)
    lock_path = target.with_name(f"{target.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if lock_path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    deadline = time.monotonic() + timeout_seconds
    locked = False
    try:
        while not locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ProtocolError(
                        f"timed out acquiring Mission lock: {lock_path}"
                    ) from exc
                time.sleep(0.05)
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def persist_blocked_contract(
    mission_path: Path | str, payload_path: Path | str
) -> dict[str, Any]:
    with exclusive_mission_file_lock(mission_path):
        mission = load_json(mission_path)
        contract = load_json(payload_path)
        validate_blocked_contract(contract)
        if is_blocked_contract_authority_enrichment(
            mission.get("blocked_contract"), contract
        ):
            enrich_current_blocked_contract_authority(mission, contract)
            atomic_write_json(mission_path, mission)
            return {
                "classification": "BLOCKED_RECOVERY_CONTRACT_AUTHORITY_ENRICHED",
                "mission_id": mission.get("mission_id"),
                "attempt_id": mission.get("attempt_id"),
                "blocker_id": contract.get("blocker_id"),
                "contract_fingerprint": canonical_json_hash(contract),
                "changed": True,
            }
        validate_mission(mission)
        replay_location = blocked_contract_replay_location(mission, contract)
        if replay_location is not None:
            return {
                "classification": "BLOCKED_RECOVERY_CONTRACT_ALREADY_APPLIED",
                "mission_id": mission.get("mission_id"),
                "attempt_id": mission.get("attempt_id"),
                "blocker_id": contract.get("blocker_id"),
                "contract_fingerprint": canonical_json_hash(contract),
                "replay_location": replay_location,
                "changed": False,
            }
        prior_contract = copy.deepcopy(mission.get("blocked_contract"))
        revised = bool(
            prior_contract and not blocked_contract_issues(prior_contract)
        )
        record_blocked_contract(mission, contract)
        validate_mission(mission)
        atomic_write_json(mission_path, mission)
        return {
            "classification": (
                "BLOCKED_RECOVERY_CONTRACT_REVISED"
                if revised
                else "BLOCKED_RECOVERY_CONTRACT_RECORDED"
            ),
            "mission_id": mission.get("mission_id"),
            "attempt_id": mission.get("attempt_id"),
            "blocker_id": contract.get("blocker_id"),
            "contract_fingerprint": canonical_json_hash(contract),
            "changed": True,
        }


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace(
        "\n", "<br>"
    )


def _mermaid_label(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("\\", "/")
        .replace('"', "'")
        .replace("\n", " ")
    )


PORTFOLIO_ACTIVE_ROUTE_FIELDS = (
    "repository_id",
    "action_id",
    "recipient_thread_id",
    "delivery_token",
    "after_cursor",
    "status",
    "observer_kind",
)


def _normalize_portfolio_active_route(
    route: Any, *, label: str
) -> dict[str, Any]:
    if not isinstance(route, dict):
        raise ProtocolError(f"{label} must be an object")
    normalized = {field: route.get(field) for field in PORTFOLIO_ACTIVE_ROUTE_FIELDS}
    for field in (
        "repository_id",
        "action_id",
        "recipient_thread_id",
        "delivery_token",
        "status",
        "observer_kind",
    ):
        if not isinstance(normalized[field], str) or not normalized[field]:
            raise ProtocolError(f"{label} requires {field}")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", normalized["delivery_token"]):
        raise ProtocolError(f"{label} delivery_token must be SHA-256")
    if normalized["status"] not in {"prepared", "sent", "waiting"}:
        raise ProtocolError(f"{label} status is not an active route status")
    if normalized["observer_kind"] not in ROUTE_OBSERVER_KINDS:
        raise ProtocolError(f"{label} observer_kind is not supported")
    after_cursor = normalized["after_cursor"]
    if after_cursor is not None and not isinstance(after_cursor, str):
        raise ProtocolError(f"{label} after_cursor must be a string or null")
    return normalized


def validate_portfolio_status(portfolio: dict[str, Any]) -> None:
    if portfolio.get("schema_version") != 2:
        raise ProtocolError("portfolio status schema_version must be 2")
    if not re.fullmatch(
        r"[0-9a-fA-F]{64}", str(portfolio.get("semantic_fingerprint") or "")
    ):
        raise ProtocolError("portfolio semantic_fingerprint must be SHA-256")
    if portfolio.get("coordinator_availability") != "AVAILABLE":
        raise ProtocolError("portfolio coordinator_availability must be AVAILABLE")
    if portfolio.get("execution_state") not in {
        "READY",
        "DRAINING",
        "WAITING_USER",
        "WAITING_EXTERNAL",
        "IDLE",
        "SAFETY_CEILING",
    }:
        raise ProtocolError("portfolio execution_state is invalid")
    active_route_count = portfolio.get("active_route_count")
    if not isinstance(active_route_count, int) or active_route_count < 0:
        raise ProtocolError(
            "portfolio active_route_count must be a non-negative integer"
        )
    concurrency_limit = portfolio.get("concurrency_limit")
    if not isinstance(concurrency_limit, int) or not (
        1 <= concurrency_limit <= MAX_COORDINATOR_WAIT_TARGETS
    ):
        raise ProtocolError("portfolio concurrency_limit must be between 1 and 8")
    if active_route_count > concurrency_limit:
        raise ProtocolError(
            "portfolio active_route_count cannot exceed concurrency_limit"
        )
    active_routes = portfolio.get("active_routes")
    if active_routes is not None:
        if not isinstance(active_routes, list):
            raise ProtocolError("portfolio active_routes must be an array")
        if len(active_routes) != active_route_count:
            raise ProtocolError(
                "portfolio active_route_count does not match its active_routes array"
            )
        seen_route_ids: set[str] = set()
        for index, route in enumerate(active_routes):
            normalized = _normalize_portfolio_active_route(
                route, label=f"portfolio active_routes[{index}]"
            )
            action_id = normalized["action_id"]
            if action_id in seen_route_ids:
                raise ProtocolError(
                    f"duplicate portfolio active route action_id: {action_id}"
                )
            seen_route_ids.add(action_id)
    repositories = portfolio.get("repositories")
    if not isinstance(repositories, list):
        raise ProtocolError("portfolio repositories must be an array")
    seen: set[str] = set()
    for row in repositories:
        if not isinstance(row, dict):
            raise ProtocolError("portfolio repository row must be an object")
        repository_id = str(row.get("repository_id") or "")
        if not repository_id or repository_id in seen:
            raise ProtocolError("portfolio repository_id must be unique and non-empty")
        seen.add(repository_id)
        if row.get("state") not in PORTFOLIO_PROJECT_STATES:
            raise ProtocolError(f"invalid portfolio project state: {row.get('state')}")
        progress = row.get("progress")
        if not isinstance(progress, dict):
            raise ProtocolError("portfolio progress is required")
        current_stage = progress.get("current_stage")
        if current_stage not in PORTFOLIO_STAGE_ORDER:
            raise ProtocolError(f"invalid portfolio current_stage: {current_stage}")
        completed = progress.get("completed_stages")
        if not isinstance(completed, list) or any(
            item not in PORTFOLIO_STAGE_ORDER for item in completed
        ):
            raise ProtocolError("portfolio completed_stages is invalid")
        for field in ("why", "owner", "next_move"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ProtocolError(f"portfolio row {field} is required")
        stop = row.get("stop")
        if row.get("state") in {
            "SYSTEM_BLOCKED",
            "PARKED_BY_POLICY",
        }:
            validate_blocked_contract(stop)
        elif stop is not None:
            validate_blocked_contract(stop)


def _active_scheduler_delivery_routes(
    scheduler_state: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return validated routes that reserve an exact external delivery slot.

    Sent/waiting route leases are already externally in flight.  A prepared
    scheduler claim has not yet become in-flight work, but it is durable,
    recipient-bound, recovery-eligible, and reserves the same concurrency
    capacity.  Both therefore have to be represented by a portfolio snapshot
    that claims to describe the scheduler revision.
    """
    scheduler = migrate_scheduler_state(scheduler_state)
    routes: list[dict[str, Any]] = []
    scheduler_claim = scheduler.get("scheduler_claim")
    if (
        isinstance(scheduler_claim, dict)
        and scheduler_claim.get("status") == "prepared"
    ):
        routes.append(_record_with_route_metadata(scheduler_claim))
    routes.extend(
        _record_with_route_metadata(item)
        for item in scheduler.get("route_leases", [])
        if isinstance(item, dict)
        and str(item.get("status") or "") in {"prepared", "sent", "waiting"}
    )
    return scheduler, routes


def _scheduler_route_portfolio_projection(route: dict[str, Any]) -> dict[str, Any]:
    action_id = str(route.get("action_id") or "")
    projected = {
        "repository_id": str(
            route.get("repository_id")
            or _action_repository_id(route.get("action", {}))
            or ""
        ),
        "action_id": action_id,
        "recipient_thread_id": str(route.get("recipient_thread_id") or ""),
        "delivery_token": str(route.get("delivery_token") or ""),
        "after_cursor": route.get("after_cursor"),
        "status": str(route.get("status") or ""),
        "observer_kind": str(route.get("observer_kind") or ""),
    }
    return _normalize_portfolio_active_route(
        projected, label=f"active scheduler route {action_id or '<missing>'}"
    )


def validate_portfolio_scheduler_consistency(
    portfolio: dict[str, Any], scheduler_state: dict[str, Any]
) -> None:
    """Fail closed when a portfolio projection is stale or loses route identity."""
    validate_portfolio_status(portfolio)
    scheduler, active_routes = _active_scheduler_delivery_routes(scheduler_state)

    portfolio_revision = portfolio.get("scheduler_revision")
    scheduler_revision = scheduler.get("revision")
    if not isinstance(portfolio_revision, int):
        raise ProtocolError("portfolio scheduler_revision must be an integer")
    if portfolio_revision != scheduler_revision:
        raise ProtocolError(
            "portfolio scheduler_revision "
            f"{portfolio_revision} does not match scheduler revision "
            f"{scheduler_revision}"
        )

    portfolio_route_count = portfolio.get("active_route_count")
    if portfolio_route_count != len(active_routes):
        raise ProtocolError(
            "portfolio active_route_count "
            f"{portfolio_route_count} does not match "
            f"{len(active_routes)} active scheduler routes"
        )

    portfolio_limit = portfolio.get("concurrency_limit")
    scheduler_limit = scheduler.get("concurrency_limit")
    if portfolio_limit != scheduler_limit:
        raise ProtocolError(
            "portfolio concurrency_limit "
            f"{portfolio_limit} does not match scheduler concurrency_limit "
            f"{scheduler_limit}"
        )

    portfolio_routes = portfolio.get("active_routes")
    if not isinstance(portfolio_routes, list):
        raise ProtocolError(
            "portfolio active_routes is required for scheduler consistency validation"
        )
    expected_by_action = {
        item["action_id"]: item
        for item in (
            _scheduler_route_portfolio_projection(route)
            for route in active_routes
        )
    }
    actual_by_action = {
        item["action_id"]: item
        for item in (
            _normalize_portfolio_active_route(
                route, label=f"portfolio active_routes[{index}]"
            )
            for index, route in enumerate(portfolio_routes)
        )
    }
    if actual_by_action != expected_by_action:
        missing = sorted(set(expected_by_action) - set(actual_by_action))
        unexpected = sorted(set(actual_by_action) - set(expected_by_action))
        mismatched = sorted(
            action_id
            for action_id in set(actual_by_action) & set(expected_by_action)
            if actual_by_action[action_id] != expected_by_action[action_id]
        )
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        if mismatched:
            details.append("mismatched=" + ",".join(mismatched))
        raise ProtocolError(
            "portfolio active_routes does not exactly match scheduler routes: "
            + "; ".join(details)
        )

    rows_by_repository = {
        str(row.get("repository_id") or ""): row
        for row in portfolio["repositories"]
        if isinstance(row, dict)
    }
    for route in active_routes:
        action_id = str(route.get("action_id") or "")
        recipient_thread_id = str(route.get("recipient_thread_id") or "")
        observer_kind = str(route.get("observer_kind") or "")
        repository_id = str(
            route.get("repository_id")
            or _action_repository_id(route.get("action", {}))
            or ""
        )
        row = rows_by_repository.get(repository_id)
        if row is None:
            raise ProtocolError(
                f"active scheduler route {action_id} has no matching portfolio "
                f"repository row for {repository_id or '<missing repository_id>'}"
            )
        route_owner = str(row.get("route_owner") or "")
        missing_identity = [
            label
            for label, value in (
                ("action_id", action_id),
                ("recipient_thread_id", recipient_thread_id),
                ("observer_kind", observer_kind),
            )
            if not value or value not in route_owner
        ]
        if missing_identity:
            raise ProtocolError(
                f"portfolio route_owner for {repository_id} does not include "
                "exact active route identity: "
                + ", ".join(missing_identity)
                + f" (action_id={action_id or '<missing>'}, "
                f"recipient_thread_id={recipient_thread_id or '<missing>'})"
            )


def render_portfolio_markdown(portfolio: dict[str, Any]) -> str:
    validate_portfolio_status(portfolio)
    repositories = portfolio["repositories"]
    lines = [
        "# Coordinator portfolio progress",
        "",
        f"- Coordinator: `{_markdown_cell(portfolio.get('coordinator_availability', 'AVAILABLE'))}`",
        f"- Execution: `{_markdown_cell(portfolio.get('execution_state', 'IDLE'))}`",
        f"- Active routes: `{_markdown_cell(portfolio.get('active_route_count', 0))} / {_markdown_cell(portfolio.get('concurrency_limit', 3))}`",
    ]
    active_routes = portfolio.get("active_routes")
    if isinstance(active_routes, list) and active_routes:
        lines.extend(
            [
                "",
                "## Active external routes",
                "",
                "| Project | Status | Observer | Action | Exact recipient | Cursor |",
                "|---|---|---|---|---|---|",
            ]
        )
        project_names = {
            str(row.get("repository_id") or ""): str(
                row.get("project_name") or row.get("repository_id") or ""
            )
            for row in repositories
        }
        for route in active_routes:
            repository_id = str(route.get("repository_id") or "")
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        project_names.get(repository_id, repository_id),
                        route.get("status"),
                        route.get("observer_kind"),
                        route.get("action_id"),
                        route.get("recipient_thread_id"),
                        route.get("after_cursor") or "not recorded",
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "```mermaid",
            "flowchart LR",
            "  classDef done fill:#d8efe1,stroke:#2f855a,color:#173d2b;",
            "  classDef current fill:#dbeafe,stroke:#2563eb,color:#172554,stroke-width:3px;",
            "  classDef blocked fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:3px;",
            "  classDef parked fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:3px;",
            "  classDef pending fill:#f3f4f6,stroke:#9ca3af,color:#374151;",
        ]
    )
    for index, row in enumerate(repositories):
        progress = row["progress"]
        completed = set(progress.get("completed_stages", []))
        current = progress["current_stage"]
        state = row["state"]
        project_name = _mermaid_label(row.get("project_name") or row["repository_id"])
        lines.append(f'  subgraph p{index}["{project_name}"]')
        node_ids = []
        for stage_index, stage in enumerate(PORTFOLIO_STAGE_ORDER):
            node_id = f"p{index}s{stage_index}"
            node_ids.append(node_id)
            label = _mermaid_label(
                progress.get("stage_labels", {}).get(stage) or stage.replace("_", " ")
            )
            if stage == current:
                css_class = (
                    "blocked"
                    if state == "SYSTEM_BLOCKED"
                    else "parked"
                    if state in {"WAITING_USER", "PARKED_BY_POLICY"}
                    else "current"
                )
            elif stage in completed:
                css_class = "done"
            else:
                css_class = "pending"
            lines.append(f'    {node_id}["{label}"]:::{css_class}')
        lines.append("    " + " --> ".join(node_ids))
        lines.append("  end")
    lines.extend(
        [
            "```",
            "",
            "| Project | State | Current stage | Why here | Owner | Exact next move |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in repositories:
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    row.get("project_name") or row["repository_id"],
                    f"`{row['state']}`",
                    row["progress"]["current_stage"],
                    row["why"],
                    row["owner"],
                    row["next_move"],
                )
            )
            + " |"
        )
    for row in repositories:
        stop = row.get("stop")
        if not isinstance(stop, dict):
            continue
        introduced = stop["introduced_by"]
        lines.extend(
            [
                "",
                f"## {_markdown_cell(row.get('project_name') or row['repository_id'])}: stop / wait card",
                "",
                f"- Origin: `{_markdown_cell(introduced['event'])}` at `{_markdown_cell(introduced['at'])}` — {_markdown_cell(introduced['evidence'])}",
                f"- Requirement: {_markdown_cell(stop['requirement'])}",
                f"- Why required here: {_markdown_cell(stop['rationale'])}",
                f"- Owner: {_markdown_cell(stop['owner'])}",
                f"- Next permitted probe: {_markdown_cell(stop['next_permitted_probe'])}",
                f"- Input route: {_markdown_cell(stop['input_route']['destination'])} as {_markdown_cell(stop['input_route']['format'])}",
                "- Qualifies when:",
            ]
        )
        lines.extend(f"  - {_markdown_cell(item)}" for item in stop["qualifies_when"])
        lines.append("- Does not qualify:")
        lines.extend(
            f"  - {_markdown_cell(item)}" for item in stop["does_not_qualify"]
        )
        lines.append("- Diagnostics already completed:")
        lines.extend(
            f"  - {_markdown_cell(item)}" for item in stop["diagnostics_completed"]
        )
    lines.extend(
        [
            "",
            "No push, PR, merge, release, publication, deployment, rights, production, or human-acceptance authority is implied.",
            "",
        ]
    )
    return "\n".join(lines)


def normalize_remote(remote: str) -> str:
    """Normalize common Git HTTPS/SSH spellings to host/path identity."""
    value = remote.strip()
    if not value:
        raise ProtocolError("remote identity is empty")

    normalized_identity = re.fullmatch(
        r"([A-Za-z0-9.-]+\.[A-Za-z]{2,})/([^\\\s]+)", value
    )
    if normalized_identity:
        host = normalized_identity.group(1).lower().removeprefix("www.")
        path = normalized_identity.group(2).strip("/")
        if path.lower().endswith(".git"):
            path = path[:-4]
        return f"{host}/{path.lower()}"

    scp = re.match(r"^[^/@\s]+@([^:\s]+):(.+)$", value)
    if scp:
        host, path = scp.group(1), scp.group(2)
    elif "://" in value:
        parsed = urlparse(value)
        if parsed.scheme.lower() == "file":
            local_path = unquote(parsed.path).replace("\\", "/").rstrip("/")
            return f"file:{local_path.lower()}"
        host = parsed.hostname or ""
        path = parsed.path
    else:
        local_path = value.replace("\\", "/").rstrip("/")
        return f"file:{local_path.lower()}"

    host = host.lower().removeprefix("www.")
    path = unquote(path).replace("\\", "/").strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    if not host or not path:
        raise ProtocolError(f"unsupported remote identity: {remote}")
    return f"{host}/{path.lower()}"


def _run_git(root: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    if completed.returncode != 0:
        raise ProtocolError(
            f"git {' '.join(args)} failed at {root}: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def inspect_repository(root: Path | str) -> dict[str, str]:
    candidate = Path(root)
    if not candidate.exists():
        raise ProtocolError(f"repository root does not exist: {candidate}")
    top = Path(_run_git(candidate, ["rev-parse", "--show-toplevel"]))
    remote = _run_git(top, ["remote", "get-url", "origin"])
    return {
        "root": str(top),
        "remote": remote,
        "repository_id": normalize_remote(remote),
        "head": _run_git(top, ["rev-parse", "HEAD"]),
        "branch": _run_git(top, ["branch", "--show-current"]),
    }


def _aliases(record: dict[str, Any]) -> list[str]:
    return [str(item) for item in record.get("aliases", [])]


def _alias_matches(record: dict[str, Any], alias: str) -> bool:
    needle = alias.casefold()
    values = _aliases(record) + [str(record.get("repository_id", ""))]
    return any(value.casefold() == needle for value in values)


def repository_candidates(
    registry: dict[str, Any], alias: str
) -> list[dict[str, Any]]:
    matches = [
        item
        for item in registry.get("repositories", [])
        if isinstance(item, dict) and _alias_matches(item, alias)
    ]
    by_repo: dict[str, dict[str, Any]] = {}
    for item in matches:
        repo_id = str(item.get("repository_id", ""))
        if repo_id:
            by_repo.setdefault(repo_id, item)
    return list(by_repo.values())


def host_by_alias(hosts: dict[str, Any], alias: str) -> dict[str, Any] | None:
    needle = alias.casefold()
    for host in hosts.get("hosts", []):
        if not isinstance(host, dict):
            continue
        values = [str(host.get("host_id", "")), *_aliases(host)]
        values.extend(str(x) for x in host.get("app_host_ids", []))
        if any(value.casefold() == needle for value in values):
            return host
    return None


def private_artifact_record(
    hosts: dict[str, Any],
    repository_id: str,
    artifact_id: str,
    preferred_host_id: str | None = None,
) -> dict[str, Any] | None:
    matches = [
        item
        for item in hosts.get("private_artifacts", [])
        if isinstance(item, dict)
        and item.get("repository_id") == repository_id
        and item.get("artifact_id") == artifact_id
        and item.get("status") == "verified"
    ]
    if preferred_host_id:
        preferred = next(
            (
                item
                for item in matches
                if item.get("host_id") == preferred_host_id
            ),
            None,
        )
        if preferred is not None:
            return preferred
    if len(matches) > 1:
        raise ProtocolError(f"ambiguous private artifact locality: {artifact_id}")
    return matches[0] if matches else None


def _adapter_threads(adapter: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in adapter.get("threads", [])
        if isinstance(item, dict) and item.get("id")
    ]


def _route_observer_kind(
    adapter: dict[str, Any], recipient_thread_id: str | None
) -> str | None:
    projection = _route_observer_projection(adapter, recipient_thread_id)
    return (
        str(projection["observer_kind"])
        if isinstance(projection, dict)
        else None
    )


def _route_observer_projection(
    adapter: dict[str, Any], recipient_thread_id: str | None
) -> dict[str, str] | None:
    """Bind an exact route to its concrete observation transport.

    Codex waits require both the task id and the app host id. ChatGPT routes
    are polled and deliberately remain hostless.
    """
    if not recipient_thread_id:
        return None
    thread = next(
        (
            item
            for item in _adapter_threads(adapter)
            if item.get("id") == recipient_thread_id
        ),
        None,
    )
    if not isinstance(thread, dict):
        raise ProtocolError(
            f"exact outbound recipient is absent from adapter: {recipient_thread_id}"
        )
    if thread.get("read_verified") is not True:
        raise ProtocolError(
            "exact outbound recipient is not read-verified: "
            + recipient_thread_id
        )
    _require_live_thread_status(thread, "exact outbound recipient")
    kind = str(thread.get("kind") or "")
    if kind == "codex":
        host_id = str(thread.get("host_id") or "")
        if not host_id:
            raise ProtocolError(
                "Codex outbound recipient lacks exact adapter host_id: "
                + recipient_thread_id
            )
        return {"observer_kind": "codex_wait", "host_id": host_id}
    if kind == "chatgpt":
        return {"observer_kind": "chatgpt_poll"}
    raise ProtocolError(
        f"unsupported outbound recipient transport for {recipient_thread_id}: {kind}"
    )


def _exact_thread(
    adapter: dict[str, Any],
    thread_id: str,
    *,
    kind: str,
    title: str | None = None,
    project_id: str | None = None,
    host_aliases: Iterable[str] = (),
) -> tuple[dict[str, Any] | None, list[str]]:
    issues: list[str] = []
    exact = next(
        (item for item in _adapter_threads(adapter) if item.get("id") == thread_id),
        None,
    )
    if exact is None:
        return None, ["exact_thread_missing"]
    if exact.get("kind") != kind:
        issues.append("thread_kind_mismatch")
    if title is not None and exact.get("title") != title:
        issues.append("exact_title_mismatch")
    if project_id is not None and exact.get("project_id") != project_id:
        issues.append("project_id_mismatch")
    if kind == "codex" and host_aliases:
        observed = str(exact.get("host_id", "")).casefold()
        accepted = {str(item).casefold() for item in host_aliases}
        if observed not in accepted:
            issues.append("worker_host_mismatch")
    if exact.get("read_verified") is not True:
        issues.append("readback_not_verified")
    return exact, issues


def _require_live_thread_status(thread: dict[str, Any], label: str) -> None:
    status = str(thread.get("status") or "").casefold()
    if not status or status in {
        "archived",
        "closed",
        "completed",
        "destroyed",
        "inactive",
        "invalid",
        "stale",
    }:
        raise ProtocolError(f"{label} adapter status is not live: {status or 'missing'}")


def _supervisor_binding_for(
    registry: dict[str, Any], repository_id: str, lane: str
) -> dict[str, Any] | None:
    for item in registry.get("supervisor_bindings", []):
        if not isinstance(item, dict):
            continue
        if (
            item.get("repository_id") == repository_id
            and item.get("supervision_lane") == lane
        ):
            return item
    return None


def _worker_binding_for(
    registry: dict[str, Any], repository_id: str, host_id: str
) -> dict[str, Any] | None:
    for item in registry.get("worker_bindings", []):
        if not isinstance(item, dict):
            continue
        if (
            item.get("repository_id") == repository_id
            and item.get("host_id") == host_id
        ):
            return item
    return None


def _select_lane(
    registry: dict[str, Any], repository_id: str, requested_lane: str | None
) -> tuple[str | None, list[str]]:
    bindings = [
        item
        for item in registry.get("supervisor_bindings", [])
        if isinstance(item, dict) and item.get("repository_id") == repository_id
    ]
    lanes = sorted({str(item.get("supervision_lane")) for item in bindings})
    if requested_lane:
        if requested_lane not in lanes:
            return None, lanes
        return requested_lane, lanes
    repository = next(
        (
            item
            for item in registry.get("repositories", [])
            if item.get("repository_id") == repository_id
        ),
        None,
    )
    if repository and repository.get("default_supervision_lane") in lanes:
        return str(repository["default_supervision_lane"]), lanes
    if len(lanes) == 1:
        return lanes[0], lanes
    return None, lanes


def _repository_record_by_id(
    registry: dict[str, Any], repository_id: str
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in registry.get("repositories", [])
            if isinstance(item, dict)
            and item.get("repository_id") == repository_id
        ),
        None,
    )


def _repository_record_by_remote(
    registry: dict[str, Any], remote: str
) -> dict[str, Any] | None:
    normalized = normalize_remote(remote)
    matches = [
        item
        for item in registry.get("repositories", [])
        if isinstance(item, dict)
        and normalize_remote(
            str(item.get("remote_identity") or item.get("repository_id") or "")
        )
        == normalized
    ]
    if len(matches) > 1:
        raise ProtocolError(f"duplicate normalized remote identity: {normalized}")
    return matches[0] if matches else None


def _repository_context_decision(
    classification: str,
    source: str,
    candidates: Iterable[str],
) -> dict[str, Any]:
    return {
        "classification": classification,
        "resolution_source": source,
        "candidate_repository_ids": sorted(set(candidates)),
        "terminal_route": "USER_DECISION",
        "ask_for_absolute_path": False,
        "user_input_surface": "coordinator",
    }


def resolve_this_repository(
    context: dict[str, Any],
    registry: dict[str, Any],
    coordinator_state: dict[str, Any],
    *,
    inspect: Callable[[str], dict[str, str]] = inspect_repository,
) -> dict[str, Any]:
    """Resolve the generic trigger without repository-name input."""

    def from_live_roots(
        roots: Iterable[str], source: str
    ) -> dict[str, Any] | None:
        inspected: list[dict[str, str]] = []
        errors: list[str] = []
        for raw_root in roots:
            if not raw_root:
                continue
            try:
                info = inspect(str(raw_root))
            except (OSError, ProtocolError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
                continue
            if _repository_record_by_remote(registry, info["repository_id"]):
                inspected.append(info)
        by_remote = {item["repository_id"]: item for item in inspected}
        if len(by_remote) == 1:
            info = next(iter(by_remote.values()))
            record = _repository_record_by_remote(registry, info["repository_id"])
            assert record is not None
            return {
                "classification": "REPOSITORY_CONTEXT_RESOLVED",
                "repository_id": record["repository_id"],
                "remote_identity": record["remote_identity"],
                "root": info["root"],
                "resolution_source": source,
                "terminal_route": None,
            }
        if len(by_remote) > 1:
            return _repository_context_decision(
                "USER_DECISION_REPOSITORY_CONTEXT",
                source,
                by_remote,
            )
        if list(roots):
            return {
                "classification": "USER_ACTION_BIND_REPOSITORY",
                "resolution_source": source,
                "candidate_repository_ids": [],
                "validation_errors": errors,
                "terminal_route": "USER_ACTION",
                "ask_for_absolute_path": False,
                "user_input_surface": "coordinator",
            }
        return None

    invocation_root = context.get("invocation_git_root")
    if invocation_root:
        resolved = from_live_roots([str(invocation_root)], "invocation_git_root")
        if resolved is not None:
            return resolved

    workspace_roots = [
        str(item)
        for item in context.get("workspace_repository_roots", [])
        if item
    ]
    if workspace_roots:
        resolved = from_live_roots(
            workspace_roots, "current_project_or_workspace_repository_root"
        )
        if resolved is not None:
            return resolved

    task_remote = context.get("current_task_remote_identity")
    if task_remote:
        record = _repository_record_by_remote(registry, str(task_remote))
        if record is None:
            return {
                "classification": "USER_ACTION_BIND_REPOSITORY",
                "resolution_source": "current_task_remote_identity",
                "candidate_repository_ids": [],
                "terminal_route": "USER_ACTION",
                "ask_for_absolute_path": False,
                "user_input_surface": "coordinator",
            }
        return {
            "classification": "REPOSITORY_CONTEXT_RESOLVED",
            "repository_id": record["repository_id"],
            "remote_identity": record["remote_identity"],
            "root": None,
            "resolution_source": "current_task_remote_identity",
            "terminal_route": None,
        }

    selector = (
        context.get("active_repository_selector")
        or coordinator_state.get("active_repository_selector")
    )
    if selector:
        record = _repository_record_by_id(registry, str(selector))
        if record is None:
            return {
                "classification": "USER_ACTION_REPAIR_ACTIVE_REPOSITORY_SELECTOR",
                "resolution_source": "active_repository_selector",
                "candidate_repository_ids": [],
                "terminal_route": "USER_ACTION",
                "ask_for_absolute_path": False,
                "user_input_surface": "coordinator",
            }
        return {
            "classification": "REPOSITORY_CONTEXT_RESOLVED",
            "repository_id": record["repository_id"],
            "remote_identity": record["remote_identity"],
            "root": None,
            "resolution_source": "active_repository_selector",
            "terminal_route": None,
        }

    pending = [
        str(item)
        for item in coordinator_state.get("pending_repository_ids", [])
        if _repository_record_by_id(registry, str(item)) is not None
    ]
    if len(set(pending)) == 1:
        repository_id = next(iter(set(pending)))
        record = _repository_record_by_id(registry, repository_id)
        assert record is not None
        return {
            "classification": "REPOSITORY_CONTEXT_RESOLVED",
            "repository_id": repository_id,
            "remote_identity": record["remote_identity"],
            "root": None,
            "resolution_source": "exact_one_pending_repository",
            "terminal_route": None,
        }
    return _repository_context_decision(
        "USER_DECISION_REPOSITORY_CONTEXT",
        "coordinator_state",
        pending,
    )


def select_lane_for_context(
    registry: dict[str, Any],
    repository_id: str,
    missions: Iterable[dict[str, Any]],
) -> tuple[str | None, list[str]]:
    active_lanes = sorted(
        {
            str(item.get("supervision_lane"))
            for item in missions
            if isinstance(item, dict)
            and item.get("repository_id") == repository_id
            and item.get("state") not in TERMINAL_STATES
            and item.get("supervision_lane")
        }
    )
    if len(active_lanes) == 1:
        return _select_lane(registry, repository_id, active_lanes[0])
    if len(active_lanes) > 1:
        return None, active_lanes
    return _select_lane(registry, repository_id, None)


def _mission_action_rank(
    mission: dict[str, Any], coordinator_state: dict[str, Any]
) -> tuple[int, str]:
    state = str(mission.get("state") or "")
    response_keys = {
        (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        for item in coordinator_state.get("pending_user_responses", [])
        if isinstance(item, dict)
    }
    mission_key = (
        str(mission.get("repository_id") or ""),
        str(mission.get("mission_id") or ""),
        str(mission.get("attempt_id") or ""),
    )
    if state in {"USER_DECISION", "USER_ACTION"} and (
        mission.get("user_response_ready") is True or mission_key in response_keys
    ):
        return 1, "user_response_resumes_terminal_route"
    if state in {
        "SUPERVISOR_ADJUDICATION_REQUESTED",
        USER_RESPONSE_ADJUDICATION_STATE,
    }:
        return 2, "supervisor_verdict_waiting"
    if state == "SUPERVISOR_WORK_ORDER_REQUESTED":
        return 2, "supervisor_work_order_waiting"
    if state == "WORKER_DISPATCHED":
        return 3, "worker_result_waiting"
    if state == "WORKER_RESULT_RECEIVED":
        return 3, "worker_result_return_waiting"
    if state == "WORK_ORDER_RECEIVED":
        return 4, "work_order_dispatch_waiting"
    if str(mission.get("priority") or "").casefold() == "critical":
        return 5, "pending_critical_mission"
    return 6, "pending_ordinary_mission"


def _repository_worker_creatable(
    registry: dict[str, Any],
    repository: dict[str, Any],
    host: dict[str, Any],
    adapter: dict[str, Any],
) -> bool:
    policy = registry.get("coordinator_policy", {})
    existing = _worker_binding_for(
        registry,
        str(repository.get("repository_id") or ""),
        str(host.get("host_id") or ""),
    )
    if existing is not None:
        allowed = bool(existing.get("allow_create_worker_task", False))
    else:
        allowed = bool(
            repository.get(
                "allow_create_worker_task",
                policy.get("allow_create_worker_task", False),
            )
        )
    capable = bool(
        adapter.get("capabilities", {}).get("create_codex_thread")
        or host.get("capabilities", {}).get("codex_thread_create")
    )
    return allowed and capable


def _continuation_was_created(
    mission: dict[str, Any], missions: Iterable[dict[str, Any]]
) -> bool:
    if mission.get("state") != "CONTINUE":
        return False
    next_work_order = mission.get("next_work_order")
    if not isinstance(next_work_order, dict):
        return False
    target = (
        str(mission.get("repository_id") or ""),
        str(next_work_order.get("mission_id") or ""),
        str(next_work_order.get("attempt_id") or ""),
    )
    if not all(target):
        return False
    return any(
        item is not mission
        and (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        == target
        for item in missions
        if isinstance(item, dict)
    )


def _mission_order_key(mission: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return a stable best-effort chronology key for historical Missions."""
    return (
        str(mission.get("updated_at") or ""),
        str(mission.get("created_at") or ""),
        str(mission.get("mission_id") or ""),
        str(mission.get("attempt_id") or ""),
    )


def _repository_successor_request_allowed(
    repository_missions: Iterable[dict[str, Any]],
    coordinator_state: dict[str, Any],
    *,
    supervision_lane: str | None = None,
) -> bool:
    """Allow one successor inquiry only from a completed repository frontier.

    A parked review, operator action, safety ceiling, or unchanged BLOCKED
    frontier is not permission to invent another Mission in the same lane.
    Historical user terminals already routed to their exact Supervisor count as
    handled for compatibility with preserved v2 evidence.
    """
    history = [
        item
        for item in repository_missions
        if isinstance(item, dict)
        and (
            supervision_lane is None
            or str(item.get("supervision_lane") or supervision_lane)
            == supervision_lane
        )
    ]
    if not history:
        return True
    latest = max(history, key=_mission_order_key)
    # A routed response proves delivery, not Supervisor acceptance. Only a
    # completed frontier authorizes a generic successor inquiry.
    return latest.get("state") == "COMPLETE"


def select_next_actionable_repository(
    registry: dict[str, Any],
    hosts: dict[str, Any],
    adapter: dict[str, Any],
    missions: Iterable[dict[str, Any]],
    coordinator_state: dict[str, Any],
    *,
    excluded_repository_ids: Iterable[str] = (),
) -> dict[str, Any]:
    current_host = host_by_alias(
        hosts, str(adapter.get("current_host_alias") or "local")
    )
    if current_host is None:
        return {
            "classification": "BINDING_REPAIR_HOST",
            "terminal_route": None,
            "mode": "binding-repair",
        }
    host_id = str(current_host["host_id"])
    mission_list = [item for item in missions if isinstance(item, dict)]
    excluded = {str(item) for item in excluded_repository_ids}
    pending_response_keys = {
        (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        for item in coordinator_state.get("pending_user_responses", [])
        if isinstance(item, dict)
    }

    def is_resumable_user_terminal(item: dict[str, Any]) -> bool:
        key = (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        return item.get("state") in {"USER_DECISION", "USER_ACTION"} and (
            item.get("user_response_ready") is True
            or key in pending_response_keys
        )

    def is_actionable_mission(item: dict[str, Any]) -> bool:
        if _continuation_was_created(item, mission_list):
            return False
        return (
            item.get("state") not in TERMINAL_STATES
            or is_resumable_user_terminal(item)
        )

    duplicate_repositories: set[str] = set()
    seen_attempts: set[tuple[str, str, str]] = set()
    for mission in mission_list:
        if not is_actionable_mission(mission):
            continue
        key = (
            str(mission.get("repository_id") or ""),
            str(mission.get("mission_id") or ""),
            str(mission.get("attempt_id") or ""),
        )
        if key in seen_attempts:
            duplicate_repositories.add(key[0])
        seen_attempts.add(key)

    candidates: list[
        tuple[int, int, str, str, str | None, str | None]
    ] = []
    for registry_index, repository in enumerate(registry.get("repositories", [])):
        if not isinstance(repository, dict):
            continue
        repository_id = str(repository.get("repository_id") or "")
        if (
            not repository_id
            or repository_id in duplicate_repositories
            or repository_id in excluded
        ):
            continue
        try:
            if normalize_remote(str(repository.get("remote_identity") or "")) != repository_id:
                continue
        except ProtocolError:
            continue
        if repository_id not in current_host.get("known_repository_roots", {}):
            authorized = {
                str(item) for item in repository.get("authorized_host_ids", [])
            }
            if host_id not in authorized:
                continue

        lane_missions = [
            {
                **item,
                "state": (
                    "RESUMING_USER_RESPONSE"
                    if is_resumable_user_terminal(item)
                    else item.get("state")
                ),
            }
            for item in mission_list
            if not _continuation_was_created(item, mission_list)
        ]
        selected_lane, _ = select_lane_for_context(
            registry, repository_id, lane_missions
        )
        if selected_lane is None:
            continue
        supervisor = _supervisor_binding_for(
            registry, repository_id, selected_lane
        )
        supervisor_repairable = bool(
            supervisor
            and supervisor.get("binding_status")
            in {"active", "needs_verification"}
        )
        if not supervisor_repairable:
            continue

        worker = _worker_binding_for(registry, repository_id, host_id)
        worker_ready = bool(
            worker
            and worker.get("binding_status") in {"active", "needs_verification"}
            and worker.get("worker_task_id")
        )
        if not worker_ready and not _repository_worker_creatable(
            registry, repository, current_host, adapter
        ):
            possible = discover_worker_candidates(
                repository_id,
                host_id,
                registry,
                current_host,
                adapter,
                mission_list,
            )
            if not possible:
                continue

        repo_missions = [
            item
            for item in mission_list
            if item.get("repository_id") == repository_id
            and is_actionable_mission(item)
        ]
        if repo_missions:
            ranked = sorted(
                (
                    *_mission_action_rank(item, coordinator_state),
                    str(item.get("mission_id") or ""),
                )
                for item in repo_missions
            )
            rank, reason, _ = ranked[0]
            selected_mission = min(
                repo_missions,
                key=lambda item: (
                    *_mission_action_rank(item, coordinator_state),
                    str(item.get("mission_id") or ""),
                ),
            )
            if (
                int(selected_mission.get("completed_worker_turns", 0))
                >= int(selected_mission.get("safety_ceiling", 8))
                and rank > 3
            ):
                continue
            mission_id = str(selected_mission.get("mission_id") or "") or None
            attempt_id = str(selected_mission.get("attempt_id") or "") or None
        elif (
            repository.get("allow_request_next_mission", True)
            and _repository_successor_request_allowed(
                (
                    item
                    for item in mission_list
                    if item.get("repository_id") == repository_id
                ),
                coordinator_state,
                supervision_lane=selected_lane,
            )
        ):
            rank, reason, mission_id, attempt_id = (
                6,
                "supervisor_next_mission_request",
                None,
                None,
            )
        else:
            continue
        stable_order = int(repository.get("stable_order", registry_index))
        candidates.append(
            (
                rank,
                stable_order,
                repository_id,
                reason,
                mission_id,
                attempt_id,
            )
        )

    if not candidates:
        return {
            "classification": "NO_ACTIONABLE_REGISTERED_REPOSITORY",
            "terminal_route": None,
            "coordinator_outcome": "IDLE_CHECKPOINT",
            "user_input_surface": "coordinator",
        }
    rank, stable_order, repository_id, reason, mission_id, attempt_id = min(
        candidates
    )
    return {
        "classification": "NEXT_ACTIONABLE_REPOSITORY_SELECTED",
        "repository_id": repository_id,
        "selection_reason": reason,
        "selection_priority": rank,
        "registry_stable_order": stable_order,
        "mission_id": mission_id,
        "attempt_id": attempt_id,
        "terminal_route": None,
        "user_input_surface": "coordinator",
    }


def _pending_user_response_keys(
    coordinator_state: dict[str, Any],
) -> set[tuple[str, str, str]]:
    return {
        (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        for item in coordinator_state.get("pending_user_responses", [])
        if isinstance(item, dict)
    }


def _routed_user_response_keys(
    coordinator_state: dict[str, Any],
) -> set[tuple[str, str, str]]:
    return {
        (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        for item in coordinator_state.get("routed_user_responses", [])
        if isinstance(item, dict)
        and item.get("response_state") in {"routed", "resolved"}
    }


def queue_coordinator_event(
    coordinator_state: dict[str, Any],
    *,
    kind: str,
    repository_id: str,
    raw_text: str,
    mission_id: str | None = None,
) -> dict[str, Any]:
    """Persist an ongoing question or direction change before routing it."""
    if kind not in {"direction_update", "project_question"}:
        raise ProtocolError("unsupported Coordinator event kind")
    if not repository_id or not raw_text.strip():
        raise ProtocolError("Coordinator event requires repository and text")
    semantic_event = {
        "kind": kind,
        "repository_id": repository_id,
        "mission_id": mission_id,
        "raw_text": raw_text.strip(),
    }
    event_key = canonical_json_hash(semantic_event)
    pending = coordinator_state.setdefault("pending_user_events", [])
    routed = coordinator_state.setdefault("routed_user_events", [])
    if not isinstance(pending, list) or not isinstance(routed, list):
        raise ProtocolError("Coordinator event ledgers must be lists")

    def same_semantic_event(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        if item.get("event_key") == event_key:
            return True
        return all(item.get(key) == value for key, value in semantic_event.items())

    # Repeated delivery of the same not-yet-routed user input is idempotent.
    # Once routed, identical words in a later user turn are a legitimate new
    # occurrence and must not be suppressed forever.
    for item in pending:
        if same_semantic_event(item):
            return {
                "classification": "COORDINATOR_EVENT_ALREADY_RECORDED",
                "event_id": item.get("event_id"),
                "deduplicated": True,
            }
    prior_occurrences = [
        int(item.get("occurrence", 1))
        for item in routed
        if same_semantic_event(item)
        and isinstance(item.get("occurrence", 1), int)
    ]
    occurrence = max(prior_occurrences, default=0) + 1
    event_id = canonical_json_hash(
        {"event_key": event_key, "occurrence": occurrence}
    )
    event = {
        "event_id": event_id,
        "event_key": event_key,
        "occurrence": occurrence,
        "kind": kind,
        "repository_id": repository_id,
        "mission_id": mission_id,
        "raw_text": raw_text.strip(),
        "priority": 1 if kind == "direction_update" else 2,
        "state": "queued",
        "queued_at": utc_now(),
    }
    pending.append(event)
    return {
        "classification": "COORDINATOR_EVENT_QUEUED",
        "event_id": event_id,
        "deduplicated": False,
        "event": copy.deepcopy(event),
    }


def acknowledge_coordinator_event_routed(
    coordinator_state: dict[str, Any],
    event_id: str,
    recipient_thread_id: str,
) -> dict[str, Any]:
    pending = coordinator_state.get("pending_user_events", [])
    if not isinstance(pending, list):
        raise ProtocolError("pending_user_events must be a list")
    matches = [
        item
        for item in pending
        if isinstance(item, dict) and item.get("event_id") == event_id
    ]
    if len(matches) != 1:
        raise ProtocolError("exact queued Coordinator event is required")
    event = matches[0]
    event["state"] = "routed"
    event["recipient_thread_id"] = recipient_thread_id
    event["routed_at"] = utc_now()
    coordinator_state["pending_user_events"] = [
        item for item in pending if item is not event
    ]
    coordinator_state.setdefault("routed_user_events", []).append(event)
    return {
        "classification": "COORDINATOR_EVENT_ROUTED",
        "event_id": event_id,
        "recipient_thread_id": recipient_thread_id,
    }


def _user_card_for_mission(
    mission: dict[str, Any], coordinator_state: dict[str, Any]
) -> dict[str, Any] | None:
    state = str(mission.get("state") or "")
    if state not in {"USER_DECISION", "USER_ACTION"}:
        return None
    key = (
        str(mission.get("repository_id") or ""),
        str(mission.get("mission_id") or ""),
        str(mission.get("attempt_id") or ""),
    )
    if (
        mission.get("user_response_ready") is True
        or key in _pending_user_response_keys(coordinator_state)
        or key in _routed_user_response_keys(coordinator_state)
    ):
        return None
    user_packet = copy.deepcopy(mission.get("user_packet") or {})
    if state == "USER_DECISION":
        policy = _effective_review_policy(mission, user_packet)
        card_kind = "review"
    else:
        policy = {
            "gate": "required",
            "depth": "light",
            "stage": str(user_packet.get("stage") or "operator-action"),
        }
        card_kind = "action"
    card_id = sha256_text("|".join((*key, state)))[:20]
    return {
        "card_id": card_id,
        "kind": card_kind,
        "repository_id": mission.get("repository_id"),
        "mission_id": mission.get("mission_id"),
        "attempt_id": mission.get("attempt_id"),
        "terminal_route": state,
        "review_depth": policy["depth"],
        "stage": policy["stage"],
        "blocking_scope": "mission",
        "status": "waiting_user",
        "card": user_packet,
        "created_at": mission.get("updated_at") or mission.get("created_at"),
    }


def build_coordinator_snapshot(
    missions: Iterable[dict[str, Any]], coordinator_state: dict[str, Any]
) -> dict[str, Any]:
    """Project-independent execution and one-card-at-a-time user projection."""
    mission_list = [item for item in missions if isinstance(item, dict)]
    pending_keys = _pending_user_response_keys(coordinator_state)
    routed_keys = _routed_user_response_keys(coordinator_state)
    cards = [
        card
        for item in mission_list
        if (card := _user_card_for_mission(item, coordinator_state)) is not None
    ]
    cards.sort(
        key=lambda item: (
            str(item.get("created_at") or ""),
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
        )
    )
    active_card_id = coordinator_state.get("active_user_card_id")
    next_card = next(
        (item for item in cards if item["card_id"] == active_card_id),
        cards[0] if cards else None,
    )

    project_states: list[dict[str, Any]] = []
    for repository_id in sorted(
        {
            str(item.get("repository_id") or "")
            for item in mission_list
            if item.get("repository_id")
        }
    ):
        project_missions = [
            item
            for item in mission_list
            if item.get("repository_id") == repository_id
        ]
        waiting_user = [
            item
            for item in project_missions
            if item.get("state") in {"USER_DECISION", "USER_ACTION"}
            and (
                str(item.get("repository_id") or ""),
                str(item.get("mission_id") or ""),
                str(item.get("attempt_id") or ""),
            )
            not in pending_keys
            and (
                str(item.get("repository_id") or ""),
                str(item.get("mission_id") or ""),
                str(item.get("attempt_id") or ""),
            )
            not in routed_keys
            and item.get("user_response_ready") is not True
        ]
        response_ready = [
            item
            for item in project_missions
            if (
                str(item.get("repository_id") or ""),
                str(item.get("mission_id") or ""),
                str(item.get("attempt_id") or ""),
            )
            in pending_keys
            or item.get("user_response_ready") is True
            or item.get("state") == USER_RESPONSE_ADJUDICATION_STATE
        ]
        running = [
            item
            for item in project_missions
            if item.get("state") not in TERMINAL_STATES
            and item.get("state") != USER_RESPONSE_ADJUDICATION_STATE
            and not _continuation_was_created(item, mission_list)
        ]
        lane_groups: dict[str, list[dict[str, Any]]] = {}
        for item in project_missions:
            lane_groups.setdefault(
                str(item.get("supervision_lane") or "default"), []
            ).append(item)
        lane_frontiers = [
            max(items, key=_mission_order_key) for items in lane_groups.values()
        ]
        blocked = [
            item for item in lane_frontiers if item.get("state") == "BLOCKED"
        ]
        if response_ready:
            run_state = "resuming_user_response"
        elif running and waiting_user:
            run_state = "partially_parked"
        elif running:
            run_state = "running"
        elif waiting_user:
            run_state = "parked_for_user"
        elif blocked:
            run_state = "blocked"
        elif lane_frontiers and all(
            item.get("state") == "COMPLETE"
            or (
                str(item.get("repository_id") or ""),
                str(item.get("mission_id") or ""),
                str(item.get("attempt_id") or ""),
            )
            in routed_keys
            for item in lane_frontiers
        ):
            run_state = "complete"
        else:
            run_state = "idle"
        project_states.append(
            {
                "repository_id": repository_id,
                "run_state": run_state,
                "running_mission_count": len(running),
                "parked_mission_count": len(waiting_user),
                "response_resume_count": len(response_ready),
            }
        )

    continuing = any(
        item["run_state"]
        in {"running", "partially_parked", "resuming_user_response"}
        for item in project_states
    )
    if continuing:
        global_state = "RUNNING"
    elif cards:
        global_state = "AWAITING_USER_ONLY"
    elif any(item["run_state"] == "blocked" for item in project_states):
        global_state = "BLOCKED"
    else:
        global_state = "IDLE"
    all_current_missions_terminal = bool(mission_list) and all(
        item.get("state") in TERMINAL_STATES
        or (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        in routed_keys
        or _continuation_was_created(item, mission_list)
        for item in mission_list
    )
    has_inflight_work = any(
        item.get("state") in EXACT_OUTBOUND_WAIT_STATES for item in mission_list
    )
    has_ready_mission_work = any(
        item["running_mission_count"] > 0 or item["response_resume_count"] > 0
        for item in project_states
    )
    cycle_should_continue_now = has_inflight_work or has_ready_mission_work
    return {
        "classification": "COORDINATOR_PROJECT_INDEPENDENT_SNAPSHOT",
        "global_state": global_state,
        "global_completion_barrier": False,
        # Availability is durable; "ongoing" is reserved for real execution
        # evidence and must never be inferred from a configured heartbeat.
        "coordinator_lifecycle": "AVAILABLE",
        "coordinator_availability": "AVAILABLE",
        "coordinator_terminal": False,
        # Recovery arming depends on an exact durable claim and is decided only
        # by build_coordinator_plan(). A project snapshot cannot authorize it.
        "cycle_should_rearm": False,
        "has_inflight_work": has_inflight_work,
        "has_ready_mission_work": has_ready_mission_work,
        "cycle_should_continue_now": cycle_should_continue_now,
        "cycle_checkpoint_allowed": not cycle_should_continue_now,
        "heartbeat_role": "recovery_watchdog",
        "all_current_missions_terminal": all_current_missions_terminal,
        "execution_can_continue": continuing,
        "project_states": project_states,
        "next_user_card": next_card,
        "queued_user_card_count": len(cards),
        "remaining_user_card_count": max(0, len(cards) - (1 if next_card else 0)),
        "presentation": "one_card_at_a_time",
        "user_input_surface": "coordinator",
    }


def _action_repository_id(action: dict[str, Any]) -> str:
    payload = action.get("payload", {})
    if not isinstance(payload, dict):
        return ""
    route = payload.get("route", {})
    selection = payload.get("selection", {})
    event = payload.get("event", {})
    for value in (
        route.get("repository_id") if isinstance(route, dict) else None,
        selection.get("repository_id") if isinstance(selection, dict) else None,
        event.get("repository_id") if isinstance(event, dict) else None,
        payload.get("repository_id"),
    ):
        if value:
            return str(value)
    return ""


def _action_route_class(action: dict[str, Any]) -> str:
    if action.get("requires_external_result") is not True:
        return "local"
    if action.get("kind") in {
        "route_direction_update",
        "route_project_question",
        "route_user_response",
    }:
        return "control"
    return "execution"


def _action_observer_kind(action: dict[str, Any]) -> str | None:
    payload = action.get("payload", {})
    route = payload.get("route", {}) if isinstance(payload, dict) else {}
    if isinstance(route, dict) and route.get("observer_kind") in ROUTE_OBSERVER_KINDS:
        return str(route["observer_kind"])
    recipient_kind = route.get("recipient_kind") if isinstance(route, dict) else None
    if recipient_kind == "worker":
        return "codex_wait"
    if recipient_kind == "supervisor":
        return "chatgpt_poll"
    if action.get("kind") in {
        "dispatch_work_order",
        "await_worker_result",
        "probe_authorized_runtime_repair",
    }:
        return "codex_wait"
    if action.get("kind") in {
        "route_direction_update",
        "route_project_question",
        "route_user_response",
        "await_supervisor_verdict",
        "await_supervisor_work_order",
        "return_worker_result",
        "request_next_mission",
        "return_authorized_runtime_recovery_result",
    }:
        return "chatgpt_poll"
    return None


def _action_mission_identity(action: dict[str, Any]) -> tuple[str, str, str]:
    payload = action.get("payload", {})
    if not isinstance(payload, dict):
        return (_action_repository_id(action), "", "")
    route = payload.get("route", {})
    selection = payload.get("selection", {})
    route = route if isinstance(route, dict) else {}
    selection = selection if isinstance(selection, dict) else {}
    return (
        _action_repository_id(action),
        str(route.get("mission_id") or selection.get("mission_id") or ""),
        str(route.get("attempt_id") or selection.get("attempt_id") or ""),
    )


def _record_with_route_metadata(record: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(record)
    action = result.get("action", {})
    if isinstance(action, dict):
        repository_id, mission_id, attempt_id = _action_mission_identity(action)
        result.setdefault("repository_id", repository_id)
        result.setdefault("mission_id", mission_id or None)
        result.setdefault("attempt_id", attempt_id or None)
        result.setdefault("route_class", _action_route_class(action))
        observer_kind = _action_observer_kind(action)
        if observer_kind in ROUTE_OBSERVER_KINDS:
            result.setdefault("observer_kind", observer_kind)
    return result


def _infer_round_robin_cursor_repository_id(state: dict[str, Any]) -> str | None:
    """Recover the most recently admitted repository from older v2 state.

    Scheduler v2 existed briefly without a persisted fairness cursor.  Route
    leases retain admission order, so the last lease is the strongest signal.
    A short-lived claim is the next-best signal, followed by completion order.
    No delivery identity is changed while adding this derived state.
    """
    route_leases = state.get("route_leases", [])
    completed_actions = state.get("completed_actions", [])
    sources = (
        reversed(route_leases) if isinstance(route_leases, list) else (),
        (state.get("scheduler_claim"),),
        (
            reversed(completed_actions)
            if isinstance(completed_actions, list)
            else ()
        ),
    )
    for records in sources:
        for record in records:
            if not isinstance(record, dict):
                continue
            repository_id = str(
                record.get("repository_id")
                or _action_repository_id(record.get("action", {}))
                or ""
            )
            if repository_id:
                return repository_id
    return None


def _round_robin_repository_ranks(
    repository_order: dict[str, int],
    cursor_repository_id: str | None,
) -> dict[str, int]:
    """Return deterministic repository ranks starting just after the cursor."""
    ordered = sorted(
        repository_order,
        key=lambda repository_id: (
            repository_order[repository_id],
            repository_id,
        ),
    )
    if cursor_repository_id in ordered:
        pivot = ordered.index(cursor_repository_id) + 1
        ordered = ordered[pivot:] + ordered[:pivot]
    return {
        repository_id: rank for rank, repository_id in enumerate(ordered)
    }


def _legacy_active_claim_view(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return a read-only compatibility view; v2 logic never consumes it."""
    scheduler_claim = state.get("scheduler_claim")
    if isinstance(scheduler_claim, dict):
        return copy.deepcopy(scheduler_claim)
    leases = state.get("route_leases", [])
    if isinstance(leases, list) and len(leases) == 1 and isinstance(leases[0], dict):
        return copy.deepcopy(leases[0])
    return None


def _sync_legacy_active_claim_view(state: dict[str, Any]) -> None:
    # Keep old readers useful while there is at most one outstanding record.
    # This field is never authoritative once schema v2 is active.
    state["active_claim"] = _legacy_active_claim_view(state)


def default_scheduler_state(
    concurrency_limit: int = DEFAULT_COORDINATOR_CONCURRENCY_LIMIT,
) -> dict[str, Any]:
    if not isinstance(concurrency_limit, int) or not (
        1 <= concurrency_limit <= MAX_COORDINATOR_WAIT_TARGETS
    ):
        raise ProtocolError("Coordinator concurrency_limit must be between 1 and 8")
    return {
        "schema_version": SCHEDULER_STATE_VERSION,
        "revision": 0,
        "concurrency_limit": concurrency_limit,
        "round_robin_cursor_repository_id": None,
        "scheduler_claim": None,
        "route_leases": [],
        "completed_actions": [],
        # Deprecated compatibility projection. It mirrors scheduler_claim or a
        # sole route lease and is never used for decisions.
        "active_claim": None,
    }


def migrate_scheduler_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a validated scheduler-v2 value without losing a live v1 route."""
    if not isinstance(state, dict):
        raise ProtocolError("Coordinator scheduler state must be an object")
    version = state.get("schema_version")
    if version == SCHEDULER_STATE_VERSION:
        migrated = copy.deepcopy(state)
        migrated.setdefault(
            "concurrency_limit", DEFAULT_COORDINATOR_CONCURRENCY_LIMIT
        )
        migrated.setdefault("scheduler_claim", None)
        migrated.setdefault("route_leases", [])
        migrated.setdefault("completed_actions", [])
        if isinstance(migrated.get("scheduler_claim"), dict):
            migrated["scheduler_claim"] = _record_with_route_metadata(
                migrated["scheduler_claim"]
            )
        if isinstance(migrated.get("route_leases"), list):
            if not all(
                isinstance(item, dict) for item in migrated["route_leases"]
            ):
                raise ProtocolError("route_leases must contain objects")
            migrated["route_leases"] = [
                _record_with_route_metadata(item)
                for item in migrated["route_leases"]
            ]
        migrated.setdefault(
            "round_robin_cursor_repository_id",
            _infer_round_robin_cursor_repository_id(migrated),
        )
        _sync_legacy_active_claim_view(migrated)
        _validate_scheduler_state_v2(migrated)
        return migrated
    if version != 1:
        raise ProtocolError("unsupported Coordinator scheduler state schema")

    migrated = {
        "schema_version": SCHEDULER_STATE_VERSION,
        "revision": int(state.get("revision", 0)),
        "concurrency_limit": DEFAULT_COORDINATOR_CONCURRENCY_LIMIT,
        "round_robin_cursor_repository_id": None,
        "scheduler_claim": None,
        "route_leases": [],
        "completed_actions": copy.deepcopy(state.get("completed_actions", [])),
    }
    if isinstance(state.get("released_claims"), list):
        migrated["released_claims"] = copy.deepcopy(state["released_claims"])
    legacy = state.get("active_claim")
    if legacy is not None:
        if not isinstance(legacy, dict):
            raise ProtocolError("legacy active_claim must be an object or null")
        record = _record_with_route_metadata(legacy)
        status = str(record.get("status") or "claimed")
        if status in {"sent", "waiting"}:
            record["status"] = "waiting"
            record.setdefault(
                "leased_at",
                record.get("sent_at") or record.get("claimed_at") or "legacy-v1",
            )
            migrated["route_leases"].append(record)
        elif status in {"claimed", "prepared"}:
            migrated["scheduler_claim"] = record
        else:
            raise ProtocolError("legacy active_claim has invalid status")
    migrated["migration"] = {
        "from_schema_version": 1,
        "preserved_active_route": legacy is not None,
    }
    migrated["round_robin_cursor_repository_id"] = (
        _infer_round_robin_cursor_repository_id(migrated)
    )
    _sync_legacy_active_claim_view(migrated)
    _validate_scheduler_state_v2(migrated)
    return migrated


def _ensure_scheduler_state_v2(state: dict[str, Any]) -> None:
    migrated = migrate_scheduler_state(state)
    state.clear()
    state.update(migrated)


def load_scheduler_state(path: Path | str) -> dict[str, Any]:
    target = Path(path)
    source = target
    if not source.is_file() and target.name == DEFAULT_SCHEDULER_STATE.name:
        sibling_v1 = target.with_name(LEGACY_SCHEDULER_STATE.name)
        if sibling_v1.is_file():
            source = sibling_v1
    raw = load_json(source) if source.is_file() else default_scheduler_state()
    return migrate_scheduler_state(raw)


def _validate_scheduler_record(
    item: Any,
    label: str,
    *,
    allowed_statuses: set[str],
) -> str:
    if not isinstance(item, dict):
        raise ProtocolError(f"{label} must be an object")
    action_id = str(item.get("action_id") or "")
    if not action_id:
        raise ProtocolError(f"{label} requires action_id")
    action = item.get("action")
    if not isinstance(action, dict):
        raise ProtocolError(f"{label} requires the claimed action")
    status = str(item.get("status") or "claimed")
    if status not in allowed_statuses:
        raise ProtocolError(f"{label} has invalid status")
    if status in {"prepared", "sent", "waiting"}:
        if action.get("requires_external_result") is not True:
            raise ProtocolError(f"{label} must reference an external action")
        if not str(item.get("recipient_thread_id") or ""):
            raise ProtocolError(f"{label} requires recipient_thread_id")
        for field in ("packet_sha256", "delivery_token"):
            if not re.fullmatch(r"[0-9a-fA-F]{64}", str(item.get(field) or "")):
                raise ProtocolError(f"{label} requires {field}")
        if item.get("observer_kind") not in ROUTE_OBSERVER_KINDS:
            raise ProtocolError(f"{label} requires a supported observer_kind")
    if status == "effect_prepared":
        if action.get("kind") not in AUTHORIZED_RUNTIME_LOCAL_ACTION_KINDS:
            raise ProtocolError(
                f"{label} effect_prepared is restricted to authorized runtime actions"
            )
        receipt = item.get("effect_receipt")
        if not isinstance(receipt, dict):
            raise ProtocolError(f"{label} effect_prepared requires effect_receipt")
        if not str(receipt.get("path") or "") or not re.fullmatch(
            r"[0-9a-fA-F]{64}", str(receipt.get("identity_sha256") or "")
        ):
            raise ProtocolError(f"{label} has invalid effect_receipt")
    return action_id


def _validate_scheduler_state_v2(state: dict[str, Any]) -> None:
    if state.get("schema_version") != SCHEDULER_STATE_VERSION:
        raise ProtocolError("unsupported Coordinator scheduler state schema")
    if not isinstance(state.get("revision", 0), int) or int(
        state.get("revision", 0)
    ) < 0:
        raise ProtocolError("scheduler revision must be a non-negative integer")
    concurrency_limit = state.get("concurrency_limit")
    if not isinstance(concurrency_limit, int) or not (
        1 <= concurrency_limit <= MAX_COORDINATOR_WAIT_TARGETS
    ):
        raise ProtocolError("Coordinator concurrency_limit must be between 1 and 8")
    round_robin_cursor = state.get("round_robin_cursor_repository_id")
    if round_robin_cursor is not None and (
        not isinstance(round_robin_cursor, str) or not round_robin_cursor
    ):
        raise ProtocolError(
            "round_robin_cursor_repository_id must be a non-empty string or null"
        )

    seen: set[str] = set()
    scheduler_claim = state.get("scheduler_claim")
    if scheduler_claim is not None:
        seen.add(
            _validate_scheduler_record(
                scheduler_claim,
                "scheduler_claim",
                allowed_statuses={"claimed", "prepared", "effect_prepared"},
            )
        )

    leases = state.get("route_leases")
    if not isinstance(leases, list):
        raise ProtocolError("route_leases must be a list")
    if len(leases) > concurrency_limit:
        raise ProtocolError("route_leases exceed Coordinator concurrency_limit")
    execution_repositories: set[str] = set()
    for index, lease in enumerate(leases):
        action_id = _validate_scheduler_record(
            lease,
            f"route_leases[{index}]",
            allowed_statuses={"sent", "waiting"},
        )
        if action_id in seen:
            raise ProtocolError(f"duplicate active scheduler action: {action_id}")
        seen.add(action_id)
        action = lease.get("action", {})
        route_class = str(lease.get("route_class") or _action_route_class(action))
        repository_id = str(
            lease.get("repository_id") or _action_repository_id(action)
        )
        if route_class == "execution" and repository_id:
            if repository_id in execution_repositories:
                raise ProtocolError(
                    "only one external execution route per repository is allowed"
                )
            execution_repositories.add(repository_id)

    completed = state.get("completed_actions", [])
    if not isinstance(completed, list):
        raise ProtocolError("completed_actions must be a list")
    completed_seen: set[str] = set()
    for index, item in enumerate(completed):
        if not isinstance(item, dict):
            raise ProtocolError(f"completed_actions[{index}] must be an object")
        action_id = str(item.get("action_id") or "")
        if not action_id:
            raise ProtocolError(f"completed_actions[{index}] requires action_id")
        if action_id in completed_seen or action_id in seen:
            raise ProtocolError(f"duplicate completed scheduler action: {action_id}")
        if item.get("requires_external_result") is True:
            if not str(item.get("recipient_thread_id") or ""):
                raise ProtocolError(
                    f"completed_actions[{index}] requires recipient_thread_id"
                )
            for field in ("packet_sha256", "delivery_token"):
                if not re.fullmatch(
                    r"[0-9a-fA-F]{64}", str(item.get(field) or "")
                ):
                    raise ProtocolError(
                        f"completed_actions[{index}] requires {field}"
                    )
            if not str(item.get("evidence") or "").strip():
                raise ProtocolError(
                    f"completed_actions[{index}] requires result evidence"
                )
        completed_seen.add(action_id)


def validate_scheduler_state(state: dict[str, Any]) -> None:
    if state.get("schema_version") == 1:
        migrate_scheduler_state(state)
        return
    _validate_scheduler_state_v2(state)


def collect_authority_signals(
    registry: dict[str, Any],
    hosts: dict[str, Any],
    adapter: dict[str, Any],
) -> list[dict[str, Any]]:
    """Hash only explicitly registered repository authority files."""
    current_host = host_by_alias(
        hosts, str(adapter.get("current_host_alias") or "local")
    )
    roots = (
        current_host.get("known_repository_roots", {})
        if isinstance(current_host, dict)
        else {}
    )
    signals: list[dict[str, Any]] = []
    for repository in registry.get("repositories", []):
        if not isinstance(repository, dict):
            continue
        repository_id = str(repository.get("repository_id") or "")
        configured = repository.get("authority_watch", [])
        if not repository_id or not isinstance(configured, list) or not configured:
            continue
        root_value = roots.get(repository_id)
        root = Path(str(root_value)).resolve() if root_value else None
        sources: list[dict[str, Any]] = []
        for raw in configured:
            if isinstance(raw, str):
                relative = raw
                role = "authority"
            elif isinstance(raw, dict):
                relative = str(raw.get("path") or "")
                role = str(raw.get("role") or "authority")
            else:
                raise ProtocolError(
                    f"invalid authority_watch entry for {repository_id}"
                )
            relative_path = Path(relative)
            if (
                not relative
                or relative_path.is_absolute()
                or ".." in relative_path.parts
            ):
                raise ProtocolError(
                    f"authority_watch must be repository-relative: {relative}"
                )
            source: dict[str, Any] = {
                "path": relative_path.as_posix(),
                "role": role,
            }
            if root is None:
                source["status"] = "root_unavailable"
            else:
                candidate = (root / relative_path).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError as exc:
                    raise ProtocolError(
                        f"authority_watch escapes repository root: {relative}"
                    ) from exc
                source["absolute_path"] = str(candidate)
                if candidate.is_file():
                    source.update(
                        {
                            "status": "present",
                            "sha256": sha256_file(candidate),
                            "size": candidate.stat().st_size,
                        }
                    )
                else:
                    source["status"] = "missing"
            sources.append(source)
        signal_payload = {
            "repository_id": repository_id,
            "root": str(root) if root is not None else None,
            "sources": sources,
        }
        signal_payload["authority_fingerprint"] = canonical_json_hash(
            signal_payload
        )
        signals.append(signal_payload)
    signals.sort(key=lambda item: str(item.get("repository_id") or ""))
    return signals


def _semantic_scheduler_value(value: Any) -> Any:
    """Remove observation-only metadata from scheduler fingerprints."""
    if isinstance(value, dict):
        return {
            str(key): _semantic_scheduler_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not str(key).endswith("_at")
            and str(key)
            not in {
                "observed_at",
                "timestamp",
                "duration_ms",
                "time_to_first_token_ms",
            }
        }
    if isinstance(value, list):
        return [_semantic_scheduler_value(item) for item in value]
    return value


def _repository_scheduler_fingerprint(
    repository_id: str,
    *,
    registry: dict[str, Any],
    hosts: dict[str, Any],
    adapter: dict[str, Any],
    missions: Iterable[dict[str, Any]],
    coordinator_state: dict[str, Any],
    authority_signal: dict[str, Any] | None,
) -> str:
    def matching(items: Iterable[Any]) -> list[dict[str, Any]]:
        return [
            _semantic_scheduler_value(item)
            for item in items
            if isinstance(item, dict)
            and item.get("repository_id") == repository_id
        ]

    current_host = host_by_alias(
        hosts, str(adapter.get("current_host_alias") or "local")
    )
    route_threads = [
        _semantic_scheduler_value(item)
        for item in _adapter_threads(adapter)
        if isinstance(item, dict)
        and (
            item.get("repository_id") == repository_id
            or any(
                binding.get("repository_id") == repository_id
                and binding.get("supervisor_thread_id") == item.get("id")
                for binding in registry.get("supervisor_bindings", [])
                if isinstance(binding, dict)
            )
        )
    ]
    payload = {
        "repository": next(
            (
                _semantic_scheduler_value(item)
                for item in registry.get("repositories", [])
                if isinstance(item, dict)
                and item.get("repository_id") == repository_id
            ),
            None,
        ),
        "supervisor_bindings": matching(
            registry.get("supervisor_bindings", [])
        ),
        "worker_bindings": matching(registry.get("worker_bindings", [])),
        "host": (
            {
                "host_id": current_host.get("host_id"),
                "status": current_host.get("status"),
                "root": current_host.get("known_repository_roots", {}).get(
                    repository_id
                ),
                "worker_task": current_host.get(
                    "available_worker_tasks", {}
                ).get(repository_id),
            }
            if isinstance(current_host, dict)
            else None
        ),
        "threads": route_threads,
        "missions": sorted(
            matching(missions),
            key=lambda item: (
                str(item.get("mission_id") or ""),
                str(item.get("attempt_id") or ""),
            ),
        ),
        "pending_user_responses": matching(
            coordinator_state.get("pending_user_responses", [])
        ),
        "routed_user_responses": matching(
            coordinator_state.get("routed_user_responses", [])
        ),
        "pending_user_events": matching(
            coordinator_state.get("pending_user_events", [])
        ),
        "routed_user_events": matching(
            coordinator_state.get("routed_user_events", [])
        ),
        "authorized_runtime_actions": matching(
            coordinator_state.get("authorized_runtime_actions", [])
        ),
        "authority_signal": _semantic_scheduler_value(authority_signal),
    }
    return canonical_json_hash(payload)


def _scheduler_action(
    kind: str,
    payload: dict[str, Any],
    *,
    priority: int,
    stable_order: int,
    requires_external_result: bool = False,
) -> dict[str, Any]:
    identity = {
        "kind": kind,
        "payload": _semantic_scheduler_value(payload),
    }
    return {
        "action_id": canonical_json_hash(identity)[:32],
        "kind": kind,
        "priority": priority,
        "stable_order": stable_order,
        "requires_external_result": requires_external_result,
        "payload": copy.deepcopy(payload),
    }


def _selection_action_kind(selection: dict[str, Any]) -> str:
    reason = str(selection.get("selection_reason") or "")
    return {
        "user_response_resumes_terminal_route": "route_user_response",
        "supervisor_verdict_waiting": "await_supervisor_verdict",
        "supervisor_work_order_waiting": "await_supervisor_work_order",
        "worker_result_waiting": "await_worker_result",
        "worker_result_return_waiting": "return_worker_result",
        "work_order_dispatch_waiting": "dispatch_work_order",
        "pending_critical_mission": "advance_mission",
        "pending_ordinary_mission": "advance_mission",
        "supervisor_next_mission_request": "request_next_mission",
    }.get(reason, "reconcile_selection")


def _selection_route(
    selection: dict[str, Any],
    *,
    registry: dict[str, Any],
    hosts: dict[str, Any],
    adapter: dict[str, Any],
    missions: Iterable[dict[str, Any]],
    kind: str,
) -> dict[str, Any]:
    repository_id = str(selection.get("repository_id") or "")
    lane, _ = select_lane_for_context(registry, repository_id, missions)
    supervisor = (
        _supervisor_binding_for(registry, repository_id, lane)
        if lane is not None
        else None
    )
    current_host = host_by_alias(
        hosts, str(adapter.get("current_host_alias") or "local")
    )
    worker = (
        _worker_binding_for(
            registry, repository_id, str(current_host.get("host_id") or "")
        )
        if isinstance(current_host, dict)
        else None
    )
    if kind in {"dispatch_work_order", "await_worker_result"}:
        recipient_kind = "worker"
        recipient_thread_id = (
            worker.get("worker_task_id") if isinstance(worker, dict) else None
        )
    elif kind in {
        "route_user_response",
        "await_supervisor_verdict",
        "await_supervisor_work_order",
        "return_worker_result",
        "request_next_mission",
    }:
        recipient_kind = "supervisor"
        recipient_thread_id = (
            supervisor.get("supervisor_thread_id")
            if isinstance(supervisor, dict)
            else None
        )
    else:
        recipient_kind = "local_transition"
        recipient_thread_id = None
    return {
        "repository_id": repository_id,
        "mission_id": selection.get("mission_id"),
        "attempt_id": selection.get("attempt_id"),
        "supervision_lane": lane,
        "recipient_kind": recipient_kind,
        "recipient_thread_id": recipient_thread_id,
        "observer_kind": _route_observer_kind(adapter, recipient_thread_id),
    }


def _runtime_backup_path(target: Path, target_pre_sha256: str) -> Path:
    return target.with_name(
        f"{target.name}.backup.{target_pre_sha256.upper()}"
    )


def _runtime_quarantine_path(target: Path, target_pre_sha256: str) -> Path:
    return target.with_name(
        f"{target.name}.quarantine.{target_pre_sha256.upper()}"
    )


def _authorized_runtime_identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_action_id": record.get("runtime_action_id"),
        "repository_id": record.get("repository_id"),
        "mission_id": record.get("mission_id"),
        "attempt_id": record.get("attempt_id"),
        "supervision_lane": record.get("supervision_lane"),
        "supervisor_thread_id": record.get("supervisor_thread_id"),
        "worker_task_id": record.get("worker_task_id"),
        "worker_host_id": record.get("worker_host_id"),
        "worker_adapter_host_id": record.get("worker_adapter_host_id"),
        "handler_id": record.get("handler_id"),
        "handler_version": record.get("handler_version"),
        "repair_execution_surface": record.get("repair_execution_surface"),
        "probe_execution_surface": record.get("probe_execution_surface"),
        "authorization_id": record.get("authorization_id"),
        "authorization_action_id": record.get("authorization_action_id"),
        "decision_evidence_sha256": record.get("decision_evidence_sha256"),
        "decision_event_id": record.get("decision_event_id"),
        "supervisor_text_sha256": record.get("supervisor_text_sha256"),
        "authorization_payload_sha256": record.get(
            "authorization_payload_sha256"
        ),
        "target_path": record.get("target_path"),
        "target_pre_sha256": record.get("target_pre_sha256"),
        "target_pre_size": record.get("target_pre_size"),
    }


def _authorized_runtime_record(
    coordinator_state: dict[str, Any], runtime_action_id: str
) -> dict[str, Any]:
    matches = [
        item
        for item in coordinator_state.get("authorized_runtime_actions", [])
        if isinstance(item, dict)
        and item.get("runtime_action_id") == runtime_action_id
    ]
    if len(matches) != 1:
        raise ProtocolError(
            f"exact authorized runtime action is required: {runtime_action_id}"
        )
    return matches[0]


def validate_authorized_runtime_action(record: dict[str, Any]) -> None:
    if not isinstance(record, dict):
        raise ProtocolError("authorized runtime action must be an object")
    allowed_fields = {
        "runtime_action_id",
        "repository_id",
        "mission_id",
        "attempt_id",
        "supervision_lane",
        "supervisor_thread_id",
        "worker_task_id",
        "worker_host_id",
        "worker_adapter_host_id",
        "handler_id",
        "handler_version",
        "repair_execution_surface",
        "probe_execution_surface",
        "authorization_id",
        "authorization_action_id",
        "decision_evidence_path",
        "decision_evidence_sha256",
        "decision_event_id",
        "supervisor_text_path",
        "supervisor_text_sha256",
        "authorization_payload_sha256",
        "target_path",
        "target_pre_sha256",
        "target_pre_size",
        "backup_path",
        "quarantine_path",
        "repository_root",
        "recovery_receipt_path",
        "identity_sha256",
        "phase",
        "authority_consumed",
        "registered_at",
        "effect_receipt",
        "probe_result",
        "recovery_result",
        "supervisor_result",
        "completed_at",
    }
    unexpected = sorted(set(record) - allowed_fields)
    if unexpected:
        raise ProtocolError(
            "authorized runtime action has unexpected fields: "
            + ", ".join(unexpected)
        )
    required_text = (
        "runtime_action_id",
        "repository_id",
        "mission_id",
        "attempt_id",
        "supervision_lane",
        "supervisor_thread_id",
        "worker_task_id",
        "worker_host_id",
        "worker_adapter_host_id",
        "authorization_id",
        "authorization_action_id",
        "decision_evidence_path",
        "decision_event_id",
        "supervisor_text_path",
        "target_path",
        "backup_path",
        "quarantine_path",
        "repository_root",
        "recovery_receipt_path",
        "registered_at",
    )
    for field in required_text:
        if not isinstance(record.get(field), str) or not record[field]:
            raise ProtocolError(f"authorized runtime action requires {field}")
    if record.get("handler_id") != AUTHORIZED_RUNTIME_HANDLER_ID:
        raise ProtocolError("unauthorized runtime handler")
    if record.get("handler_version") != AUTHORIZED_RUNTIME_HANDLER_VERSION:
        raise ProtocolError("unsupported authorized runtime handler version")
    if (
        record.get("repair_execution_surface")
        != AUTHORIZED_RUNTIME_REPAIR_EXECUTION_SURFACE
    ):
        raise ProtocolError("authorized runtime repair execution surface mismatch")
    if (
        record.get("probe_execution_surface")
        != AUTHORIZED_RUNTIME_PROBE_EXECUTION_SURFACE
    ):
        raise ProtocolError("authorized runtime probe execution surface mismatch")
    if not re.fullmatch(
        r"[0-9a-f]{32}", str(record.get("authorization_action_id") or "")
    ):
        raise ProtocolError("authorized runtime action requires authorization_action_id")
    for field in (
        "decision_evidence_sha256",
        "supervisor_text_sha256",
        "authorization_payload_sha256",
        "target_pre_sha256",
        "identity_sha256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", str(record.get(field) or "")):
            raise ProtocolError(f"authorized runtime action requires lowercase {field}")
    if not isinstance(record.get("target_pre_size"), int) or int(
        record["target_pre_size"]
    ) < 0:
        raise ProtocolError("authorized runtime target_pre_size must be non-negative")
    if (
        record["target_pre_size"] != AUTHORIZED_RUNTIME_TARGET_PRE_SIZE
        or record["target_pre_sha256"] != AUTHORIZED_RUNTIME_TARGET_PRE_SHA256
    ):
        raise ProtocolError("authorized runtime target identity is not allowlisted")
    if record.get("phase") not in AUTHORIZED_RUNTIME_PHASES:
        raise ProtocolError("authorized runtime action has invalid phase")
    if not isinstance(record.get("authority_consumed"), bool):
        raise ProtocolError("authorized runtime authority_consumed must be boolean")
    expected_target = _normalized_absolute_path(
        DEFAULT_CODEX_DENY_READ_STATE_PATH
    )
    if _normalized_absolute_path(record["target_path"]) != expected_target:
        raise ProtocolError("authorized runtime target path is not allowlisted")
    target = Path(record["target_path"])
    expected_backup = _runtime_backup_path(
        target, record["target_pre_sha256"]
    )
    expected_quarantine = _runtime_quarantine_path(
        target, record["target_pre_sha256"]
    )
    if _normalized_absolute_path(record["backup_path"]) != _normalized_absolute_path(
        expected_backup
    ):
        raise ProtocolError("authorized runtime backup path drift")
    if _normalized_absolute_path(
        record["quarantine_path"]
    ) != _normalized_absolute_path(expected_quarantine):
        raise ProtocolError("authorized runtime quarantine path drift")
    expected_receipt = (
        Path(record["repository_root"])
        / AUTHORIZED_RUNTIME_RECEIPT_RELATIVE_PATH
    )
    if _normalized_absolute_path(
        record["recovery_receipt_path"]
    ) != _normalized_absolute_path(expected_receipt):
        raise ProtocolError("authorized runtime recovery receipt path drift")
    if record["identity_sha256"] != canonical_json_hash(
        _authorized_runtime_identity(record)
    ):
        raise ProtocolError("authorized runtime identity hash mismatch")
    effect_receipt = record.get("effect_receipt")
    if effect_receipt is not None:
        if not isinstance(effect_receipt, dict):
            raise ProtocolError("authorized runtime effect_receipt must be an object")
        if set(effect_receipt) - {
            "path",
            "identity_sha256",
            "phase",
            "authority_consumed",
            "target_pre_sha256",
            "target_post_sha256",
            "updated_at",
        }:
            raise ProtocolError("authorized runtime effect_receipt has unexpected fields")
        if effect_receipt.get("path") != record["recovery_receipt_path"]:
            raise ProtocolError("authorized runtime effect receipt path mismatch")
        if effect_receipt.get("identity_sha256") != record["identity_sha256"]:
            raise ProtocolError("authorized runtime effect receipt identity mismatch")
        if effect_receipt.get("authority_consumed") is not record[
            "authority_consumed"
        ]:
            raise ProtocolError("authorized runtime effect receipt authority mismatch")
    if record["phase"] in {
        "EFFECT_INTENT",
        "EFFECT_PREPARED",
        "REPAIR_PREPARED",
        "ROLLBACK_REQUIRED",
    } and not isinstance(effect_receipt, dict):
        raise ProtocolError("effectful authorized runtime phase requires a receipt")
    phase = str(record["phase"])
    consumed = bool(record["authority_consumed"])
    if phase == "AUTHORIZED":
        if consumed or effect_receipt is not None:
            raise ProtocolError("AUTHORIZED runtime phase must be unconsumed")
    elif phase == "EFFECT_INTENT":
        if consumed or effect_receipt.get("phase") != "EFFECT_INTENT":
            raise ProtocolError("EFFECT_INTENT must be durable and unconsumed")
    elif phase in {"EFFECT_PREPARED", "REPAIR_PREPARED", "ROLLBACK_REQUIRED"}:
        if not consumed or effect_receipt.get("authority_consumed") is not True:
            raise ProtocolError(f"{phase} runtime phase must be consumed")
    if phase == "ROLLBACK_REQUIRED" and not isinstance(
        record.get("probe_result"), dict
    ):
        raise ProtocolError("ROLLBACK_REQUIRED requires an exact probe_result")
    if phase in {"AUTHORIZED", "EFFECT_INTENT", "EFFECT_PREPARED", "REPAIR_PREPARED"} and any(
        field in record
        for field in ("probe_result", "recovery_result", "supervisor_result", "completed_at")
    ):
        raise ProtocolError(f"{phase} contains a future runtime result")
    if phase == "ROLLBACK_REQUIRED" and any(
        field in record
        for field in ("recovery_result", "supervisor_result", "completed_at")
    ):
        raise ProtocolError("ROLLBACK_REQUIRED contains a future runtime result")
    if phase in {"RESULT_READY", "COMPLETE"}:
        recovery_result = record.get("recovery_result")
        if (
            not isinstance(recovery_result, dict)
            or not isinstance(recovery_result.get("classification"), str)
            or not recovery_result["classification"]
            or recovery_result.get("authority_consumed") is not consumed
        ):
            raise ProtocolError(f"{phase} requires an authority-bound recovery_result")
    if phase == "RESULT_READY" and any(
        field in record for field in ("supervisor_result", "completed_at")
    ):
        raise ProtocolError("RESULT_READY contains a future Supervisor result")
    if phase == "COMPLETE" and (
        not isinstance(record.get("supervisor_result"), dict)
        or record["supervisor_result"].get("disposition")
        not in {"accepted", "rejected", "reconcile"}
        or not isinstance(
            record["supervisor_result"].get("evidence_sha256"), str
        )
        or not isinstance(record.get("completed_at"), str)
        or not record["completed_at"]
    ):
        raise ProtocolError("COMPLETE requires Supervisor result and completed_at")


def register_authorized_runtime_action(
    coordinator_state: dict[str, Any],
    spec: dict[str, Any],
    *,
    registry: dict[str, Any],
    hosts: dict[str, Any],
    adapter: dict[str, Any],
    scheduler_state: dict[str, Any],
    trusted_events_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Register one exact Supervisor-authorized, fixed-handler recovery."""
    allowed_spec_fields = {
        "schema_version",
        "runtime_action_id",
        "repository_id",
        "mission_id",
        "attempt_id",
        "supervision_lane",
        "supervisor_thread_id",
        "worker_task_id",
        "worker_host_id",
        "handler_id",
        "authorization_id",
        "decision_evidence_path",
        "decision_evidence_sha256",
        "target_path",
        "target_pre_sha256",
        "target_pre_size",
    }
    unexpected = sorted(set(spec) - allowed_spec_fields)
    if unexpected:
        raise ProtocolError(
            "authorized runtime spec has unexpected fields: "
            + ", ".join(unexpected)
        )
    if spec.get("schema_version") != 1:
        raise ProtocolError("authorized runtime spec schema_version must be 1")
    required = allowed_spec_fields - {"schema_version"}
    missing = sorted(field for field in required if field not in spec)
    if missing:
        raise ProtocolError(
            "authorized runtime spec missing: " + ", ".join(missing)
        )
    if spec.get("handler_id") != AUTHORIZED_RUNTIME_HANDLER_ID:
        raise ProtocolError("unauthorized runtime handler")
    target_path = str(spec.get("target_path") or "")
    if _normalized_absolute_path(target_path) != _normalized_absolute_path(
        DEFAULT_CODEX_DENY_READ_STATE_PATH
    ):
        raise ProtocolError("authorized runtime target path is not allowlisted")
    decision_path = Path(str(spec.get("decision_evidence_path") or ""))
    trusted_root = Path(
        DEFAULT_COORDINATOR_EVENTS
        if trusted_events_dir is None
        else trusted_events_dir
    ).resolve(strict=False)
    try:
        decision_path.resolve(strict=False).relative_to(trusted_root)
    except ValueError as exc:
        raise ProtocolError(
            "Supervisor decision evidence is outside the trusted event ledger"
        ) from exc
    evidence_sha = str(spec.get("decision_evidence_sha256") or "").lower()
    if not decision_path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha):
        raise ProtocolError("exact Supervisor decision evidence is required")
    if sha256_file(decision_path) != evidence_sha:
        raise ProtocolError("Supervisor decision evidence SHA-256 mismatch")
    decision = load_json(decision_path)
    required_decision_values = {
        "event_kind": "SUPERVISOR_DIRECTION_UPDATE_VERDICT",
        "disposition": "ADOPTED",
        "repository_id": str(spec.get("repository_id") or ""),
        "mission_id": str(spec.get("mission_id") or ""),
        "supervisor_thread_id": str(spec.get("supervisor_thread_id") or ""),
        "authorization_id": str(spec.get("authorization_id") or ""),
        "resulting_action_id": str(spec.get("runtime_action_id") or ""),
        "repair_authorization_gate": "SATISFIED",
        "runtime_recovery_gate": "PENDING",
    }
    for field, expected in required_decision_values.items():
        if str(decision.get(field) or "") != expected:
            raise ProtocolError(
                f"Supervisor decision evidence does not authorize {field}"
            )
    if str(decision.get("attempt_id") or "") != str(
        spec.get("attempt_id") or ""
    ):
        raise ProtocolError("Supervisor decision evidence attempt mismatch")
    decision_event_id = str(decision.get("event_id") or "")
    if not decision_event_id:
        raise ProtocolError("Supervisor decision evidence requires event_id")
    supervisor_text_path = Path(
        str(decision.get("supervisor_text_path") or "")
    )
    supervisor_text_sha = str(
        decision.get("supervisor_text_sha256") or ""
    ).lower()
    if (
        not supervisor_text_path.is_file()
        or not re.fullmatch(r"[0-9a-f]{64}", supervisor_text_sha)
        or sha256_file(supervisor_text_path) != supervisor_text_sha
    ):
        raise ProtocolError("Supervisor text evidence identity mismatch")
    supervisor_text = supervisor_text_path.read_text(encoding="utf-8")
    action_match = re.search(
        r"action_id:\s*\r?\n\s*([0-9a-fA-F]{32})",
        supervisor_text,
    )
    payload_match = re.search(
        r"payload_sha256:\s*\r?\n\s*([0-9a-fA-F]{64})",
        supervisor_text,
    )
    if action_match is None or payload_match is None:
        raise ProtocolError("Supervisor text lacks authorization delivery identity")
    authorization_action_id = action_match.group(1).lower()
    authorization_payload_sha = payload_match.group(1).lower()
    if str(spec.get("target_path") or "") not in supervisor_text:
        raise ProtocolError("Supervisor text exact target does not match spec")
    if str(AUTHORIZED_RUNTIME_TARGET_PRE_SIZE) not in supervisor_text or (
        AUTHORIZED_RUNTIME_TARGET_PRE_SHA256.upper() not in supervisor_text.upper()
    ):
        raise ProtocolError("Supervisor text target identity does not match handler")
    repair_executor_match = re.search(
        r"(?mi)^repair_executor:\s*\r?\n[ \t]+([^\r\n]+)$",
        supervisor_text,
    )
    required_probe_surface_match = re.search(
        r"(?mi)^required_probe_surface:\s*\r?\n[ \t]+([^\r\n]+)$",
        supervisor_text,
    )
    disallowed_probe_surface_match = re.search(
        r"(?mi)^disallowed_probe_surface:\s*\r?\n[ \t]+([^\r\n]+)$",
        supervisor_text,
    )
    repair_executor = (
        repair_executor_match.group(1).strip()
        if repair_executor_match is not None
        else ""
    )
    if repair_executor.casefold() != (
        AUTHORIZED_RUNTIME_REPAIR_EXECUTOR_AUTHORITY.casefold()
    ):
        raise ProtocolError(
            "Supervisor text does not bind repair to the Thank runtime owner"
        )
    required_probe_surface = (
        required_probe_surface_match.group(1).strip().casefold()
        if required_probe_surface_match is not None
        else ""
    )
    if required_probe_surface != "restricted workspace-write windows sandbox":
        raise ProtocolError(
            "Supervisor text does not bind probe to restricted workspace-write"
        )
    disallowed_probe_surface = (
        disallowed_probe_surface_match.group(1).strip().casefold()
        if disallowed_probe_surface_match is not None
        else ""
    )
    if disallowed_probe_surface != AUTHORIZED_RUNTIME_DISALLOWED_PROBE_SURFACE:
        raise ProtocolError(
            "Supervisor text does not disallow danger-full-access probe"
        )
    _ensure_scheduler_state_v2(scheduler_state)
    authorization_completion = next(
        (
            item
            for item in scheduler_state.get("completed_actions", [])
            if isinstance(item, dict)
            and item.get("action_id") == authorization_action_id
        ),
        None,
    )
    if not isinstance(authorization_completion, dict):
        raise ProtocolError("authorization delivery is absent from scheduler history")
    expected_completion = {
        "kind": "route_direction_update",
        "outcome": "ADOPTED_RUNTIME_RECOVERY_ACTION",
        "packet_sha256": authorization_payload_sha,
        "recipient_thread_id": str(spec.get("supervisor_thread_id") or ""),
        "repository_id": str(spec.get("repository_id") or ""),
        "route_class": "control",
    }
    for field, expected in expected_completion.items():
        if authorization_completion.get(field) != expected:
            raise ProtocolError(
                f"authorization scheduler completion {field} mismatch"
            )
    completion_evidence = Path(
        str(authorization_completion.get("evidence") or "")
    )
    if not completion_evidence.is_absolute():
        completion_evidence = SKILL_ROOT / completion_evidence
    if _normalized_absolute_path(
        completion_evidence
    ) != _normalized_absolute_path(decision_path):
        raise ProtocolError("authorization scheduler evidence path mismatch")
    target_pre_sha = str(spec.get("target_pre_sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", target_pre_sha):
        raise ProtocolError("target_pre_sha256 must be a SHA-256 digest")
    if not isinstance(spec.get("target_pre_size"), int) or int(
        spec["target_pre_size"]
    ) < 0:
        raise ProtocolError("target_pre_size must be a non-negative integer")
    if (
        target_pre_sha != AUTHORIZED_RUNTIME_TARGET_PRE_SHA256
        or int(spec["target_pre_size"]) != AUTHORIZED_RUNTIME_TARGET_PRE_SIZE
    ):
        raise ProtocolError("authorized runtime target identity is not allowlisted")

    repository_id = str(spec.get("repository_id") or "")
    lane = str(spec.get("supervision_lane") or "")
    supervisor = _supervisor_binding_for(registry, repository_id, lane)
    if (
        not isinstance(supervisor, dict)
        or supervisor.get("binding_status") != "active"
        or supervisor.get("supervisor_thread_id")
        != spec.get("supervisor_thread_id")
    ):
        raise ProtocolError("authorized runtime Supervisor binding mismatch")
    supervisor_thread, supervisor_issues = _exact_thread(
        adapter,
        str(spec["supervisor_thread_id"]),
        kind="chatgpt",
        title=str(supervisor.get("expected_supervisor_title") or ""),
        project_id=str(supervisor.get("supervisor_project_id") or ""),
    )
    if supervisor_issues or not isinstance(supervisor_thread, dict):
        raise ProtocolError(
            "authorized runtime Supervisor adapter binding is not read-verified: "
            + ", ".join(supervisor_issues or ["exact_thread_missing"])
        )
    _require_live_thread_status(supervisor_thread, "authorized runtime Supervisor")
    worker_host_id = str(spec.get("worker_host_id") or "")
    worker_binding = _worker_binding_for(registry, repository_id, worker_host_id)
    if (
        not isinstance(worker_binding, dict)
        or worker_binding.get("binding_status") != "active"
        or worker_binding.get("worker_task_id") != spec.get("worker_task_id")
        or worker_binding.get("host_id") != worker_host_id
    ):
        raise ProtocolError("authorized runtime Worker binding mismatch")
    host = host_by_alias(hosts, worker_host_id)
    if not isinstance(host, dict):
        raise ProtocolError("authorized runtime Worker host is unavailable")
    repository_root = str(
        host.get("known_repository_roots", {}).get(repository_id) or ""
    )
    if not repository_root or not Path(repository_root).is_dir():
        raise ProtocolError("authorized runtime repository root is unavailable")
    root_verification = host.get("root_verifications", {}).get(repository_id)
    if (
        _normalized_absolute_path(str(worker_binding.get("root_hint") or ""))
        != _normalized_absolute_path(repository_root)
        or not isinstance(root_verification, dict)
        or root_verification.get("repository_id") != repository_id
        or _normalized_absolute_path(str(root_verification.get("root") or ""))
        != _normalized_absolute_path(repository_root)
    ):
        raise ProtocolError("authorized runtime repository root binding mismatch")
    host_aliases = [
        worker_host_id,
        *host.get("app_host_ids", []),
        *_aliases(host),
    ]
    worker_thread, worker_issues = _exact_thread(
        adapter,
        str(spec["worker_task_id"]),
        kind="codex",
        host_aliases=host_aliases,
    )
    if worker_issues or not isinstance(worker_thread, dict):
        raise ProtocolError(
            "authorized runtime Worker adapter binding is not read-verified: "
            + ", ".join(worker_issues or ["exact_thread_missing"])
        )
    _require_live_thread_status(worker_thread, "authorized runtime Worker")
    if (
        worker_thread.get("repository_id") != repository_id
        or _normalized_absolute_path(str(worker_thread.get("cwd") or ""))
        != _normalized_absolute_path(repository_root)
    ):
        raise ProtocolError("authorized runtime Worker repository/cwd mismatch")
    worker_projection = _route_observer_projection(
        adapter, str(spec["worker_task_id"])
    )
    worker_observer = (
        worker_projection.get("observer_kind")
        if isinstance(worker_projection, dict)
        else None
    )
    supervisor_observer = _route_observer_kind(
        adapter, str(spec["supervisor_thread_id"])
    )
    if worker_observer != "codex_wait" or supervisor_observer != "chatgpt_poll":
        raise ProtocolError("authorized runtime route transport mismatch")

    target = Path(target_path)
    record: dict[str, Any] = {
        "runtime_action_id": str(spec["runtime_action_id"]),
        "repository_id": repository_id,
        "mission_id": str(spec["mission_id"]),
        "attempt_id": str(spec["attempt_id"]),
        "supervision_lane": lane,
        "supervisor_thread_id": str(spec["supervisor_thread_id"]),
        "worker_task_id": str(spec["worker_task_id"]),
        "worker_host_id": worker_host_id,
        "worker_adapter_host_id": str(worker_projection["host_id"]),
        "handler_id": AUTHORIZED_RUNTIME_HANDLER_ID,
        "handler_version": AUTHORIZED_RUNTIME_HANDLER_VERSION,
        "repair_execution_surface": (
            AUTHORIZED_RUNTIME_REPAIR_EXECUTION_SURFACE
        ),
        "probe_execution_surface": AUTHORIZED_RUNTIME_PROBE_EXECUTION_SURFACE,
        "authorization_id": str(spec["authorization_id"]),
        "authorization_action_id": authorization_action_id,
        "decision_evidence_path": str(decision_path.resolve(strict=False)),
        "decision_evidence_sha256": evidence_sha,
        "decision_event_id": decision_event_id,
        "supervisor_text_path": str(
            supervisor_text_path.resolve(strict=False)
        ),
        "supervisor_text_sha256": supervisor_text_sha,
        "authorization_payload_sha256": authorization_payload_sha,
        "target_path": str(target.resolve(strict=False)),
        "target_pre_sha256": target_pre_sha,
        "target_pre_size": int(spec["target_pre_size"]),
        "backup_path": str(_runtime_backup_path(target, target_pre_sha)),
        "quarantine_path": str(_runtime_quarantine_path(target, target_pre_sha)),
        "repository_root": str(Path(repository_root).resolve(strict=False)),
        "recovery_receipt_path": str(
            (Path(repository_root) / AUTHORIZED_RUNTIME_RECEIPT_RELATIVE_PATH).resolve(
                strict=False
            )
        ),
        "phase": "AUTHORIZED",
        "authority_consumed": False,
        "registered_at": utc_now(),
    }
    record["identity_sha256"] = canonical_json_hash(
        _authorized_runtime_identity(record)
    )
    validate_authorized_runtime_action(record)
    ledger = coordinator_state.setdefault("authorized_runtime_actions", [])
    if not isinstance(ledger, list):
        raise ProtocolError("authorized_runtime_actions must be a list")
    existing = next(
        (
            item
            for item in ledger
            if isinstance(item, dict)
            and item.get("runtime_action_id") == record["runtime_action_id"]
        ),
        None,
    )
    if isinstance(existing, dict):
        if existing.get("identity_sha256") != record["identity_sha256"]:
            raise ProtocolError("conflicting authorized runtime action replay")
        return {
            "classification": "AUTHORIZED_RUNTIME_ACTION_ALREADY_REGISTERED",
            "runtime_action_id": record["runtime_action_id"],
            "identity_sha256": record["identity_sha256"],
            "deduplicated": True,
        }
    if any(
        isinstance(item, dict)
        and item.get("identity_sha256") == record["identity_sha256"]
        for item in ledger
    ):
        raise ProtocolError("authorized runtime identity already has another action id")
    ledger.append(record)
    return {
        "classification": "AUTHORIZED_RUNTIME_ACTION_REGISTERED",
        "runtime_action_id": record["runtime_action_id"],
        "identity_sha256": record["identity_sha256"],
        "phase": record["phase"],
        "deduplicated": False,
    }


def _runtime_action_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_action_id": record["runtime_action_id"],
        "identity_sha256": record["identity_sha256"],
        "handler_id": record["handler_id"],
        "authorization_id": record["authorization_id"],
        "authorization_action_id": record["authorization_action_id"],
        "authorization_payload_sha256": record[
            "authorization_payload_sha256"
        ],
        "repair_execution_surface": record["repair_execution_surface"],
        "probe_execution_surface": record["probe_execution_surface"],
        "decision_event_id": record["decision_event_id"],
        "decision_evidence_sha256": record["decision_evidence_sha256"],
        "target_pre_sha256": record["target_pre_sha256"],
        "target_pre_size": record["target_pre_size"],
        "target_path": record["target_path"],
        "backup_path": record["backup_path"],
        "quarantine_path": record["quarantine_path"],
        "recovery_receipt_path": record["recovery_receipt_path"],
    }


def _runtime_route(record: dict[str, Any], recipient: str) -> dict[str, Any]:
    if recipient == "worker":
        thread_id = record["worker_task_id"]
        observer_kind = "codex_wait"
        host_id = record["worker_adapter_host_id"]
    elif recipient == "supervisor":
        thread_id = record["supervisor_thread_id"]
        observer_kind = "chatgpt_poll"
        host_id = None
    else:
        raise ProtocolError("invalid authorized runtime recipient")
    result = {
        "repository_id": record["repository_id"],
        "mission_id": record["mission_id"],
        "attempt_id": record["attempt_id"],
        "supervision_lane": record["supervision_lane"],
        "recipient_kind": recipient,
        "recipient_thread_id": thread_id,
        "observer_kind": observer_kind,
    }
    if host_id is not None:
        result["host_id"] = host_id
    return result


def _fixed_runtime_probe_contract(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "probe_execution_surface": record["probe_execution_surface"],
        "same_worker_required": True,
        "probe_a": {
            "cwd": r"C:\tmp",
            "argv": ["cmd.exe", "/d", "/c", "exit", "0"],
        },
        "postcheck": {
            "path": record["target_path"],
            "required": ["exists", "nonempty", "not_nul_only", "valid_json"],
        },
        "probe_b": {
            "cwd": record["repository_root"],
            "argv": ["cmd.exe", "/d", "/c", "exit", "0"],
            "only_after": "probe_a_and_postcheck_pass",
        },
        "runtime_doctor": {
            "mode": "existing_read_only_entrypoint",
            "only_after": "probe_a_and_probe_b_pass",
            "expected": {
                "ymm4_discovery": "pass",
                "ymm4_version": (
                    "4.54.0.1+76b177dd451f9d162816dabc4ac658180e869582"
                ),
                "source_project_sha256": (
                    "4f8dc13976cb4ef56ea582d75e1ff92ae9d2780fff4cf53c13923d561955bdbf"
                ),
                "canonical_script_sha256": (
                    "989312b58c31ad538b4ca622cba5a9dfebec1f9288ccb1312199fdb83aec0e9e"
                ),
                "voice_profile": {
                    "name": "ゆっくり霊夢赤縁",
                    "engine": "AquesTalk V1_7",
                    "speed": 125,
                },
                "acl_ownership_policy_mutation_count": 0,
            },
            "prohibited": ["YMM4_UI_launch", "render", "candidate_generation"],
        },
        "receipt_completion_required": {
            "path": record["recovery_receipt_path"],
            "fields": [
                "regenerated_state.size",
                "regenerated_state.sha256",
                "regenerated_state.json_parse_result",
                "probe_a",
                "probe_b",
                "runtime_doctor",
                "rollback_performed",
                "authority_consumed",
                "mutation_counts",
                "final_blocker_classification",
                "product_resume_readiness",
            ],
        },
        "stop_boundary": (
            "no YMM4, render, or candidate generation before both probes pass"
        ),
    }


def _runtime_completion_applied(
    record: dict[str, Any], completion: dict[str, Any]
) -> bool:
    action = completion.get("action", {})
    kind = str(action.get("kind") or "")
    outcome = str(completion.get("outcome") or "")
    phase = str(record.get("phase") or "")
    if kind == "execute_authorized_runtime_repair":
        if outcome == "repair_prepared":
            return bool(
                record.get("authority_consumed") is True
                and phase
                in {"REPAIR_PREPARED", "ROLLBACK_REQUIRED", "RESULT_READY", "COMPLETE"}
            )
        if outcome == "precondition_mismatch":
            return bool(
                phase in {"RESULT_READY", "COMPLETE"}
                and record.get("recovery_result", {}).get("classification")
                == "AUTHORIZED_RUNTIME_PRECONDITION_MISMATCH"
            )
    if kind == "probe_authorized_runtime_repair":
        probe = record.get("probe_result", {})
        return bool(
            isinstance(probe, dict)
            and probe.get("action_id") == completion.get("action_id")
            and probe.get("outcome") == outcome
            and str(probe.get("evidence") or "")
            == str(completion.get("evidence") or "")
        )
    if kind == "rollback_authorized_runtime_repair":
        return bool(
            phase in {"RESULT_READY", "COMPLETE"}
            and record.get("recovery_result", {}).get("rolled_back") is True
        )
    if kind == "return_authorized_runtime_recovery_result":
        result = record.get("supervisor_result", {})
        return bool(
            phase == "COMPLETE"
            and isinstance(result, dict)
            and result.get("disposition") == outcome
            and _normalized_absolute_path(str(result.get("evidence_path") or ""))
            == _normalized_absolute_path(str(completion.get("evidence") or ""))
        )
    return False


def _runtime_unapplied_completion(
    record: dict[str, Any], scheduler_state: dict[str, Any]
) -> dict[str, Any] | None:
    for completion in scheduler_state.get("completed_actions", []):
        if not isinstance(completion, dict):
            continue
        action = completion.get("action", {})
        if not isinstance(action, dict) or action.get("kind") not in AUTHORIZED_RUNTIME_ACTION_KINDS:
            continue
        runtime = action.get("payload", {}).get("runtime_action", {})
        if (
            runtime.get("runtime_action_id") != record["runtime_action_id"]
            or runtime.get("identity_sha256") != record["identity_sha256"]
        ):
            continue
        if not _runtime_completion_applied(record, completion):
            return completion
    return None


def _authorized_runtime_candidates(
    coordinator_state: dict[str, Any],
    scheduler_state: dict[str, Any],
    repository_order: dict[str, int],
) -> tuple[list[dict[str, Any]], set[str]]:
    candidates: list[dict[str, Any]] = []
    active_repositories: set[str] = set()
    active_runtime_action_ids: set[str] = set()
    for active in [
        scheduler_state.get("scheduler_claim"),
        *scheduler_state.get("route_leases", []),
    ]:
        if not isinstance(active, dict):
            continue
        runtime_payload = active.get("action", {}).get("payload", {}).get(
            "runtime_action", {}
        )
        runtime_action_id = str(
            runtime_payload.get("runtime_action_id") or ""
        )
        if runtime_action_id:
            active_runtime_action_ids.add(runtime_action_id)
    for record in sorted(
        coordinator_state.get("authorized_runtime_actions", []),
        key=lambda item: (
            repository_order.get(str(item.get("repository_id") or ""), 999999),
            str(item.get("runtime_action_id") or ""),
        ),
    ):
        validate_authorized_runtime_action(record)
        if record["phase"] == "COMPLETE":
            continue
        repository_id = record["repository_id"]
        active_repositories.add(repository_id)
        unapplied = _runtime_unapplied_completion(record, scheduler_state)
        if isinstance(unapplied, dict):
            candidates.append(
                _scheduler_action(
                    "reconcile_authorized_runtime_completion",
                    {
                        "runtime_action": _runtime_action_payload(record),
                        "completed_action": {
                            "action_id": unapplied.get("action_id"),
                            "outcome": unapplied.get("outcome"),
                            "evidence": unapplied.get("evidence"),
                            "sha256": canonical_json_hash(unapplied),
                        },
                        "route": {
                            "repository_id": repository_id,
                            "mission_id": record["mission_id"],
                            "attempt_id": record["attempt_id"],
                            "supervision_lane": record["supervision_lane"],
                            "recipient_kind": "local_transition",
                            "recipient_thread_id": None,
                            "observer_kind": None,
                        },
                    },
                    priority=0,
                    stable_order=repository_order.get(repository_id, 999999),
                )
            )
            continue
        if record["runtime_action_id"] in active_runtime_action_ids:
            continue
        phase = record["phase"]
        payload: dict[str, Any] = {
            "runtime_action": _runtime_action_payload(record),
        }
        kind: str
        external = False
        if phase in {"AUTHORIZED", "EFFECT_INTENT", "EFFECT_PREPARED"}:
            kind = "execute_authorized_runtime_repair"
            payload["route"] = {
                "repository_id": repository_id,
                "mission_id": record["mission_id"],
                "attempt_id": record["attempt_id"],
                "supervision_lane": record["supervision_lane"],
                "recipient_kind": "local_transition",
                "recipient_thread_id": None,
                "observer_kind": None,
            }
        elif phase == "REPAIR_PREPARED":
            kind = "probe_authorized_runtime_repair"
            external = True
            payload["route"] = _runtime_route(record, "worker")
            payload["probe_contract"] = _fixed_runtime_probe_contract(record)
            payload["effect_receipt"] = copy.deepcopy(record["effect_receipt"])
        elif phase == "ROLLBACK_REQUIRED":
            kind = "rollback_authorized_runtime_repair"
            payload["route"] = {
                "repository_id": repository_id,
                "mission_id": record["mission_id"],
                "attempt_id": record["attempt_id"],
                "supervision_lane": record["supervision_lane"],
                "recipient_kind": "local_transition",
                "recipient_thread_id": None,
                "observer_kind": None,
            }
            payload["probe_result"] = copy.deepcopy(record.get("probe_result"))
        elif phase == "RESULT_READY":
            kind = "return_authorized_runtime_recovery_result"
            external = True
            payload["route"] = _runtime_route(record, "supervisor")
            payload["recovery_result"] = copy.deepcopy(
                record.get("recovery_result")
            )
        else:
            raise ProtocolError(f"unsupported authorized runtime phase: {phase}")
        candidates.append(
            _scheduler_action(
                kind,
                payload,
                priority=3,
                stable_order=repository_order.get(repository_id, 999999),
                requires_external_result=external,
            )
        )
    return candidates, active_repositories


def build_coordinator_plan(
    registry: dict[str, Any],
    hosts: dict[str, Any],
    adapter: dict[str, Any],
    missions: Iterable[dict[str, Any]],
    coordinator_state: dict[str, Any],
    scheduler_state: dict[str, Any],
    *,
    authority_signals: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the one deterministic plan shared by status and recovery."""
    _ensure_scheduler_state_v2(scheduler_state)
    mission_list = [copy.deepcopy(item) for item in missions if isinstance(item, dict)]
    base = build_coordinator_snapshot(mission_list, coordinator_state)
    # Sensors stay outside this pure decision function. The CLI supplies the
    # current allowlisted authority hashes; unit callers may pass an empty set.
    signals = list(authority_signals or [])
    signal_by_repository = {
        str(item.get("repository_id") or ""): item
        for item in signals
        if isinstance(item, dict) and item.get("repository_id")
    }
    repository_order = {
        str(item.get("repository_id") or ""): int(
            item.get("stable_order", index)
        )
        for index, item in enumerate(registry.get("repositories", []))
        if isinstance(item, dict) and item.get("repository_id")
    }
    repository_fingerprints = {
        repository_id: _repository_scheduler_fingerprint(
            repository_id,
            registry=registry,
            hosts=hosts,
            adapter=adapter,
            missions=mission_list,
            coordinator_state=coordinator_state,
            authority_signal=signal_by_repository.get(repository_id),
        )
        for repository_id in repository_order
    }
    state_fingerprint = canonical_json_hash(
        {
            "repositories": repository_fingerprints,
            "active_repository_selector": coordinator_state.get(
                "active_repository_selector"
            ),
            "pending_repository_ids": coordinator_state.get(
                "pending_repository_ids", []
            ),
        }
    )
    completed_ids = {
        str(item.get("action_id") or "")
        for item in scheduler_state.get("completed_actions", [])
        if isinstance(item, dict)
    }
    candidates: list[dict[str, Any]] = []
    runtime_candidates, runtime_action_repositories = (
        _authorized_runtime_candidates(
            coordinator_state,
            scheduler_state,
            repository_order,
        )
    )
    candidates.extend(
        item
        for item in runtime_candidates
        if item["action_id"] not in completed_ids
    )

    pending_events = sorted(
        (
            item
            for item in coordinator_state.get("pending_user_events", [])
            if isinstance(item, dict)
        ),
        key=lambda item: (
            int(item.get("priority", 99)),
            str(item.get("queued_at") or ""),
            str(item.get("event_id") or ""),
        ),
    )
    for event in pending_events:
        repository_id = str(event.get("repository_id") or "")
        lane, lane_candidates = select_lane_for_context(
            registry, repository_id, mission_list
        )
        supervisor = (
            _supervisor_binding_for(registry, repository_id, lane)
            if lane is not None
            else None
        )
        recipient = (
            supervisor.get("supervisor_thread_id")
            if isinstance(supervisor, dict)
            else None
        )
        kind = (
            "route_direction_update"
            if event.get("kind") == "direction_update"
            else "route_project_question"
        )
        if recipient is None:
            kind = "clarify_event_route"
        action = _scheduler_action(
            kind,
            {
                "event": _semantic_scheduler_value(event),
                "route": {
                    "repository_id": repository_id,
                    "mission_id": event.get("mission_id"),
                    "supervision_lane": lane,
                    "candidate_lanes": lane_candidates,
                    "recipient_kind": "supervisor",
                    "recipient_thread_id": recipient,
                    "observer_kind": _route_observer_kind(adapter, recipient),
                },
            },
            priority=int(event.get("priority", 2)),
            stable_order=repository_order.get(repository_id, 999999),
            requires_external_result=recipient is not None,
        )
        if action["action_id"] not in completed_ids:
            candidates.append(action)

    next_card = base.get("next_user_card")
    if isinstance(next_card, dict):
        repository_id = str(next_card.get("repository_id") or "")
        action = _scheduler_action(
            "present_user_card",
            {
                "repository_id": repository_id,
                "card": next_card,
            },
            priority=20,
            stable_order=repository_order.get(repository_id, 999999),
        )
        if action["action_id"] not in completed_ids:
            candidates.append(action)

    for repository_id in repository_order:
        if repository_id in runtime_action_repositories:
            continue
        repository_missions = [
            item
            for item in mission_list
            if item.get("repository_id") == repository_id
        ]
        if repository_missions:
            default_lane = next(
                (
                    str(item.get("default_supervision_lane") or "default")
                    for item in registry.get("repositories", [])
                    if isinstance(item, dict)
                    and item.get("repository_id") == repository_id
                ),
                "default",
            )
            lane_groups: dict[str, list[dict[str, Any]]] = {}
            for item in repository_missions:
                lane_groups.setdefault(
                    str(item.get("supervision_lane") or default_lane), []
                ).append(item)
            for lane, lane_missions in sorted(lane_groups.items()):
                latest = max(lane_missions, key=_mission_order_key)
                if latest.get("state") != "BLOCKED":
                    continue
                authority_signal = signal_by_repository.get(repository_id)
                blocked_contract = latest.get("blocked_contract")
                contract_issues = blocked_contract_issues(blocked_contract)
                if contract_issues:
                    action = _scheduler_action(
                        "repair_blocker_contract",
                        {
                            "repository_id": repository_id,
                            "supervision_lane": lane,
                            "mission_id": latest.get("mission_id"),
                            "attempt_id": latest.get("attempt_id"),
                            "supervisor_thread_id": latest.get(
                                "supervisor_thread_id"
                            ),
                            "worker_task_id": latest.get("worker_task_id"),
                            "contract_version": BLOCKED_CONTRACT_VERSION,
                            "required_fields": list(
                                BLOCKED_CONTRACT_REQUIRED_FIELDS
                            ),
                            "issues": contract_issues,
                            "repair_boundary": (
                                "reconstruct from the persisted Worker Report and "
                                "Supervisor verdict; do not probe or infer new facts"
                            ),
                        },
                        priority=25,
                        stable_order=repository_order[repository_id],
                    )
                    if action["action_id"] not in completed_ids:
                        candidates.append(action)
                    continue
                action = _scheduler_action(
                    "inspect_blocked_recovery",
                    {
                        "repository_id": repository_id,
                        "supervision_lane": lane,
                        "mission_id": latest.get("mission_id"),
                        "attempt_id": latest.get("attempt_id"),
                        "supervisor_thread_id": latest.get(
                            "supervisor_thread_id"
                        ),
                        "worker_task_id": latest.get("worker_task_id"),
                        "blocked_packet": blocked_contract,
                        "blocker_revision": canonical_json_hash(
                            {
                                "mission": _semantic_scheduler_value(latest),
                                "authority_fingerprint": (
                                    authority_signal or {}
                                ).get("authority_fingerprint"),
                            }
                        ),
                    },
                    priority=30,
                    stable_order=repository_order[repository_id],
                )
                if action["action_id"] not in completed_ids:
                    candidates.append(action)

        authority_signal = signal_by_repository.get(repository_id)
        if authority_signal is not None:
            action = _scheduler_action(
                "reconcile_repository_authority",
                {
                    "repository_id": repository_id,
                    "authority_signal": authority_signal,
                },
                priority=40,
                stable_order=repository_order[repository_id],
            )
            if action["action_id"] not in completed_ids:
                candidates.append(action)

    excluded: set[str] = set()
    while len(excluded) <= len(repository_order):
        selection = select_next_actionable_repository(
            registry,
            hosts,
            adapter,
            mission_list,
            coordinator_state,
            excluded_repository_ids=excluded,
        )
        if selection.get("classification") != "NEXT_ACTIONABLE_REPOSITORY_SELECTED":
            break
        repository_id = str(selection.get("repository_id") or "")
        kind = _selection_action_kind(selection)
        is_successor = kind == "request_next_mission"
        route = _selection_route(
            selection,
            registry=registry,
            hosts=hosts,
            adapter=adapter,
            missions=mission_list,
            kind=kind,
        )
        selected_mission = next(
            (
                item
                for item in mission_list
                if item.get("repository_id") == repository_id
                and item.get("mission_id") == selection.get("mission_id")
                and str(item.get("attempt_id") or "")
                == str(selection.get("attempt_id") or "")
            ),
            None,
        )
        action_revision = canonical_json_hash(
            {
                "selection": selection,
                "mission": _semantic_scheduler_value(selected_mission),
                "authority_fingerprint": (
                    signal_by_repository.get(repository_id) or {}
                ).get("authority_fingerprint"),
                "route": route,
            }
        )
        action = _scheduler_action(
            kind,
            {
                "selection": selection,
                "route": route,
                "action_revision": action_revision,
            },
            priority=(50 if is_successor else 10 + int(selection.get("selection_priority", 6))),
            stable_order=repository_order.get(repository_id, 999999),
            requires_external_result=kind
            in {
                "route_user_response",
                "await_supervisor_verdict",
                "await_supervisor_work_order",
                "await_worker_result",
                "return_worker_result",
                "dispatch_work_order",
                "request_next_mission",
            },
        )
        excluded.add(repository_id)
        if action["action_id"] in completed_ids:
            continue
        candidates.append(action)

    round_robin_cursor = scheduler_state.get(
        "round_robin_cursor_repository_id"
    )
    round_robin_ranks = _round_robin_repository_ranks(
        repository_order,
        round_robin_cursor,
    )
    candidates.sort(
        key=lambda item: (
            int(item.get("priority", 999999)),
            round_robin_ranks.get(
                _action_repository_id(item),
                len(round_robin_ranks) + int(item.get("stable_order", 999999)),
            ),
            int(item.get("stable_order", 999999)),
            str(item.get("action_id") or ""),
        )
    )
    scheduler_claim = copy.deepcopy(scheduler_state.get("scheduler_claim"))
    route_leases = [
        _record_with_route_metadata(item)
        for item in scheduler_state.get("route_leases", [])
        if isinstance(item, dict)
    ]
    observed_active_records = list(route_leases)
    if (
        isinstance(scheduler_claim, dict)
        and scheduler_claim.get("action", {}).get("requires_external_result") is True
        and scheduler_claim.get("status") == "prepared"
    ):
        observed_active_records.append(
            _record_with_route_metadata(scheduler_claim)
        )
    for active_record in observed_active_records:
        recipient_thread_id = str(
            active_record.get("recipient_thread_id") or ""
        )
        expected_observer = _route_observer_kind(
            adapter, recipient_thread_id
        )
        if active_record.get("observer_kind") != expected_observer:
            raise ProtocolError(
                "persisted route observer_kind does not match exact adapter recipient: "
                + recipient_thread_id
            )
    leased_action_ids = {
        str(item.get("action_id") or "") for item in route_leases
    }
    scheduler_claim_id = (
        str(scheduler_claim.get("action_id") or "")
        if isinstance(scheduler_claim, dict)
        else ""
    )
    leased_execution_identities = {
        (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        for item in route_leases
        if str(item.get("route_class") or "execution") == "execution"
    }
    leased_execution_repositories = {
        identity[0] for identity in leased_execution_identities if identity[0]
    }
    ready_actions = [
        copy.deepcopy(item)
        for item in candidates
        if str(item.get("action_id") or "") not in leased_action_ids
        and str(item.get("action_id") or "") != scheduler_claim_id
        and not (
            _action_route_class(item) == "execution"
            and _action_mission_identity(item) in leased_execution_identities
        )
        and not (
            item.get("kind") == "reconcile_repository_authority"
            and _action_repository_id(item) in leased_execution_repositories
        )
    ]

    active_route_keys = {
        (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        for item in route_leases
    }
    legacy_routes: list[dict[str, Any]] = []
    for item in mission_list:
        if item.get("state") not in EXACT_OUTBOUND_WAIT_STATES:
            continue
        key = (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        if key in active_route_keys:
            continue
        recipient = (
            item.get("worker_task_id")
            if item.get("state") == "WORKER_DISPATCHED"
            else item.get("supervisor_thread_id")
        )
        legacy_observer = _route_observer_kind(adapter, str(recipient or ""))
        expected_legacy_observer = (
            "codex_wait"
            if item.get("state") == "WORKER_DISPATCHED"
            else "chatgpt_poll"
        )
        if legacy_observer != expected_legacy_observer:
            raise ProtocolError(
                "legacy inferred route transport does not match Mission recipient role: "
                + str(recipient or "")
            )
        legacy_routes.append(
            {
                "action_id": "legacy-" + canonical_json_hash(
                    {
                        "identity": key,
                        "state": item.get("state"),
                        "recipient_thread_id": recipient,
                    }
                )[:24],
                "repository_id": key[0],
                "mission_id": key[1] or None,
                "attempt_id": key[2] or None,
                "status": "waiting",
                "state": item.get("state"),
                "recipient_thread_id": recipient,
                "after_cursor": None,
                "route_class": "execution",
                "observer_kind": legacy_observer,
                "legacy_inferred_route": True,
            }
        )

    active_routes = route_leases + legacy_routes
    # Validate the final projection, including routes inferred from legacy
    # Mission state.  Earlier validation of persisted leases alone is not
    # sufficient because an inferred route must not bypass the adapter.
    for route in active_routes:
        recipient = str(route.get("recipient_thread_id") or "")
        if not recipient:
            raise ProtocolError("active route lacks an exact recipient_thread_id")
        projection = _route_observer_projection(adapter, recipient)
        if not isinstance(projection, dict) or (
            route.get("observer_kind") != projection["observer_kind"]
        ):
            raise ProtocolError(
                "active route observer_kind does not match exact adapter recipient: "
                + recipient
            )
        if projection["observer_kind"] == "codex_wait":
            if route.get("host_id") not in {None, projection["host_id"]}:
                raise ProtocolError(
                    "active Codex route host_id does not match exact adapter recipient: "
                    + recipient
                )
            route["host_id"] = projection["host_id"]
        else:
            route.pop("host_id", None)
    wait_targets: list[dict[str, Any]] = []
    poll_targets: list[dict[str, Any]] = []
    target_indexes: dict[str, dict[tuple[str, str], int]] = {
        "codex_wait": {},
        "chatgpt_poll": {},
    }
    for route in active_routes:
        recipient = str(route.get("recipient_thread_id") or "")
        if not recipient:
            continue
        observer_kind = str(route.get("observer_kind") or "")
        if observer_kind not in ROUTE_OBSERVER_KINDS:
            raise ProtocolError(
                f"active route lacks a supported observer_kind: {recipient}"
            )
        targets = wait_targets if observer_kind == "codex_wait" else poll_targets
        target_index = target_indexes[observer_kind]
        cursor = str(route.get("after_cursor") or "")
        host_id = str(route.get("host_id") or "")
        if observer_kind == "codex_wait" and not host_id:
            raise ProtocolError(
                f"Codex wait route lacks exact adapter host_id: {recipient}"
            )
        wait_key = (
            f"{host_id}:{recipient}" if observer_kind == "codex_wait" else recipient,
            cursor,
        )
        action_id = str(route.get("action_id") or "")
        if wait_key in target_index:
            target = targets[target_index[wait_key]]
            action_ids = target.setdefault("action_ids", [target["action_id"]])
            if action_id and action_id not in action_ids:
                action_ids.append(action_id)
            continue
        target_index[wait_key] = len(targets)
        target = {
                "action_id": action_id,
                "action_ids": [action_id] if action_id else [],
                "repository_id": route.get("repository_id"),
                "mission_id": route.get("mission_id"),
                "attempt_id": route.get("attempt_id"),
                "recipient_thread_id": recipient,
                "after_cursor": route.get("after_cursor"),
                "delivery_token": route.get("delivery_token"),
                "status": route.get("status", "waiting"),
                "observer_kind": observer_kind,
                "legacy_inferred_route": bool(
                    route.get("legacy_inferred_route", False)
                ),
            }
        if observer_kind == "codex_wait":
            target["host_id"] = host_id
        targets.append(target)

    concurrency_limit = int(
        scheduler_state.get(
            "concurrency_limit", DEFAULT_COORDINATOR_CONCURRENCY_LIMIT
        )
    )
    external_claim_reservation = int(
        isinstance(scheduler_claim, dict)
        and scheduler_claim.get("action", {}).get("requires_external_result") is True
    )
    capacity_remaining = max(
        0,
        concurrency_limit - len(active_routes) - external_claim_reservation,
    )
    active_execution_repositories = {
        str(item.get("repository_id") or "")
        for item in active_routes
        if str(item.get("route_class") or "execution") == "execution"
        and item.get("repository_id")
    }

    def action_is_claimable(action: dict[str, Any]) -> bool:
        if action.get("requires_external_result") is not True:
            return True
        if capacity_remaining <= 0:
            return False
        return not (
            _action_route_class(action) == "execution"
            and _action_repository_id(action) in active_execution_repositories
        )

    claimable_actions = [item for item in ready_actions if action_is_claimable(item)]
    next_action = (
        copy.deepcopy(scheduler_claim.get("action"))
        if isinstance(scheduler_claim, dict)
        else (
            copy.deepcopy(claimable_actions[0])
            if claimable_actions
            else None
        )
    )
    required_handoff_actions = [
        copy.deepcopy(item)
        for item in ready_actions
        if item.get("kind") in PROTOCOL_HANDOFF_ACTION_KINDS
    ]
    if (
        isinstance(scheduler_claim, dict)
        and scheduler_claim.get("action", {}).get("kind")
        in PROTOCOL_HANDOFF_ACTION_KINDS
    ):
        required_handoff_actions.insert(
            0, copy.deepcopy(scheduler_claim["action"])
        )
    has_scheduler_claim = isinstance(scheduler_claim, dict)
    has_inflight_work = bool(wait_targets or poll_targets)
    route_cursor_complete = all(
        bool(str(item.get("after_cursor") or "")) for item in route_leases
    )
    checkpoint_after_wait_allowed = bool(
        route_leases
        and not has_scheduler_claim
        and not claimable_actions
        and route_cursor_complete
    )
    wake_required = bool(
        has_scheduler_claim or ready_actions or has_inflight_work
    )
    scheduler_claim_requires_recovery = bool(
        has_scheduler_claim
        and (
            scheduler_claim.get("action", {}).get("requires_external_result") is True
            or (
                scheduler_claim.get("status") == "effect_prepared"
                and scheduler_claim.get("action", {}).get("kind")
                in AUTHORIZED_RUNTIME_LOCAL_ACTION_KINDS
            )
        )
    )
    runtime_ledger_requires_recovery = any(
        isinstance(item, dict)
        and item.get("phase")
        in {
            "EFFECT_INTENT",
            "EFFECT_PREPARED",
            "REPAIR_PREPARED",
            "ROLLBACK_REQUIRED",
            "RESULT_READY",
        }
        for item in coordinator_state.get("authorized_runtime_actions", [])
    )
    runtime_reconciliation_required = any(
        isinstance(item, dict)
        and _runtime_unapplied_completion(item, scheduler_state) is not None
        for item in coordinator_state.get("authorized_runtime_actions", [])
    )
    watchdog_should_be_armed = bool(
        scheduler_claim_requires_recovery
        or has_inflight_work
        or runtime_ledger_requires_recovery
        or runtime_reconciliation_required
    )

    if has_scheduler_claim or has_inflight_work:
        global_state = "RUNNING"
    elif isinstance(next_action, dict):
        global_state = "READY"
    elif base.get("next_user_card"):
        global_state = "AWAITING_USER_ONLY"
    else:
        global_state = base["global_state"]

    result = copy.deepcopy(base)
    result.update(
        {
            "classification": "COORDINATOR_DETERMINISTIC_PLAN",
            "global_state": global_state,
            "state_fingerprint": state_fingerprint,
            "has_ready_action": bool(ready_actions),
            "has_claimable_action": bool(claimable_actions)
            and not has_scheduler_claim,
            "has_inflight_work": has_inflight_work,
            "exact_outbound_waits": copy.deepcopy(active_routes),
            "scheduler_claim": scheduler_claim,
            "active_routes": copy.deepcopy(active_routes),
            "wait_targets": wait_targets,
            "poll_targets": poll_targets,
            "concurrency_limit": concurrency_limit,
            "capacity_remaining": capacity_remaining,
            "round_robin_cursor_repository_id": round_robin_cursor,
            "ready_actions": ready_actions,
            "required_handoff_actions": required_handoff_actions,
            "protocol_handoff_required": bool(required_handoff_actions),
            "claimable_action_count": len(claimable_actions),
            # Deprecated compatibility projection. New executors must use
            # scheduler_claim and active_routes independently.
            "active_claim": _legacy_active_claim_view(scheduler_state),
            "next_action": next_action,
            "ready_action_count": len(ready_actions),
            "cycle_should_continue_now": wake_required,
            "cycle_checkpoint_allowed": not wake_required,
            # This is structural foreground-handoff eligibility. The executor
            # must separately have completed the bounded exact-route wait; this
            # value is not a wait receipt and does not broaden ordinary cycle
            # checkpoint permission.
            "checkpoint_after_wait_allowed": checkpoint_after_wait_allowed,
            "route_cursor_complete": route_cursor_complete,
            "checkpoint_blockers": [
                reason
                for reason, present in (
                    ("scheduler_claim", has_scheduler_claim),
                    ("claimable_ready_action", bool(claimable_actions)),
                    ("required_protocol_handoff", bool(required_handoff_actions)),
                    ("missing_route_cursor", bool(route_leases) and not route_cursor_complete),
                )
                if present
            ],
            "execution_can_continue": wake_required,
            "wake_required": wake_required,
            "wake_reason": (
                "scheduler_claim"
                if has_scheduler_claim
                else (
                    str(next_action.get("kind"))
                    if isinstance(next_action, dict)
                    else (
                        "active_routes"
                        if has_inflight_work
                        else "ready_but_capacity_or_repository_limited"
                        if ready_actions
                        else "unchanged_idle"
                    )
                )
            ),
            "watchdog_should_be_armed": watchdog_should_be_armed,
            "watchdog_should_be_paused": not watchdog_should_be_armed,
            "watchdog_idle_policy": "paused",
            "cycle_should_rearm": watchdog_should_be_armed,
            "coordinator_availability": "AVAILABLE",
            "execution_state": (
                "DRAINING"
                if has_scheduler_claim or has_inflight_work
                else "READY"
                if ready_actions
                else (
                    "WAITING_USER"
                    if base.get("next_user_card")
                    else (
                        "WAITING_EXTERNAL"
                        if global_state == "BLOCKED"
                        else "IDLE"
                    )
                )
            ),
            "completed_action_count": len(completed_ids),
            "pending_user_event_count": len(pending_events),
            "scheduler_revision": int(scheduler_state.get("revision", 0)),
        }
    )
    return result


def _route_lease_index(
    scheduler_state: dict[str, Any], action_id: str
) -> int | None:
    for index, item in enumerate(scheduler_state.get("route_leases", [])):
        if isinstance(item, dict) and item.get("action_id") == action_id:
            return index
    return None


def _runtime_action_from_scheduler_record(
    active: dict[str, Any], coordinator_state: dict[str, Any]
) -> dict[str, Any]:
    action = active.get("action", {})
    runtime_payload = action.get("payload", {}).get("runtime_action", {})
    runtime_action_id = str(runtime_payload.get("runtime_action_id") or "")
    record = _authorized_runtime_record(coordinator_state, runtime_action_id)
    validate_authorized_runtime_action(record)
    if runtime_payload.get("identity_sha256") != record["identity_sha256"]:
        raise ProtocolError("claimed authorized runtime identity mismatch")
    if "execution_surface" in runtime_payload:
        raise ProtocolError("claimed authorized runtime uses ambiguous execution surface")
    if (
        runtime_payload.get("repair_execution_surface")
        != record["repair_execution_surface"]
    ):
        raise ProtocolError("claimed authorized runtime repair surface mismatch")
    if (
        runtime_payload.get("probe_execution_surface")
        != record["probe_execution_surface"]
    ):
        raise ProtocolError("claimed authorized runtime probe surface mismatch")
    return record


def _authorized_runtime_receipt_document(
    record: dict[str, Any], phase: str
) -> dict[str, Any]:
    def preserved_file_observation(path_value: str) -> dict[str, Any]:
        path = Path(path_value)
        observation: dict[str, Any] = {
            "path": path_value,
            "exists": path.exists(),
            "is_file": path.is_file(),
            "size": None,
            "sha256": None,
            "content_class": "absent" if not path.exists() else "non_file",
            "expected_identity_match": False,
        }
        if not path.is_file():
            return observation
        content = path.read_bytes()
        actual_sha256 = sha256_bytes(content)
        content_class = (
            "empty_file"
            if not content
            else "all_nul_bytes"
            if all(byte == 0 for byte in content)
            else "non_nul_bytes"
        )
        observation.update(
            {
                "size": len(content),
                "sha256": actual_sha256,
                "content_class": content_class,
                "expected_identity_match": bool(
                    len(content) == record["target_pre_size"]
                    and actual_sha256 == record["target_pre_sha256"]
                    and content_class == "all_nul_bytes"
                ),
            }
        )
        return observation

    result = {
        "schema_version": 1,
        "runtime_action_id": record["runtime_action_id"],
        "identity_sha256": record["identity_sha256"],
        "handler_id": record["handler_id"],
        "authorization_id": record["authorization_id"],
        "authorization_action_id": record["authorization_action_id"],
        "authorization_payload_sha256": record[
            "authorization_payload_sha256"
        ],
        "repair_execution_surface": record["repair_execution_surface"],
        "probe_execution_surface": record["probe_execution_surface"],
        "decision_evidence": {
            "event_id": record["decision_event_id"],
            "path": record["decision_evidence_path"],
            "sha256": record["decision_evidence_sha256"],
            "supervisor_text_path": record["supervisor_text_path"],
            "supervisor_text_sha256": record["supervisor_text_sha256"],
        },
        "phase": phase,
        "authority_consumed": record["authority_consumed"],
        "original_target": {
            "path": record["target_path"],
            "size": record["target_pre_size"],
            "sha256": record["target_pre_sha256"],
            "content_class": "all_nul_bytes",
        },
        "backup": preserved_file_observation(record["backup_path"]),
        "quarantine": preserved_file_observation(record["quarantine_path"]),
        "regenerated_state": {
            "path": record["target_path"],
            "size": None,
            "sha256": None,
            "json_parse_result": "pending",
        },
        "probe_a": {"status": "pending"},
        "postcheck": {"status": "pending"},
        "probe_b": {"status": "pending"},
        "runtime_doctor": {
            "status": "pending",
            "expected": _fixed_runtime_probe_contract(record)[
                "runtime_doctor"
            ]["expected"],
        },
        "rollback_performed": False,
        "mutation_counts": {
            "acl": 0,
            "ownership": 0,
            "os_policy": 0,
            "execution_policy": 0,
        },
        "final_blocker_classification": "PENDING",
        "product_resume_readiness": "NOT_YET_SATISFIED",
        "updated_at": utc_now(),
    }
    if isinstance(record.get("probe_result"), dict):
        result["probe_result"] = copy.deepcopy(record["probe_result"])
        probe_outcome = record["probe_result"].get("outcome")
        if probe_outcome == "probe_a_failed":
            result["probe_a"] = {"status": "failed"}
            result["final_blocker_classification"] = (
                "CODEX_SANDBOX_STATE_REPAIR_FAILED"
            )
        elif probe_outcome == "probe_b_failed":
            result["probe_a"] = {"status": "passed"}
            result["probe_b"] = {"status": "failed"}
            result["final_blocker_classification"] = (
                "CWD_OR_PATH_SCOPED_SANDBOX_FAILURE"
            )
        elif probe_outcome == "runtime_doctor_failed":
            result["probe_a"] = {"status": "passed"}
            result["probe_b"] = {"status": "passed"}
            result["runtime_doctor"]["status"] = "failed"
            result["final_blocker_classification"] = (
                "SANDBOX_RECOVERED_RUNTIME_READINESS_FAILED"
            )
        elif probe_outcome == "probe_passed":
            result["probe_a"] = {"status": "passed"}
            result["probe_b"] = {"status": "passed"}
            result["runtime_doctor"]["status"] = "passed"
            result["final_blocker_classification"] = (
                "RUNTIME_RECOVERY_PASSED_AWAITING_SUPERVISOR"
            )
    # A Worker-owned probe receipt is evidence, not a template.  Later local
    # rollback transitions may annotate it, but must retain every detailed
    # command, cwd, sandbox, exit/error, postcheck, and regenerated-state field.
    receipt_path = Path(record["recovery_receipt_path"])
    if isinstance(record.get("probe_result"), dict) and receipt_path.is_file():
        try:
            existing = load_json(receipt_path)
        except (OSError, json.JSONDecodeError, ProtocolError):
            existing = None
        if (
            isinstance(existing, dict)
            and existing.get("runtime_action_id") == record["runtime_action_id"]
            and existing.get("identity_sha256") == record["identity_sha256"]
            and existing.get("probe_action_id")
            == record["probe_result"].get("action_id")
        ):
            coordinator_owned = {
                "phase",
                "authority_consumed",
                "rollback_performed",
                "final_blocker_classification",
                "product_resume_readiness",
                "recovery_result",
                "supervisor_result",
                "updated_at",
            }
            for field, value in existing.items():
                if field not in coordinator_owned:
                    result[field] = copy.deepcopy(value)
    if isinstance(record.get("recovery_result"), dict):
        result["recovery_result"] = copy.deepcopy(record["recovery_result"])
        result["rollback_performed"] = bool(
            record["recovery_result"].get("rolled_back", False)
        )
        result["final_blocker_classification"] = str(
            record["recovery_result"].get("classification")
            or result["final_blocker_classification"]
        )
    return result


def _write_authorized_runtime_receipt(
    record: dict[str, Any], phase: str
) -> dict[str, Any]:
    receipt_path = Path(record["recovery_receipt_path"])
    document = _authorized_runtime_receipt_document(record, phase)
    atomic_write_json(receipt_path, document)
    effect_receipt = {
        "path": str(receipt_path),
        "identity_sha256": record["identity_sha256"],
        "phase": phase,
        "authority_consumed": record["authority_consumed"],
        "target_pre_sha256": record["target_pre_sha256"],
        "updated_at": document["updated_at"],
    }
    target = Path(record["target_path"])
    if target.is_file():
        effect_receipt["target_post_sha256"] = sha256_file(target)
    record["effect_receipt"] = effect_receipt
    return effect_receipt


def _planned_authorized_runtime_effect_receipt(
    record: dict[str, Any]
) -> dict[str, Any]:
    existing = record.get("effect_receipt")
    if isinstance(existing, dict):
        return copy.deepcopy(existing)
    return {
        "path": record["recovery_receipt_path"],
        "identity_sha256": record["identity_sha256"],
        "phase": "EFFECT_INTENT",
        "authority_consumed": False,
        "target_pre_sha256": record["target_pre_sha256"],
        "updated_at": utc_now(),
    }


def _prepare_authorized_runtime_effect_claim(
    scheduler_state: dict[str, Any],
    action_id: str,
    effect_receipt: dict[str, Any],
) -> None:
    active = scheduler_state.get("scheduler_claim")
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        raise ProtocolError("exact authorized runtime scheduler claim is required")
    if active.get("action", {}).get("kind") not in AUTHORIZED_RUNTIME_LOCAL_ACTION_KINDS:
        raise ProtocolError("claimed action is not an authorized runtime local effect")
    status = str(active.get("status") or "claimed")
    if status == "effect_prepared":
        existing_receipt = active.get("effect_receipt")
        if not isinstance(existing_receipt, dict) or any(
            existing_receipt.get(field) != effect_receipt.get(field)
            for field in ("path", "identity_sha256")
        ):
            raise ProtocolError("authorized runtime effect receipt replay mismatch")
        active["effect_receipt"] = copy.deepcopy(effect_receipt)
        return
    if status != "claimed":
        raise ProtocolError("authorized runtime local effect claim cannot be prepared")
    active["status"] = "effect_prepared"
    active["effect_receipt"] = copy.deepcopy(effect_receipt)
    active["effect_prepared_at"] = utc_now()
    scheduler_state["revision"] = int(scheduler_state.get("revision", 0)) + 1
    _sync_legacy_active_claim_view(scheduler_state)


def _runtime_target_matches_precondition(record: dict[str, Any]) -> bool:
    target = Path(record["target_path"])
    return bool(
        target.is_file()
        and target.stat().st_size == record["target_pre_size"]
        and sha256_file(target) == record["target_pre_sha256"]
        and all(byte == 0 for byte in target.read_bytes())
    )


def _runtime_completion(
    scheduler_state: dict[str, Any], action_id: str
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in scheduler_state.get("completed_actions", [])
            if isinstance(item, dict) and item.get("action_id") == action_id
        ),
        None,
    )


def execute_authorized_runtime_action(
    coordinator_state: dict[str, Any],
    scheduler_state: dict[str, Any],
    action_id: str,
    *,
    dry_run: bool = False,
    persist_state: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Prepare the fixed deny-read state repair without running any probe."""
    _ensure_scheduler_state_v2(scheduler_state)
    active = scheduler_state.get("scheduler_claim")
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        completed = _runtime_completion(scheduler_state, action_id)
        if isinstance(completed, dict):
            completed_action = completed.get("action", {})
            runtime_payload = completed_action.get("payload", {}).get(
                "runtime_action", {}
            )
            runtime_action_id = str(
                runtime_payload.get("runtime_action_id") or ""
            )
            if runtime_action_id:
                record = _authorized_runtime_record(
                    coordinator_state, runtime_action_id
                )
                if completed.get("outcome") == "repair_prepared" and record[
                    "phase"
                ] in {"AUTHORIZED", "EFFECT_INTENT", "EFFECT_PREPARED"}:
                    backup = Path(record["backup_path"])
                    quarantine = Path(record["quarantine_path"])
                    if not all(
                        path.is_file()
                        and sha256_file(path) == record["target_pre_sha256"]
                        for path in (backup, quarantine)
                    ):
                        raise ProtocolError(
                            "completed runtime repair lacks preserved recovery state"
                        )
                    record["authority_consumed"] = True
                    record["phase"] = "REPAIR_PREPARED"
                    _write_authorized_runtime_receipt(
                        record, "REPAIR_PREPARED"
                    )
                elif completed.get("outcome") == "precondition_mismatch" and record[
                    "phase"
                ] == "AUTHORIZED":
                    record["phase"] = "RESULT_READY"
                    record["recovery_result"] = {
                        "classification": "AUTHORIZED_RUNTIME_PRECONDITION_MISMATCH",
                        "authority_consumed": False,
                        "target_mutated": False,
                    }
                    _write_authorized_runtime_receipt(
                        record, "PRECONDITION_MISMATCH"
                    )
                if persist_state is not None:
                    persist_state()
            return {
                "classification": "AUTHORIZED_RUNTIME_REPAIR_ALREADY_EXECUTED",
                "action_id": action_id,
                "deduplicated": True,
            }
        raise ProtocolError("exact authorized runtime scheduler claim is required")
    if active.get("action", {}).get("kind") != "execute_authorized_runtime_repair":
        raise ProtocolError("claimed action is not the authorized runtime repair")
    record = _runtime_action_from_scheduler_record(active, coordinator_state)
    if record["phase"] in {
        "REPAIR_PREPARED",
        "ROLLBACK_REQUIRED",
        "RESULT_READY",
        "COMPLETE",
    }:
        outcome = (
            "precondition_mismatch"
            if record.get("recovery_result", {}).get("classification")
            == "AUTHORIZED_RUNTIME_PRECONDITION_MISMATCH"
            else "repair_prepared"
        )
        complete_coordinator_action(
            scheduler_state,
            action_id,
            outcome,
            evidence=record["recovery_receipt_path"],
            coordinator_state=coordinator_state,
        )
        if persist_state is not None:
            persist_state()
        return {
            "classification": "AUTHORIZED_RUNTIME_REPAIR_COMPLETION_RECONCILED",
            "action_id": action_id,
            "runtime_action_id": record["runtime_action_id"],
            "deduplicated": True,
        }
    if record["phase"] not in {
        "AUTHORIZED",
        "EFFECT_INTENT",
        "EFFECT_PREPARED",
    }:
        raise ProtocolError("authorized runtime repair is not executable in this phase")
    target = Path(record["target_path"])
    backup = Path(record["backup_path"])
    quarantine = Path(record["quarantine_path"])
    receipt_path = Path(record["recovery_receipt_path"])
    target_matches = _runtime_target_matches_precondition(record)
    preserved_pair = bool(
        quarantine.is_file()
        and sha256_file(quarantine) == record["target_pre_sha256"]
        and backup.is_file()
        and sha256_file(backup) == record["target_pre_sha256"]
    )
    resumable_quarantine = bool(preserved_pair and not target.exists())
    stale_preserved_pair = bool(quarantine.exists() and target.exists())
    if dry_run:
        return {
            "classification": "AUTHORIZED_RUNTIME_REPAIR_DRY_RUN",
            "action_id": action_id,
            "runtime_action_id": record["runtime_action_id"],
            "target_precondition_matches": target_matches,
            "crash_resume_available": resumable_quarantine,
            "authority_consumed": record["authority_consumed"],
            "would_mutate": False,
        }

    if stale_preserved_pair or (
        not target_matches and not resumable_quarantine
    ):
        record["phase"] = "RESULT_READY"
        record["recovery_result"] = {
            "classification": "AUTHORIZED_RUNTIME_PRECONDITION_MISMATCH",
            "authority_consumed": False,
            "target_mutated": False,
            "expected_sha256": record["target_pre_sha256"],
            "observed_sha256": sha256_file(target) if target.is_file() else None,
            "reason": (
                "stale_preserved_pair_with_active_target"
                if stale_preserved_pair
                else "target_identity_mismatch"
            ),
        }
        effect_receipt = _write_authorized_runtime_receipt(
            record, "PRECONDITION_MISMATCH"
        )
        complete_coordinator_action(
            scheduler_state,
            action_id,
            "precondition_mismatch",
            evidence=str(receipt_path),
            coordinator_state=coordinator_state,
        )
        if persist_state is not None:
            persist_state()
        return {
            "classification": "AUTHORIZED_RUNTIME_PRECONDITION_MISMATCH",
            "action_id": action_id,
            "runtime_action_id": record["runtime_action_id"],
            "effect_receipt": effect_receipt,
            "authority_consumed": False,
            "target_mutated": False,
            "deduplicated": False,
        }

    planned_receipt = _planned_authorized_runtime_effect_receipt(record)
    record["effect_receipt"] = copy.deepcopy(planned_receipt)
    record["phase"] = "EFFECT_INTENT"
    _prepare_authorized_runtime_effect_claim(
        scheduler_state, action_id, planned_receipt
    )
    if persist_state is not None:
        persist_state()

    if resumable_quarantine:
        record["authority_consumed"] = True

    original_bytes: bytes
    if backup.is_file():
        if backup.stat().st_size != record["target_pre_size"] or sha256_file(
            backup
        ) != record["target_pre_sha256"]:
            raise ProtocolError("authorized runtime backup identity mismatch")
        original_bytes = backup.read_bytes()
    else:
        if not target_matches:
            raise ProtocolError("authorized runtime target changed before backup")
        original_bytes = target.read_bytes()
        atomic_write_bytes(backup, original_bytes)
        record["authority_consumed"] = True

    # The durable intent predates every filesystem write.  Once the first
    # byte-exact backup exists, authority is consumed and that transition is
    # persisted before the target can be quarantined.
    record["authority_consumed"] = True
    record["phase"] = "EFFECT_PREPARED"
    prepared_receipt = _write_authorized_runtime_receipt(
        record, "EFFECT_PREPARED"
    )
    _prepare_authorized_runtime_effect_claim(
        scheduler_state, action_id, prepared_receipt
    )
    if persist_state is not None:
        persist_state()

    if quarantine.is_file():
        if quarantine.stat().st_size != record["target_pre_size"] or sha256_file(
            quarantine
        ) != record["target_pre_sha256"]:
            raise ProtocolError("authorized runtime quarantine identity mismatch")
    else:
        if not _runtime_target_matches_precondition(record):
            raise ProtocolError("authorized runtime target changed before quarantine")
        os.replace(target, quarantine)
        record["authority_consumed"] = True
    if sha256_file(backup) != record["target_pre_sha256"] or sha256_file(
        quarantine
    ) != record["target_pre_sha256"]:
        raise ProtocolError("authorized runtime preserved copy verification failed")
    record["phase"] = "REPAIR_PREPARED"
    effect_receipt = _write_authorized_runtime_receipt(
        record, "REPAIR_PREPARED"
    )
    _prepare_authorized_runtime_effect_claim(
        scheduler_state, action_id, effect_receipt
    )
    complete_coordinator_action(
        scheduler_state,
        action_id,
        "repair_prepared",
        evidence=str(receipt_path),
        coordinator_state=coordinator_state,
    )
    if persist_state is not None:
        persist_state()
    return {
        "classification": "AUTHORIZED_RUNTIME_REPAIR_PREPARED",
        "action_id": action_id,
        "runtime_action_id": record["runtime_action_id"],
        "backup_path": str(backup),
        "quarantine_path": str(quarantine),
        "effect_receipt": effect_receipt,
        "authority_consumed": True,
        "deduplicated": False,
    }


def rollback_authorized_runtime_action(
    coordinator_state: dict[str, Any],
    scheduler_state: dict[str, Any],
    action_id: str,
    *,
    dry_run: bool = False,
    persist_state: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Restore the byte-exact original while preserving backup/quarantine."""
    _ensure_scheduler_state_v2(scheduler_state)
    active = scheduler_state.get("scheduler_claim")
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        completed = _runtime_completion(scheduler_state, action_id)
        if isinstance(completed, dict):
            completed_action = completed.get("action", {})
            runtime_payload = completed_action.get("payload", {}).get(
                "runtime_action", {}
            )
            runtime_action_id = str(
                runtime_payload.get("runtime_action_id") or ""
            )
            if runtime_action_id:
                record = _authorized_runtime_record(
                    coordinator_state, runtime_action_id
                )
                if record["phase"] == "ROLLBACK_REQUIRED":
                    target = Path(record["target_path"])
                    if not _runtime_target_matches_precondition(record):
                        raise ProtocolError(
                            "completed runtime rollback lacks restored target"
                        )
                    record["phase"] = "RESULT_READY"
                    record["recovery_result"] = {
                        "classification": "CODEX_SANDBOX_STATE_REPAIR_FAILED",
                        "authority_consumed": True,
                        "rolled_back": True,
                        "target_sha256": sha256_file(target),
                        "probe_result": copy.deepcopy(
                            record.get("probe_result")
                        ),
                    }
                    _write_authorized_runtime_receipt(
                        record, "ROLLBACK_COMPLETE"
                    )
                    if persist_state is not None:
                        persist_state()
            return {
                "classification": "AUTHORIZED_RUNTIME_ROLLBACK_ALREADY_COMPLETED",
                "action_id": action_id,
                "deduplicated": True,
            }
        raise ProtocolError("exact authorized runtime rollback claim is required")
    if active.get("action", {}).get("kind") != "rollback_authorized_runtime_repair":
        raise ProtocolError("claimed action is not the authorized runtime rollback")
    record = _runtime_action_from_scheduler_record(active, coordinator_state)
    if record["phase"] in {"RESULT_READY", "COMPLETE"} and record.get(
        "recovery_result", {}
    ).get("rolled_back") is True:
        complete_coordinator_action(
            scheduler_state,
            action_id,
            "rolled_back",
            evidence=record["recovery_receipt_path"],
            coordinator_state=coordinator_state,
        )
        if persist_state is not None:
            persist_state()
        return {
            "classification": "AUTHORIZED_RUNTIME_ROLLBACK_COMPLETION_RECONCILED",
            "action_id": action_id,
            "runtime_action_id": record["runtime_action_id"],
            "deduplicated": True,
        }
    if record["phase"] != "ROLLBACK_REQUIRED":
        raise ProtocolError("authorized runtime rollback is not required")
    target = Path(record["target_path"])
    backup = Path(record["backup_path"])
    quarantine = Path(record["quarantine_path"])
    preserved = all(
        path.is_file()
        and path.stat().st_size == record["target_pre_size"]
        and sha256_file(path) == record["target_pre_sha256"]
        for path in (backup, quarantine)
    )
    if dry_run:
        return {
            "classification": "AUTHORIZED_RUNTIME_ROLLBACK_DRY_RUN",
            "action_id": action_id,
            "preserved_original_verified": preserved,
            "would_mutate": False,
        }
    if not preserved:
        raise ProtocolError("authorized runtime rollback source identity mismatch")
    effect_receipt = _write_authorized_runtime_receipt(
        record, "ROLLBACK_EFFECT_PREPARED"
    )
    _prepare_authorized_runtime_effect_claim(
        scheduler_state, action_id, effect_receipt
    )
    if persist_state is not None:
        persist_state()
    atomic_write_bytes(target, backup.read_bytes())
    if target.stat().st_size != record["target_pre_size"] or sha256_file(
        target
    ) != record["target_pre_sha256"]:
        raise ProtocolError("authorized runtime rollback verification failed")
    record["phase"] = "RESULT_READY"
    record["recovery_result"] = {
        "classification": "CODEX_SANDBOX_STATE_REPAIR_FAILED",
        "authority_consumed": True,
        "rolled_back": True,
        "target_sha256": sha256_file(target),
        "probe_result": copy.deepcopy(record.get("probe_result")),
    }
    effect_receipt = _write_authorized_runtime_receipt(
        record, "ROLLBACK_COMPLETE"
    )
    scheduler_state["scheduler_claim"]["effect_receipt"] = copy.deepcopy(
        effect_receipt
    )
    complete_coordinator_action(
        scheduler_state,
        action_id,
        "rolled_back",
        evidence=record["recovery_receipt_path"],
        coordinator_state=coordinator_state,
    )
    if persist_state is not None:
        persist_state()
    return {
        "classification": "AUTHORIZED_RUNTIME_ROLLBACK_COMPLETED",
        "action_id": action_id,
        "runtime_action_id": record["runtime_action_id"],
        "target_sha256": record["target_pre_sha256"],
        "backup_preserved": True,
        "quarantine_preserved": True,
        "effect_receipt": effect_receipt,
        "deduplicated": False,
    }


def reconcile_authorized_runtime_completion(
    coordinator_state: dict[str, Any],
    scheduler_state: dict[str, Any],
    action_id: str,
    *,
    dry_run: bool = False,
    persist_state: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Apply one scheduler-completed runtime transition missing from the ledger."""
    _ensure_scheduler_state_v2(scheduler_state)
    active = scheduler_state.get("scheduler_claim")
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        completed = _runtime_completion(scheduler_state, action_id)
        if isinstance(completed, dict) and completed.get("outcome") == "reconciled":
            return {
                "classification": "AUTHORIZED_RUNTIME_COMPLETION_ALREADY_RECONCILED",
                "action_id": action_id,
                "deduplicated": True,
            }
        raise ProtocolError("exact authorized runtime reconciliation claim is required")
    action = active.get("action", {})
    if action.get("kind") != "reconcile_authorized_runtime_completion":
        raise ProtocolError("claimed action is not runtime completion reconciliation")
    runtime_payload = action.get("payload", {}).get("runtime_action", {})
    record = _authorized_runtime_record(
        coordinator_state, str(runtime_payload.get("runtime_action_id") or "")
    )
    validate_authorized_runtime_action(record)
    if runtime_payload.get("identity_sha256") != record["identity_sha256"]:
        raise ProtocolError("runtime reconciliation identity mismatch")
    completion_ref = action.get("payload", {}).get("completed_action", {})
    completed_action_id = str(completion_ref.get("action_id") or "")
    completion = _runtime_completion(scheduler_state, completed_action_id)
    if (
        not isinstance(completion, dict)
        or completion_ref.get("sha256") != canonical_json_hash(completion)
        or completion_ref.get("outcome") != completion.get("outcome")
        or str(completion_ref.get("evidence") or "")
        != str(completion.get("evidence") or "")
    ):
        raise ProtocolError("runtime reconciliation completion identity mismatch")
    if dry_run:
        return {
            "classification": "AUTHORIZED_RUNTIME_COMPLETION_RECONCILE_DRY_RUN",
            "action_id": action_id,
            "completed_action_id": completed_action_id,
            "would_mutate": False,
        }
    _apply_authorized_runtime_completion(
        coordinator_state,
        {"action": copy.deepcopy(completion.get("action"))},
        completed_action_id,
        str(completion.get("outcome") or ""),
        completion.get("evidence"),
    )
    if not _runtime_completion_applied(record, completion):
        raise ProtocolError("runtime completion reconciliation did not converge")
    complete_coordinator_action(
        scheduler_state,
        action_id,
        "reconciled",
        evidence=str(completion.get("evidence") or ""),
    )
    if persist_state is not None:
        persist_state()
    return {
        "classification": "AUTHORIZED_RUNTIME_COMPLETION_RECONCILED",
        "action_id": action_id,
        "completed_action_id": completed_action_id,
        "runtime_action_id": record["runtime_action_id"],
        "phase": record["phase"],
        "deduplicated": False,
    }


def claim_coordinator_action(
    scheduler_state: dict[str, Any],
    plan: dict[str, Any],
    action_id: str,
    *,
    owner_task_id: str | None = None,
) -> dict[str, Any]:
    _ensure_scheduler_state_v2(scheduler_state)
    active = scheduler_state.get("scheduler_claim")
    if isinstance(active, dict):
        if active.get("action_id") == action_id:
            return {
                "classification": "COORDINATOR_ACTION_ALREADY_CLAIMED",
                "action_id": action_id,
                "deduplicated": True,
            }
        raise ProtocolError("another short-lived Coordinator scheduler claim is active")
    if _route_lease_index(scheduler_state, action_id) is not None:
        return {
            "classification": "COORDINATOR_ACTION_ALREADY_WAITING",
            "action_id": action_id,
            "deduplicated": True,
        }
    action = plan.get("next_action")
    if not isinstance(action, dict) or action.get("action_id") != action_id:
        raise ProtocolError("scheduler action is stale, capacity-limited, or not next")
    if int(plan.get("scheduler_revision", -1)) != int(
        scheduler_state.get("revision", 0)
    ):
        raise ProtocolError("scheduler plan revision is stale")
    if action.get("requires_external_result") is True:
        if int(plan.get("capacity_remaining", 0)) <= 0:
            raise ProtocolError("Coordinator external route capacity is exhausted")
        repository_id = _action_repository_id(action)
        if _action_route_class(action) == "execution" and any(
            str(item.get("route_class") or "execution") == "execution"
            and str(item.get("repository_id") or "") == repository_id
            for item in scheduler_state.get("route_leases", [])
            if isinstance(item, dict)
        ):
            raise ProtocolError(
                "repository already has an external execution route lease"
            )
    scheduler_state["scheduler_claim"] = _record_with_route_metadata(
        {
            "action_id": action_id,
            "action": copy.deepcopy(action),
            "status": "claimed",
            "state_fingerprint": plan.get("state_fingerprint"),
            "owner_task_id": owner_task_id,
            "claimed_at": utc_now(),
        }
    )
    scheduler_state["revision"] = int(scheduler_state.get("revision", 0)) + 1
    _sync_legacy_active_claim_view(scheduler_state)
    return {
        "classification": "COORDINATOR_ACTION_CLAIMED",
        "action_id": action_id,
        "action": copy.deepcopy(action),
        "deduplicated": False,
    }


def prepare_coordinator_action_delivery(
    scheduler_state: dict[str, Any],
    action_id: str,
    recipient_thread_id: str,
    packet_sha256: str,
) -> dict[str, Any]:
    """Persist an idempotent delivery envelope before any external send."""
    _ensure_scheduler_state_v2(scheduler_state)
    lease_index = _route_lease_index(scheduler_state, action_id)
    active = (
        scheduler_state["route_leases"][lease_index]
        if lease_index is not None
        else scheduler_state.get("scheduler_claim")
    )
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        raise ProtocolError("exact Coordinator scheduler claim is required")
    if active.get("action", {}).get("requires_external_result") is not True:
        raise ProtocolError("local Coordinator action cannot prepare delivery")
    route = active.get("action", {}).get("payload", {}).get("route", {})
    expected = str(route.get("recipient_thread_id") or "")
    if not expected:
        raise ProtocolError("external action lacks an exact recipient route")
    if expected != recipient_thread_id:
        raise ProtocolError("outbound recipient does not match claimed exact route")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(packet_sha256 or "")):
        raise ProtocolError(
            "external delivery requires packet_sha256 as a 64-character hex digest"
        )
    normalized_hash = packet_sha256.lower()
    delivery_token = canonical_json_hash(
        {
            "action_id": action_id,
            "recipient_thread_id": recipient_thread_id,
            "packet_sha256": normalized_hash,
        }
    )
    status = str(active.get("status") or "claimed")
    if status in {"prepared", "sent", "waiting"}:
        if (
            active.get("recipient_thread_id") != recipient_thread_id
            or active.get("packet_sha256") != normalized_hash
            or active.get("delivery_token") != delivery_token
        ):
            raise ProtocolError("prepared delivery envelope identity mismatch")
        return {
            "classification": "COORDINATOR_DELIVERY_ALREADY_PREPARED",
            "action_id": action_id,
            "recipient_thread_id": recipient_thread_id,
            "packet_sha256": normalized_hash,
            "delivery_token": delivery_token,
            "deduplicated": True,
        }
    if status != "claimed" or lease_index is not None:
        raise ProtocolError("delivery can only be prepared from an unsent claim")
    active.update(
        {
            "status": "prepared",
            "recipient_thread_id": recipient_thread_id,
            "packet_sha256": normalized_hash,
            "delivery_token": delivery_token,
            "prepared_at": utc_now(),
        }
    )
    scheduler_state["revision"] = int(scheduler_state.get("revision", 0)) + 1
    _sync_legacy_active_claim_view(scheduler_state)
    return {
        "classification": "COORDINATOR_DELIVERY_PREPARED",
        "action_id": action_id,
        "recipient_thread_id": recipient_thread_id,
        "packet_sha256": normalized_hash,
        "delivery_token": delivery_token,
        "envelope": {
            "protocol": "supervise-repo-loop/delivery-v1",
            "action_id": action_id,
            "delivery_token": delivery_token,
            "payload_sha256": normalized_hash,
        },
        "deduplicated": False,
    }


def mark_coordinator_action_sent(
    scheduler_state: dict[str, Any],
    action_id: str,
    recipient_thread_id: str,
    *,
    packet_sha256: str | None = None,
    after_cursor: str | None = None,
) -> dict[str, Any]:
    """Move a sent claim to a route lease, releasing the global scheduler."""
    _ensure_scheduler_state_v2(scheduler_state)
    lease_index = _route_lease_index(scheduler_state, action_id)
    if lease_index is not None:
        lease = scheduler_state["route_leases"][lease_index]
        if lease.get("recipient_thread_id") != recipient_thread_id:
            raise ProtocolError("sent recipient does not match route lease")
        if not isinstance(packet_sha256, str) or (
            lease.get("packet_sha256") != packet_sha256.lower()
        ):
            raise ProtocolError("sent packet does not match route lease")
        existing_cursor = lease.get("after_cursor")
        if existing_cursor and after_cursor and existing_cursor != after_cursor:
            raise ProtocolError("conflicting wait cursor for recorded delivery")
        if existing_cursor is None and after_cursor is not None:
            lease["after_cursor"] = after_cursor
            scheduler_state["revision"] = int(
                scheduler_state.get("revision", 0)
            ) + 1
            _sync_legacy_active_claim_view(scheduler_state)
            return {
                "classification": "COORDINATOR_ACTION_WAIT_CURSOR_RECORDED",
                "action_id": action_id,
                "recipient_thread_id": recipient_thread_id,
                "after_cursor": after_cursor,
                "deduplicated": False,
            }
        return {
            "classification": "COORDINATOR_SEND_RECEIPT_ALREADY_RECORDED",
            "action_id": action_id,
            "recipient_thread_id": recipient_thread_id,
            "after_cursor": existing_cursor,
            "deduplicated": True,
        }

    active = scheduler_state.get("scheduler_claim")
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        raise ProtocolError("exact active Coordinator scheduler claim is required")
    if active.get("action", {}).get("requires_external_result") is not True:
        raise ProtocolError("local Coordinator action cannot be marked as sent")
    if active.get("status") != "prepared":
        raise ProtocolError("external delivery must be prepared before send")
    route = active.get("action", {}).get("payload", {}).get("route", {})
    expected = str(route.get("recipient_thread_id") or "")
    if expected and expected != recipient_thread_id:
        raise ProtocolError("outbound recipient does not match claimed exact route")
    if not isinstance(packet_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", packet_sha256
    ):
        raise ProtocolError(
            "external send requires packet_sha256 as a 64-character hex digest"
        )
    if active.get("recipient_thread_id") != recipient_thread_id:
        raise ProtocolError("sent recipient does not match prepared delivery")
    if active.get("packet_sha256") != packet_sha256.lower():
        raise ProtocolError("sent packet does not match prepared delivery")
    if not re.fullmatch(
        r"[0-9a-fA-F]{64}", str(active.get("delivery_token") or "")
    ):
        raise ProtocolError("prepared delivery_token is required before send")
    leases = scheduler_state.get("route_leases", [])
    if len(leases) >= int(scheduler_state["concurrency_limit"]):
        raise ProtocolError("Coordinator external route capacity is exhausted")
    record = _record_with_route_metadata(active)
    if record.get("route_class") == "execution" and any(
        str(item.get("route_class") or "execution") == "execution"
        and str(item.get("repository_id") or "")
        == str(record.get("repository_id") or "")
        for item in leases
        if isinstance(item, dict)
    ):
        raise ProtocolError("repository already has an external execution route lease")
    record.update(
        {
            "status": "waiting",
            "recipient_thread_id": recipient_thread_id,
            "packet_sha256": packet_sha256.lower(),
            "after_cursor": after_cursor,
            "sent_at": utc_now(),
            "leased_at": utc_now(),
        }
    )
    leases.append(record)
    repository_id = str(record.get("repository_id") or "")
    if repository_id:
        scheduler_state["round_robin_cursor_repository_id"] = repository_id
    scheduler_state["scheduler_claim"] = None
    scheduler_state["revision"] = int(scheduler_state.get("revision", 0)) + 1
    _sync_legacy_active_claim_view(scheduler_state)
    return {
        "classification": "COORDINATOR_ACTION_WAITING_EXACT_RESULT",
        "action_id": action_id,
        "recipient_thread_id": recipient_thread_id,
        "after_cursor": after_cursor,
        "route_lease_created": True,
        "capacity_remaining": int(scheduler_state["concurrency_limit"])
        - len(leases),
        "deduplicated": False,
    }


def _validate_authorized_runtime_probe_evidence(
    record: dict[str, Any], action_id: str, outcome: str, evidence: str | None
) -> dict[str, Any]:
    if str(evidence or "") != record["recovery_receipt_path"]:
        raise ProtocolError(
            "authorized runtime probe requires the exact recovery receipt"
        )
    receipt_path = Path(record["recovery_receipt_path"])
    if not receipt_path.is_file():
        raise ProtocolError("authorized runtime probe receipt is missing")
    receipt = load_json(receipt_path)
    exact_values = {
        "runtime_action_id": record["runtime_action_id"],
        "identity_sha256": record["identity_sha256"],
        "authorization_id": record["authorization_id"],
        "authorization_action_id": record["authorization_action_id"],
        "authorization_payload_sha256": record[
            "authorization_payload_sha256"
        ],
    }
    for field, expected in exact_values.items():
        if receipt.get(field) != expected:
            raise ProtocolError(f"runtime probe receipt {field} mismatch")
    if receipt.get("authority_consumed") is not True:
        raise ProtocolError("runtime probe receipt must show consumed authority")
    original = receipt.get("original_target", {})
    if not isinstance(original, dict) or any(
        original.get(field) != expected
        for field, expected in {
            "path": record["target_path"],
            "size": record["target_pre_size"],
            "sha256": record["target_pre_sha256"],
            "content_class": "all_nul_bytes",
        }.items()
    ):
        raise ProtocolError("runtime probe receipt original target mismatch")
    for field in ("backup", "quarantine"):
        preserved = receipt.get(field, {})
        expected_path = record[f"{field}_path"]
        if not isinstance(preserved, dict) or (
            preserved.get("path") != expected_path
            or preserved.get("exists") is not True
            or preserved.get("is_file") is not True
            or preserved.get("size") != record["target_pre_size"]
            or preserved.get("sha256") != record["target_pre_sha256"]
            or preserved.get("content_class") != "all_nul_bytes"
            or preserved.get("expected_identity_match") is not True
            or not Path(expected_path).is_file()
            or Path(expected_path).stat().st_size != record["target_pre_size"]
            or sha256_file(expected_path) != record["target_pre_sha256"]
        ):
            raise ProtocolError(f"runtime probe receipt {field} mismatch")
    mutation_counts = receipt.get("mutation_counts")
    if mutation_counts != {
        "acl": 0,
        "ownership": 0,
        "os_policy": 0,
        "execution_policy": 0,
    }:
        raise ProtocolError("runtime probe receipt reports forbidden mutation")
    if receipt.get("product_resume_readiness") != "NOT_YET_SATISFIED":
        raise ProtocolError(
            "Worker receipt cannot clear product resume readiness"
        )
    if "execution_surface" in receipt:
        raise ProtocolError("runtime probe receipt uses ambiguous execution surface")
    if (
        receipt.get("repair_execution_surface")
        != AUTHORIZED_RUNTIME_REPAIR_EXECUTION_SURFACE
    ):
        raise ProtocolError("runtime probe receipt repair execution surface mismatch")
    if (
        receipt.get("probe_execution_surface")
        != AUTHORIZED_RUNTIME_PROBE_EXECUTION_SURFACE
    ):
        raise ProtocolError("runtime probe receipt probe execution surface mismatch")
    if receipt.get("rollback_performed") is not False:
        raise ProtocolError("Worker probe receipt cannot claim local rollback")

    early_failures = {"delivery_failed", "task_start_failed"}
    expected_phase = (
        "REPAIR_PREPARED" if outcome in early_failures else "WORKER_PROBE_RESULT"
    )
    if receipt.get("phase") != expected_phase:
        raise ProtocolError("runtime probe receipt phase mismatch")
    if outcome not in early_failures:
        if receipt.get("probe_action_id") != action_id:
            raise ProtocolError("runtime probe receipt action identity mismatch")
    probe_a = receipt.get("probe_a", {})
    postcheck = receipt.get("postcheck", {})
    probe_b = receipt.get("probe_b", {})
    doctor = receipt.get("runtime_doctor", {})
    statuses = (
        probe_a.get("status") if isinstance(probe_a, dict) else None,
        postcheck.get("status") if isinstance(postcheck, dict) else None,
        probe_b.get("status") if isinstance(probe_b, dict) else None,
        doctor.get("status") if isinstance(doctor, dict) else None,
    )
    allowed_statuses = {
        "delivery_failed": {("pending", "pending", "pending", "pending")},
        "task_start_failed": {("pending", "pending", "pending", "pending")},
        "regeneration_failed": {("passed", "failed", "not_started", "not_started")},
        "postcheck_failed": {("passed", "failed", "not_started", "not_started")},
        "probe_a_failed": {("failed", "not_started", "not_started", "not_started")},
        "probe_b_failed": {("passed", "passed", "failed", "not_started")},
        "runtime_doctor_failed": {("passed", "passed", "passed", "failed")},
        "probe_passed": {("passed", "passed", "passed", "passed")},
    }
    if statuses not in allowed_statuses.get(outcome, set()):
        raise ProtocolError("runtime probe receipt phase ordering mismatch")

    probe_contract = _fixed_runtime_probe_contract(record)

    def validate_process_step(
        step: dict[str, Any], contract: dict[str, Any], label: str
    ) -> None:
        status = str(step.get("status") or "")
        if status in {"pending", "not_started"}:
            return
        if step.get("cwd") != contract["cwd"] or step.get("argv") != contract["argv"]:
            raise ProtocolError(f"runtime {label} exact cwd/argv mismatch")
        if step.get("sandbox_mode") != "restricted_workspace_write":
            raise ProtocolError(f"runtime {label} sandbox mode mismatch")
        exit_code = step.get("exit_code")
        helper_error = step.get("helper_error")
        if status == "passed":
            if exit_code != 0 or helper_error not in (None, ""):
                raise ProtocolError(f"runtime {label} pass lacks clean exit 0")
        elif status == "failed":
            if not (
                (isinstance(exit_code, int) and exit_code != 0)
                or (isinstance(helper_error, str) and helper_error.strip())
            ):
                raise ProtocolError(f"runtime {label} failure lacks exit/error evidence")
        else:
            raise ProtocolError(f"runtime {label} status is unsupported")

    validate_process_step(probe_a, probe_contract["probe_a"], "probe_a")
    validate_process_step(probe_b, probe_contract["probe_b"], "probe_b")

    post_status = str(postcheck.get("status") or "")
    if post_status in {"passed", "failed"}:
        checks = postcheck.get("checks")
        if postcheck.get("path") != record["target_path"] or not isinstance(
            checks, dict
        ) or set(checks) != {"exists", "nonempty", "not_nul_only", "valid_json"}:
            raise ProtocolError("runtime postcheck evidence shape mismatch")
        if any(not isinstance(value, bool) for value in checks.values()):
            raise ProtocolError("runtime postcheck checks must be boolean")
        if post_status == "passed" and not all(checks.values()):
            raise ProtocolError("runtime postcheck pass has a failed check")
        if post_status == "failed" and (
            all(checks.values())
            or not isinstance(postcheck.get("error"), str)
            or not postcheck["error"].strip()
        ):
            raise ProtocolError("runtime postcheck failure lacks exact evidence")

    if outcome == "probe_a_failed":
        regenerated = receipt.get("regenerated_state", {})
        target = Path(record["target_path"])
        if target.exists() and not target.is_file():
            raise ProtocolError("probe_a failure target exists but is not a file")
        if target.is_file():
            try:
                json.loads(target.read_text(encoding="utf-8"))
                actual_parse_result = "valid"
            except (OSError, UnicodeError, json.JSONDecodeError):
                actual_parse_result = "invalid"
            if (
                not isinstance(regenerated, dict)
                or regenerated.get("path") != record["target_path"]
                or regenerated.get("exists") is not True
                or regenerated.get("size") != target.stat().st_size
                or regenerated.get("sha256") != sha256_file(target)
                or regenerated.get("json_parse_result") != actual_parse_result
            ):
                raise ProtocolError(
                    "probe_a failure lacks exact regenerated-state identity"
                )
        elif not isinstance(regenerated, dict) or any(
            regenerated.get(field) != expected
            for field, expected in {
                "path": record["target_path"],
                "exists": False,
                "size": None,
                "sha256": None,
                "json_parse_result": "absent",
            }.items()
        ):
            raise ProtocolError(
                "probe_a failure requires explicit absent regenerated state"
            )

    if outcome in {"probe_b_failed", "runtime_doctor_failed", "probe_passed"}:
        regenerated = receipt.get("regenerated_state", {})
        target = Path(record["target_path"])
        if (
            not isinstance(regenerated, dict)
            or regenerated.get("path") != record["target_path"]
            or not isinstance(regenerated.get("size"), int)
            or regenerated["size"] <= 0
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(regenerated.get("sha256") or "")
            )
            or regenerated.get("json_parse_result") != "valid"
            or not target.is_file()
            or target.stat().st_size != regenerated["size"]
            or sha256_file(target) != regenerated["sha256"]
            or regenerated["sha256"] == record["target_pre_sha256"]
        ):
            raise ProtocolError("runtime probe regenerated state proof is invalid")
        try:
            json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("runtime probe regenerated state is not valid JSON") from exc

    if outcome == "postcheck_failed":
        regenerated = receipt.get("regenerated_state", {})
        target = Path(record["target_path"])
        if (
            not isinstance(regenerated, dict)
            or regenerated.get("path") != record["target_path"]
            or regenerated.get("json_parse_result") != "invalid"
            or not target.is_file()
            or regenerated.get("size") != target.stat().st_size
            or regenerated.get("sha256") != sha256_file(target)
        ):
            raise ProtocolError("failed postcheck lacks exact invalid-state identity")
    if outcome == "regeneration_failed":
        regenerated = receipt.get("regenerated_state", {})
        if (
            not isinstance(regenerated, dict)
            or regenerated.get("path") != record["target_path"]
            or regenerated.get("status") != "failed"
            or not isinstance(regenerated.get("error"), str)
            or not regenerated["error"].strip()
        ):
            raise ProtocolError("regeneration failure lacks exact error evidence")

    expected_doctor = probe_contract["runtime_doctor"]["expected"]
    if outcome in {"runtime_doctor_failed", "probe_passed"}:
        if not isinstance(doctor, dict) or doctor.get("expected") != expected_doctor:
            raise ProtocolError("runtime doctor expected identity mismatch")
    if outcome == "probe_passed" and doctor.get("observed") != expected_doctor:
        raise ProtocolError("runtime doctor success lacks exact observed identity")
    if outcome == "probe_passed" and doctor.get("error") not in (None, ""):
        raise ProtocolError("runtime doctor pass contains an error")
    if outcome == "runtime_doctor_failed" and (
        not isinstance(doctor.get("observed"), dict)
        or not isinstance(doctor.get("error"), str)
        or not doctor["error"].strip()
    ):
        raise ProtocolError("runtime doctor failure lacks observed/error evidence")
    return receipt


def _validate_authorized_runtime_supervisor_evidence(
    record: dict[str, Any], outcome: str, evidence: str | None
) -> dict[str, Any]:
    if outcome not in {"accepted", "rejected", "reconcile"}:
        raise ProtocolError("runtime Supervisor outcome is not structured")
    evidence_path = Path(str(evidence or ""))
    if not evidence_path.is_file():
        raise ProtocolError("runtime Supervisor result evidence is missing")
    result = load_json(evidence_path)
    expected = {
        "event_kind": "SUPERVISOR_RUNTIME_RECOVERY_VERDICT",
        "disposition": outcome,
        "repository_id": record["repository_id"],
        "mission_id": record["mission_id"],
        "attempt_id": record["attempt_id"],
        "supervisor_thread_id": record["supervisor_thread_id"],
        "runtime_action_id": record["runtime_action_id"],
        "runtime_identity_sha256": record["identity_sha256"],
        "authorization_id": record["authorization_id"],
        "recovery_receipt_sha256": sha256_file(
            record["recovery_receipt_path"]
        ),
    }
    for field, value in expected.items():
        if str(result.get(field) or "") != str(value):
            raise ProtocolError(f"runtime Supervisor evidence {field} mismatch")
    readiness = str(result.get("product_resume_readiness") or "")
    if outcome == "accepted":
        if readiness != "ELIGIBLE_FOR_LATER_SUPERVISOR_WORK_ORDER":
            raise ProtocolError("accepted runtime result has invalid resume readiness")
    elif readiness != "NOT_YET_SATISFIED":
        raise ProtocolError("non-accepted runtime result cannot clear resume readiness")
    return {
        "disposition": outcome,
        "evidence_path": str(evidence_path.resolve(strict=False)),
        "evidence_sha256": sha256_file(evidence_path),
        "product_resume_readiness": readiness,
        "recorded_at": utc_now(),
    }


def _apply_authorized_runtime_completion(
    coordinator_state: dict[str, Any],
    active: dict[str, Any],
    action_id: str,
    outcome: str,
    evidence: str | None,
) -> None:
    action = active.get("action", {})
    kind = str(action.get("kind") or "")
    if kind not in AUTHORIZED_RUNTIME_ACTION_KINDS:
        return
    record = _runtime_action_from_scheduler_record(active, coordinator_state)
    if kind == "execute_authorized_runtime_repair":
        if outcome == "precondition_mismatch":
            if record["phase"] in {"AUTHORIZED", "EFFECT_INTENT"}:
                if record.get("authority_consumed") is True:
                    raise ProtocolError("runtime precondition mismatch consumed authority")
                record["phase"] = "RESULT_READY"
                record["recovery_result"] = {
                    "classification": "AUTHORIZED_RUNTIME_PRECONDITION_MISMATCH",
                    "authority_consumed": False,
                    "target_mutated": False,
                }
                _write_authorized_runtime_receipt(
                    record, "PRECONDITION_MISMATCH"
                )
            if record["phase"] not in {"RESULT_READY", "COMPLETE"} or record[
                "authority_consumed"
            ]:
                raise ProtocolError("invalid runtime precondition-mismatch completion")
        elif outcome == "repair_prepared":
            if record["phase"] in {
                "AUTHORIZED",
                "EFFECT_INTENT",
                "EFFECT_PREPARED",
            }:
                backup = Path(record["backup_path"])
                quarantine = Path(record["quarantine_path"])
                if not all(
                    path.is_file()
                    and path.stat().st_size == record["target_pre_size"]
                    and sha256_file(path) == record["target_pre_sha256"]
                    for path in (backup, quarantine)
                ):
                    raise ProtocolError(
                        "completed runtime repair lacks byte-exact preserved state"
                    )
                record["authority_consumed"] = True
                record["phase"] = "REPAIR_PREPARED"
                _write_authorized_runtime_receipt(record, "REPAIR_PREPARED")
            if record["phase"] not in {
                "REPAIR_PREPARED",
                "ROLLBACK_REQUIRED",
                "RESULT_READY",
                "COMPLETE",
            } or not record["authority_consumed"]:
                raise ProtocolError("runtime repair completion lacks prepared effect")
        else:
            raise ProtocolError("invalid authorized runtime repair outcome")
        return
    if kind == "rollback_authorized_runtime_repair":
        if outcome == "rolled_back" and record["phase"] == "ROLLBACK_REQUIRED":
            target = Path(record["target_path"])
            backup = Path(record["backup_path"])
            quarantine = Path(record["quarantine_path"])
            if not _runtime_target_matches_precondition(record) or not all(
                path.is_file()
                and sha256_file(path) == record["target_pre_sha256"]
                for path in (backup, quarantine)
            ):
                raise ProtocolError(
                    "completed runtime rollback lacks byte-exact restored state"
                )
            record["phase"] = "RESULT_READY"
            record["recovery_result"] = {
                "classification": "CODEX_SANDBOX_STATE_REPAIR_FAILED",
                "authority_consumed": True,
                "rolled_back": True,
                "target_sha256": sha256_file(target),
                "probe_result": copy.deepcopy(record.get("probe_result")),
            }
            _write_authorized_runtime_receipt(record, "ROLLBACK_COMPLETE")
        if outcome != "rolled_back" or record["phase"] not in {
            "RESULT_READY",
            "COMPLETE",
        }:
            raise ProtocolError("invalid authorized runtime rollback outcome")
        return
    if kind == "probe_authorized_runtime_repair":
        rollback_outcomes = {
            "delivery_failed",
            "task_start_failed",
            "regeneration_failed",
            "postcheck_failed",
            "probe_a_failed",
        }
        retained_state_outcomes = {
            "probe_b_failed",
            "runtime_doctor_failed",
            "probe_passed",
        }
        if outcome not in rollback_outcomes | retained_state_outcomes:
            raise ProtocolError("authorized runtime probe outcome is not structured")
        receipt = _validate_authorized_runtime_probe_evidence(
            record, action_id, outcome, evidence
        )
        existing_probe = record.get("probe_result")
        if isinstance(existing_probe, dict):
            if (
                existing_probe.get("action_id") != action_id
                or existing_probe.get("outcome") != outcome
                or str(existing_probe.get("evidence") or "")
                != str(evidence or "")
            ):
                raise ProtocolError("conflicting authorized runtime probe replay")
            return
        record["probe_result"] = {
            "action_id": action_id,
            "outcome": outcome,
            "evidence": evidence,
            "evidence_sha256": sha256_file(record["recovery_receipt_path"]),
            "receipt_phase": receipt.get("phase"),
            "recorded_at": utc_now(),
        }
        if outcome == "probe_passed":
            record["phase"] = "RESULT_READY"
            record["recovery_result"] = {
                "classification": "CODEX_SANDBOX_STATE_REPAIR_SUCCEEDED",
                "authority_consumed": True,
                "rolled_back": False,
                "probe_result": copy.deepcopy(record["probe_result"]),
            }
        elif outcome in rollback_outcomes:
            record["phase"] = "ROLLBACK_REQUIRED"
        else:
            classification = (
                "CWD_OR_PATH_SCOPED_SANDBOX_FAILURE"
                if outcome == "probe_b_failed"
                else "SANDBOX_RECOVERED_RUNTIME_READINESS_FAILED"
            )
            record["phase"] = "RESULT_READY"
            record["recovery_result"] = {
                "classification": classification,
                "authority_consumed": True,
                "rolled_back": False,
                "regenerated_state_retained": True,
                "probe_result": copy.deepcopy(record["probe_result"]),
            }
        return
    if kind == "return_authorized_runtime_recovery_result":
        if not isinstance(record.get("recovery_result"), dict):
            raise ProtocolError("runtime Supervisor return lacks recovery_result")
        if record["phase"] == "COMPLETE":
            existing_result = record.get("supervisor_result")
            if not isinstance(existing_result, dict) or (
                existing_result.get("disposition") != outcome
                or _normalized_absolute_path(existing_result.get("evidence_path", ""))
                != _normalized_absolute_path(str(evidence or ""))
            ):
                raise ProtocolError("conflicting runtime Supervisor result replay")
            return
        if record["phase"] != "RESULT_READY":
            raise ProtocolError("runtime Supervisor return is out of phase")
        record["supervisor_result"] = (
            _validate_authorized_runtime_supervisor_evidence(
                record, outcome, evidence
            )
        )
        record["phase"] = "COMPLETE"
        record["completed_at"] = utc_now()
        return


def complete_coordinator_action(
    scheduler_state: dict[str, Any],
    action_id: str,
    outcome: str,
    *,
    evidence: str | None = None,
    coordinator_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_scheduler_state_v2(scheduler_state)
    active = scheduler_state.get("scheduler_claim")
    source = "scheduler_claim"
    lease_index = _route_lease_index(scheduler_state, action_id)
    if lease_index is not None:
        active = scheduler_state["route_leases"][lease_index]
        source = "route_lease"
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        existing_completion = next(
            (
                item
                for item in scheduler_state.get("completed_actions", [])
                if isinstance(item, dict) and item.get("action_id") == action_id
            ),
            None,
        )
        if isinstance(existing_completion, dict):
            if existing_completion.get("outcome") != outcome or (
                str(existing_completion.get("evidence") or "")
                != str(evidence or "")
            ):
                raise ProtocolError("conflicting completed action replay")
            completed_action = existing_completion.get("action")
            if (
                isinstance(completed_action, dict)
                and completed_action.get("kind") in AUTHORIZED_RUNTIME_ACTION_KINDS
            ):
                if coordinator_state is None:
                    raise ProtocolError(
                        "authorized runtime completion replay requires Coordinator state"
                    )
                replay_active = {"action": completed_action}
                _apply_authorized_runtime_completion(
                    coordinator_state,
                    replay_active,
                    action_id,
                    outcome,
                    evidence,
                )
            return {
                "classification": "COORDINATOR_ACTION_ALREADY_COMPLETED",
                "action_id": action_id,
                "outcome": outcome,
                "deduplicated": True,
            }
        raise ProtocolError("exact Coordinator scheduler claim or route lease is required")
    requires_external_result = (
        active.get("action", {}).get("requires_external_result") is True
    )
    action_kind = str(active.get("action", {}).get("kind") or "")
    if action_kind in AUTHORIZED_RUNTIME_ACTION_KINDS:
        if coordinator_state is None:
            raise ProtocolError(
                "authorized runtime completion requires Coordinator state"
            )
        validate_coordinator_state(coordinator_state)
    if requires_external_result and source != "route_lease":
        raise ProtocolError(
            "external Coordinator action cannot complete before exact send receipt"
        )
    if requires_external_result and not str(evidence or "").strip():
        raise ProtocolError(
            "external Coordinator action completion requires result evidence"
        )
    if action_kind in AUTHORIZED_RUNTIME_LOCAL_ACTION_KINDS:
        runtime_record = _runtime_action_from_scheduler_record(
            active, coordinator_state or {}
        )
        if str(evidence or "") != runtime_record["recovery_receipt_path"]:
            raise ProtocolError(
                "authorized runtime local completion requires exact effect receipt"
            )
        receipt_path = Path(runtime_record["recovery_receipt_path"])
        if not receipt_path.is_file():
            raise ProtocolError(
                "authorized runtime local completion receipt is missing"
            )
        receipt = load_json(receipt_path)
        if receipt.get("identity_sha256") != runtime_record["identity_sha256"]:
            raise ProtocolError(
                "authorized runtime local completion receipt identity mismatch"
            )
        status = str(active.get("status") or "claimed")
        if outcome == "precondition_mismatch":
            if status not in {"claimed", "effect_prepared"} or runtime_record[
                "authority_consumed"
            ]:
                raise ProtocolError(
                    "precondition mismatch must precede effect preparation"
                )
        elif status != "effect_prepared":
            raise ProtocolError(
                "authorized runtime local effect must be prepared before completion"
            )
    if coordinator_state is not None:
        _apply_authorized_runtime_completion(
            coordinator_state, active, action_id, outcome, evidence
        )
    record = {
        "action_id": action_id,
        "kind": active.get("action", {}).get("kind"),
        "action": (
            copy.deepcopy(active.get("action"))
            if action_kind in AUTHORIZED_RUNTIME_ACTION_KINDS
            else None
        ),
        "requires_external_result": requires_external_result,
        "state_fingerprint": active.get("state_fingerprint"),
        "outcome": outcome,
        "evidence": evidence,
        "repository_id": active.get("repository_id"),
        "route_class": active.get("route_class"),
        "recipient_thread_id": active.get("recipient_thread_id"),
        "packet_sha256": active.get("packet_sha256"),
        "delivery_token": active.get("delivery_token"),
        "after_cursor": active.get("after_cursor"),
        "completed_at": utc_now(),
    }
    completed = [
        item
        for item in scheduler_state.get("completed_actions", [])
        if isinstance(item, dict) and item.get("action_id") != action_id
    ]
    completed.append(record)
    scheduler_state["completed_actions"] = completed
    if source == "route_lease":
        del scheduler_state["route_leases"][lease_index]
    else:
        scheduler_state["scheduler_claim"] = None
        repository_id = str(active.get("repository_id") or "")
        if repository_id:
            scheduler_state["round_robin_cursor_repository_id"] = repository_id
    scheduler_state["revision"] = int(scheduler_state.get("revision", 0)) + 1
    _sync_legacy_active_claim_view(scheduler_state)
    return {
        "classification": "COORDINATOR_ACTION_COMPLETED",
        "action_id": action_id,
        "outcome": outcome,
        "closed": source,
        "deduplicated": False,
    }


def release_coordinator_action(
    scheduler_state: dict[str, Any],
    action_id: str,
    reason: str,
) -> dict[str, Any]:
    """Release only an unprepared short-lived scheduler claim."""
    _ensure_scheduler_state_v2(scheduler_state)
    if _route_lease_index(scheduler_state, action_id) is not None:
        raise ProtocolError(
            "sent or waiting Coordinator action must be reconciled, not released"
        )
    active = scheduler_state.get("scheduler_claim")
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        existing_release = next(
            (
                item
                for item in scheduler_state.get("released_claims", [])
                if isinstance(item, dict) and item.get("action_id") == action_id
            ),
            None,
        )
        if isinstance(existing_release, dict):
            if existing_release.get("reason") != reason:
                raise ProtocolError("conflicting released action replay")
            return {
                "classification": "COORDINATOR_ACTION_ALREADY_RELEASED",
                "action_id": action_id,
                "reason": reason,
                "deduplicated": True,
            }
        raise ProtocolError("exact active Coordinator scheduler claim is required")
    if active.get("status") != "claimed":
        raise ProtocolError(
            "prepared Coordinator action must be reconciled, not released"
        )
    released = scheduler_state.setdefault("released_claims", [])
    if not isinstance(released, list):
        raise ProtocolError("released_claims must be a list")
    released.append(
        {
            "action_id": action_id,
            "status": active.get("status"),
            "reason": reason,
            "released_at": utc_now(),
        }
    )
    scheduler_state["scheduler_claim"] = None
    scheduler_state["revision"] = int(scheduler_state.get("revision", 0)) + 1
    _sync_legacy_active_claim_view(scheduler_state)
    return {
        "classification": "COORDINATOR_ACTION_RELEASED_FOR_RETRY",
        "action_id": action_id,
        "reason": reason,
        "deduplicated": False,
    }


def discover_worker_candidates(
    repository_id: str,
    host_id: str,
    registry: dict[str, Any],
    host: dict[str, Any],
    adapter: dict[str, Any],
    missions: Iterable[dict[str, Any]],
    *,
    inspect: Callable[[str], dict[str, str]] = inspect_repository,
) -> list[dict[str, Any]]:
    host_aliases = {
        host_id.casefold(),
        *(str(item).casefold() for item in host.get("aliases", [])),
        *(str(item).casefold() for item in host.get("app_host_ids", [])),
    }
    bindings_by_task = {
        str(item.get("worker_task_id")): str(item.get("repository_id"))
        for item in registry.get("worker_bindings", [])
        if isinstance(item, dict) and item.get("worker_task_id")
    }
    busy_tasks = {
        str(item.get("worker_task_id"))
        for item in missions
        if isinstance(item, dict)
        and item.get("worker_task_id")
        and item.get("state") not in TERMINAL_STATES
    }
    candidates: list[dict[str, Any]] = []
    for thread in _adapter_threads(adapter):
        task_id = str(thread.get("id") or "")
        if thread.get("kind") != "codex" or thread.get("read_verified") is not True:
            continue
        if str(thread.get("status") or "").casefold() in {
            "destroyed",
            "invalid",
            "stale",
        }:
            continue
        if str(thread.get("host_id") or "").casefold() not in host_aliases:
            continue
        bound_repository = bindings_by_task.get(task_id)
        if bound_repository and bound_repository != repository_id:
            continue
        if task_id in busy_tasks:
            continue
        cwd = thread.get("cwd")
        if not cwd:
            continue
        try:
            live = inspect(str(cwd))
        except (OSError, ProtocolError, subprocess.SubprocessError):
            continue
        if live["repository_id"] != repository_id:
            continue
        observed_remote = thread.get("repository_id")
        if observed_remote:
            try:
                if normalize_remote(str(observed_remote)) != repository_id:
                    continue
            except ProtocolError:
                continue
        candidates.append(
            {
                "worker_task_id": task_id,
                "title": str(thread.get("title") or ""),
                "root": live["root"],
                "remote_identity": live["repository_id"],
            }
        )
    return sorted(candidates, key=lambda item: item["worker_task_id"])


def worker_bootstrap_handshake(
    repository_id: str,
    host_id: str,
    root: str,
    worker_task_id: str,
) -> dict[str, Any]:
    return {
        "message_type": "COORDINATOR_WORKER_BOOTSTRAP",
        "repository_id": repository_id,
        "host_id": host_id,
        "worker_task_id": worker_task_id,
        "verified_root": root,
        "normalized_remote_identity": repository_id,
        "protocol_sha256": sha256_file(PROTOCOL_PATH),
        "persistent_worker": True,
        "mission_specific_task_creation": False,
        "user_input_required": False,
    }


def ensure_worker_binding(
    repository_id: str,
    host_id: str,
    root: str,
    registry: dict[str, Any],
    host: dict[str, Any],
    adapter: dict[str, Any],
    missions: Iterable[dict[str, Any]],
    *,
    inspect: Callable[[str], dict[str, str]] = inspect_repository,
    create_worker: Callable[[str, str, str], dict[str, Any]] | None = None,
    send_message: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    existing = _worker_binding_for(registry, repository_id, host_id)
    if (
        existing
        and existing.get("worker_task_id")
        and existing.get("binding_status") == "active"
    ):
        return {
            "classification": "WORKER_BINDING_REUSED",
            "worker_task_id": existing["worker_task_id"],
            "registry_changed": False,
            "worker_tasks_created": 0,
            "user_input_required": False,
        }

    candidates = discover_worker_candidates(
        repository_id,
        host_id,
        registry,
        host,
        adapter,
        missions,
        inspect=inspect,
    )
    if len(candidates) > 1:
        return {
            "classification": "USER_DECISION_WORKER_CANDIDATES",
            "candidate_workers": [
                {
                    "candidate_ref": f"worker-candidate-{index + 1}",
                    "title": item["title"],
                    "verified_remote_identity": item["remote_identity"],
                }
                for index, item in enumerate(candidates)
            ],
            "terminal_route": "USER_DECISION",
            "user_input_surface": "coordinator",
            "ask_for_task_id": False,
        }

    created = False
    if not candidates:
        repository = _repository_record_by_id(registry, repository_id)
        assert repository is not None
        if not _repository_worker_creatable(
            registry, repository, host, adapter
        ):
            return {
                "classification": "USER_ACTION_CREATE_OR_BIND_WORKER_TASK",
                "terminal_route": "USER_ACTION",
                "user_input_surface": "coordinator",
                "ask_user_to_bootstrap_worker": False,
            }
        if create_worker is None:
            return {
                "classification": "WORKER_AUTO_CREATE_REQUIRED",
                "create_worker_task": True,
                "repository_id": repository_id,
                "host_id": host_id,
                "root": root,
                "worker_tasks_to_create": 1,
                "user_input_required": False,
                "terminal_route": None,
            }
        created_thread = create_worker(repository_id, host_id, root)
        adapter_with_created = copy.deepcopy(adapter)
        adapter_with_created.setdefault("threads", []).append(created_thread)
        candidates = discover_worker_candidates(
            repository_id,
            host_id,
            registry,
            host,
            adapter_with_created,
            missions,
            inspect=inspect,
        )
        if len(candidates) != 1:
            raise ProtocolError("created Worker failed exact root/remote verification")
        adapter.setdefault("threads", []).append(copy.deepcopy(created_thread))
        created = True

    candidate = candidates[0]
    binding = existing
    if binding is None:
        binding = {
            "schema_version": SCHEMA_VERSION,
            "repository_id": repository_id,
            "host_id": host_id,
            "allow_create_worker_task": bool(
                registry.get("coordinator_policy", {}).get(
                    "allow_create_worker_task", False
                )
            ),
        }
        registry.setdefault("worker_bindings", []).append(binding)
    binding.update(
        {
            "worker_task_id": candidate["worker_task_id"],
            "root_hint": candidate["root"],
            "last_verified_at": utc_now(),
            "binding_status": "active",
            "binding_source": (
                "coordinator_auto_created"
                if created
                else "coordinator_auto_discovered"
            ),
        }
    )
    handshake = worker_bootstrap_handshake(
        repository_id,
        host_id,
        candidate["root"],
        candidate["worker_task_id"],
    )
    if created and send_message is not None:
        send_message(candidate["worker_task_id"], handshake)
    return {
        "classification": (
            "WORKER_AUTO_CREATED_AND_BOUND"
            if created
            else "WORKER_AUTO_DISCOVERED_AND_BOUND"
        ),
        "worker_task_id": candidate["worker_task_id"],
        "registry_changed": True,
        "worker_tasks_created": 1 if created else 0,
        "bootstrap_handshake": handshake if created else None,
        "bootstrap_sent_by_coordinator": bool(created and send_message),
        "user_input_required": False,
        "terminal_route": None,
    }


def _mission_response_key(mission: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(mission.get("repository_id") or ""),
        str(mission.get("mission_id") or ""),
        str(mission.get("attempt_id") or ""),
    )


def _user_response_target_route(mission: dict[str, Any]) -> str:
    state = str(mission.get("state") or "")
    if state in {"USER_DECISION", "USER_ACTION"}:
        return state
    raise ProtocolError("USER_RESPONSE requires USER_DECISION or USER_ACTION")


def normalize_user_response(
    mission: dict[str, Any],
    raw_user_response: str,
    *,
    related_artifact: Any = None,
    current_external_effect_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    terminal_route = _user_response_target_route(mission)
    if not str(raw_user_response).strip():
        raise ProtocolError("USER_RESPONSE cannot be empty")
    supervisor_thread_id = str(mission.get("supervisor_thread_id") or "")
    if not supervisor_thread_id:
        raise ProtocolError("USER_RESPONSE requires the exact Supervisor binding")
    packet = {
        "repository_id": mission["repository_id"],
        "mission_id": mission["mission_id"],
        "attempt_id": mission["attempt_id"],
        "terminal_route_being_resumed": terminal_route,
        "raw_user_response": raw_user_response,
        "related_artifact": related_artifact,
        "current_external_effect_state": (
            copy.deepcopy(current_external_effect_state)
            if current_external_effect_state is not None
            else copy.deepcopy(mission.get("external_effects", {}))
        ),
    }
    return {
        "classification": "USER_RESPONSE_PREPARED_FOR_EXACT_SUPERVISOR",
        "message_type": "USER_RESPONSE",
        "user_input_surface": "coordinator",
        "recipient_kind": "web_supervisor",
        "recipient_thread_id": supervisor_thread_id,
        "prohibited_recipient_worker_task_id": mission.get("worker_task_id"),
        "packet": packet,
    }


def queue_user_response(
    mission: dict[str, Any],
    coordinator_state: dict[str, Any],
    raw_user_response: str,
    *,
    related_artifact: Any = None,
    current_external_effect_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Durably stage a freeform response before exact-Supervisor delivery."""
    prepared = normalize_user_response(
        mission,
        raw_user_response,
        related_artifact=related_artifact,
        current_external_effect_state=current_external_effect_state,
    )
    packet = prepared["packet"]
    key = _mission_response_key(mission)
    response_id = sha256_text(
        json.dumps(
            {
                "identity": key,
                "raw_user_response": packet["raw_user_response"],
                "terminal_route": packet["terminal_route_being_resumed"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    pending = coordinator_state.setdefault("pending_user_responses", [])
    for item in pending:
        if not isinstance(item, dict):
            continue
        item_key = (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        if item_key != key:
            continue
        if item.get("response_id") == response_id:
            return {
                **prepared,
                "classification": "USER_RESPONSE_ALREADY_QUEUED",
                "response_id": response_id,
                "deduplicated": True,
            }
        raise ProtocolError("a different USER_RESPONSE is already queued for this Mission")

    queued_at = utc_now()
    queue_item = {
        "repository_id": mission["repository_id"],
        "mission_id": mission["mission_id"],
        "attempt_id": mission["attempt_id"],
        "terminal_route_being_resumed": packet["terminal_route_being_resumed"],
        "response_id": response_id,
        "response_state": "ready_to_route",
        "priority": 1,
        "recipient_thread_id": prepared["recipient_thread_id"],
        "packet": copy.deepcopy(packet),
        "queued_at": queued_at,
    }
    pending.append(queue_item)
    mission["user_response_ready"] = True
    mission["queued_user_response_id"] = response_id
    mission["updated_at"] = queued_at
    mission.setdefault("events", []).append(
        {
            "event": "USER_RESPONSE_QUEUED",
            "at": queued_at,
            "details": {
                "response_id": response_id,
                "recipient_thread_id": prepared["recipient_thread_id"],
            },
        }
    )
    return {
        **prepared,
        "classification": "USER_RESPONSE_QUEUED_FOR_EXACT_SUPERVISOR",
        "response_id": response_id,
        "deduplicated": False,
    }


def acknowledge_user_response_routed(
    mission: dict[str, Any],
    coordinator_state: dict[str, Any],
    response_id: str,
) -> dict[str, Any]:
    """Advance a queued response only after exact-Supervisor send succeeds."""
    if mission.get("state") not in {"USER_DECISION", "USER_ACTION"}:
        raise ProtocolError("USER_RESPONSE routing acknowledgement requires a parked Mission")
    key = _mission_response_key(mission)
    pending = coordinator_state.setdefault("pending_user_responses", [])
    matched_index: int | None = None
    matched: dict[str, Any] | None = None
    for index, item in enumerate(pending):
        if not isinstance(item, dict):
            continue
        item_key = (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        if item_key == key and item.get("response_id") == response_id:
            matched_index = index
            matched = item
            break
    if matched is None or matched_index is None:
        raise ProtocolError("queued USER_RESPONSE was not found for routing acknowledgement")
    if matched.get("recipient_thread_id") != mission.get("supervisor_thread_id"):
        raise ProtocolError("queued USER_RESPONSE exact Supervisor binding drift")

    pending.pop(matched_index)
    routed_at = utc_now()
    routed = copy.deepcopy(matched)
    routed["response_state"] = "routed"
    routed["routed_at"] = routed_at
    coordinator_state.setdefault("routed_user_responses", []).append(routed)
    mission["user_response_ready"] = False
    mission["last_routed_user_response_id"] = response_id
    mission.pop("queued_user_response_id", None)
    _record_state(
        mission,
        USER_RESPONSE_ADJUDICATION_STATE,
        {
            "response_id": response_id,
            "recipient_thread_id": matched["recipient_thread_id"],
            "terminal_route_being_resumed": matched[
                "terminal_route_being_resumed"
            ],
        },
    )
    return {
        "classification": "USER_RESPONSE_ROUTED_TO_EXACT_SUPERVISOR",
        "response_id": response_id,
        "recipient_thread_id": matched["recipient_thread_id"],
        "mission_state": mission["state"],
    }


def resolve_coordinator_target(
    target: str,
    *,
    context: dict[str, Any],
    registry: dict[str, Any],
    hosts: dict[str, Any],
    adapter: dict[str, Any],
    coordinator_state: dict[str, Any],
    missions: Iterable[dict[str, Any]],
    inspect: Callable[[str], dict[str, str]] = inspect_repository,
    create_worker: Callable[[str, str, str], dict[str, Any]] | None = None,
    send_message: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    mission_list = [item for item in missions if isinstance(item, dict)]
    if target == "this-repository":
        selection = resolve_this_repository(
            context,
            registry,
            coordinator_state,
            inspect=inspect,
        )
        generic_prompt = COORDINATOR_PROMPT_THIS_REPOSITORY
    elif target == "next-actionable-registered-repository":
        selection = select_next_actionable_repository(
            registry,
            hosts,
            adapter,
            mission_list,
            coordinator_state,
        )
        generic_prompt = COORDINATOR_PROMPT_NEXT_ACTIONABLE
    else:
        raise ProtocolError(f"unknown Coordinator target: {target}")
    if selection.get("repository_id") is None:
        selection["generic_prompt"] = generic_prompt
        return selection

    repository_id = str(selection["repository_id"])
    lane, lane_candidates = select_lane_for_context(
        registry, repository_id, mission_list
    )
    if lane is None:
        return {
            "classification": "USER_DECISION_SUPERVISION_LANE",
            "repository_id": repository_id,
            "candidate_lanes": lane_candidates,
            "terminal_route": "USER_DECISION",
            "user_input_surface": "coordinator",
            "generic_prompt": generic_prompt,
        }
    current_host = host_by_alias(
        hosts, str(adapter.get("current_host_alias") or "local")
    )
    if current_host is None:
        return {
            "classification": "BINDING_REPAIR_HOST",
            "repository_id": repository_id,
            "mode": "binding-repair",
            "terminal_route": None,
            "generic_prompt": generic_prompt,
        }
    host_id = str(current_host["host_id"])
    root_candidate = selection.get("root") or current_host.get(
        "known_repository_roots", {}
    ).get(repository_id)
    if not root_candidate:
        return {
            "classification": "BINDING_REPAIR_ROOT",
            "repository_id": repository_id,
            "host_id": host_id,
            "mode": "binding-repair",
            "terminal_route": None,
            "generic_prompt": generic_prompt,
        }
    try:
        live_root = inspect(str(root_candidate))
    except (OSError, ProtocolError, subprocess.SubprocessError) as exc:
        return {
            "classification": "BINDING_REPAIR_ROOT",
            "repository_id": repository_id,
            "host_id": host_id,
            "root_issues": [str(exc)],
            "mode": "binding-repair",
            "terminal_route": None,
            "generic_prompt": generic_prompt,
        }
    if live_root["repository_id"] != repository_id:
        return {
            "classification": "BINDING_REPAIR_ROOT_REMOTE_MISMATCH",
            "repository_id": repository_id,
            "observed_remote_identity": live_root["repository_id"],
            "host_id": host_id,
            "mode": "binding-repair",
            "terminal_route": None,
            "generic_prompt": generic_prompt,
        }
    current_host.setdefault("known_repository_roots", {})[
        repository_id
    ] = live_root["root"]
    worker_result = ensure_worker_binding(
        repository_id,
        host_id,
        live_root["root"],
        registry,
        current_host,
        adapter,
        mission_list,
        inspect=inspect,
        create_worker=create_worker,
        send_message=send_message,
    )
    if worker_result["classification"] in {
        "USER_DECISION_WORKER_CANDIDATES",
        "USER_ACTION_CREATE_OR_BIND_WORKER_TASK",
        "WORKER_AUTO_CREATE_REQUIRED",
    }:
        return {
            **selection,
            **worker_result,
            "repository_id": repository_id,
            "supervision_lane": lane,
            "generic_prompt": generic_prompt,
        }

    resolved = resolve_launch(
        repository_id,
        mode="coordinator",
        registry=registry,
        hosts=hosts,
        adapter=adapter,
        lane=lane,
    )
    resolved.update(
        {
            "generic_prompt": generic_prompt,
            "resolution_source": selection.get("resolution_source"),
            "selection_reason": selection.get("selection_reason"),
            "worker_binding_action": worker_result["classification"],
            "registry_changed": worker_result["registry_changed"],
            "worker_tasks_created": worker_result["worker_tasks_created"],
            "bootstrap_sent_by_coordinator": worker_result.get(
                "bootstrap_sent_by_coordinator", False
            ),
            "user_visible_codex_entry_points": USER_VISIBLE_CODEX_ENTRY_POINTS,
            "user_input_surface": "coordinator",
        }
    )
    return resolved


def _verified_root(
    host: dict[str, Any],
    repository_id: str,
    worker_binding: dict[str, Any],
    adapter: dict[str, Any],
    aliases: Iterable[str],
) -> tuple[str | None, list[str]]:
    candidates: list[str] = []
    verification_map = host.get("root_verifications", {})
    if repository_id in verification_map:
        value = verification_map[repository_id]
        if isinstance(value, dict):
            if value.get("repository_id") == repository_id and value.get("root"):
                return str(value["root"]), []

    for source in (
        worker_binding.get("root_hint"),
        host.get("known_repository_roots", {}).get(repository_id),
    ):
        if source and str(source) not in candidates:
            candidates.append(str(source))

    aliases = {alias.casefold() for alias in aliases}
    for project in adapter.get("projects", []):
        if not isinstance(project, dict):
            continue
        if str(project.get("label", "")).casefold() in aliases and project.get("path"):
            path = str(project["path"])
            if path not in candidates:
                candidates.append(path)

    errors: list[str] = []
    for candidate in candidates:
        try:
            info = inspect_repository(candidate)
        except ProtocolError as exc:
            errors.append(str(exc))
            continue
        if info["repository_id"] == repository_id:
            return info["root"], errors
        errors.append(
            f"remote mismatch at {candidate}: {info['repository_id']} != {repository_id}"
        )
    return None, errors or ["no_root_candidate"]


def resolve_launch(
    alias: str,
    *,
    mode: str,
    registry: dict[str, Any],
    hosts: dict[str, Any],
    adapter: dict[str, Any],
    lane: str | None = None,
    private_artifact_id: str | None = None,
    external_target_host_id: str | None = None,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ProtocolError(f"unknown mode: {mode}")
    candidates = repository_candidates(registry, alias)
    if not candidates:
        return {
            "classification": "USER_ACTION_BIND_REPOSITORY",
            "alias": alias,
            "candidate_repository_ids": [],
            "terminal_route": "USER_ACTION",
        }
    if len(candidates) > 1:
        return {
            "classification": "USER_DECISION_ALIAS_COLLISION",
            "alias": alias,
            "candidate_repository_ids": sorted(
                item["repository_id"] for item in candidates
            ),
            "terminal_route": "USER_DECISION",
        }

    repository_record = candidates[0]
    repository_id = str(repository_record["repository_id"])
    selected_lane, available_lanes = _select_lane(registry, repository_id, lane)
    if selected_lane is None and mode != "single-thread":
        return {
            "classification": "USER_DECISION_SUPERVISION_LANE",
            "repository_id": repository_id,
            "candidate_lanes": available_lanes,
            "terminal_route": "USER_DECISION",
        }

    observed_host_alias = str(adapter.get("current_host_alias", "local"))
    current_host = host_by_alias(hosts, observed_host_alias)
    if current_host is None:
        return {
            "classification": "BINDING_REPAIR_HOST",
            "repository_id": repository_id,
            "observed_host_alias": observed_host_alias,
            "terminal_route": None,
            "mode": "binding-repair",
        }

    selected_host = current_host
    selected_artifact: dict[str, Any] | None = None
    if private_artifact_id:
        selected_artifact = private_artifact_record(
            hosts,
            repository_id,
            private_artifact_id,
            preferred_host_id=str(current_host["host_id"]),
        )
        if selected_artifact is None:
            return {
                "classification": "USER_ACTION_BIND_PRIVATE_ARTIFACT",
                "repository_id": repository_id,
                "private_artifact_id": private_artifact_id,
                "terminal_route": "USER_ACTION",
            }
        exact_artifact_host = host_by_alias(
            hosts, str(selected_artifact["host_id"])
        )
        if exact_artifact_host is None:
            return {
                "classification": "BINDING_REPAIR_ARTIFACT_HOST",
                "repository_id": repository_id,
                "private_artifact_id": private_artifact_id,
                "terminal_route": None,
                "mode": "binding-repair",
            }
        selected_host = exact_artifact_host

    host_id = str(selected_host["host_id"])
    selected_lane = selected_lane or "single-thread"
    supervisor_binding = _supervisor_binding_for(
        registry, repository_id, selected_lane
    )
    worker_binding = _worker_binding_for(registry, repository_id, host_id)
    if supervisor_binding is None and mode != "single-thread":
        return {
            "classification": "USER_ACTION_CREATE_OR_BIND_SUPERVISOR_CHAT",
            "repository_id": repository_id,
            "supervision_lane": selected_lane,
            "terminal_route": "USER_ACTION",
            "mode": "binding-repair",
        }
    if worker_binding is None and mode != "single-thread":
        return {
            "classification": "USER_ACTION_CREATE_OR_BIND_WORKER_TASK",
            "repository_id": repository_id,
            "host_id": host_id,
            "terminal_route": "USER_ACTION",
            "mode": "binding-repair",
        }

    effective_worker_binding = worker_binding or {
        "root_hint": selected_host.get("known_repository_roots", {}).get(
            repository_id
        )
    }

    root, root_issues = _verified_root(
        selected_host,
        repository_id,
        effective_worker_binding,
        adapter,
        _aliases(repository_record),
    )
    if root is None:
        return {
            "classification": "BINDING_REPAIR_ROOT",
            "repository_id": repository_id,
            "supervision_lane": selected_lane,
            "host_id": host_id,
            "root_issues": root_issues,
            "terminal_route": None,
            "mode": "binding-repair",
        }

    effects = {name: "not_required" for name in EXTERNAL_EFFECT_NAMES}
    if external_target_host_id and external_target_host_id != host_id:
        effects["transport"] = "pending"
        effects["recipient_open"] = "unverified"

    artifact_verification = "not_requested"
    if selected_artifact is not None:
        if host_id == str(current_host["host_id"]):
            artifact_path = Path(str(selected_artifact["path"]))
            if not artifact_path.is_file():
                return {
                    "classification": "BINDING_REPAIR_PRIVATE_ARTIFACT_MISSING",
                    "repository_id": repository_id,
                    "private_artifact_id": selected_artifact["artifact_id"],
                    "host_id": host_id,
                    "mode": "binding-repair",
                    "terminal_route": None,
                }
            actual_hash = sha256_file(artifact_path)
            if actual_hash != selected_artifact["sha256"]:
                return {
                    "classification": "BINDING_REPAIR_PRIVATE_ARTIFACT_HASH",
                    "repository_id": repository_id,
                    "private_artifact_id": selected_artifact["artifact_id"],
                    "host_id": host_id,
                    "expected_sha256": selected_artifact["sha256"],
                    "actual_sha256": actual_hash,
                    "mode": "binding-repair",
                    "terminal_route": None,
                }
            artifact_verification = "live_path_and_sha256"
        else:
            artifact_verification = "registry_verified_remote_host"

    result: dict[str, Any] = {
        "classification": "RESOLVED",
        "repository_id": repository_id,
        "repository_alias": alias,
        "remote_identity": repository_record.get(
            "remote_identity", repository_id
        ),
        "current_host_id": str(current_host["host_id"]),
        "execution_host_id": host_id,
        "root": root,
        "supervision_lane": selected_lane,
        "mode": mode,
        "in_place": mode == "single-thread",
        "create_supervisor_chat": False,
        "create_worker_task": False,
        "external_effects": effects,
        "terminal_route": None,
    }
    if selected_artifact is not None:
        result["private_artifact"] = {
            "artifact_id": selected_artifact["artifact_id"],
            "host_id": selected_artifact["host_id"],
            "path": selected_artifact["path"],
            "sha256": selected_artifact["sha256"],
            "verification": artifact_verification,
        }

    if mode == "single-thread":
        result.update(
            {
                "supervisor_thread_id": None,
                "worker_task_id": None,
                "bindings_validated": True,
            }
        )
        return result

    assert supervisor_binding is not None
    assert worker_binding is not None

    supervisor_status = supervisor_binding.get(
        "binding_status", "needs_verification"
    )
    supervisor_id = str(supervisor_binding.get("supervisor_thread_id") or "")
    if supervisor_status in {"inactive", "invalid"} or (
        supervisor_status == "needs_verification" and supervisor_id
    ):
        result.update(
            {
                "classification": "BINDING_REPAIR_SUPERVISOR_STATUS",
                "mode": "binding-repair",
                "bindings_validated": False,
                "supervisor_binding_status": supervisor_status,
            }
        )
        return result
    supervisor, supervisor_issues = _exact_thread(
        adapter,
        supervisor_id,
        kind="chatgpt",
        title=str(supervisor_binding.get("expected_supervisor_title", "")),
        project_id=str(supervisor_binding.get("supervisor_project_id", "")),
    )
    if supervisor_issues:
        can_create = bool(
            supervisor_binding.get("allow_create_supervisor_chat")
            and adapter.get("capabilities", {}).get(
                "create_regular_chatgpt_project_chat"
            )
        )
        result.update(
            {
                "classification": (
                    "BINDING_REPAIR_SUPERVISOR"
                    if supervisor_id
                    else "SUPERVISOR_BINDING_MISSING"
                ),
                "mode": "binding-repair",
                "bindings_validated": False,
                "supervisor_issues": supervisor_issues,
                "creation_capability": can_create,
            }
        )
        if not supervisor_id and not can_create:
            result["classification"] = (
                "USER_ACTION_CREATE_OR_BIND_SUPERVISOR_CHAT"
            )
            result["terminal_route"] = "USER_ACTION"
        elif not supervisor_id and can_create:
            result["classification"] = "SUPERVISOR_CREATION_ALLOWED"
            result["create_supervisor_chat"] = True
        return result

    worker_status = worker_binding.get("binding_status", "needs_verification")
    worker_id = str(worker_binding.get("worker_task_id") or "")
    if worker_status in {"inactive", "invalid"} or (
        worker_status == "needs_verification" and worker_id
    ):
        result.update(
            {
                "classification": "BINDING_REPAIR_WORKER_STATUS",
                "mode": "binding-repair",
                "bindings_validated": False,
                "worker_binding_status": worker_status,
            }
        )
        return result
    host_aliases = [
        host_id,
        *selected_host.get("app_host_ids", []),
        *_aliases(selected_host),
    ]
    worker, worker_issues = _exact_thread(
        adapter,
        worker_id,
        kind="codex",
        host_aliases=host_aliases,
    )
    if worker is not None:
        observed_worker_repo = worker.get("repository_id")
        if observed_worker_repo:
            if observed_worker_repo != repository_id:
                worker_issues.append("worker_repository_identity_mismatch")
        else:
            worker_cwd = worker.get("cwd")
            if not worker_cwd:
                worker_issues.append("worker_repository_identity_unverified")
            else:
                try:
                    worker_repo = inspect_repository(str(worker_cwd))
                    if worker_repo["repository_id"] != repository_id:
                        worker_issues.append(
                            "worker_repository_identity_mismatch"
                        )
                except ProtocolError:
                    worker_issues.append("worker_repository_identity_unverified")
    if worker_issues:
        can_create = bool(
            worker_binding.get("allow_create_worker_task")
            and adapter.get("capabilities", {}).get("create_codex_thread")
        )
        result.update(
            {
                "classification": (
                    "BINDING_REPAIR_WORKER"
                    if worker_id
                    else "WORKER_BINDING_MISSING"
                ),
                "mode": "binding-repair",
                "bindings_validated": False,
                "worker_issues": worker_issues,
                "creation_capability": can_create,
            }
        )
        if not worker_id and not can_create:
            result["classification"] = "USER_ACTION_CREATE_OR_BIND_WORKER_TASK"
            result["terminal_route"] = "USER_ACTION"
        elif not worker_id and can_create:
            result["classification"] = "WORKER_CREATION_ALLOWED"
            result["create_worker_task"] = True
        return result

    result.update(
        {
            "supervisor_project_id": supervisor_binding[
                "supervisor_project_id"
            ],
            "supervisor_thread_id": supervisor["id"],
            "expected_supervisor_title": supervisor_binding[
                "expected_supervisor_title"
            ],
            "worker_task_id": worker["id"],
            "bindings_validated": True,
            "supervisor_binding_status": supervisor_status,
            "worker_binding_status": worker_status,
        }
    )
    return result


def _required(mapping: dict[str, Any], keys: Iterable[str], label: str) -> None:
    missing = [key for key in keys if mapping.get(key) in (None, "", [])]
    if missing:
        raise ProtocolError(f"{label} missing required fields: {', '.join(missing)}")


def compile_work_order(
    mission: dict[str, Any], protocol_path: Path | str = PROTOCOL_PATH
) -> dict[str, Any]:
    required = (
        "repository_id",
        "mission_id",
        "attempt_id",
        "authority_revision",
        "active_artifact",
        "canonical_revision",
        "read_scope",
        "write_scope",
        "acceptance_delta",
        "stop_delta",
        "external_effects",
    )
    _required(mission, required, "mission")
    for name, state in mission["external_effects"].items():
        if name not in EXTERNAL_EFFECT_NAMES or state not in EXTERNAL_EFFECT_STATES:
            raise ProtocolError(f"invalid external effect: {name}={state}")

    web_only = mission.get("web_only_resources", [])
    embedded = mission.get("embedded_context", [])
    embedded_ids = {
        item.get("resource_id") for item in embedded if isinstance(item, dict)
    }
    unresolved = [
        item
        for item in web_only
        if isinstance(item, dict)
        and item.get("required")
        and item.get("resource_id") not in embedded_ids
    ]
    if unresolved:
        raise ProtocolError("required Web-only resource is not embedded for Worker")

    protocol = Path(protocol_path)
    protocol_bytes = protocol.read_bytes()
    common_policy_keys = {
        "git_safety_policy",
        "common_protocol",
        "global_prohibited",
        "worker_report_schema",
        "terminal_definitions",
    }
    repeated = sorted(common_policy_keys.intersection(mission))
    if repeated:
        raise ProtocolError(
            "common policy must be referenced, not embedded: " + ", ".join(repeated)
        )

    delta_keys = (
        "repository_id",
        "launch_set_id",
        "mission_id",
        "attempt_id",
        "authority_revision",
        "active_artifact",
        "canonical_revision",
        "exact_base",
        "read_scope",
        "write_scope",
        "private_inputs",
        "preserve_delta",
        "prohibited_delta",
        "acceptance_delta",
        "stop_delta",
        "review_policy",
        "authority_documents",
        "external_effects",
        "embedded_context",
    )
    delta = {key: copy.deepcopy(mission[key]) for key in delta_keys if key in mission}
    packet = {
        "schema_version": SCHEMA_VERSION,
        "packet_type": "SUPERVISOR_WORK_ORDER",
        "protocol_ref": {
            "path": "references/protocol-v2.md",
            "sha256": sha256_bytes(protocol_bytes),
        },
        "mission_delta": delta,
    }
    packet["packet_sha256"] = sha256_text(
        json.dumps(packet, ensure_ascii=False, sort_keys=True)
    )
    return packet


def default_external_effects() -> dict[str, str]:
    return {name: "not_required" for name in EXTERNAL_EFFECT_NAMES}


def default_review_policy() -> dict[str, str]:
    return {
        "gate": "none",
        "depth": "light",
        "stage": "mission",
    }


def normalize_review_policy(value: Any) -> dict[str, str]:
    if value is None:
        return default_review_policy()
    if not isinstance(value, dict):
        raise ProtocolError("review_policy must be an object")
    policy = {
        "gate": str(value.get("gate") or "none").casefold(),
        "depth": str(value.get("depth") or "light").casefold(),
        "stage": str(value.get("stage") or "mission").strip(),
    }
    if policy["gate"] not in REVIEW_GATES:
        raise ProtocolError("review_policy gate must be none or required")
    if policy["depth"] not in REVIEW_DEPTHS:
        raise ProtocolError("review_policy depth must be light, standard, or deep")
    if not policy["stage"]:
        raise ProtocolError("review_policy stage cannot be empty")
    return policy


def _effective_review_policy(
    mission: dict[str, Any], user_packet: dict[str, Any] | None = None
) -> dict[str, str]:
    packet_policy = user_packet.get("review_policy") if user_packet else None
    if packet_policy is not None:
        return normalize_review_policy(packet_policy)
    if mission.get("review_policy") is not None:
        return normalize_review_policy(mission["review_policy"])
    # Compatibility for v2 Missions created before stage review policy existed.
    return {
        "gate": "required",
        "depth": "standard",
        "stage": str((user_packet or {}).get("stage") or "legacy-mission"),
    }


def _review_card_field(card: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = card.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def validate_review_card(card: dict[str, Any], policy: dict[str, str]) -> None:
    if not isinstance(card, dict):
        raise ProtocolError("USER_DECISION requires a review card object")
    required_groups = {
        "purpose": ("purpose",),
        "owner": ("owner",),
        "state": ("state",),
        "artifact identity": ("artifact", "related_artifact"),
        "artifact entrypoint": ("review_entry", "artifact_entrypoint"),
        "review criteria": ("criteria", "decision_points"),
        "reply contract": ("reply_contract", "reply_format"),
        "post-reply behavior": ("post_reply_behavior", "next_move"),
        "non-escalation boundary": (
            "non_escalation_boundary",
            "non_escalation_boundaries",
        ),
    }
    missing = [
        label
        for label, names in required_groups.items()
        if _review_card_field(card, *names) is None
    ]
    if missing:
        raise ProtocolError("review card missing: " + ", ".join(missing))
    criteria = _review_card_field(card, "criteria", "decision_points")
    if not isinstance(criteria, list) or not criteria:
        raise ProtocolError("review card criteria must be a non-empty list")
    minimum_criteria = {"light": 1, "standard": 2, "deep": 3}[policy["depth"]]
    if len(criteria) < minimum_criteria:
        raise ProtocolError(
            f"{policy['depth']} review requires at least "
            f"{minimum_criteria} explicit criteria"
        )
    if policy["depth"] == "deep":
        deep_missing = [
            name
            for name in ("evidence_summary", "risk_if_wrong")
            if _review_card_field(card, name) is None
        ]
        if deep_missing:
            raise ProtocolError(
                "deep review card missing: " + ", ".join(deep_missing)
            )


def validate_user_action_card(card: dict[str, Any]) -> None:
    if not isinstance(card, dict):
        raise ProtocolError("USER_ACTION requires an action card object")
    required = ("purpose", "effect", "requirements", "state", "owner", "next_move")
    missing = [name for name in required if _review_card_field(card, name) is None]
    if missing:
        raise ProtocolError("user action card missing: " + ", ".join(missing))


def new_mission(payload: dict[str, Any]) -> dict[str, Any]:
    mode = payload.get("mode", "coordinator")
    _required(
        payload,
        (
            "repository_id",
            "launch_set_id",
            "mission_id",
            "attempt_id",
            "worker_task_id",
            "host_id",
        ),
        "mission identity",
    )
    if mode != "single-thread":
        _required(
            payload,
            ("supervisor_thread_id", "supervision_lane"),
            "Supervisor routing",
        )
    review_policy = normalize_review_policy(payload.get("review_policy"))
    now = utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "repository_id": payload["repository_id"],
        "launch_set_id": payload["launch_set_id"],
        "mission_id": payload["mission_id"],
        "attempt_id": payload["attempt_id"],
        "worker_task_id": payload["worker_task_id"],
        "host_id": payload["host_id"],
        "supervisor_thread_id": payload.get("supervisor_thread_id"),
        "supervision_lane": payload.get("supervision_lane"),
        "mode": mode,
        "state": "TRIGGERED",
        "mission_status": "pending",
        "review_status": payload.get(
            "review_status",
            "pending" if review_policy["gate"] == "required" else "not_required",
        ),
        "review_policy": review_policy,
        "external_effects": payload.get(
            "external_effects", default_external_effects()
        ),
        "dispatch_keys": [],
        "returned_report_hashes": [],
        "completed_worker_turns": int(payload.get("completed_worker_turns", 0)),
        "safety_ceiling": int(payload.get("safety_ceiling", 8)),
        "events": [{"state": "TRIGGERED", "at": now}],
        "created_at": now,
        "updated_at": now,
    }


def _record_state(
    mission: dict[str, Any], state: str, details: dict[str, Any] | None = None
) -> None:
    mission["state"] = state
    mission["updated_at"] = utc_now()
    event = {"state": state, "at": mission["updated_at"]}
    if details:
        event["details"] = copy.deepcopy(details)
    mission["events"].append(event)


LINEAR_EVENTS = {
    ("TRIGGERED", "repository_resolved"): "REPOSITORY_RESOLVED",
    ("REPOSITORY_RESOLVED", "host_resolved"): "HOST_RESOLVED",
    ("HOST_RESOLVED", "bindings_validated"): "BINDINGS_VALIDATED",
    (
        "BINDINGS_VALIDATED",
        "supervisor_work_order_requested",
    ): "SUPERVISOR_WORK_ORDER_REQUESTED",
    (
        "SUPERVISOR_WORK_ORDER_REQUESTED",
        "work_order_received",
    ): "WORK_ORDER_RECEIVED",
}


def advance_linear(
    mission: dict[str, Any], event: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    target = LINEAR_EVENTS.get((mission["state"], event))
    if not target:
        raise ProtocolError(f"{event} not allowed from {mission['state']}")
    _record_state(mission, target, details)
    return mission


def dispatch_worker(mission: dict[str, Any], work_order_hash: str) -> dict[str, Any]:
    key = (
        f"{mission['launch_set_id']}:{mission['mission_id']}:"
        f"{mission['attempt_id']}"
    )
    if key in mission["dispatch_keys"]:
        raise ProtocolError(f"duplicate dispatch rejected: {key}")
    if mission["state"] != "WORK_ORDER_RECEIVED":
        raise ProtocolError(f"dispatch not allowed from {mission['state']}")
    mission["dispatch_keys"].append(key)
    mission["mission_status"] = "running"
    _record_state(
        mission,
        "WORKER_DISPATCHED",
        {"dispatch_key": key, "work_order_sha256": work_order_hash},
    )
    return mission


def receive_worker_result(
    mission: dict[str, Any], worker_report: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    if mission["state"] != "WORKER_DISPATCHED":
        raise ProtocolError(f"worker result not allowed from {mission['state']}")
    validate_worker_report_packet(worker_report, mission)
    canonical = json.dumps(worker_report, ensure_ascii=False, sort_keys=True)
    report_hash = sha256_text(canonical)
    _record_state(
        mission,
        "WORKER_RESULT_RECEIVED",
        {
            "worker_report_sha256": report_hash,
            "worker_task_id": worker_report["worker_task_id"],
            "host_id": worker_report["host_id"],
            "result_classification": worker_report["result_classification"],
        },
    )
    mission["worker_report_sha256"] = report_hash
    mission["completed_worker_turns"] += 1
    mission["external_effects"] = copy.deepcopy(
        worker_report["external_effect_state"]
    )
    return mission, report_hash


def validate_worker_report_packet(
    worker_report: dict[str, Any], mission: dict[str, Any] | None = None
) -> None:
    if not isinstance(worker_report, dict):
        raise ProtocolError("Worker Report packet must be a JSON object")
    missing = [key for key in WORKER_REPORT_FIELDS if key not in worker_report]
    if missing:
        raise ProtocolError(
            "Worker Report missing required fields: " + ", ".join(missing)
        )
    if not str(worker_report.get("full_worker_report", "")).strip():
        raise ProtocolError("full_worker_report must be non-empty")
    effects = worker_report.get("external_effect_state")
    if not isinstance(effects, dict):
        raise ProtocolError("external_effect_state must be an object")
    for name in EXTERNAL_EFFECT_NAMES:
        if effects.get(name) not in EXTERNAL_EFFECT_STATES:
            raise ProtocolError(f"invalid Worker external effect: {name}")
    if mission is not None:
        for key in (
            "repository_id",
            "mission_id",
            "attempt_id",
            "worker_task_id",
            "host_id",
        ):
            if worker_report.get(key) != mission.get(key):
                raise ProtocolError(f"Worker Report {key} identity mismatch")


def request_adjudication(
    mission: dict[str, Any], report_hash: str
) -> dict[str, Any]:
    if report_hash in mission["returned_report_hashes"]:
        raise ProtocolError(f"duplicate Worker Report return rejected: {report_hash}")
    if mission["state"] != "WORKER_RESULT_RECEIVED":
        raise ProtocolError(
            f"Supervisor adjudication request not allowed from {mission['state']}"
        )
    if mission.get("worker_report_sha256") != report_hash:
        raise ProtocolError("Worker Report hash does not match received result")
    mission["returned_report_hashes"].append(report_hash)
    _record_state(
        mission,
        "SUPERVISOR_ADJUDICATION_REQUESTED",
        {"worker_report_sha256": report_hash},
    )
    return mission


def apply_supervisor_verdict(
    mission: dict[str, Any],
    verdict: str,
    *,
    next_work_order: dict[str, Any] | None = None,
    user_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verdict = verdict.lower()
    allowed = {
        "accept",
        "bounded_repair",
        "reject",
        "continue",
        "complete",
        "user_decision",
        "user_action",
        "blocked",
    }
    if verdict not in allowed:
        raise ProtocolError(f"unknown Supervisor verdict: {verdict}")
    resuming_user_response = mission["state"] == USER_RESPONSE_ADJUDICATION_STATE
    if mission["state"] not in {
        "SUPERVISOR_ADJUDICATION_REQUESTED",
        USER_RESPONSE_ADJUDICATION_STATE,
    }:
        raise ProtocolError(f"verdict not allowed from {mission['state']}")

    continuation_verdicts = {"bounded_repair", "continue"}
    if not resuming_user_response:
        continuation_verdicts.update({"accept", "reject"})
    if verdict in continuation_verdicts:
        if not next_work_order:
            raise ProtocolError(f"{verdict} requires a non-empty next Work Order")
    if verdict in {"user_decision", "user_action"} and not user_packet:
        raise ProtocolError(f"{verdict} requires a non-empty user packet")
    if verdict == "blocked":
        validate_blocked_contract(user_packet)
    if verdict == "user_decision":
        review_policy = _effective_review_policy(mission, user_packet)
        if review_policy["gate"] != "required":
            raise ProtocolError("USER_DECISION requires review_policy gate=required")
        validate_review_card(user_packet or {}, review_policy)
        mission["review_policy"] = review_policy
        mission["review_status"] = "pending"
    elif verdict == "user_action":
        validate_user_action_card(user_packet or {})

    _record_state(
        mission,
        "SUPERVISOR_VERDICT_RECEIVED",
        {"verdict": verdict},
    )
    mission["supervisor_verdict"] = verdict

    if resuming_user_response:
        mission.pop("user_packet", None)
        if verdict == "accept":
            mission["review_status"] = "accepted"
            mission["mission_status"] = "complete"
            _record_state(mission, "COMPLETE", {"verdict": verdict})
            return mission
        if verdict == "reject" and not next_work_order:
            mission["review_status"] = "rejected"
            mission["mission_status"] = "rejected"
            _record_state(mission, "COMPLETE", {"verdict": verdict})
            return mission
        if verdict in {"bounded_repair", "reject", "continue"}:
            if verdict == "bounded_repair":
                mission["review_status"] = "bounded_repair"
                mission["mission_status"] = "running"
            elif verdict == "reject":
                mission["review_status"] = "rejected"
                mission["mission_status"] = "superseded"
            else:
                mission["mission_status"] = "running"
            mission["next_work_order"] = copy.deepcopy(next_work_order)
            if mission["completed_worker_turns"] >= mission["safety_ceiling"]:
                _record_state(
                    mission,
                    "SAFETY_CEILING",
                    {
                        "verdict": verdict,
                        "completed_worker_turns": mission[
                            "completed_worker_turns"
                        ],
                        "safety_ceiling": mission["safety_ceiling"],
                    },
                )
            else:
                _record_state(mission, "CONTINUE", {"verdict": verdict})
            return mission
        terminal = verdict.upper()
        if terminal == "COMPLETE":
            mission["mission_status"] = "complete"
        elif terminal == "BLOCKED":
            mission["mission_status"] = "blocked"
            if user_packet:
                mission["blocked_contract"] = copy.deepcopy(user_packet)
        if terminal in {"USER_DECISION", "USER_ACTION"}:
            mission["user_packet"] = copy.deepcopy(user_packet)
        _record_state(mission, terminal, {"verdict": verdict})
        return mission

    if verdict in {"accept", "bounded_repair", "reject", "continue"}:
        if verdict == "accept":
            mission["review_status"] = "accepted"
            mission["mission_status"] = "complete"
        elif verdict == "bounded_repair":
            mission["review_status"] = "bounded_repair"
            mission["mission_status"] = "running"
        elif verdict == "reject":
            mission["review_status"] = "rejected"
            mission["mission_status"] = "superseded"
        else:
            mission["review_status"] = "pending"
            mission["mission_status"] = "running"
        mission["next_work_order"] = copy.deepcopy(next_work_order)
        if mission["completed_worker_turns"] >= mission["safety_ceiling"]:
            _record_state(
                mission,
                "SAFETY_CEILING",
                {
                    "verdict": verdict,
                    "completed_worker_turns": mission[
                        "completed_worker_turns"
                    ],
                    "safety_ceiling": mission["safety_ceiling"],
                },
            )
        else:
            _record_state(mission, "CONTINUE", {"verdict": verdict})
        return mission

    terminal = verdict.upper()
    if terminal == "COMPLETE":
        mission["mission_status"] = "complete"
    elif terminal == "BLOCKED":
        mission["mission_status"] = "blocked"
        if user_packet:
            mission["blocked_contract"] = copy.deepcopy(user_packet)
    if terminal in {"USER_DECISION", "USER_ACTION"}:
        mission["user_packet"] = copy.deepcopy(user_packet)
    _record_state(mission, terminal, {"verdict": verdict})
    return mission


def start_successor(
    prior: dict[str, Any], successor_payload: dict[str, Any]
) -> dict[str, Any]:
    if not (
        prior.get("review_status") == "rejected"
        and prior.get("mission_status") == "superseded"
        and prior.get("state") == "CONTINUE"
    ):
        raise ProtocolError("successor requires rejected + superseded prior Mission")
    return start_continuation(prior, successor_payload)


def start_continuation(
    prior: dict[str, Any], next_payload: dict[str, Any]
) -> dict[str, Any]:
    if prior.get("state") != "CONTINUE":
        raise ProtocolError("continuation requires prior CONTINUE state")
    identity = (
        next_payload.get("mission_id"),
        next_payload.get("attempt_id"),
    )
    if identity == (prior.get("mission_id"), prior.get("attempt_id")):
        raise ProtocolError(
            "continuation must use a new mission_id or attempt_id"
        )
    payload = {
        "repository_id": prior["repository_id"],
        "launch_set_id": prior["launch_set_id"],
        "mission_id": next_payload.get("mission_id"),
        "attempt_id": next_payload.get("attempt_id"),
        "worker_task_id": next_payload.get(
            "worker_task_id", prior["worker_task_id"]
        ),
        "host_id": next_payload.get("host_id", prior["host_id"]),
        "supervisor_thread_id": next_payload.get(
            "supervisor_thread_id", prior["supervisor_thread_id"]
        ),
        "supervision_lane": next_payload.get(
            "supervision_lane", prior["supervision_lane"]
        ),
        "mode": prior.get("mode", "coordinator"),
        "completed_worker_turns": prior["completed_worker_turns"],
        "safety_ceiling": prior["safety_ceiling"],
        "external_effects": next_payload.get(
            "external_effects", prior["external_effects"]
        ),
    }
    return new_mission(payload)


def validate_mission(mission: dict[str, Any]) -> None:
    if mission.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError("unsupported Mission schema")
    if mission.get("mission_status") not in MISSION_STATUSES:
        raise ProtocolError("invalid mission_status")
    if mission.get("review_status") not in REVIEW_STATUSES:
        raise ProtocolError("invalid review_status")
    if mission.get("review_policy") is not None:
        normalize_review_policy(mission["review_policy"])
    _required(
        mission,
        (
            "worker_task_id",
            "host_id",
            "completed_worker_turns",
            "safety_ceiling",
        ),
        "Mission routing",
    )
    if mission.get("mode") != "single-thread":
        _required(
            mission,
            ("supervisor_thread_id", "supervision_lane"),
            "Mission Supervisor routing",
        )
    if mission["completed_worker_turns"] < 0 or mission["safety_ceiling"] < 1:
        raise ProtocolError("invalid Mission turn counter or safety ceiling")
    effects = mission.get("external_effects", {})
    for name in EXTERNAL_EFFECT_NAMES:
        if effects.get(name) not in EXTERNAL_EFFECT_STATES:
            raise ProtocolError(f"invalid or missing external effect: {name}")
    if mission.get("state") == "WORKER_RESULT_RECEIVED" and mission.get(
        "next_work_order"
    ):
        raise ProtocolError("next Work Order cannot exist before adjudication")
    if mission.get("state") == USER_RESPONSE_ADJUDICATION_STATE:
        if not mission.get("last_routed_user_response_id"):
            raise ProtocolError(
                "user-response adjudication requires a routed response identity"
            )
    if mission.get("state") == "BLOCKED" and mission.get(
        "blocked_contract"
    ) is not None:
        validate_blocked_contract(mission["blocked_contract"])
    validate_blocked_contract_history(mission)


def _notification_key(mission: dict[str, Any]) -> str:
    return "|".join(
        str(mission[key])
        for key in ("repository_id", "mission_id", "attempt_id", "state")
    )


def _safe_fragment(value: str) -> str:
    fragment = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return fragment[:80] or "item"


def powershell_notifier(packet_path: Path, packet: dict[str, Any]) -> bool:
    helper = SKILL_ROOT / "scripts" / "notify_terminal.ps1"
    executable = "powershell.exe" if os.name == "nt" else "pwsh"
    completed = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-Title",
            f"supervise-repo-loop: {packet['terminal_state']}",
            "-Body",
            f"{packet['repository_id']} / {packet['mission_id']}",
            "-PacketPath",
            str(packet_path),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    return completed.returncode == 0


def emit_terminal_packet(
    mission: dict[str, Any],
    *,
    terminal_dir: Path | str = DEFAULT_TERMINALS,
    ledger_path: Path | str = DEFAULT_NOTIFICATION_LEDGER,
    notifier: Callable[[Path, dict[str, Any]], bool] | None = powershell_notifier,
    dry_run: bool = False,
) -> dict[str, Any]:
    if mission.get("state") not in TERMINAL_STATES:
        raise ProtocolError(f"not a terminal Mission state: {mission.get('state')}")
    key = _notification_key(mission)
    ledger_file = Path(ledger_path)
    ledger = (
        load_json(ledger_file)
        if ledger_file.exists()
        else {"schema_version": SCHEMA_VERSION, "notifications": {}}
    )
    existing = ledger["notifications"].get(key)
    if existing:
        return {
            "deduplicated": True,
            "notification_key": key,
            "packet_path": existing["packet_path"],
            "notification_status": existing["notification_status"],
        }

    packet = {
        "schema_version": SCHEMA_VERSION,
        "repository_id": mission["repository_id"],
        "launch_set_id": mission["launch_set_id"],
        "mission_id": mission["mission_id"],
        "attempt_id": mission["attempt_id"],
        "supervisor_thread_id": mission.get("supervisor_thread_id"),
        "supervision_lane": mission.get("supervision_lane"),
        "worker_task_id": mission["worker_task_id"],
        "host_id": mission["host_id"],
        "terminal_state": mission["state"],
        "mission_status": mission["mission_status"],
        "review_status": mission["review_status"],
        "external_effects": copy.deepcopy(mission["external_effects"]),
        "supervisor_verdict": mission.get("supervisor_verdict"),
        "user_packet": copy.deepcopy(mission.get("user_packet")),
        "blocked_contract": copy.deepcopy(mission.get("blocked_contract")),
        "created_at": utc_now(),
    }
    repo_tag = sha256_text(mission["repository_id"])[:12]
    filename = (
        f"{repo_tag}__{_safe_fragment(str(mission['mission_id']))}__"
        f"{_safe_fragment(str(mission['attempt_id']))}__{mission['state']}.json"
    )
    packet_path = Path(terminal_dir) / filename
    atomic_write_json(packet_path, packet)

    if dry_run:
        notification_status = "dry_run"
    elif notifier is None:
        notification_status = "not_attempted"
    else:
        try:
            notification_status = (
                "sent" if notifier(packet_path, packet) else "failed"
            )
        except Exception:
            notification_status = "failed"

    ledger["notifications"][key] = {
        "packet_path": str(packet_path),
        "notification_status": notification_status,
        "recorded_at": utc_now(),
    }
    atomic_write_json(ledger_file, ledger)
    return {
        "deduplicated": False,
        "notification_key": key,
        "packet_path": str(packet_path),
        "notification_status": notification_status,
    }


def migrate_legacy_registry(
    legacy: dict[str, Any], *, observed_at: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    observed_at = observed_at or utc_now()
    repositories: dict[str, dict[str, Any]] = {}
    supervisor_bindings: dict[tuple[str, str], dict[str, Any]] = {}
    worker_bindings: dict[tuple[str, str], dict[str, Any]] = {}
    for old in legacy.get("bindings", []):
        if not isinstance(old, dict):
            continue
        remote = old.get("remote_identity") or old.get("remote")
        repository_id = normalize_remote(remote) if remote else None
        aliases = list(
            dict.fromkeys(
                [
                    str(old.get("project_name", "")).strip(),
                    *[str(x) for x in old.get("aliases", [])],
                ]
            )
        )
        aliases = [alias for alias in aliases if alias]
        migrated_repository_id = (
            repository_id or f"legacy-unknown:{uuid.uuid4()}"
        )
        repository_record = {
            "schema_version": SCHEMA_VERSION,
            "repository_id": migrated_repository_id,
            "aliases": aliases,
            "default_supervision_lane": old.get(
                "default_supervision_lane", old.get("supervision_lane", "development")
            ),
            "remote_identity": migrated_repository_id,
            "migration": {
                "source_schema_version": legacy.get("schema_version", 1),
                "migrated_at": observed_at,
                "legacy_record": copy.deepcopy(old),
            },
        }
        repo_id = repository_record["repository_id"]
        repositories.setdefault(repo_id, repository_record)
        lane = old.get("supervision_lane", "development")
        supervisor_record = {
            "schema_version": SCHEMA_VERSION,
            "repository_id": repo_id,
            "supervision_lane": lane,
            "supervisor_project_id": old.get("supervisor_project_id"),
            "supervisor_thread_id": old.get("supervisor_thread_id"),
            "expected_supervisor_title": old.get("expected_supervisor_title"),
            "last_verified_at": old.get("last_verified_at"),
            "binding_status": "needs_verification",
            "allow_create_supervisor_chat": bool(
                old.get("allow_create_supervisor_chat", False)
            ),
            "migration": {
                "source_schema_version": legacy.get("schema_version", 1),
                "migrated_at": observed_at,
                "legacy_record": copy.deepcopy(old),
            },
        }
        supervisor_bindings.setdefault((repo_id, str(lane)), supervisor_record)
        host_id = old.get("host_id") or "legacy-host-needs-verification"
        worker_record = {
            "schema_version": SCHEMA_VERSION,
            "repository_id": repo_id,
            "worker_task_id": old.get("worker_task_id")
            or old.get("worker_thread_id"),
            "host_id": host_id,
            "root_hint": old.get("root_hint") or old.get("project_root"),
            "last_verified_at": old.get("last_verified_at"),
            "binding_status": "needs_verification",
            "allow_create_worker_task": bool(
                old.get("allow_create_worker_task", False)
            ),
            "migration": {
                "source_schema_version": legacy.get("schema_version", 1),
                "migrated_at": observed_at,
                "legacy_record": copy.deepcopy(old),
            },
        }
        worker_bindings.setdefault((repo_id, str(host_id)), worker_record)
    registry = {
        "schema_version": SCHEMA_VERSION,
        "repositories": list(repositories.values()),
        "supervisor_bindings": list(supervisor_bindings.values()),
        "worker_bindings": list(worker_bindings.values()),
        "migration": {
            "source_schema_version": legacy.get("schema_version", 1),
            "migrated_at": observed_at,
            "legacy_record_count": len(legacy.get("bindings", [])),
        },
    }
    report = {
        "classification": "MIGRATED_NONDESTRUCTIVELY",
        "input_records": len(legacy.get("bindings", [])),
        "repository_records": len(repositories),
        "supervisor_records": len(supervisor_bindings),
        "worker_records": len(worker_bindings),
        "needs_verification": len(supervisor_bindings) + len(worker_bindings),
        "legacy_payloads_preserved": all(
            "legacy_record" in item["migration"]
            for item in (
                list(repositories.values())
                + list(supervisor_bindings.values())
                + list(worker_bindings.values())
            )
        ),
    }
    return registry, report


def migrate_coordinator_ux(
    registry: dict[str, Any],
    coordinator_state: dict[str, Any] | None = None,
    *,
    migrated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    migrated_at = migrated_at or utc_now()
    migrated_registry = copy.deepcopy(registry)
    migrated_registry.setdefault("coordinator_policy", {}).setdefault(
        "allow_create_worker_task", True
    )
    migrated_registry["coordinator_policy"].setdefault(
        "allow_create_supervisor_chat", False
    )
    migrated_registry["coordinator_policy"].update(
        {
            "user_visible_codex_entry_points": USER_VISIBLE_CODEX_ENTRY_POINTS,
            "mission_specific_worker_creation": False,
            "user_input_surface": "coordinator",
        }
    )
    for index, repository in enumerate(
        migrated_registry.get("repositories", [])
    ):
        if not isinstance(repository, dict):
            continue
        repository.setdefault("stable_order", index)
        repository.setdefault("allow_request_next_mission", True)
    migrated_registry.setdefault("migration", {})[
        "coordinator_only_ux_v2"
    ] = {
        "classification": "NONDESTRUCTIVE_BINDING_PRESERVING_MIGRATION",
        "migrated_at": migrated_at,
        "existing_repository_records_preserved": len(
            migrated_registry.get("repositories", [])
        ),
        "existing_supervisor_bindings_preserved": len(
            migrated_registry.get("supervisor_bindings", [])
        ),
        "existing_worker_bindings_preserved": len(
            migrated_registry.get("worker_bindings", [])
        ),
    }

    prior_state = copy.deepcopy(coordinator_state or {})
    migrated_state = prior_state
    migrated_state.update(
        {
            "schema_version": SCHEMA_VERSION,
            "contract": "global-coordinator-only-v2",
            "user_visible_codex_entry_points": USER_VISIBLE_CODEX_ENTRY_POINTS,
            "normal_user_input_surface": "coordinator",
            "standard_prompts": {
                "this_repository": COORDINATOR_PROMPT_THIS_REPOSITORY,
                "next_actionable_registered_repository": (
                    COORDINATOR_PROMPT_NEXT_ACTIONABLE
                ),
            },
        }
    )
    migrated_state.setdefault("active_repository_selector", None)
    migrated_state.setdefault("pending_repository_ids", [])
    migrated_state.setdefault("pending_user_responses", [])
    migrated_state.setdefault("authorized_runtime_actions", [])
    presentation_policy = migrated_state.setdefault("presentation_policy", {})
    if not isinstance(presentation_policy, dict):
        raise ProtocolError("presentation_policy must be an object")
    if presentation_policy.get("automation_arm_condition") in {
        None,
        "external_active_claim_or_exact_outbound_wait",
        "external_scheduler_claim_or_route_leases",
    }:
        presentation_policy["automation_arm_condition"] = (
            "external_route_or_recovery_owned_runtime_phase"
        )
    migrated_state.setdefault("coordinator_task", {})
    migrated_state["coordinator_task"].setdefault("scope", "all_repositories")
    migrated_state["coordinator_task"].setdefault("task_id", None)
    migrated_state["coordinator_task"].setdefault("binding_status", "active")
    migrated_state["migration"] = {
        "classification": "COORDINATOR_ONLY_UX_ACTIVATED",
        "migrated_at": migrated_at,
        "legacy_state_preserved": bool(coordinator_state),
        "bindings_preserved": True,
    }
    report = {
        "classification": "COORDINATOR_UX_MIGRATION_PASS",
        "schema_version": SCHEMA_VERSION,
        "migrated_at": migrated_at,
        "binding_counts": {
            "repositories": len(migrated_registry.get("repositories", [])),
            "supervisors": len(
                migrated_registry.get("supervisor_bindings", [])
            ),
            "workers": len(migrated_registry.get("worker_bindings", [])),
        },
        "legacy_payload_loss": False,
        "user_visible_codex_entry_points": USER_VISIBLE_CODEX_ENTRY_POINTS,
        "coordinator_only_user_input": True,
        "change_management": [
            {
                "item": "user-visible Codex threads",
                "before": "repository-scoped manual entry points",
                "after": "one global Coordinator",
                "migration": "preserve internal bindings and expose one entry point",
                "rollback_condition": "Coordinator cannot route a terminal packet",
            },
            {
                "item": "internal Worker tasks",
                "before": "persistent tasks could be presented to the user",
                "after": "persistent repository-by-host backend resources",
                "migration": "preserve and auto-bind exact existing tasks",
                "rollback_condition": "exact repository or host identity cannot be verified",
            },
            {
                "item": "Supervisor interaction",
                "before": "manual bootstrap and relay could be required",
                "after": "Coordinator sends and receives all Supervisor packets",
                "migration": "reuse exact repository-by-lane binding",
                "rollback_condition": "exact Supervisor endpoint is invalid",
            },
            {
                "item": "Worker interaction",
                "before": "manual bootstrap and relay could be required",
                "after": "Coordinator dispatches and receives all Worker packets",
                "migration": "reuse or create one persistent Worker per host",
                "rollback_condition": "Worker identity cannot be verified",
            },
            {
                "item": "repository addressing",
                "before": "repository aliases could be typed by the user",
                "after": "context remote identity or stable global queue",
                "migration": "retain aliases only as an internal compatibility path",
                "rollback_condition": "context resolves to more than one remote identity",
            },
            {
                "item": "standard Prompt",
                "before": "repository-specific prompt rewriting",
                "after": "two fixed generic prompts",
                "migration": "replace UI examples and default prompt",
                "rollback_condition": "generic context resolution is unavailable",
            },
            {
                "item": "first-run bootstrap",
                "before": "user-authored endpoint bootstrap",
                "after": "Coordinator-owned endpoint handshake",
                "migration": "bootstrap automatically after exact creation",
                "rollback_condition": "handshake readback fails",
            },
            {
                "item": "Worker creation",
                "before": "manual task creation could be required",
                "after": "create once only when no valid candidate exists",
                "migration": "capability and policy gated auto-creation",
                "rollback_condition": "creation capability or policy is absent",
            },
            {
                "item": "user response routing",
                "before": "manual Worker or Supervisor relay",
                "after": "Coordinator to exact Supervisor only",
                "migration": "normalize freeform response into USER_RESPONSE",
                "rollback_condition": "Mission or Supervisor identity is missing",
            },
            {
                "item": "documentation",
                "before": "repository-specific and manual-relay examples",
                "after": "one short Coordinator operation document",
                "migration": "remove replacement and relay instructions",
                "rollback_condition": "documentation contract test fails",
            },
            {
                "item": "backward compatibility",
                "before": "alias-driven internal resolution",
                "after": "generic path is standard; alias path remains internal",
                "migration": "preserve schema v2 records and hidden legacy option",
                "rollback_condition": "existing binding validation regresses",
            },
        ],
    }
    return migrated_registry, migrated_state, report


def validate_coordinator_state(state: dict[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError("unsupported Coordinator state schema")
    if state.get("contract") != "global-coordinator-only-v2":
        raise ProtocolError("unsupported Coordinator user contract")
    if state.get("user_visible_codex_entry_points") != 1:
        raise ProtocolError("exactly one user-visible Codex entry point is required")
    if state.get("normal_user_input_surface") != "coordinator":
        raise ProtocolError("normal user input must route to the Coordinator")
    prompts = state.get("standard_prompts", {})
    if prompts.get("this_repository") != COORDINATOR_PROMPT_THIS_REPOSITORY:
        raise ProtocolError("this-repository prompt drift")
    if (
        prompts.get("next_actionable_registered_repository")
        != COORDINATOR_PROMPT_NEXT_ACTIONABLE
    ):
        raise ProtocolError("next-actionable prompt drift")
    pending = state.get("pending_user_responses", [])
    if not isinstance(pending, list):
        raise ProtocolError("pending_user_responses must be a list")
    seen_response_keys: set[tuple[str, str, str]] = set()
    for index, item in enumerate(pending):
        if not isinstance(item, dict):
            raise ProtocolError(f"pending_user_responses[{index}] must be an object")
        key = (
            str(item.get("repository_id") or ""),
            str(item.get("mission_id") or ""),
            str(item.get("attempt_id") or ""),
        )
        if not all(key):
            raise ProtocolError(
                f"pending_user_responses[{index}] has incomplete Mission identity"
            )
        if key in seen_response_keys:
            raise ProtocolError(f"duplicate pending USER_RESPONSE identity: {key}")
        seen_response_keys.add(key)
    pending_events = state.get("pending_user_events", [])
    routed_events = state.get("routed_user_events", [])
    if not isinstance(pending_events, list) or not isinstance(routed_events, list):
        raise ProtocolError("Coordinator event ledgers must be lists")
    seen_event_ids: set[str] = set()
    for label, items in (
        ("pending_user_events", pending_events),
        ("routed_user_events", routed_events),
    ):
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ProtocolError(f"{label}[{index}] must be an object")
            event_id = str(item.get("event_id") or "")
            if not event_id or event_id in seen_event_ids:
                raise ProtocolError(f"invalid or duplicate Coordinator event: {event_id}")
            if item.get("kind") not in {"direction_update", "project_question"}:
                raise ProtocolError(f"{label}[{index}] has invalid kind")
            if not str(item.get("repository_id") or ""):
                raise ProtocolError(f"{label}[{index}] requires repository_id")
            seen_event_ids.add(event_id)
    runtime_actions = state.get("authorized_runtime_actions", [])
    if not isinstance(runtime_actions, list):
        raise ProtocolError("authorized_runtime_actions must be a list")
    seen_runtime_action_ids: set[str] = set()
    seen_runtime_identity_hashes: set[str] = set()
    for index, item in enumerate(runtime_actions):
        try:
            validate_authorized_runtime_action(item)
        except ProtocolError as exc:
            raise ProtocolError(
                f"authorized_runtime_actions[{index}]: {exc}"
            ) from exc
        action_id = str(item["runtime_action_id"])
        identity_sha = str(item["identity_sha256"])
        if action_id in seen_runtime_action_ids:
            raise ProtocolError(f"duplicate authorized runtime action: {action_id}")
        if identity_sha in seen_runtime_identity_hashes:
            raise ProtocolError(
                f"duplicate authorized runtime identity: {identity_sha}"
            )
        seen_runtime_action_ids.add(action_id)
        seen_runtime_identity_hashes.add(identity_sha)
    presentation = state.get("presentation_policy", {})
    if presentation.get("global_completion_barrier") is True:
        raise ProtocolError("global completion barrier is forbidden")
    if presentation.get("review_gate_scope") not in {None, "mission"}:
        raise ProtocolError("review gates must be scoped to one Mission")
    if presentation.get("all_terminal_stops_coordinator") is True:
        raise ProtocolError("current Mission terminals cannot stop the Coordinator")
    if presentation.get("ongoing_rearm") is True:
        raise ProtocolError("idle periodic Coordinator rearm is forbidden")
    if presentation.get("automation_idle_policy") not in {None, "paused"}:
        raise ProtocolError("idle recovery automation must remain paused")
    if presentation.get("automation_arm_condition") not in {
        None,
        "external_active_claim_or_exact_outbound_wait",
        "external_scheduler_claim_or_route_leases",
        "external_route_or_recovery_owned_runtime_phase",
    }:
        raise ProtocolError("recovery automation has an unsafe arm condition")
    if presentation.get("cycle_mode") not in {None, "event_driven_drain"}:
        raise ProtocolError("Coordinator cycle_mode must drain in-flight exact routes")
    if presentation.get("heartbeat_role") not in {None, "recovery_watchdog"}:
        raise ProtocolError("heartbeat must be a recovery watchdog")
    if presentation.get("wait_for_inflight_exact_target") is False:
        raise ProtocolError("in-flight exact routes must be awaited")
    if presentation.get("checkpoint_with_inflight") not in {None, False}:
        raise ProtocolError(
            "unconditional checkpoint_with_inflight is forbidden; only "
            "plan.checkpoint_after_wait_allowed may authorize a bounded-wait "
            "foreground handoff"
        )


def validate_registry(registry: dict[str, Any], hosts: dict[str, Any]) -> None:
    if registry.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError("unsupported binding registry schema")
    repository_required = (
        "schema_version",
        "repository_id",
        "aliases",
        "default_supervision_lane",
        "remote_identity",
    )
    supervisor_required = (
        "schema_version",
        "repository_id",
        "supervision_lane",
        "supervisor_project_id",
        "supervisor_thread_id",
        "expected_supervisor_title",
        "last_verified_at",
        "binding_status",
        "allow_create_supervisor_chat",
    )
    worker_required = (
        "schema_version",
        "repository_id",
        "worker_task_id",
        "host_id",
        "root_hint",
        "last_verified_at",
        "binding_status",
        "allow_create_worker_task",
    )
    repositories = registry.get("repositories", [])
    repository_ids: set[str] = set()
    for index, repository in enumerate(repositories):
        _required(repository, repository_required, f"repository[{index}]")
        repo_id = repository["repository_id"]
        if repo_id in repository_ids:
            raise ProtocolError(f"duplicate repository identity: {repo_id}")
        if repository["remote_identity"] != repo_id:
            raise ProtocolError(f"repository[{index}] remote identity drift")
        repository_ids.add(repo_id)

    known_hosts = {
        item.get("host_id")
        for item in hosts.get("hosts", [])
        if isinstance(item, dict)
    }
    statuses = {"active", "needs_verification", "inactive", "invalid"}
    supervisor_keys: set[tuple[str, str]] = set()
    for index, binding in enumerate(registry.get("supervisor_bindings", [])):
        missing_keys = [key for key in supervisor_required if key not in binding]
        if missing_keys:
            raise ProtocolError(
                f"supervisor_binding[{index}] missing keys: {missing_keys}"
            )
        if binding["repository_id"] not in repository_ids:
            raise ProtocolError(
                f"supervisor_binding[{index}] references unknown repository"
            )
        if binding["binding_status"] not in statuses:
            raise ProtocolError(f"supervisor_binding[{index}] invalid status")
        if binding["binding_status"] == "active":
            _required(
                binding,
                (
                    "supervisor_project_id",
                    "supervisor_thread_id",
                    "expected_supervisor_title",
                    "last_verified_at",
                ),
                f"active supervisor_binding[{index}]",
            )
        key = (binding["repository_id"], binding["supervision_lane"])
        if key in supervisor_keys:
            raise ProtocolError(f"duplicate Supervisor binding: {key}")
        supervisor_keys.add(key)
    for repository in repositories:
        default_key = (
            repository["repository_id"],
            repository["default_supervision_lane"],
        )
        if default_key not in supervisor_keys:
            raise ProtocolError(
                f"default supervision lane has no binding: {default_key}"
            )

    worker_keys: set[tuple[str, str]] = set()
    for index, binding in enumerate(registry.get("worker_bindings", [])):
        missing_keys = [key for key in worker_required if key not in binding]
        if missing_keys:
            raise ProtocolError(
                f"worker_binding[{index}] missing keys: {missing_keys}"
            )
        if binding["repository_id"] not in repository_ids:
            raise ProtocolError(
                f"worker_binding[{index}] references unknown repository"
            )
        if binding["host_id"] not in known_hosts:
            raise ProtocolError(
                f"worker_binding[{index}] references unknown host"
            )
        if binding["binding_status"] not in statuses:
            raise ProtocolError(f"worker_binding[{index}] invalid status")
        if binding["binding_status"] == "active":
            _required(
                binding,
                ("worker_task_id", "root_hint", "last_verified_at"),
                f"active worker_binding[{index}]",
            )
        key = (binding["repository_id"], binding["host_id"])
        if key in worker_keys:
            raise ProtocolError(f"duplicate Worker binding: {key}")
        worker_keys.add(key)

    artifact_keys: set[tuple[str, str, str]] = set()
    artifact_statuses = {
        "verified",
        "needs_verification",
        "missing",
        "invalid",
    }
    for index, artifact in enumerate(hosts.get("private_artifacts", [])):
        required_artifact_keys = (
            "artifact_id",
            "repository_id",
            "host_id",
            "path",
            "sha256",
            "status",
            "last_verified_at",
        )
        missing_keys = [
            key for key in required_artifact_keys if key not in artifact
        ]
        if missing_keys:
            raise ProtocolError(
                f"private_artifact[{index}] missing keys: {missing_keys}"
            )
        if artifact["repository_id"] not in repository_ids:
            raise ProtocolError(
                f"private_artifact[{index}] references unknown repository"
            )
        if artifact["host_id"] not in known_hosts:
            raise ProtocolError(
                f"private_artifact[{index}] references unknown host"
            )
        if artifact["status"] not in artifact_statuses:
            raise ProtocolError(f"private_artifact[{index}] invalid status")
        if artifact["status"] == "verified":
            _required(
                artifact,
                ("path", "sha256", "last_verified_at"),
                f"verified private_artifact[{index}]",
            )
            if not re.fullmatch(r"[0-9a-f]{64}", str(artifact["sha256"])):
                raise ProtocolError(
                    f"private_artifact[{index}] invalid sha256"
                )
        key = (
            artifact["repository_id"],
            artifact["artifact_id"],
            artifact["host_id"],
        )
        if key in artifact_keys:
            raise ProtocolError(f"duplicate private artifact record: {key}")
        artifact_keys.add(key)


def _json_stdout(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _mission_path(missions_dir: Path, mission: dict[str, Any]) -> Path:
    identity = "|".join(
        str(mission[key]) for key in ("repository_id", "mission_id", "attempt_id")
    )
    return missions_dir / f"{sha256_text(identity)[:20]}.json"


def load_missions(missions_dir: Path | str) -> list[dict[str, Any]]:
    directory = Path(missions_dir)
    if not directory.exists():
        return []
    missions: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        missions.append(load_json(path))
    return missions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Coordinator prompts:\n"
            f"  {COORDINATOR_PROMPT_THIS_REPOSITORY}\n"
            f"  {COORDINATOR_PROMPT_NEXT_ACTIONABLE}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    normalize = sub.add_parser("normalize-remote")
    normalize.add_argument("remote")

    resolve = sub.add_parser("resolve")
    resolve.add_argument("--alias", help=argparse.SUPPRESS)
    resolve.add_argument(
        "--target",
        choices=[
            "this-repository",
            "next-actionable-registered-repository",
        ],
        default="this-repository",
    )
    resolve.add_argument("--mode", choices=sorted(MODES), default="coordinator")
    resolve.add_argument("--lane")
    resolve.add_argument("--private-artifact-id")
    resolve.add_argument("--external-target-host-id")
    resolve.add_argument("--registry", type=Path, default=DEFAULT_BINDINGS)
    resolve.add_argument("--hosts", type=Path, default=DEFAULT_HOSTS)
    resolve.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    resolve.add_argument("--context", type=Path)
    resolve.add_argument(
        "--coordinator-state",
        type=Path,
        default=DEFAULT_COORDINATOR_STATE,
    )
    resolve.add_argument("--missions-dir", type=Path, default=DEFAULT_MISSIONS)
    resolve.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    resolve.add_argument("--dry-run", action="store_true")

    sub.add_parser("contract")

    compile_parser = sub.add_parser("compile-work-order")
    compile_parser.add_argument("--mission", required=True, type=Path)
    compile_parser.add_argument("--output", required=True, type=Path)

    migrate = sub.add_parser("migrate")
    migrate.add_argument("--legacy", required=True, type=Path)
    migrate.add_argument("--output", required=True, type=Path)
    migrate.add_argument("--report", required=True, type=Path)

    migrate_ux = sub.add_parser("migrate-coordinator-ux")
    migrate_ux.add_argument("--registry", type=Path, default=DEFAULT_BINDINGS)
    migrate_ux.add_argument(
        "--coordinator-state",
        type=Path,
        default=DEFAULT_COORDINATOR_STATE,
    )
    migrate_ux.add_argument(
        "--report",
        type=Path,
        default=SKILL_ROOT / "state" / "migration-readback.v2.json",
    )
    migrate_ux.add_argument("--dry-run", action="store_true")

    migrate_scheduler = sub.add_parser("migrate-scheduler-state")
    migrate_scheduler.add_argument(
        "--input", type=Path, default=LEGACY_SCHEDULER_STATE
    )
    migrate_scheduler.add_argument(
        "--output", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    migrate_scheduler.add_argument("--dry-run", action="store_true")

    validate = sub.add_parser("validate")
    validate.add_argument("--registry", type=Path, default=DEFAULT_BINDINGS)
    validate.add_argument("--hosts", type=Path, default=DEFAULT_HOSTS)
    validate.add_argument(
        "--coordinator-state",
        type=Path,
        default=DEFAULT_COORDINATOR_STATE,
    )
    validate.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    validate.add_argument("--missions-dir", type=Path, default=DEFAULT_MISSIONS)
    validate.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )

    mission_init = sub.add_parser("mission-init")
    mission_init.add_argument("--payload", required=True, type=Path)
    mission_init.add_argument("--missions-dir", type=Path, default=DEFAULT_MISSIONS)

    mission_event = sub.add_parser("mission-event")
    mission_event.add_argument("--mission", required=True, type=Path)
    mission_event.add_argument("--event", required=True)
    mission_event.add_argument("--payload", type=Path)

    mission_blocker_contract = sub.add_parser("mission-blocker-contract")
    mission_blocker_contract.add_argument("--mission", required=True, type=Path)
    mission_blocker_contract.add_argument("--payload", required=True, type=Path)

    mission_continue = sub.add_parser("mission-continue")
    mission_continue.add_argument("--prior", required=True, type=Path)
    mission_continue.add_argument("--payload", required=True, type=Path)
    mission_continue.add_argument(
        "--missions-dir", type=Path, default=DEFAULT_MISSIONS
    )

    terminal = sub.add_parser("terminal")
    terminal.add_argument("--mission", required=True, type=Path)
    terminal.add_argument("--terminal-dir", type=Path, default=DEFAULT_TERMINALS)
    terminal.add_argument(
        "--ledger", type=Path, default=DEFAULT_NOTIFICATION_LEDGER
    )
    terminal.add_argument("--dry-run", action="store_true")

    coordinator_status = sub.add_parser("coordinator-status")
    coordinator_status.add_argument(
        "--coordinator-state",
        type=Path,
        default=DEFAULT_COORDINATOR_STATE,
    )
    coordinator_status.add_argument(
        "--missions-dir", type=Path, default=DEFAULT_MISSIONS
    )
    coordinator_status.add_argument(
        "--registry", type=Path, default=DEFAULT_BINDINGS
    )
    coordinator_status.add_argument("--hosts", type=Path, default=DEFAULT_HOSTS)
    coordinator_status.add_argument(
        "--adapter", type=Path, default=DEFAULT_ADAPTER
    )
    coordinator_status.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )

    portfolio_render = sub.add_parser("portfolio-render")
    portfolio_render.add_argument(
        "--input", type=Path, default=DEFAULT_PORTFOLIO_JSON
    )
    portfolio_render.add_argument(
        "--output", type=Path, default=DEFAULT_PORTFOLIO_MARKDOWN
    )
    portfolio_render.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    portfolio_render.add_argument("--dry-run", action="store_true")

    coordinator_plan = sub.add_parser("coordinator-plan")
    coordinator_plan.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    coordinator_plan.add_argument(
        "--missions-dir", type=Path, default=DEFAULT_MISSIONS
    )
    coordinator_plan.add_argument(
        "--registry", type=Path, default=DEFAULT_BINDINGS
    )
    coordinator_plan.add_argument("--hosts", type=Path, default=DEFAULT_HOSTS)
    coordinator_plan.add_argument(
        "--adapter", type=Path, default=DEFAULT_ADAPTER
    )
    coordinator_plan.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )

    coordinator_claim = sub.add_parser("coordinator-action-claim")
    coordinator_claim.add_argument("--action-id", required=True)
    coordinator_claim.add_argument("--owner-task-id")
    coordinator_claim.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    coordinator_claim.add_argument(
        "--missions-dir", type=Path, default=DEFAULT_MISSIONS
    )
    coordinator_claim.add_argument(
        "--registry", type=Path, default=DEFAULT_BINDINGS
    )
    coordinator_claim.add_argument("--hosts", type=Path, default=DEFAULT_HOSTS)
    coordinator_claim.add_argument(
        "--adapter", type=Path, default=DEFAULT_ADAPTER
    )
    coordinator_claim.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_claim.add_argument("--dry-run", action="store_true")

    coordinator_prepare = sub.add_parser("coordinator-action-prepare")
    coordinator_prepare.add_argument("--action-id", required=True)
    coordinator_prepare.add_argument("--recipient-thread-id", required=True)
    coordinator_prepare.add_argument("--packet-sha256", required=True)
    coordinator_prepare.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_prepare.add_argument("--dry-run", action="store_true")

    coordinator_sent = sub.add_parser("coordinator-action-sent")
    coordinator_sent.add_argument("--action-id", required=True)
    coordinator_sent.add_argument("--recipient-thread-id", required=True)
    coordinator_sent.add_argument("--packet-sha256", required=True)
    coordinator_sent.add_argument("--after-cursor")
    coordinator_sent.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_sent.add_argument("--dry-run", action="store_true")

    coordinator_complete = sub.add_parser("coordinator-action-complete")
    coordinator_complete.add_argument("--action-id", required=True)
    coordinator_complete.add_argument("--outcome", required=True)
    coordinator_complete.add_argument("--evidence")
    coordinator_complete.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_complete.add_argument("--coordinator-state", type=Path)
    coordinator_complete.add_argument("--dry-run", action="store_true")

    coordinator_release = sub.add_parser("coordinator-action-release")
    coordinator_release.add_argument("--action-id", required=True)
    coordinator_release.add_argument("--reason", required=True)
    coordinator_release.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_release.add_argument("--dry-run", action="store_true")

    runtime_register = sub.add_parser("authorized-runtime-action-register")
    runtime_register.add_argument("--spec", type=Path, required=True)
    runtime_register.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    runtime_register.add_argument("--registry", type=Path, default=DEFAULT_BINDINGS)
    runtime_register.add_argument("--hosts", type=Path, default=DEFAULT_HOSTS)
    runtime_register.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    runtime_register.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    runtime_register.add_argument("--dry-run", action="store_true")

    runtime_execute = sub.add_parser("authorized-runtime-action-execute")
    runtime_execute.add_argument("--action-id", required=True)
    runtime_execute.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    runtime_execute.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    runtime_execute.add_argument("--dry-run", action="store_true")

    runtime_rollback = sub.add_parser("authorized-runtime-action-rollback")
    runtime_rollback.add_argument("--action-id", required=True)
    runtime_rollback.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    runtime_rollback.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    runtime_rollback.add_argument("--dry-run", action="store_true")

    runtime_reconcile = sub.add_parser(
        "authorized-runtime-action-reconcile-completion"
    )
    runtime_reconcile.add_argument("--action-id", required=True)
    runtime_reconcile.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    runtime_reconcile.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    runtime_reconcile.add_argument("--dry-run", action="store_true")

    coordinator_event = sub.add_parser("queue-coordinator-event")
    coordinator_event.add_argument(
        "--kind", choices=["direction_update", "project_question"], required=True
    )
    coordinator_event.add_argument("--repository-id", required=True)
    coordinator_event.add_argument("--mission-id")
    coordinator_event.add_argument("--text", required=True)
    coordinator_event.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    coordinator_event.add_argument("--dry-run", action="store_true")

    coordinator_event_ack = sub.add_parser("ack-coordinator-event")
    coordinator_event_ack.add_argument("--event-id", required=True)
    coordinator_event_ack.add_argument("--recipient-thread-id", required=True)
    coordinator_event_ack.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    coordinator_event_ack.add_argument("--dry-run", action="store_true")

    user_response = sub.add_parser("route-user-response")
    user_response.add_argument("--mission", required=True, type=Path)
    user_response.add_argument("--response", required=True)
    user_response.add_argument("--related-artifact", type=Path)
    user_response.add_argument(
        "--coordinator-state",
        type=Path,
        default=DEFAULT_COORDINATOR_STATE,
    )
    user_response.add_argument("--dry-run", action="store_true")

    acknowledge_response = sub.add_parser("ack-user-response")
    acknowledge_response.add_argument("--mission", required=True, type=Path)
    acknowledge_response.add_argument("--response-id", required=True)
    acknowledge_response.add_argument(
        "--coordinator-state",
        type=Path,
        default=DEFAULT_COORDINATOR_STATE,
    )
    acknowledge_response.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "normalize-remote":
            print(normalize_remote(args.remote))
            return 0

        if args.command == "contract":
            _json_stdout(
                {
                    "classification": "COORDINATOR_ONLY_USER_CONTRACT",
                    "user_visible_codex_entry_points": (
                        USER_VISIBLE_CODEX_ENTRY_POINTS
                    ),
                    "normal_user_input_surface": "coordinator",
                    "standard_prompts": [
                        COORDINATOR_PROMPT_THIS_REPOSITORY,
                        COORDINATOR_PROMPT_NEXT_ACTIONABLE,
                    ],
                    "direct_supervisor_user_input": False,
                    "direct_worker_user_input": False,
                }
            )
            return 0

        if args.command == "resolve":
            registry = load_json(args.registry)
            hosts = load_json(args.hosts)
            adapter = load_json(args.adapter)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            validate_registry(registry, hosts)
            if args.alias:
                result = resolve_launch(
                    args.alias,
                    mode=args.mode,
                    registry=registry,
                    hosts=hosts,
                    adapter=adapter,
                    lane=args.lane,
                    private_artifact_id=args.private_artifact_id,
                    external_target_host_id=args.external_target_host_id,
                )
                result["compatibility_path"] = "legacy_internal_alias"
            else:
                if args.mode != "coordinator":
                    raise ProtocolError(
                        "generic Coordinator targets require coordinator mode"
                    )
                coordinator_state = load_json(args.coordinator_state)
                validate_coordinator_state(coordinator_state)
                missions = load_missions(args.missions_dir)
                if args.target == "next-actionable-registered-repository":
                    scheduler_state = load_scheduler_state(args.scheduler_state)
                    plan = build_coordinator_plan(
                        registry,
                        hosts,
                        adapter,
                        missions,
                        coordinator_state,
                        scheduler_state,
                        authority_signals=collect_authority_signals(
                            registry, hosts, adapter
                        ),
                    )
                    action = plan.get("next_action")
                    if not isinstance(action, dict):
                        result = {
                            "classification": (
                                "NO_ACTIONABLE_REGISTERED_REPOSITORY"
                            ),
                            "coordinator_outcome": "IDLE_CHECKPOINT",
                            "terminal_route": None,
                            "scheduler_plan_id": plan["state_fingerprint"],
                            "scheduler_action": None,
                        }
                    else:
                        selection = action.get("payload", {}).get("selection")
                        if isinstance(selection, dict) and selection.get(
                            "repository_id"
                        ):
                            route = action.get("payload", {}).get("route", {})
                            result = resolve_launch(
                                str(selection["repository_id"]),
                                mode="coordinator",
                                registry=registry,
                                hosts=hosts,
                                adapter=adapter,
                                lane=route.get("supervision_lane"),
                            )
                            result.update(
                                {
                                    "selection_reason": selection.get(
                                        "selection_reason"
                                    ),
                                    "mission_id": selection.get("mission_id"),
                                    "attempt_id": selection.get("attempt_id"),
                                }
                            )
                        else:
                            result = {
                                "classification": (
                                    "COORDINATOR_ACTION_RESOLVED"
                                ),
                                "terminal_route": None,
                            }
                        result.update(
                            {
                                "scheduler_plan_id": plan[
                                    "state_fingerprint"
                                ],
                                "scheduler_action": action,
                            }
                        )
                else:
                    if args.context:
                        context = load_json(args.context)
                    else:
                        current_task = adapter.get("current_task", {})
                        context = {
                            "invocation_git_root": str(Path.cwd()),
                            "workspace_repository_roots": [
                                str(item["path"])
                                for item in adapter.get("projects", [])
                                if isinstance(item, dict)
                                and item.get("is_git_repository") is True
                                and item.get("path")
                            ],
                            "current_task_remote_identity": (
                                current_task.get("remote_identity")
                                or current_task.get("repository_id")
                                or adapter.get("current_task_remote_identity")
                            ),
                            "active_repository_selector": (
                                coordinator_state.get(
                                    "active_repository_selector"
                                )
                            ),
                        }
                    result = resolve_coordinator_target(
                        args.target,
                        context=context,
                        registry=registry,
                        hosts=hosts,
                        adapter=adapter,
                        coordinator_state=coordinator_state,
                        missions=missions,
                    )
                if not args.dry_run and result.get("registry_changed"):
                    atomic_write_json(args.registry, registry)
                    atomic_write_json(args.hosts, hosts)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return (
                0
                if result.get("classification")
                in {
                    "RESOLVED",
                    "NO_ACTIONABLE_REGISTERED_REPOSITORY",
                    "COORDINATOR_ACTION_RESOLVED",
                }
                else 2
            )

        if args.command == "compile-work-order":
            packet = compile_work_order(load_json(args.mission))
            atomic_write_json(args.output, packet)
            _json_stdout(
                {
                    "classification": "COMPILED",
                    "output": str(args.output),
                    "packet_sha256": packet["packet_sha256"],
                }
            )
            return 0

        if args.command == "migrate":
            registry, report = migrate_legacy_registry(load_json(args.legacy))
            atomic_write_json(args.output, registry)
            atomic_write_json(args.report, report)
            _json_stdout(report)
            return 0

        if args.command == "migrate-coordinator-ux":
            current_state = (
                load_json(args.coordinator_state)
                if args.coordinator_state.exists()
                else None
            )
            registry, coordinator_state, report = migrate_coordinator_ux(
                load_json(args.registry),
                current_state,
            )
            if not args.dry_run:
                atomic_write_json(args.registry, registry)
                atomic_write_json(args.coordinator_state, coordinator_state)
                atomic_write_json(args.report, report)
            report["dry_run"] = bool(args.dry_run)
            _json_stdout(report)
            return 0

        if args.command == "migrate-scheduler-state":
            if not args.input.is_file():
                raise ProtocolError(f"scheduler migration input is missing: {args.input}")
            raw_scheduler_state = load_json(args.input)
            from_schema_version = raw_scheduler_state.get("schema_version")
            scheduler_state = migrate_scheduler_state(raw_scheduler_state)
            if args.output.is_file() and args.output.resolve() != args.input.resolve():
                existing = migrate_scheduler_state(load_json(args.output))
                if existing != scheduler_state:
                    raise ProtocolError(
                        "scheduler migration output already contains different state"
                    )
            if not args.dry_run:
                atomic_write_json(args.output, scheduler_state)
            _json_stdout(
                {
                    "classification": "COORDINATOR_SCHEDULER_STATE_MIGRATED",
                    "from_schema_version": from_schema_version,
                    "to_schema_version": scheduler_state["schema_version"],
                    "scheduler_claim_present": isinstance(
                        scheduler_state.get("scheduler_claim"), dict
                    ),
                    "route_lease_count": len(
                        scheduler_state.get("route_leases", [])
                    ),
                    "output": str(args.output),
                    "dry_run": bool(args.dry_run),
                }
            )
            return 0

        if args.command == "validate":
            registry = load_json(args.registry)
            hosts = load_json(args.hosts)
            adapter = load_json(args.adapter)
            coordinator_state = load_json(args.coordinator_state)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            missions = load_missions(args.missions_dir)
            validate_registry(registry, hosts)
            validate_coordinator_state(coordinator_state)
            for mission in missions:
                validate_mission(mission)
            plan = build_coordinator_plan(
                registry,
                hosts,
                adapter,
                missions,
                coordinator_state,
                scheduler_state,
                authority_signals=collect_authority_signals(
                    registry, hosts, adapter
                ),
            )
            if plan.get("next_action") and not plan.get(
                "cycle_should_continue_now"
            ):
                raise ProtocolError("scheduler action/continue invariant failed")
            if plan.get("has_inflight_work") and plan.get(
                "cycle_checkpoint_allowed"
            ):
                raise ProtocolError("in-flight/checkpoint invariant failed")
            _json_stdout(
                {
                    "classification": "VALID",
                    "schema_version": 2,
                    "mission_count": len(missions),
                    "scheduler_revision": scheduler_state.get("revision", 0),
                    "next_action_id": (
                        plan.get("next_action") or {}
                    ).get("action_id"),
                }
            )
            return 0

        if args.command == "authorized-runtime-action-register":
            coordinator_state = load_json(args.coordinator_state)
            registry = load_json(args.registry)
            hosts = load_json(args.hosts)
            adapter = load_json(args.adapter)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            validate_coordinator_state(coordinator_state)
            validate_registry(registry, hosts)
            result = register_authorized_runtime_action(
                coordinator_state,
                load_json(args.spec),
                registry=registry,
                hosts=hosts,
                adapter=adapter,
                scheduler_state=scheduler_state,
            )
            validate_coordinator_state(coordinator_state)
            if not args.dry_run:
                atomic_write_json(args.coordinator_state, coordinator_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "mission-init":
            mission = new_mission(load_json(args.payload))
            path = _mission_path(args.missions_dir, mission)
            if path.exists():
                raise ProtocolError(f"Mission attempt already exists: {path}")
            atomic_write_json(path, mission)
            _json_stdout({"classification": "MISSION_CREATED", "path": str(path)})
            return 0

        if args.command == "mission-event":
            mission = load_json(args.mission)
            payload = load_json(args.payload) if args.payload else {}
            event = args.event
            if event in {name for _, name in LINEAR_EVENTS}:
                advance_linear(mission, event, payload)
            elif event == "worker_dispatched":
                dispatch_worker(mission, str(payload.get("work_order_sha256", "")))
            elif event == "worker_result_received":
                receive_worker_result(
                    mission, payload.get("worker_report", {})
                )
            elif event == "supervisor_adjudication_requested":
                request_adjudication(
                    mission, str(payload.get("worker_report_sha256", ""))
                )
            elif event == "supervisor_verdict":
                apply_supervisor_verdict(
                    mission,
                    str(payload.get("verdict", "")),
                    next_work_order=payload.get("next_work_order"),
                    user_packet=payload.get("user_packet"),
                )
            else:
                raise ProtocolError(f"unknown Mission event: {event}")
            validate_mission(mission)
            atomic_write_json(args.mission, mission)
            _json_stdout(
                {"classification": "MISSION_UPDATED", "state": mission["state"]}
            )
            return 0

        if args.command == "mission-blocker-contract":
            _json_stdout(persist_blocked_contract(args.mission, args.payload))
            return 0

        if args.command == "mission-continue":
            prior = load_json(args.prior)
            next_mission = start_continuation(prior, load_json(args.payload))
            path = _mission_path(args.missions_dir, next_mission)
            if path.exists():
                raise ProtocolError(f"Mission attempt already exists: {path}")
            atomic_write_json(path, next_mission)
            _json_stdout(
                {
                    "classification": "MISSION_CONTINUATION_CREATED",
                    "path": str(path),
                    "completed_worker_turns": next_mission[
                        "completed_worker_turns"
                    ],
                    "safety_ceiling": next_mission["safety_ceiling"],
                }
            )
            return 0

        if args.command == "terminal":
            mission = load_json(args.mission)
            validate_mission(mission)
            result = emit_terminal_packet(
                mission,
                terminal_dir=args.terminal_dir,
                ledger_path=args.ledger,
                dry_run=args.dry_run,
            )
            _json_stdout(result)
            return 0

        if args.command == "portfolio-render":
            portfolio = load_json(args.input)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            validate_portfolio_scheduler_consistency(portfolio, scheduler_state)
            rendered = render_portfolio_markdown(portfolio)
            if not args.dry_run:
                atomic_write_text(args.output, rendered)
            _json_stdout(
                {
                    "classification": "PORTFOLIO_STATUS_RENDERED",
                    "input": str(args.input),
                    "output": str(args.output),
                    "repository_count": len(portfolio["repositories"]),
                    "semantic_fingerprint": portfolio.get(
                        "semantic_fingerprint"
                    ),
                    "scheduler_revision": scheduler_state.get("revision"),
                    "active_route_count": portfolio.get("active_route_count"),
                    "dry_run": bool(args.dry_run),
                }
            )
            return 0

        if args.command in {
            "coordinator-status",
            "coordinator-plan",
            "coordinator-action-claim",
        }:
            registry = load_json(args.registry)
            hosts = load_json(args.hosts)
            adapter = load_json(args.adapter)
            coordinator_state = load_json(args.coordinator_state)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            validate_registry(registry, hosts)
            validate_coordinator_state(coordinator_state)
            missions = load_missions(args.missions_dir)
            plan = build_coordinator_plan(
                registry,
                hosts,
                adapter,
                missions,
                coordinator_state,
                scheduler_state,
                authority_signals=collect_authority_signals(
                    registry, hosts, adapter
                ),
            )
            if args.command == "coordinator-action-claim":
                result = claim_coordinator_action(
                    scheduler_state,
                    plan,
                    args.action_id,
                    owner_task_id=args.owner_task_id,
                )
                if not args.dry_run:
                    atomic_write_json(args.scheduler_state, scheduler_state)
                result["dry_run"] = bool(args.dry_run)
                _json_stdout(result)
            else:
                _json_stdout(plan)
            return 0

        if args.command == "coordinator-action-sent":
            scheduler_state = load_scheduler_state(args.scheduler_state)
            result = mark_coordinator_action_sent(
                scheduler_state,
                args.action_id,
                args.recipient_thread_id,
                packet_sha256=args.packet_sha256,
                after_cursor=args.after_cursor,
            )
            if not args.dry_run:
                atomic_write_json(args.scheduler_state, scheduler_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "coordinator-action-prepare":
            scheduler_state = load_scheduler_state(args.scheduler_state)
            result = prepare_coordinator_action_delivery(
                scheduler_state,
                args.action_id,
                args.recipient_thread_id,
                args.packet_sha256,
            )
            if not args.dry_run:
                atomic_write_json(args.scheduler_state, scheduler_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "coordinator-action-complete":
            scheduler_state = load_scheduler_state(args.scheduler_state)
            coordinator_state = (
                load_json(args.coordinator_state)
                if args.coordinator_state is not None
                else None
            )
            if coordinator_state is not None:
                validate_coordinator_state(coordinator_state)
            result = complete_coordinator_action(
                scheduler_state,
                args.action_id,
                args.outcome,
                evidence=args.evidence,
                coordinator_state=coordinator_state,
            )
            if not args.dry_run:
                if coordinator_state is not None:
                    validate_coordinator_state(coordinator_state)
                    atomic_write_json(args.coordinator_state, coordinator_state)
                atomic_write_json(args.scheduler_state, scheduler_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0


        if args.command in {
            "authorized-runtime-action-execute",
            "authorized-runtime-action-rollback",
            "authorized-runtime-action-reconcile-completion",
        }:
            coordinator_state = load_json(args.coordinator_state)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            validate_coordinator_state(coordinator_state)

            def persist_runtime_state() -> None:
                validate_scheduler_state(scheduler_state)
                validate_coordinator_state(coordinator_state)
                atomic_write_json(args.coordinator_state, coordinator_state)
                atomic_write_json(args.scheduler_state, scheduler_state)

            if args.command == "authorized-runtime-action-execute":
                result = execute_authorized_runtime_action(
                    coordinator_state,
                    scheduler_state,
                    args.action_id,
                    dry_run=args.dry_run,
                    persist_state=(None if args.dry_run else persist_runtime_state),
                )
            elif args.command == "authorized-runtime-action-rollback":
                result = rollback_authorized_runtime_action(
                    coordinator_state,
                    scheduler_state,
                    args.action_id,
                    dry_run=args.dry_run,
                    persist_state=(None if args.dry_run else persist_runtime_state),
                )
            else:
                result = reconcile_authorized_runtime_completion(
                    coordinator_state,
                    scheduler_state,
                    args.action_id,
                    dry_run=args.dry_run,
                    persist_state=(None if args.dry_run else persist_runtime_state),
                )
            if not args.dry_run:
                persist_runtime_state()
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "coordinator-action-release":
            scheduler_state = load_scheduler_state(args.scheduler_state)
            result = release_coordinator_action(
                scheduler_state, args.action_id, args.reason
            )
            if not args.dry_run:
                atomic_write_json(args.scheduler_state, scheduler_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "queue-coordinator-event":
            coordinator_state = load_json(args.coordinator_state)
            validate_coordinator_state(coordinator_state)
            result = queue_coordinator_event(
                coordinator_state,
                kind=args.kind,
                repository_id=args.repository_id,
                mission_id=args.mission_id,
                raw_text=args.text,
            )
            if not args.dry_run:
                atomic_write_json(args.coordinator_state, coordinator_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "ack-coordinator-event":
            coordinator_state = load_json(args.coordinator_state)
            validate_coordinator_state(coordinator_state)
            result = acknowledge_coordinator_event_routed(
                coordinator_state,
                args.event_id,
                args.recipient_thread_id,
            )
            if not args.dry_run:
                atomic_write_json(args.coordinator_state, coordinator_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "route-user-response":
            mission = load_json(args.mission)
            coordinator_state = load_json(args.coordinator_state)
            validate_mission(mission)
            validate_coordinator_state(coordinator_state)
            related_artifact: Any = None
            if args.related_artifact:
                related_artifact = load_json(args.related_artifact)
            result = queue_user_response(
                mission,
                coordinator_state,
                args.response,
                related_artifact=related_artifact,
            )
            validate_mission(mission)
            validate_coordinator_state(coordinator_state)
            if not args.dry_run:
                atomic_write_json(args.mission, mission)
                atomic_write_json(args.coordinator_state, coordinator_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0


        if args.command == "ack-user-response":
            mission = load_json(args.mission)
            coordinator_state = load_json(args.coordinator_state)
            validate_mission(mission)
            validate_coordinator_state(coordinator_state)
            result = acknowledge_user_response_routed(
                mission, coordinator_state, args.response_id
            )
            validate_mission(mission)
            validate_coordinator_state(coordinator_state)
            if not args.dry_run:
                atomic_write_json(args.mission, mission)
                atomic_write_json(args.coordinator_state, coordinator_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        raise ProtocolError(f"unsupported command: {args.command}")
    except (OSError, ValueError, ProtocolError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
