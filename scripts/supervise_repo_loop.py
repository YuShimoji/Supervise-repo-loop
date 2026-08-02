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
DEFAULT_FRONTIER_STATE = SKILL_ROOT / "state" / "frontier-ledger.v1.json"
DEFAULT_FRONTIER_JOURNAL = SKILL_ROOT / "state" / "frontier-transactions"
DEFAULT_PROJECT_CONTEXT_STATE = SKILL_ROOT / "state" / "project-context-ledger.v1.json"
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
MISSION_VALUE_CONTRACT_VERSION = 1
MISSION_WORK_CLASSES = {"quick_win", "bounded_slice", "strategic_bet"}
MISSION_OBJECTIVE_FITS = {"direct", "enabling", "exploratory"}
PRIMARY_WRITER_REBIND_CONFIRMATION = "REBIND_PRIMARY_COORDINATOR_WRITER"
FRONTIER_STATE_VERSION = 1
FRONTIER_PORTFOLIO_VERSION = 3
PROJECT_CONTEXT_PORTFOLIO_VERSION = 4
FRONTIER_SAFETY_MODE = "TRANSPORT_ONLY_RECONCILIATION"
PROJECT_CONTEXT_STATE_VERSION = 1
PROJECT_CONTEXT_SAFETY_MODE = "CONTEXT_RECONCILIATION_REQUIRED"
FRONTIER_DISPOSITIONS = {
    "active",
    "accepted",
    "rejected",
    "superseded",
    "parked",
    "none",
}
FRONTIER_SOURCE_ACTORS = {
    "human",
    "supervisor",
    "worker",
    "repo_observation",
    "coordinator",
}
FRONTIER_SOURCE_PRECEDENCE = {
    "coordinator": 1,
    "repo_observation": 2,
    "worker": 3,
    "supervisor": 4,
    "human": 5,
}
FRONTIER_ADVANCE_DISPOSITIONS = {"active", "accepted"}
EXTERNAL_RESULT_LIFECYCLE_STATES = {
    "created",
    "dispatched",
    "delivery_acknowledged",
    "result_received",
    "result_parsed",
    "result_validated",
    "result_applied",
    "failed",
    "stale_result_quarantined",
    "cancelled",
}
EXTERNAL_RESULT_COMPATIBILITY_STATES = {"legacy_unverified"}
FRONTIER_TRANSPORT_ACTION_KINDS = {
    "route_direction_update",
    "route_project_question",
    "route_user_response",
    "clarify_event_route",
}
FRONTIER_RECONCILIATION_ACTION_KINDS = {
    "reconcile_repository_frontier",
    "reconcile_repository_authority",
}
PROJECT_CONTEXT_RECONCILIATION_ACTION_KINDS = {
    "reconcile_project_context",
    "reconcile_repository_frontier",
    "reconcile_repository_authority",
    "route_direction_update",
    "route_project_question",
    "route_user_response",
}
PROJECT_CONTEXT_BOUND_ACTION_KINDS = {
    "advance_mission",
    "dispatch_work_order",
    "inspect_blocked_recovery",
    "present_user_card",
    "repair_blocker_contract",
    "request_next_mission",
    "probe_authorized_runtime_repair",
    "resolve_mission_value_gate",
    "return_authorized_runtime_recovery_result",
    "return_worker_result",
    "await_supervisor_verdict",
    "await_supervisor_work_order",
    "await_worker_result",
}

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
    "reconcile_project_context",
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
AUTHORIZED_RUNTIME_PROBE_STATUS_MATRIX = {
    "delivery_failed": {
        "probe_a": "pending",
        "postcheck": "pending",
        "probe_b": "pending",
        "runtime_doctor": "pending",
    },
    "task_start_failed": {
        "probe_a": "pending",
        "postcheck": "pending",
        "probe_b": "pending",
        "runtime_doctor": "pending",
    },
    "regeneration_failed": {
        "probe_a": "passed",
        "postcheck": "failed",
        "probe_b": "not_started",
        "runtime_doctor": "not_started",
    },
    "postcheck_failed": {
        "probe_a": "passed",
        "postcheck": "failed",
        "probe_b": "not_started",
        "runtime_doctor": "not_started",
    },
    "probe_a_failed": {
        "probe_a": "failed",
        "postcheck": "not_started",
        "probe_b": "not_started",
        "runtime_doctor": "not_started",
    },
    "probe_b_failed": {
        "probe_a": "passed",
        "postcheck": "passed",
        "probe_b": "failed",
        "runtime_doctor": "not_started",
    },
    "runtime_doctor_failed": {
        "probe_a": "passed",
        "postcheck": "passed",
        "probe_b": "passed",
        "runtime_doctor": "failed",
    },
    "probe_passed": {
        "probe_a": "passed",
        "postcheck": "passed",
        "probe_b": "passed",
        "runtime_doctor": "passed",
    },
}
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


def _frontier_key(repository_id: str, lane_id: str) -> str:
    repository = str(repository_id or "").strip()
    lane = str(lane_id or "").strip()
    if not repository or not lane:
        raise ProtocolError("frontier identity requires repository_id and lane_id")
    return f"{repository}|{lane}"


def default_frontier_state(
    repository_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a non-authoritative legacy migration boundary.

    Missing legacy lineage is deliberately represented as ``legacy_unverified``;
    no current artifact is inferred from timestamps, filenames, or Mission order.
    """
    repositories = sorted(
        {str(item).strip() for item in repository_ids if str(item).strip()}
    )
    return {
        "schema_version": FRONTIER_STATE_VERSION,
        "revision": 0,
        "safety_mode": FRONTIER_SAFETY_MODE,
        "records": {},
        "events": [],
        "repository_status": {
            repository_id: "legacy_unverified"
            for repository_id in repositories
        },
        "retired_artifacts": [],
        "applied_results": [],
        "quarantined_results": [],
        "failed_results": [],
    }


def migrate_frontier_state(
    state: dict[str, Any] | None,
    repository_ids: Iterable[str] = (),
) -> dict[str, Any]:
    if state is None:
        return default_frontier_state(repository_ids)
    if not isinstance(state, dict):
        raise ProtocolError("frontier state must be an object")
    if state.get("schema_version") != FRONTIER_STATE_VERSION:
        raise ProtocolError("unsupported frontier state schema")
    migrated = copy.deepcopy(state)
    migrated.setdefault("revision", 0)
    migrated.setdefault("safety_mode", FRONTIER_SAFETY_MODE)
    migrated.setdefault("records", {})
    migrated.setdefault("events", [])
    migrated.setdefault("repository_status", {})
    migrated.setdefault("retired_artifacts", [])
    migrated.setdefault("applied_results", [])
    migrated.setdefault("quarantined_results", [])
    migrated.setdefault("failed_results", [])
    for repository_id in repository_ids:
        normalized = str(repository_id or "").strip()
        if normalized:
            migrated["repository_status"].setdefault(
                normalized, "legacy_unverified"
            )
    migrated["safety_mode"] = (
        "FRONTIER_VERIFIED"
        if migrated["repository_status"]
        and all(
            status == "verified"
            for status in migrated["repository_status"].values()
        )
        else FRONTIER_SAFETY_MODE
    )
    validate_frontier_state(migrated)
    return migrated


def load_frontier_state(
    path: Path | str,
    repository_ids: Iterable[str] = (),
) -> dict[str, Any]:
    target = Path(path)
    raw = load_json(target) if target.is_file() else None
    return migrate_frontier_state(raw, repository_ids)


def default_project_context_state(
    repository_ids: Iterable[str] = (),
) -> dict[str, Any]:
    repositories = sorted(
        {str(item).strip() for item in repository_ids if str(item).strip()}
    )
    return {
        "schema_version": PROJECT_CONTEXT_STATE_VERSION,
        "revision": 0,
        "safety_mode": PROJECT_CONTEXT_SAFETY_MODE,
        "contexts": {},
        "events": [],
        "repository_status": {
            repository_id: "legacy_unverified" for repository_id in repositories
        },
    }


def migrate_project_context_state(
    state: dict[str, Any] | None,
    repository_ids: Iterable[str] = (),
) -> dict[str, Any]:
    if state is None:
        return default_project_context_state(repository_ids)
    if not isinstance(state, dict):
        raise ProtocolError("project context state must be an object")
    if state.get("schema_version") != PROJECT_CONTEXT_STATE_VERSION:
        raise ProtocolError("unsupported project context state schema")
    migrated = copy.deepcopy(state)
    migrated.setdefault("revision", 0)
    migrated.setdefault("safety_mode", PROJECT_CONTEXT_SAFETY_MODE)
    migrated.setdefault("contexts", {})
    migrated.setdefault("events", [])
    migrated.setdefault("repository_status", {})
    for repository_id in repository_ids:
        normalized = str(repository_id or "").strip()
        if normalized:
            migrated["repository_status"].setdefault(
                normalized, "legacy_unverified"
            )
    migrated["safety_mode"] = (
        "PROJECT_CONTEXT_VERIFIED"
        if migrated["repository_status"]
        and all(
            status == "verified"
            for status in migrated["repository_status"].values()
        )
        else PROJECT_CONTEXT_SAFETY_MODE
    )
    validate_project_context_state(migrated)
    return migrated


def load_project_context_state(
    path: Path | str,
    repository_ids: Iterable[str] = (),
) -> dict[str, Any]:
    target = Path(path)
    raw = load_json(target) if target.is_file() else None
    return migrate_project_context_state(raw, repository_ids)


def _validate_project_context_roadmap(roadmap: Any) -> None:
    if not isinstance(roadmap, dict):
        raise ProtocolError("project context roadmap must be an object")
    for field in (
        "overall_position",
        "current_block",
        "next_gate",
        "completion_definition",
    ):
        if not isinstance(roadmap.get(field), str) or not roadmap[field].strip():
            raise ProtocolError(f"project context roadmap requires {field}")
    for field in ("completed_blocks", "next_blocks"):
        values = roadmap.get(field)
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item.strip() for item in values
        ):
            raise ProtocolError(f"project context roadmap {field} is invalid")


def validate_project_context_record(record: Any) -> None:
    if not isinstance(record, dict):
        raise ProtocolError("ProjectContextRecord must be an object")
    required = (
        "repository_id",
        "project_context_revision",
        "project_context_event_id",
        "based_on_project_context_revision",
        "source_actor",
        "source_message_id",
        "authority_revision",
        "authority_fingerprint",
        "north_star",
        "current_bottleneck",
        "completion_definition",
        "roadmap",
        "active_lanes",
        "lane_frontier_event_ids",
        "cross_lane_conflicts",
        "decisions_since_prior",
        "evidence_manifest",
        "omitted_evidence",
        "supersedes_context_event_ids",
        "recorded_at",
    )
    missing = [field for field in required if field not in record]
    if missing:
        raise ProtocolError("ProjectContextRecord missing: " + ", ".join(missing))
    for field in (
        "repository_id",
        "project_context_event_id",
        "source_message_id",
        "authority_revision",
        "north_star",
        "current_bottleneck",
        "completion_definition",
        "recorded_at",
    ):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ProtocolError(f"ProjectContextRecord requires {field}")
    revision = record.get("project_context_revision")
    based_on = record.get("based_on_project_context_revision")
    if not isinstance(revision, int) or revision < 1:
        raise ProtocolError("project context revision must be positive")
    if not isinstance(based_on, int) or revision != based_on + 1:
        raise ProtocolError("project context revision must advance by one")
    if record.get("source_actor") not in FRONTIER_SOURCE_ACTORS:
        raise ProtocolError("project context source_actor is invalid")
    if not re.fullmatch(
        r"[0-9a-fA-F]{64}", str(record.get("authority_fingerprint") or "")
    ):
        raise ProtocolError("project context authority_fingerprint is invalid")
    _validate_project_context_roadmap(record.get("roadmap"))
    if record["roadmap"].get("completion_definition") != record.get(
        "completion_definition"
    ):
        raise ProtocolError(
            "project context completion definition must match its roadmap"
        )
    active_lanes = record.get("active_lanes")
    if (
        not isinstance(active_lanes, list)
        or not active_lanes
        or any(not isinstance(item, str) or not item.strip() for item in active_lanes)
        or len(active_lanes) != len(set(active_lanes))
    ):
        raise ProtocolError("project context active_lanes must be unique and non-empty")
    lane_events = record.get("lane_frontier_event_ids")
    if not isinstance(lane_events, dict) or set(lane_events) != set(active_lanes):
        raise ProtocolError(
            "project context lane frontier map must match every active lane"
        )
    if any(not isinstance(value, str) or not value.strip() for value in lane_events.values()):
        raise ProtocolError("project context lane frontier event IDs are invalid")
    conflicts = record.get("cross_lane_conflicts")
    if not isinstance(conflicts, list) or any(
        not isinstance(item, str) or not item.strip() for item in conflicts
    ):
        raise ProtocolError("project context cross_lane_conflicts is invalid")
    decisions = record.get("decisions_since_prior")
    if not isinstance(decisions, list):
        raise ProtocolError("project context decisions_since_prior must be an array")
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ProtocolError("project context decision must be an object")
        for field in ("event_id", "lane_id", "disposition", "source_actor"):
            if not isinstance(decision.get(field), str) or not decision[field].strip():
                raise ProtocolError(f"project context decision requires {field}")
        if decision["disposition"] not in FRONTIER_DISPOSITIONS:
            raise ProtocolError("project context decision disposition is invalid")
        if decision["source_actor"] not in FRONTIER_SOURCE_ACTORS:
            raise ProtocolError("project context decision source_actor is invalid")
    evidence = record.get("evidence_manifest")
    if not isinstance(evidence, list) or not evidence:
        raise ProtocolError("project context evidence_manifest must be non-empty")
    evidence_ids: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise ProtocolError("project context evidence entry must be an object")
        for field in ("evidence_id", "kind", "locator", "authority_role"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ProtocolError(f"project context evidence requires {field}")
        if item["evidence_id"] in evidence_ids:
            raise ProtocolError("project context evidence IDs must be unique")
        evidence_ids.add(item["evidence_id"])
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(item.get("sha256") or "")):
            raise ProtocolError("project context evidence sha256 is invalid")
    omitted = record.get("omitted_evidence")
    if not isinstance(omitted, list):
        raise ProtocolError("project context omitted_evidence must be an array")
    omitted_ids: set[str] = set()
    for item in omitted:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(field), str) or not item[field].strip()
            for field in ("evidence_id", "reason")
        ):
            raise ProtocolError("project context omitted evidence is invalid")
        if item["evidence_id"] in omitted_ids:
            raise ProtocolError(
                "project context omitted evidence IDs must be unique"
            )
        if item["evidence_id"] in evidence_ids:
            raise ProtocolError(
                "project context evidence cannot be included and omitted"
            )
        omitted_ids.add(item["evidence_id"])
    supersedes = record.get("supersedes_context_event_ids")
    if not isinstance(supersedes, list) or any(
        not isinstance(item, str) or not item.strip() for item in supersedes
    ):
        raise ProtocolError("project context supersedes list is invalid")
    if len(supersedes) != len(set(supersedes)):
        raise ProtocolError("project context supersedes IDs must be unique")
    if record["project_context_event_id"] in supersedes:
        raise ProtocolError("project context event cannot supersede itself")


def validate_project_context_state(state: Any) -> None:
    if not isinstance(state, dict):
        raise ProtocolError("project context state must be an object")
    if state.get("schema_version") != PROJECT_CONTEXT_STATE_VERSION:
        raise ProtocolError("unsupported project context state schema")
    if not isinstance(state.get("revision"), int) or state["revision"] < 0:
        raise ProtocolError("project context state revision is invalid")
    if state.get("safety_mode") not in {
        PROJECT_CONTEXT_SAFETY_MODE,
        "PROJECT_CONTEXT_VERIFIED",
    }:
        raise ProtocolError("project context safety_mode is invalid")
    if not isinstance(state.get("contexts"), dict):
        raise ProtocolError("project contexts must be an object")
    if not isinstance(state.get("events"), list):
        raise ProtocolError("project context events must be an array")
    if not isinstance(state.get("repository_status"), dict):
        raise ProtocolError("project context repository_status must be an object")
    for repository_id, record in state["contexts"].items():
        validate_project_context_record(record)
        if record.get("repository_id") != repository_id:
            raise ProtocolError("project context repository key mismatch")
    event_by_id: dict[str, dict[str, Any]] = {}
    for event in state["events"]:
        validate_project_context_record(event)
        event_id = str(event["project_context_event_id"])
        if event_id in event_by_id:
            raise ProtocolError("project context event IDs must be unique")
        event_by_id[event_id] = event
    for status in state["repository_status"].values():
        if status not in {
            "legacy_unverified",
            "reconciliation_required",
            "authority_conflict",
            "verified",
        }:
            raise ProtocolError("project context repository status is invalid")
    for repository_id, record in state["contexts"].items():
        if event_by_id.get(str(record["project_context_event_id"])) != record:
            raise ProtocolError(
                "current project context must match an append-only event"
            )
        status = state["repository_status"].get(repository_id)
        allowed_statuses = (
            {"verified", "authority_conflict"}
            if not record.get("cross_lane_conflicts")
            else {"reconciliation_required"}
        )
        if status not in allowed_statuses:
            raise ProtocolError("project context repository status is stale")
    for repository_id, status in state["repository_status"].items():
        if status in {"verified", "authority_conflict"} and repository_id not in state[
            "contexts"
        ]:
            raise ProtocolError(
                "project context repository status requires a current record"
            )
    expected_safety_mode = (
        "PROJECT_CONTEXT_VERIFIED"
        if state["repository_status"]
        and all(
            status == "verified"
            for status in state["repository_status"].values()
        )
        else PROJECT_CONTEXT_SAFETY_MODE
    )
    if state["safety_mode"] != expected_safety_mode:
        raise ProtocolError("project context safety mode is stale")


def apply_project_context_event(
    state: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    validate_project_context_state(state)
    validate_project_context_record(candidate)
    event_id = str(candidate["project_context_event_id"])
    existing = next(
        (
            item
            for item in state["events"]
            if item.get("project_context_event_id") == event_id
        ),
        None,
    )
    if existing is not None:
        if existing != candidate:
            raise ProtocolError("conflicting project context event replay")
        return {
            "classification": "PROJECT_CONTEXT_EVENT_ALREADY_APPLIED",
            "project_context_event_id": event_id,
            "deduplicated": True,
        }
    repository_id = str(candidate["repository_id"])
    current = state["contexts"].get(repository_id)
    current_revision = (
        int(current.get("project_context_revision", 0))
        if isinstance(current, dict)
        else 0
    )
    if candidate["based_on_project_context_revision"] != current_revision:
        return {
            "classification": "PROJECT_CONTEXT_EVENT_STALE",
            "expected_project_context_revision": current_revision,
            "based_on_project_context_revision": candidate[
                "based_on_project_context_revision"
            ],
            "deduplicated": False,
        }
    if isinstance(current, dict):
        current_precedence = FRONTIER_SOURCE_PRECEDENCE[current["source_actor"]]
        candidate_precedence = FRONTIER_SOURCE_PRECEDENCE[candidate["source_actor"]]
        if candidate_precedence < current_precedence:
            return {
                "classification": "PROJECT_CONTEXT_PRECEDENCE_REJECTED",
                "current_source_actor": current["source_actor"],
                "candidate_source_actor": candidate["source_actor"],
                "deduplicated": False,
            }
        if current["project_context_event_id"] not in candidate.get(
            "supersedes_context_event_ids", []
        ):
            return {
                "classification": "PROJECT_CONTEXT_SUPERSESSION_REQUIRED",
                "current_project_context_event_id": current[
                    "project_context_event_id"
                ],
                "deduplicated": False,
            }
    next_state = copy.deepcopy(state)
    next_state["events"].append(copy.deepcopy(candidate))
    next_state["contexts"][repository_id] = copy.deepcopy(candidate)
    next_state["repository_status"][repository_id] = (
        "verified"
        if not candidate.get("cross_lane_conflicts")
        else "reconciliation_required"
    )
    next_state["revision"] = int(next_state["revision"]) + 1
    next_state["safety_mode"] = (
        "PROJECT_CONTEXT_VERIFIED"
        if next_state["repository_status"]
        and all(
            status == "verified"
            for status in next_state["repository_status"].values()
        )
        else PROJECT_CONTEXT_SAFETY_MODE
    )
    validate_project_context_state(next_state)
    state.clear()
    state.update(next_state)
    return {
        "classification": "PROJECT_CONTEXT_EVENT_APPLIED",
        "project_context_event_id": event_id,
        "project_context_revision": candidate["project_context_revision"],
        "deduplicated": False,
    }


def _repository_frontier_fingerprint(
    frontier_state: dict[str, Any], repository_id: str
) -> str:
    records = sorted(
        (
            copy.deepcopy(record)
            for record in frontier_state.get("records", {}).values()
            if isinstance(record, dict)
            and record.get("repository_id") == repository_id
        ),
        key=lambda item: (str(item.get("lane_id") or ""), int(item.get("frontier_epoch", 0))),
    )
    return canonical_json_hash(records)


def _project_context_current_requirements(
    context_state: dict[str, Any],
    frontier_state: dict[str, Any],
    repository_id: str,
    authority_signal: dict[str, Any] | None,
) -> dict[str, Any]:
    validate_project_context_state(context_state)
    validate_frontier_state(frontier_state)
    record = context_state.get("contexts", {}).get(repository_id)
    if not isinstance(record, dict):
        raise ProtocolError("project context is missing")
    if context_state.get("repository_status", {}).get(repository_id) != "verified":
        raise ProtocolError("project context requires reconciliation")
    if record.get("cross_lane_conflicts"):
        raise ProtocolError("project context has unresolved cross-lane conflicts")
    if not isinstance(authority_signal, dict):
        raise ProtocolError("project context authority signal is missing")
    validate_authority_signal_liveness(authority_signal)
    if authority_signal.get("repository_id") != repository_id:
        raise ProtocolError("project context authority repository mismatch")
    if authority_signal.get("authority_fingerprint") != record.get(
        "authority_fingerprint"
    ):
        raise ProtocolError("project context authority fingerprint is stale")
    if record.get("authority_revision") != authority_signal.get("git", {}).get(
        "head_sha"
    ):
        raise ProtocolError("project context authority revision is stale")
    for lane_id, event_id in record["lane_frontier_event_ids"].items():
        current = frontier_state.get("records", {}).get(
            _frontier_key(repository_id, lane_id)
        )
        if not isinstance(current, dict):
            raise ProtocolError(f"project context active lane is missing: {lane_id}")
        if current.get("frontier_event_id") != event_id:
            raise ProtocolError(f"project context active lane is stale: {lane_id}")
    return record


def effective_project_context_safety_mode(
    context_state: dict[str, Any],
    frontier_state: dict[str, Any],
    authority_signals: Iterable[dict[str, Any]],
    repository_ids: Iterable[str] | None = None,
) -> str:
    """Derive observed safety without mutating the append-only context ledger."""
    validate_project_context_state(context_state)
    validate_frontier_state(frontier_state)
    signal_by_repository = {
        str(item.get("repository_id") or ""): item
        for item in authority_signals
        if isinstance(item, dict) and item.get("repository_id")
    }
    repositories = list(
        repository_ids
        if repository_ids is not None
        else context_state.get("repository_status", {}).keys()
    )
    if not repositories:
        return PROJECT_CONTEXT_SAFETY_MODE
    for repository_id in repositories:
        try:
            _project_context_current_requirements(
                context_state,
                frontier_state,
                str(repository_id),
                signal_by_repository.get(str(repository_id)),
            )
        except ProtocolError:
            return PROJECT_CONTEXT_SAFETY_MODE
    return "PROJECT_CONTEXT_VERIFIED"


def build_supervisor_context_envelope(
    context_state: dict[str, Any],
    frontier_state: dict[str, Any],
    repository_id: str,
    lane_id: str,
    action_kind: str,
    authority_signal: dict[str, Any],
) -> dict[str, Any]:
    record = _project_context_current_requirements(
        context_state, frontier_state, repository_id, authority_signal
    )
    if lane_id not in record["active_lanes"]:
        raise ProtocolError("Supervisor context lane is not active")
    frontier_certificate = issue_frontier_certificate(
        frontier_state, repository_id, lane_id, authority_signal
    )
    lane_frontiers = []
    for active_lane in sorted(record["active_lanes"]):
        frontier = frontier_state["records"][_frontier_key(repository_id, active_lane)]
        lane_frontiers.append(
            {
                "lane_id": active_lane,
                "frontier_epoch": frontier["frontier_epoch"],
                "frontier_event_id": frontier["frontier_event_id"],
                "artifact_id": frontier.get("artifact_id"),
                "artifact_revision": frontier.get("artifact_revision"),
                "artifact_sha256": frontier.get("artifact_sha256"),
                "disposition": frontier["disposition"],
                "source_actor": frontier["source_actor"],
            }
        )
    retired_artifacts = sorted(
        (
            copy.deepcopy(item)
            for item in frontier_state.get("retired_artifacts", [])
            if isinstance(item, dict)
            and item.get("repository_id") == repository_id
        ),
        key=lambda item: (
            str(item.get("lane_id") or ""),
            int(item.get("frontier_epoch", 0)),
            str(item.get("frontier_event_id") or ""),
        ),
    )
    envelope = {
        "schema_version": 1,
        "repository_id": repository_id,
        "lane_id": lane_id,
        "action_kind": action_kind,
        "project_context_revision": record["project_context_revision"],
        "project_context_event_id": record["project_context_event_id"],
        "repository_frontier_fingerprint": _repository_frontier_fingerprint(
            frontier_state, repository_id
        ),
        "frontier_certificate": frontier_certificate,
        "authority_revision": record["authority_revision"],
        "authority_fingerprint": record["authority_fingerprint"],
        "north_star": record["north_star"],
        "current_bottleneck": record["current_bottleneck"],
        "completion_definition": record["completion_definition"],
        "roadmap": copy.deepcopy(record["roadmap"]),
        "active_lanes": copy.deepcopy(record["active_lanes"]),
        "lane_frontiers": lane_frontiers,
        "cross_lane_conflicts": copy.deepcopy(record["cross_lane_conflicts"]),
        "decisions_since_prior": copy.deepcopy(record["decisions_since_prior"]),
        "evidence_manifest": copy.deepcopy(record["evidence_manifest"]),
        "omitted_evidence": copy.deepcopy(record["omitted_evidence"]),
        "retired_artifacts": retired_artifacts,
    }
    envelope["envelope_id"] = canonical_json_hash(envelope)
    return envelope


def validate_supervisor_context_envelope(
    envelope: Any,
    context_state: dict[str, Any],
    frontier_state: dict[str, Any],
    authority_signal: dict[str, Any],
    *,
    expected_action_kind: str | None = None,
) -> None:
    if not isinstance(envelope, dict):
        raise ProtocolError("SupervisorContextEnvelope must be an object")
    repository_id = str(envelope.get("repository_id") or "")
    lane_id = str(envelope.get("lane_id") or "")
    action_kind = str(envelope.get("action_kind") or "")
    if expected_action_kind is not None and action_kind != expected_action_kind:
        raise ProtocolError("Supervisor context action kind mismatch")
    expected = build_supervisor_context_envelope(
        context_state,
        frontier_state,
        repository_id,
        lane_id,
        action_kind,
        authority_signal,
    )
    if envelope != expected:
        raise ProtocolError("Supervisor context envelope is stale or incomplete")


def validate_supervisor_context_result_binding(
    envelope: Any,
    context_state: dict[str, Any],
    frontier_state: dict[str, Any],
    *,
    expected_action_kind: str,
) -> None:
    """Validate the based-on context without rejecting an authorized effect.

    A Worker or Supervisor route may legitimately change Git/authority state.
    Result identity is independently bound to that new observation elsewhere.
    This check therefore compare-and-swaps the context event and every lane
    frontier that existed when the route was issued, while deliberately not
    requiring the pre-effect authority fingerprint to equal the post-effect
    observation.
    """
    if not isinstance(envelope, dict):
        raise ProtocolError("SupervisorContextEnvelope must be an object")
    validate_project_context_state(context_state)
    validate_frontier_state(frontier_state)
    hashed = copy.deepcopy(envelope)
    envelope_id = str(hashed.pop("envelope_id", ""))
    if not re.fullmatch(r"[0-9a-fA-F]{64}", envelope_id):
        raise ProtocolError("Supervisor context envelope ID is invalid")
    if canonical_json_hash(hashed) != envelope_id:
        raise ProtocolError("Supervisor context envelope ID does not match content")
    if envelope.get("action_kind") != expected_action_kind:
        raise ProtocolError("Supervisor context action kind mismatch")
    repository_id = str(envelope.get("repository_id") or "")
    lane_id = str(envelope.get("lane_id") or "")
    record = context_state.get("contexts", {}).get(repository_id)
    if not isinstance(record, dict):
        raise ProtocolError("project context is missing")
    if context_state.get("repository_status", {}).get(repository_id) != "verified":
        raise ProtocolError("project context requires reconciliation")
    if envelope.get("project_context_revision") != record.get(
        "project_context_revision"
    ) or envelope.get("project_context_event_id") != record.get(
        "project_context_event_id"
    ):
        raise ProtocolError("Supervisor context revision is stale")
    context_fields = (
        "authority_revision",
        "authority_fingerprint",
        "north_star",
        "current_bottleneck",
        "completion_definition",
        "roadmap",
        "active_lanes",
        "cross_lane_conflicts",
        "decisions_since_prior",
        "evidence_manifest",
        "omitted_evidence",
    )
    for field in context_fields:
        if envelope.get(field) != record.get(field):
            raise ProtocolError(
                f"Supervisor context {field} differs from the current record"
            )
    if lane_id not in record["active_lanes"]:
        raise ProtocolError("Supervisor context lane is not active")
    if envelope.get("repository_frontier_fingerprint") != (
        _repository_frontier_fingerprint(frontier_state, repository_id)
    ):
        raise ProtocolError("Supervisor context repository frontier is stale")
    current_lane_frontiers = []
    for active_lane in sorted(record["active_lanes"]):
        frontier = frontier_state.get("records", {}).get(
            _frontier_key(repository_id, active_lane)
        )
        if not isinstance(frontier, dict):
            raise ProtocolError(
                f"Supervisor context active lane is missing: {active_lane}"
            )
        if record["lane_frontier_event_ids"].get(active_lane) != frontier.get(
            "frontier_event_id"
        ):
            raise ProtocolError(
                f"Supervisor context active lane is stale: {active_lane}"
            )
        current_lane_frontiers.append(
            {
                "lane_id": active_lane,
                "frontier_epoch": frontier["frontier_epoch"],
                "frontier_event_id": frontier["frontier_event_id"],
                "artifact_id": frontier.get("artifact_id"),
                "artifact_revision": frontier.get("artifact_revision"),
                "artifact_sha256": frontier.get("artifact_sha256"),
                "disposition": frontier["disposition"],
                "source_actor": frontier["source_actor"],
            }
        )
    if envelope.get("lane_frontiers") != current_lane_frontiers:
        raise ProtocolError("Supervisor context lane frontier projection is stale")
    certificate = envelope.get("frontier_certificate")
    current = frontier_state["records"][_frontier_key(repository_id, lane_id)]
    expected_certificate = _frontier_certificate_payload(
        current,
        {"authority_fingerprint": envelope.get("authority_fingerprint")},
    )
    expected_certificate["certificate_id"] = canonical_json_hash(
        expected_certificate
    )
    if certificate != expected_certificate:
        raise ProtocolError("Supervisor context frontier certificate is stale")


def project_context_gate_decision(
    context_state: dict[str, Any],
    frontier_state: dict[str, Any],
    repository_id: str,
    lane_id: str,
    action_kind: str,
    authority_signal: dict[str, Any] | None,
) -> dict[str, Any]:
    if action_kind in PROJECT_CONTEXT_RECONCILIATION_ACTION_KINDS:
        return {
            "classification": "PROJECT_CONTEXT_RECONCILIATION_ALLOWED",
            "reasons": [],
            "envelope": None,
        }
    if action_kind not in PROJECT_CONTEXT_BOUND_ACTION_KINDS:
        return {
            "classification": "PROJECT_CONTEXT_NOT_REQUIRED",
            "reasons": [],
            "envelope": None,
        }
    try:
        if not isinstance(authority_signal, dict):
            raise ProtocolError("project context authority signal is missing")
        envelope = build_supervisor_context_envelope(
            context_state,
            frontier_state,
            repository_id,
            lane_id,
            action_kind,
            authority_signal,
        )
    except ProtocolError as exc:
        return {
            "classification": "PROJECT_CONTEXT_RECONCILIATION_REQUIRED",
            "reasons": [str(exc)],
            "envelope": None,
        }
    return {
        "classification": "PROJECT_CONTEXT_CERTIFIED",
        "reasons": [],
        "envelope": envelope,
    }


def validate_frontier_record(record: Any) -> None:
    if not isinstance(record, dict):
        raise ProtocolError("FrontierRecord must be an object")
    required = (
        "repository_id",
        "lane_id",
        "frontier_epoch",
        "frontier_event_id",
        "artifact_id",
        "artifact_revision",
        "artifact_sha256",
        "branch",
        "head_sha",
        "disposition",
        "source_actor",
        "source_message_id",
        "source_result_id",
        "based_on_frontier_epoch",
        "supersedes_event_ids",
        "recorded_at",
    )
    missing = [field for field in required if field not in record]
    if missing:
        raise ProtocolError("FrontierRecord missing: " + ", ".join(missing))
    _frontier_key(record["repository_id"], record["lane_id"])
    epoch = record.get("frontier_epoch")
    based_on = record.get("based_on_frontier_epoch")
    if not isinstance(epoch, int) or epoch < 1:
        raise ProtocolError("FrontierRecord frontier_epoch must be positive")
    if not isinstance(based_on, int) or based_on < 0 or epoch != based_on + 1:
        raise ProtocolError(
            "FrontierRecord epoch must be based_on_frontier_epoch + 1"
        )
    for field in (
        "frontier_event_id",
        "source_message_id",
        "source_result_id",
        "recorded_at",
    ):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ProtocolError(f"FrontierRecord requires {field}")
    for field in ("artifact_id", "artifact_revision", "branch"):
        if record.get(field) is not None and (
            not isinstance(record[field], str) or not record[field].strip()
        ):
            raise ProtocolError(f"FrontierRecord {field} must be string or null")
    for field, length in (("artifact_sha256", 64), ("head_sha", 40)):
        value = record.get(field)
        if value is not None and not re.fullmatch(
            rf"[0-9a-fA-F]{{{length}}}", str(value)
        ):
            raise ProtocolError(f"FrontierRecord {field} has invalid digest")
    if record.get("disposition") not in FRONTIER_DISPOSITIONS:
        raise ProtocolError("FrontierRecord disposition is invalid")
    if record.get("source_actor") not in FRONTIER_SOURCE_ACTORS:
        raise ProtocolError("FrontierRecord source_actor is invalid")
    supersedes = record.get("supersedes_event_ids")
    if not isinstance(supersedes, list) or any(
        not isinstance(item, str) or not item for item in supersedes
    ):
        raise ProtocolError("FrontierRecord supersedes_event_ids is invalid")
    if record.get("disposition") == "none" and any(
        record.get(field) is not None
        for field in ("artifact_id", "artifact_revision", "artifact_sha256")
    ):
        raise ProtocolError("FrontierRecord disposition none cannot name an artifact")


def validate_frontier_state(state: dict[str, Any]) -> None:
    if state.get("schema_version") != FRONTIER_STATE_VERSION:
        raise ProtocolError("unsupported frontier state schema")
    if not isinstance(state.get("revision"), int) or state["revision"] < 0:
        raise ProtocolError("frontier revision must be non-negative")
    if state.get("safety_mode") not in {
        FRONTIER_SAFETY_MODE,
        "FRONTIER_VERIFIED",
    }:
        raise ProtocolError("frontier safety_mode is invalid")
    records = state.get("records")
    events = state.get("events")
    if not isinstance(records, dict) or not isinstance(events, list):
        raise ProtocolError("frontier records/events have invalid shape")
    event_by_id: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events):
        validate_frontier_record(event)
        event_id = str(event["frontier_event_id"])
        if event_id in event_by_id:
            raise ProtocolError(f"duplicate frontier event: {event_id}")
        event_by_id[event_id] = event
    for key, record in records.items():
        validate_frontier_record(record)
        if key != _frontier_key(record["repository_id"], record["lane_id"]):
            raise ProtocolError("frontier record key mismatch")
        if event_by_id.get(str(record["frontier_event_id"])) != record:
            raise ProtocolError("current frontier record must exist in event ledger")
    if not isinstance(state.get("repository_status"), dict):
        raise ProtocolError("frontier repository_status must be an object")
    for field in (
        "retired_artifacts",
        "applied_results",
        "quarantined_results",
        "failed_results",
    ):
        if not isinstance(state.get(field), list):
            raise ProtocolError(f"frontier {field} must be an array")


def _retire_frontier_artifact(
    state: dict[str, Any],
    record: dict[str, Any],
    *,
    disposition: str,
) -> None:
    artifact_id = record.get("artifact_id")
    if not artifact_id:
        return
    identity = {
        "repository_id": record["repository_id"],
        "lane_id": record["lane_id"],
        "artifact_id": artifact_id,
        "artifact_revision": record.get("artifact_revision"),
        "artifact_sha256": record.get("artifact_sha256"),
        "frontier_event_id": record["frontier_event_id"],
        "frontier_epoch": record["frontier_epoch"],
        "disposition": disposition,
        "source_actor": record["source_actor"],
        "source_precedence": FRONTIER_SOURCE_PRECEDENCE[record["source_actor"]],
    }
    retired = state.setdefault("retired_artifacts", [])
    if not any(
        isinstance(item, dict)
        and item.get("repository_id") == identity["repository_id"]
        and item.get("lane_id") == identity["lane_id"]
        and item.get("artifact_id") == identity["artifact_id"]
        and item.get("frontier_event_id") == identity["frontier_event_id"]
        for item in retired
    ):
        retired.append(identity)


def apply_frontier_event(
    state: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Apply one CAS-bound monotonic frontier event or reject it unchanged."""
    validate_frontier_state(state)
    validate_frontier_record(candidate)
    event_id = str(candidate["frontier_event_id"])
    existing_event = next(
        (
            item
            for item in state["events"]
            if isinstance(item, dict)
            and item.get("frontier_event_id") == event_id
        ),
        None,
    )
    if existing_event is not None:
        if existing_event != candidate:
            raise ProtocolError("conflicting frontier event replay")
        return {
            "classification": "FRONTIER_EVENT_ALREADY_APPLIED",
            "frontier_event_id": event_id,
            "deduplicated": True,
        }
    key = _frontier_key(candidate["repository_id"], candidate["lane_id"])
    current = state["records"].get(key)
    current_epoch = int(current.get("frontier_epoch", 0)) if current else 0
    if candidate["based_on_frontier_epoch"] != current_epoch:
        return {
            "classification": "FRONTIER_EVENT_STALE",
            "frontier_event_id": event_id,
            "expected_frontier_epoch": current_epoch,
            "based_on_frontier_epoch": candidate["based_on_frontier_epoch"],
            "deduplicated": False,
        }
    candidate_precedence = FRONTIER_SOURCE_PRECEDENCE[candidate["source_actor"]]
    if isinstance(current, dict):
        current_precedence = FRONTIER_SOURCE_PRECEDENCE[current["source_actor"]]
        if candidate_precedence < current_precedence:
            return {
                "classification": "FRONTIER_EVENT_PRECEDENCE_REJECTED",
                "frontier_event_id": event_id,
                "current_source_actor": current["source_actor"],
                "candidate_source_actor": candidate["source_actor"],
                "deduplicated": False,
            }
        changes_artifact = candidate.get("artifact_id") != current.get("artifact_id")
        if (
            changes_artifact
            and current.get("artifact_id")
            and current["frontier_event_id"]
            not in candidate.get("supersedes_event_ids", [])
        ):
            return {
                "classification": "FRONTIER_EVENT_SUPERSESSION_REQUIRED",
                "frontier_event_id": event_id,
                "current_frontier_event_id": current["frontier_event_id"],
                "deduplicated": False,
            }
    for retired in state.get("retired_artifacts", []):
        if not isinstance(retired, dict):
            continue
        same_artifact = (
            retired.get("repository_id") == candidate["repository_id"]
            and retired.get("lane_id") == candidate["lane_id"]
            and retired.get("artifact_id") == candidate.get("artifact_id")
        )
        if (
            same_artifact
            and candidate.get("disposition") in FRONTIER_ADVANCE_DISPOSITIONS
            and candidate_precedence < int(retired.get("source_precedence", 0))
        ):
            return {
                "classification": "FRONTIER_EVENT_PRECEDENCE_REJECTED",
                "frontier_event_id": event_id,
                "retired_frontier_event_id": retired.get("frontier_event_id"),
                "deduplicated": False,
            }
    next_state = copy.deepcopy(state)
    if isinstance(current, dict):
        if current.get("artifact_id") != candidate.get("artifact_id"):
            _retire_frontier_artifact(
                next_state, current, disposition="superseded"
            )
        elif candidate.get("disposition") in {
            "rejected",
            "superseded",
            "parked",
        }:
            _retire_frontier_artifact(
                next_state,
                candidate,
                disposition=str(candidate["disposition"]),
            )
    next_state["events"].append(copy.deepcopy(candidate))
    next_state["records"][key] = copy.deepcopy(candidate)
    repository_records = [
        item
        for item in next_state["records"].values()
        if isinstance(item, dict)
        and item.get("repository_id") == candidate["repository_id"]
    ]
    eligible_record = bool(repository_records) and all(
        item.get("disposition") in FRONTIER_ADVANCE_DISPOSITIONS
        and bool(item.get("artifact_id"))
        and bool(item.get("artifact_revision"))
        and bool(item.get("artifact_sha256"))
        and bool(item.get("branch"))
        and bool(item.get("head_sha"))
        for item in repository_records
    )
    next_state["repository_status"][candidate["repository_id"]] = (
        "verified" if eligible_record else "reconciliation_required"
    )
    next_state["revision"] = int(next_state["revision"]) + 1
    if all(
        status == "verified"
        for status in next_state["repository_status"].values()
    ):
        next_state["safety_mode"] = "FRONTIER_VERIFIED"
    else:
        next_state["safety_mode"] = FRONTIER_SAFETY_MODE
    validate_frontier_state(next_state)
    state.clear()
    state.update(next_state)
    return {
        "classification": "FRONTIER_EVENT_APPLIED",
        "frontier_event_id": event_id,
        "frontier_epoch": candidate["frontier_epoch"],
        "deduplicated": False,
    }


def _frontier_certificate_payload(
    record: dict[str, Any], authority_signal: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": FRONTIER_STATE_VERSION,
        "repository_id": record["repository_id"],
        "lane_id": record["lane_id"],
        "frontier_epoch": record["frontier_epoch"],
        "frontier_event_id": record["frontier_event_id"],
        "artifact_id": record.get("artifact_id"),
        "artifact_revision": record.get("artifact_revision"),
        "artifact_sha256": record.get("artifact_sha256"),
        "branch": record.get("branch"),
        "head_sha": record.get("head_sha"),
        "disposition": record["disposition"],
        "source_actor": record["source_actor"],
        "authority_fingerprint": authority_signal.get("authority_fingerprint"),
    }


def validate_authority_signal_liveness(authority_signal: Any) -> None:
    """Require independently observable Git and configured authority sources."""
    if not isinstance(authority_signal, dict):
        raise ProtocolError("authority signal must be an object")
    fingerprint = str(authority_signal.get("authority_fingerprint") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint):
        raise ProtocolError("authority signal fingerprint is invalid")
    payload = {
        key: value
        for key, value in authority_signal.items()
        if key != "authority_fingerprint"
    }
    if canonical_json_hash(payload) != fingerprint:
        raise ProtocolError("authority signal fingerprint does not match content")
    if not str(authority_signal.get("root") or ""):
        raise ProtocolError("authority signal repository root is unavailable")
    git = authority_signal.get("git")
    if not isinstance(git, dict) or git.get("status") != "present":
        raise ProtocolError("authority signal Git high-water state is unavailable")
    if not str(git.get("branch") or "").strip():
        raise ProtocolError("authority signal Git branch is unavailable")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", str(git.get("head_sha") or "")):
        raise ProtocolError("authority signal Git HEAD is invalid")
    if authority_signal.get("authority_watch_configured") is True:
        sources = authority_signal.get("sources")
        high_water_marks = authority_signal.get("high_water_marks")
        if not isinstance(sources, list) or not sources:
            raise ProtocolError("configured authority sources are missing")
        if any(
            not isinstance(source, dict)
            or source.get("status") != "present"
            or not re.fullmatch(
                r"[0-9a-fA-F]{64}", str(source.get("sha256") or "")
            )
            for source in sources
        ):
            raise ProtocolError("configured authority source is not live")
        if not isinstance(high_water_marks, list) or len(high_water_marks) != len(
            sources
        ):
            raise ProtocolError("authority high-water marks are incomplete")
        if any(
            not isinstance(mark, dict) or mark.get("exists") is not True
            for mark in high_water_marks
        ):
            raise ProtocolError("authority high-water source no longer exists")


def issue_frontier_certificate(
    state: dict[str, Any],
    repository_id: str,
    lane_id: str,
    authority_signal: dict[str, Any],
) -> dict[str, Any]:
    validate_frontier_state(state)
    key = _frontier_key(repository_id, lane_id)
    record = state["records"].get(key)
    if not isinstance(record, dict):
        raise ProtocolError("frontier certificate requires a current record")
    if record.get("disposition") not in FRONTIER_ADVANCE_DISPOSITIONS:
        raise ProtocolError("frontier certificate requires an eligible disposition")
    if (
        not str(record.get("artifact_id") or "").strip()
        or not str(record.get("artifact_revision") or "").strip()
        or not re.fullmatch(
            r"[0-9a-fA-F]{64}", str(record.get("artifact_sha256") or "")
        )
    ):
        raise ProtocolError("frontier certificate requires an exact artifact")
    if not str(record.get("branch") or "").strip():
        raise ProtocolError("frontier certificate requires an exact branch")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", str(record.get("head_sha") or "")):
        raise ProtocolError("frontier certificate requires an exact HEAD")
    if authority_signal.get("repository_id") != repository_id:
        raise ProtocolError("frontier certificate authority repository mismatch")
    validate_authority_signal_liveness(authority_signal)
    git = authority_signal.get("git")
    if not isinstance(git, dict):
        raise ProtocolError("frontier certificate requires Git high-water state")
    if git.get("branch") != record.get("branch"):
        raise ProtocolError("frontier certificate branch mismatch")
    if git.get("head_sha") != record.get("head_sha"):
        raise ProtocolError("frontier certificate head mismatch")
    payload = _frontier_certificate_payload(record, authority_signal)
    payload["certificate_id"] = canonical_json_hash(payload)
    return payload


def validate_frontier_certificate(
    certificate: Any,
    state: dict[str, Any],
    authority_signal: dict[str, Any],
    *,
    expected_artifact: dict[str, Any] | None = None,
) -> None:
    if not isinstance(certificate, dict):
        raise ProtocolError("frontier certificate must be an object")
    repository_id = str(certificate.get("repository_id") or "")
    lane_id = str(certificate.get("lane_id") or "")
    current = state.get("records", {}).get(_frontier_key(repository_id, lane_id))
    if not isinstance(current, dict):
        raise ProtocolError("frontier certificate has no current frontier")
    expected = issue_frontier_certificate(
        state, repository_id, lane_id, authority_signal
    )
    if certificate != expected:
        raise ProtocolError("frontier certificate is stale or does not match current frontier")
    if expected_artifact is not None:
        aliases = {
            "artifact_id": ("artifact_id", "id"),
            "artifact_revision": ("artifact_revision", "revision"),
            "artifact_sha256": ("artifact_sha256", "sha256"),
        }
        for frontier_field, fields in aliases.items():
            value = next(
                (expected_artifact[field] for field in fields if field in expected_artifact),
                None,
            )
            if value is not None and certificate.get(frontier_field) != value:
                raise ProtocolError("frontier certificate artifact is historical")


def frontier_gate_decision(
    state: dict[str, Any],
    repository_id: str,
    lane_id: str,
    *,
    action_kind: str,
    expected_artifact: dict[str, Any] | None,
    authority_signal: dict[str, Any] | None,
) -> dict[str, Any]:
    if action_kind in FRONTIER_RECONCILIATION_ACTION_KINDS:
        return {
            "classification": "FRONTIER_RECONCILIATION_ALLOWED",
            "reasons": [],
            "certificate": None,
        }
    if action_kind in FRONTIER_TRANSPORT_ACTION_KINDS:
        return {
            "classification": "FRONTIER_TRANSPORT_ALLOWED",
            "reasons": [],
            "certificate": None,
        }
    reasons: list[str] = []
    try:
        key = _frontier_key(repository_id, lane_id)
        record = state.get("records", {}).get(key)
        if not isinstance(record, dict):
            reasons.append("missing_frontier")
            raise ProtocolError("missing frontier")
        if record.get("disposition") == "none":
            if not isinstance(authority_signal, dict):
                reasons.append("authority_unverified")
                raise ProtocolError("authority signal missing")
            validate_authority_signal_liveness(authority_signal)
            git = authority_signal.get("git")
            if not isinstance(git, dict):
                reasons.append("authority_unverified")
                raise ProtocolError("authority Git high water missing")
            if (
                git.get("branch") != record.get("branch")
                or git.get("head_sha") != record.get("head_sha")
            ):
                reasons.append("authority_changed")
                raise ProtocolError("none frontier authority changed")
            applied = next(
                (
                    item
                    for item in state.get("applied_results", [])
                    if isinstance(item, dict)
                    and item.get("result_id") == record.get("source_result_id")
                    and item.get("frontier_event_id")
                    == record.get("frontier_event_id")
                ),
                None,
            )
            if not isinstance(applied, dict):
                # A legacy/direct tombstone still blocks ordinary work, but it
                # is not the semantic receipt that suppresses reconciliation.
                # Only a reducer-applied result can certify unchanged absence.
                return {
                    "classification": "NO_ACTIVE_CANDIDATE",
                    "reasons": ["no_candidate", "unbound_none_frontier"],
                    "certificate": None,
                    "frontier_event_id": record.get("frontier_event_id"),
                    "frontier_epoch": record.get("frontier_epoch"),
                }
            if applied.get("authority_fingerprint") != authority_signal.get(
                "authority_fingerprint"
            ):
                reasons.append("authority_changed")
                raise ProtocolError("none frontier authority fingerprint changed")
            return {
                "classification": "FRONTIER_RECONCILED_NO_ACTIVE_CANDIDATE",
                "reasons": ["no_candidate"],
                "certificate": None,
                "frontier_event_id": record.get("frontier_event_id"),
                "frontier_epoch": record.get("frontier_epoch"),
            }
        if record.get("disposition") not in FRONTIER_ADVANCE_DISPOSITIONS:
            reasons.append(
                "no_candidate"
                if record.get("disposition") == "none"
                else str(record.get("disposition") or "unresolved")
            )
            raise ProtocolError("frontier is not advanceable")
        if not isinstance(authority_signal, dict):
            reasons.append("authority_unverified")
            raise ProtocolError("authority signal missing")
        certificate = issue_frontier_certificate(
            state, repository_id, lane_id, authority_signal
        )
        try:
            validate_frontier_certificate(
                certificate,
                state,
                authority_signal,
                expected_artifact=expected_artifact,
            )
        except ProtocolError as exc:
            historical_opt_in = (
                isinstance(expected_artifact, dict)
                and expected_artifact.get("historical_review") is True
                and action_kind == "present_user_card"
            )
            required_history_fields = (
                "artifact_id",
                "artifact_revision",
                "artifact_sha256",
            )
            historical = next(
                (
                    copy.deepcopy(item)
                    for item in state.get("retired_artifacts", [])
                    if historical_opt_in
                    and isinstance(item, dict)
                    and item.get("repository_id") == repository_id
                    and item.get("lane_id") == lane_id
                    and all(
                        expected_artifact.get(field) is not None
                        and item.get(field) == expected_artifact.get(field)
                        for field in required_history_fields
                    )
                ),
                None,
            )
            if "historical" in str(exc) and isinstance(historical, dict):
                return {
                    "classification": "FRONTIER_HISTORICAL_REVIEW_ALLOWED",
                    "reasons": [],
                    "certificate": certificate,
                    "historical_artifact": historical,
                }
            raise
    except ProtocolError as exc:
        if "historical" in str(exc) and "historical" not in reasons:
            reasons.append("historical")
        elif not reasons:
            reasons.append(str(exc))
        classification = "FRONTIER_RECONCILIATION_REQUIRED"
        if "no_candidate" in reasons:
            classification = "NO_ACTIVE_CANDIDATE"
        elif "authority_unverified" in reasons or any(
            "authority" in reason.lower() for reason in reasons
        ):
            classification = "AUTHORITY_CONFLICT"
        return {
            "classification": classification,
            "reasons": reasons,
            "certificate": None,
        }
    return {
        "classification": "FRONTIER_CERTIFIED",
        "reasons": [],
        "certificate": certificate,
    }


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


def portfolio_semantic_fingerprint(portfolio: dict[str, Any]) -> str:
    semantic = copy.deepcopy(portfolio)
    semantic.pop("semantic_fingerprint", None)
    for field in ("generated_at", "observed_at", "rendered_at"):
        semantic.pop(field, None)
    return canonical_json_hash(semantic)


def migrate_portfolio_to_frontier_v3(
    portfolio: dict[str, Any],
    frontier_state: dict[str, Any],
    authority_signals: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    if portfolio.get("schema_version") == FRONTIER_PORTFOLIO_VERSION:
        migrated = copy.deepcopy(portfolio)
    else:
        validate_portfolio_status(portfolio)
        migrated = copy.deepcopy(portfolio)
        migrated["schema_version"] = FRONTIER_PORTFOLIO_VERSION
    validate_frontier_state(frontier_state)
    signal_by_repository = {
        str(item.get("repository_id") or ""): item
        for item in authority_signals
        if isinstance(item, dict) and item.get("repository_id")
    }
    migrated["frontier_revision"] = frontier_state["revision"]
    migrated["frontier_safety_mode"] = frontier_state["safety_mode"]
    for row in migrated.get("repositories", []):
        if not isinstance(row, dict):
            continue
        repository_id = str(row.get("repository_id") or "")
        matches = [
            record
            for record in frontier_state.get("records", {}).values()
            if isinstance(record, dict)
            and record.get("repository_id") == repository_id
        ]
        record = max(
            matches,
            key=lambda item: int(item.get("frontier_epoch", 0)),
            default=None,
        )
        signal = signal_by_repository.get(repository_id)
        certificate = None
        if isinstance(record, dict) and isinstance(signal, dict):
            try:
                certificate = issue_frontier_certificate(
                    frontier_state,
                    repository_id,
                    str(record["lane_id"]),
                    signal,
                )
            except ProtocolError:
                certificate = None
        if certificate is not None:
            row["frontier_status"] = "verified"
            row["frontier_certificate"] = certificate
            row["frontier_epoch"] = record["frontier_epoch"]
            row["frontier_disposition"] = record["disposition"]
            row["active_artifact"] = {
                "artifact_id": record.get("artifact_id"),
                "artifact_revision": record.get("artifact_revision"),
                "artifact_sha256": record.get("artifact_sha256"),
            }
        else:
            repository_status = str(
                frontier_state.get("repository_status", {}).get(
                    repository_id, "reconciliation_required"
                )
            )
            row["frontier_status"] = (
                repository_status
                if repository_status in {
                    "legacy_unverified",
                    "reconciliation_required",
                }
                else "reconciliation_required"
            )
            row["frontier_certificate"] = None
            row["frontier_epoch"] = (
                record.get("frontier_epoch") if isinstance(record, dict) else None
            )
            row["frontier_disposition"] = (
                record.get("disposition") if isinstance(record, dict) else None
            )
            row["active_artifact"] = None
    migrated["semantic_fingerprint"] = portfolio_semantic_fingerprint(migrated)
    validate_portfolio_status(migrated)
    return migrated


def validate_portfolio_frontier_consistency(
    portfolio: dict[str, Any],
    frontier_state: dict[str, Any],
    authority_signals: Iterable[dict[str, Any]],
) -> None:
    validate_portfolio_status(portfolio)
    validate_frontier_state(frontier_state)
    if portfolio.get("schema_version") not in {
        FRONTIER_PORTFOLIO_VERSION,
        PROJECT_CONTEXT_PORTFOLIO_VERSION,
    }:
        raise ProtocolError(
            "portfolio frontier projection requires schema_version 3 or 4"
        )
    if portfolio.get("frontier_revision") != frontier_state.get("revision"):
        raise ProtocolError("portfolio frontier revision is stale")
    if portfolio.get("frontier_safety_mode") != frontier_state.get("safety_mode"):
        raise ProtocolError("portfolio frontier safety mode is stale")
    signal_by_repository = {
        str(item.get("repository_id") or ""): item
        for item in authority_signals
        if isinstance(item, dict) and item.get("repository_id")
    }
    for row in portfolio.get("repositories", []):
        repository_id = str(row.get("repository_id") or "")
        certificate = row.get("frontier_certificate")
        status = str(row.get("frontier_status") or "")
        if certificate is None:
            if status not in {"legacy_unverified", "reconciliation_required"}:
                raise ProtocolError(
                    f"portfolio frontier {repository_id} lacks a certificate"
                )
            continue
        signal = signal_by_repository.get(repository_id)
        if not isinstance(signal, dict):
            raise ProtocolError(
                f"portfolio frontier {repository_id} lacks authority signal"
            )
        validate_frontier_certificate(certificate, frontier_state, signal)
        if status != "verified":
            raise ProtocolError(
                f"portfolio frontier {repository_id} certificate is not verified"
            )
        if row.get("frontier_epoch") != certificate.get("frontier_epoch"):
            raise ProtocolError(
                f"portfolio frontier {repository_id} epoch is stale"
            )
        if row.get("frontier_disposition") != certificate.get("disposition"):
            raise ProtocolError(
                f"portfolio frontier {repository_id} disposition is stale"
            )


def _portfolio_project_context_projection(
    record: dict[str, Any]
) -> dict[str, Any]:
    return {
        "project_context_revision": record["project_context_revision"],
        "project_context_event_id": record["project_context_event_id"],
        "authority_revision": record["authority_revision"],
        "authority_fingerprint": record["authority_fingerprint"],
        "north_star": record["north_star"],
        "current_bottleneck": record["current_bottleneck"],
        "completion_definition": record["completion_definition"],
        "roadmap": copy.deepcopy(record["roadmap"]),
        "active_lanes": copy.deepcopy(record["active_lanes"]),
        "lane_frontier_event_ids": copy.deepcopy(
            record["lane_frontier_event_ids"]
        ),
        "cross_lane_conflicts": copy.deepcopy(record["cross_lane_conflicts"]),
        "decisions_since_prior": copy.deepcopy(record["decisions_since_prior"]),
        "evidence_manifest": copy.deepcopy(record["evidence_manifest"]),
        "omitted_evidence": copy.deepcopy(record["omitted_evidence"]),
    }


def migrate_portfolio_to_project_context_v4(
    portfolio: dict[str, Any],
    project_context_state: dict[str, Any],
    frontier_state: dict[str, Any],
    authority_signals: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    signals = list(authority_signals)
    if portfolio.get("schema_version") == PROJECT_CONTEXT_PORTFOLIO_VERSION:
        migrated = copy.deepcopy(portfolio)
    else:
        migrated = migrate_portfolio_to_frontier_v3(
            portfolio, frontier_state, signals
        )
    validate_project_context_state(project_context_state)
    migrated["schema_version"] = PROJECT_CONTEXT_PORTFOLIO_VERSION
    migrated["project_context_revision"] = project_context_state["revision"]
    signal_by_repository = {
        str(item.get("repository_id") or ""): item
        for item in signals
        if isinstance(item, dict) and item.get("repository_id")
    }
    migrated["project_context_safety_mode"] = (
        effective_project_context_safety_mode(
            project_context_state,
            frontier_state,
            signals,
            (
                str(row.get("repository_id") or "")
                for row in migrated.get("repositories", [])
                if isinstance(row, dict)
            ),
        )
    )
    for row in migrated.get("repositories", []):
        if not isinstance(row, dict):
            continue
        repository_id = str(row.get("repository_id") or "")
        record = project_context_state.get("contexts", {}).get(repository_id)
        certified = False
        if isinstance(record, dict):
            try:
                _project_context_current_requirements(
                    project_context_state,
                    frontier_state,
                    repository_id,
                    signal_by_repository.get(repository_id),
                )
                certified = True
            except ProtocolError:
                certified = False
        if certified:
            row["project_context_status"] = "verified"
            row["project_context_revision"] = record[
                "project_context_revision"
            ]
            row["project_context_event_id"] = record[
                "project_context_event_id"
            ]
            row["project_context"] = _portfolio_project_context_projection(
                record
            )
            row["roadmap"] = copy.deepcopy(record["roadmap"])
        else:
            status = str(
                project_context_state.get("repository_status", {}).get(
                    repository_id, "legacy_unverified"
                )
            )
            row["project_context_status"] = (
                status
                if status
                in {
                    "legacy_unverified",
                    "reconciliation_required",
                    "authority_conflict",
                }
                else "reconciliation_required"
            )
            row["project_context_revision"] = (
                record.get("project_context_revision")
                if isinstance(record, dict)
                else None
            )
            row["project_context_event_id"] = (
                record.get("project_context_event_id")
                if isinstance(record, dict)
                else None
            )
            row["project_context"] = None
            # Context reconciliation invalidates ordinary running/ready work,
            # but it must not erase a durable user/external wait, stop, or
            # terminal state already projected by the deterministic plan.
            # Those control-plane states remain actionable while ordinary work
            # is gated and must stay aligned with next_user_action/routes.
            if row.get("state") in {"RUNNING", "READY"}:
                row["state"] = "READY"
                row["why"] = (
                    "The project-wide current context is missing or stale."
                )
                row["next_move"] = (
                    "Reconcile the project context against every active lane and "
                    "the current authority observation."
                )
            row["roadmap"] = {
                "overall_position": "project context reconciliation",
                "current_block": "verify the current project position",
                "next_gate": "ProjectContextRecord accepted",
                "completion_definition": (
                    "North star, roadmap, evidence, and every active lane "
                    "frontier agree."
                ),
                "completed_blocks": [],
                "next_blocks": ["apply one exact reconciliation event"],
            }
    migrated["semantic_fingerprint"] = portfolio_semantic_fingerprint(migrated)
    validate_portfolio_status(migrated)
    return migrated


def validate_portfolio_project_context_consistency(
    portfolio: dict[str, Any],
    project_context_state: dict[str, Any],
    frontier_state: dict[str, Any],
    authority_signals: Iterable[dict[str, Any]],
) -> None:
    validate_portfolio_status(portfolio)
    if portfolio.get("schema_version") != PROJECT_CONTEXT_PORTFOLIO_VERSION:
        raise ProtocolError(
            "portfolio project-context projection requires schema_version 4"
        )
    expected = migrate_portfolio_to_project_context_v4(
        portfolio,
        project_context_state,
        frontier_state,
        list(authority_signals),
    )
    for field in (
        "project_context_revision",
        "project_context_safety_mode",
    ):
        if portfolio.get(field) != expected.get(field):
            raise ProtocolError(f"portfolio {field} is stale")
    expected_by_repository = {
        str(row.get("repository_id") or ""): row
        for row in expected.get("repositories", [])
        if isinstance(row, dict)
    }
    for row in portfolio.get("repositories", []):
        repository_id = str(row.get("repository_id") or "")
        expected_row = expected_by_repository.get(repository_id, {})
        for field in (
            "project_context_status",
            "project_context_revision",
            "project_context_event_id",
            "project_context",
            "roadmap",
            "state",
            "why",
            "next_move",
        ):
            if row.get(field) != expected_row.get(field):
                raise ProtocolError(
                    f"portfolio project context {repository_id} {field} is stale"
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
    version = portfolio.get("schema_version")
    if version not in {
        2,
        FRONTIER_PORTFOLIO_VERSION,
        PROJECT_CONTEXT_PORTFOLIO_VERSION,
    }:
        raise ProtocolError("portfolio status schema_version must be 2, 3, or 4")
    if not re.fullmatch(
        r"[0-9a-fA-F]{64}", str(portfolio.get("semantic_fingerprint") or "")
    ):
        raise ProtocolError("portfolio semantic_fingerprint must be SHA-256")
    if version in {
        FRONTIER_PORTFOLIO_VERSION,
        PROJECT_CONTEXT_PORTFOLIO_VERSION,
    }:
        if portfolio.get("semantic_fingerprint") != portfolio_semantic_fingerprint(
            portfolio
        ):
            raise ProtocolError(
                "portfolio semantic_fingerprint does not match content"
            )
        if not isinstance(portfolio.get("frontier_revision"), int) or portfolio[
            "frontier_revision"
        ] < 0:
            raise ProtocolError(
                "portfolio frontier_revision must be non-negative"
            )
        if portfolio.get("frontier_safety_mode") not in {
            FRONTIER_SAFETY_MODE,
            "FRONTIER_VERIFIED",
        }:
            raise ProtocolError("portfolio frontier_safety_mode is invalid")
    if version == PROJECT_CONTEXT_PORTFOLIO_VERSION:
        if not isinstance(portfolio.get("project_context_revision"), int) or portfolio[
            "project_context_revision"
        ] < 0:
            raise ProtocolError(
                "portfolio project_context_revision must be non-negative"
            )
        if portfolio.get("project_context_safety_mode") not in {
            PROJECT_CONTEXT_SAFETY_MODE,
            "PROJECT_CONTEXT_VERIFIED",
        }:
            raise ProtocolError(
                "portfolio project_context_safety_mode is invalid"
            )
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
    if "next_user_action" not in portfolio:
        raise ProtocolError("portfolio next_user_action is required")
    next_user_action = portfolio.get("next_user_action")
    if next_user_action is not None:
        if not isinstance(next_user_action, dict):
            raise ProtocolError("portfolio next_user_action must be an object or null")
        required_action_fields = (
            "repository_id",
            "kind",
            "purpose",
            "why_now",
            "entrypoint",
            "reply_format",
            "owner",
            "post_reply_behavior",
            "non_escalation_boundary",
        )
        for field in required_action_fields:
            if not isinstance(next_user_action.get(field), str) or not str(
                next_user_action[field]
            ).strip():
                raise ProtocolError(f"portfolio next_user_action {field} is required")
        if next_user_action.get("kind") not in {"USER_DECISION", "USER_ACTION"}:
            raise ProtocolError("portfolio next_user_action kind is invalid")
        requirements = next_user_action.get("requirements")
        if not isinstance(requirements, list) or not requirements or any(
            not isinstance(item, str) or not item.strip() for item in requirements
        ):
            raise ProtocolError(
                "portfolio next_user_action requirements must be a non-empty string array"
            )
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
        if version in {
            FRONTIER_PORTFOLIO_VERSION,
            PROJECT_CONTEXT_PORTFOLIO_VERSION,
        }:
            if row.get("frontier_status") not in {
                "verified",
                "legacy_unverified",
                "reconciliation_required",
            }:
                raise ProtocolError("portfolio frontier_status is invalid")
            if row.get("frontier_certificate") is not None and not isinstance(
                row.get("frontier_certificate"), dict
            ):
                raise ProtocolError(
                    "portfolio frontier_certificate must be object or null"
                )
        if version == PROJECT_CONTEXT_PORTFOLIO_VERSION:
            if row.get("project_context_status") not in {
                "verified",
                "legacy_unverified",
                "reconciliation_required",
                "authority_conflict",
            }:
                raise ProtocolError("portfolio project_context_status is invalid")
            if row.get("project_context") is not None and not isinstance(
                row.get("project_context"), dict
            ):
                raise ProtocolError(
                    "portfolio project_context must be object or null"
                )
            if row.get("project_context_status") == "verified":
                context_projection = row.get("project_context")
                if not isinstance(context_projection, dict):
                    raise ProtocolError(
                        "verified portfolio project context requires its projection"
                    )
                if row.get("project_context_revision") != context_projection.get(
                    "project_context_revision"
                ) or row.get("project_context_event_id") != context_projection.get(
                    "project_context_event_id"
                ):
                    raise ProtocolError(
                        "portfolio project context identity is inconsistent"
                    )
                if row.get("roadmap") != context_projection.get("roadmap"):
                    raise ProtocolError(
                        "portfolio roadmap differs from certified project context"
                    )
            elif row.get("project_context") is not None:
                raise ProtocolError(
                    "unverified portfolio project context must not expose a stale projection"
                )
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
        roadmap = row.get("roadmap")
        if not isinstance(roadmap, dict):
            raise ProtocolError("portfolio roadmap is required")
        for field in (
            "overall_position",
            "current_block",
            "completion_definition",
            "next_gate",
        ):
            if not isinstance(roadmap.get(field), str) or not roadmap[field].strip():
                raise ProtocolError(f"portfolio roadmap {field} is required")
        for field in ("completed_blocks", "next_blocks"):
            values = roadmap.get(field)
            if not isinstance(values, list) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise ProtocolError(f"portfolio roadmap {field} is invalid")
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
    waiting_user_ids = {
        str(row.get("repository_id") or "")
        for row in repositories
        if row.get("state") == "WAITING_USER"
    }
    if waiting_user_ids and next_user_action is None:
        raise ProtocolError(
            "portfolio WAITING_USER requires one complete next_user_action"
        )
    if next_user_action is not None and str(
        next_user_action.get("repository_id") or ""
    ) not in waiting_user_ids:
        raise ProtocolError(
            "portfolio next_user_action must identify a WAITING_USER repository"
        )


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
    if portfolio.get("schema_version") == PROJECT_CONTEXT_PORTFOLIO_VERSION:
        lines.extend(
            [
                f"- Project context: `{_markdown_cell(portfolio.get('project_context_safety_mode'))}`",
                f"- Project-context revision: `{_markdown_cell(portfolio.get('project_context_revision'))}`",
            ]
        )
    next_user_action = portfolio.get("next_user_action")
    lines.extend(["", "## Next user action", ""])
    if next_user_action is None:
        lines.append("None. The Coordinator can continue without user input.")
    else:
        action_project = next(
            (
                row.get("project_name") or row.get("repository_id")
                for row in repositories
                if row.get("repository_id") == next_user_action.get("repository_id")
            ),
            next_user_action.get("repository_id"),
        )
        lines.extend(
            [
                f"- Project: {_markdown_cell(action_project)}",
                f"- Kind: `{_markdown_cell(next_user_action['kind'])}`",
                f"- Purpose: {_markdown_cell(next_user_action['purpose'])}",
                f"- Why now: {_markdown_cell(next_user_action['why_now'])}",
                f"- Entrypoint: {_markdown_cell(next_user_action['entrypoint'])}",
                f"- Owner: {_markdown_cell(next_user_action['owner'])}",
                f"- Reply format: {_markdown_cell(next_user_action['reply_format'])}",
                "- Requirements:",
            ]
        )
        lines.extend(
            f"  - {_markdown_cell(item)}"
            for item in next_user_action["requirements"]
        )
        lines.extend(
            [
                f"- After reply: {_markdown_cell(next_user_action['post_reply_behavior'])}",
                f"- Boundary: {_markdown_cell(next_user_action['non_escalation_boundary'])}",
            ]
        )
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
    lines.extend(
        [
            "",
            "## Project roadmap position",
            "",
            "| Project | Overall position | Current block | Completed blocks | Next blocks | Next gate | Completion definition |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in repositories:
        roadmap = row["roadmap"]
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    row.get("project_name") or row["repository_id"],
                    roadmap["overall_position"],
                    roadmap["current_block"],
                    ", ".join(roadmap["completed_blocks"]) or "none",
                    ", ".join(roadmap["next_blocks"]) or "none",
                    roadmap["next_gate"],
                    roadmap["completion_definition"],
                )
            )
            + " |"
        )
    if portfolio.get("schema_version") == PROJECT_CONTEXT_PORTFOLIO_VERSION:
        lines.extend(
            [
                "",
                "## Certified project context",
                "",
                "| Project | Status | North star | Current bottleneck | Active lanes | Evidence | Omitted |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        for row in repositories:
            context_projection = row.get("project_context")
            if isinstance(context_projection, dict):
                values = (
                    row.get("project_name") or row["repository_id"],
                    row.get("project_context_status"),
                    context_projection.get("north_star"),
                    context_projection.get("current_bottleneck"),
                    ", ".join(context_projection.get("active_lanes", [])),
                    str(len(context_projection.get("evidence_manifest", []))),
                    str(len(context_projection.get("omitted_evidence", []))),
                )
            else:
                values = (
                    row.get("project_name") or row["repository_id"],
                    row.get("project_context_status"),
                    "not certified",
                    "reconciliation required",
                    "not certified",
                    "0",
                    "unknown",
                )
            lines.append(
                "| "
                + " | ".join(_markdown_cell(value) for value in values)
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
    frontier_state: dict[str, Any] | None = None,
    authority_signals: Iterable[dict[str, Any]] = (),
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
    frontier_view = (
        migrate_frontier_state(
            frontier_state,
            (
                str(item.get("repository_id") or "")
                for item in registry.get("repositories", [])
                if isinstance(item, dict)
            ),
        )
        if frontier_state is not None
        else None
    )
    frontier_signals = {
        str(item.get("repository_id") or ""): item
        for item in authority_signals
        if isinstance(item, dict) and item.get("repository_id")
    }
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
        selected_mission: dict[str, Any] | None = None
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
        if frontier_view is not None:
            action_kind = _selection_action_kind(
                {"selection_reason": reason}
            )
            expected_artifact = (
                copy.deepcopy(selected_mission.get("active_artifact"))
                if isinstance(selected_mission, dict)
                and isinstance(selected_mission.get("active_artifact"), dict)
                else None
            )
            frontier_decision = frontier_gate_decision(
                frontier_view,
                repository_id,
                selected_lane,
                action_kind=action_kind,
                expected_artifact=expected_artifact,
                authority_signal=frontier_signals.get(repository_id),
            )
            if frontier_decision["classification"] != "FRONTIER_CERTIFIED":
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
    if event.get("state") == "delivery_acknowledged":
        if event.get("recipient_thread_id") != recipient_thread_id:
            raise ProtocolError("conflicting Coordinator event delivery replay")
        return {
            "classification": "COORDINATOR_EVENT_DELIVERY_ALREADY_ACKNOWLEDGED",
            "event_id": event_id,
            "recipient_thread_id": recipient_thread_id,
            "semantic_result_applied": False,
            "deduplicated": True,
        }
    event["state"] = "delivery_acknowledged"
    event["recipient_thread_id"] = recipient_thread_id
    event["delivery_acknowledged_at"] = utc_now()
    return {
        "classification": "COORDINATOR_EVENT_DELIVERY_ACKNOWLEDGED",
        "event_id": event_id,
        "recipient_thread_id": recipient_thread_id,
        "semantic_result_applied": False,
        "deduplicated": False,
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


def _is_applied_frontier_context_continuation(
    action: dict[str, Any], scheduler_state: dict[str, Any]
) -> bool:
    """Bind a context handoff to the exact frontier result it must continue."""
    if action.get("kind") != "reconcile_project_context":
        return False
    repository_id = _action_repository_id(action)
    payload = action.get("payload", {})
    records = payload.get("current_lane_frontiers", []) if isinstance(
        payload, dict
    ) else []
    frontier_event_ids = {
        str(record.get("frontier_event_id") or "")
        for record in records
        if isinstance(record, dict) and record.get("frontier_event_id")
    }
    if not repository_id or not frontier_event_ids:
        return False
    return any(
        isinstance(completed, dict)
        and completed.get("kind") == "reconcile_repository_frontier"
        and completed.get("repository_id") == repository_id
        and completed.get("outcome") == "result_applied"
        and completed.get("external_lifecycle_state") == "result_applied"
        and isinstance(completed.get("evidence"), dict)
        and str(completed["evidence"].get("frontier_event_id") or "")
        in frontier_event_ids
        for completed in scheduler_state.get("completed_actions", [])
    )


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
        "reconcile_repository_frontier",
        "reconcile_project_context",
        "resolve_mission_value_gate",
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
        if action.get("requires_external_result") is True:
            status = str(result.get("status") or "claimed")
            lifecycle_state = (
                "dispatched" if status in {"sent", "waiting"} else "created"
            )
            result.setdefault("external_lifecycle_state", lifecycle_state)
            result.setdefault(
                "external_lifecycle_history",
                [
                    {
                        "state": lifecycle_state,
                        "at": str(
                            result.get("sent_at")
                            or result.get("claimed_at")
                            or "legacy-v2"
                        ),
                    }
                ],
            )
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
        if not all(
            isinstance(item, dict)
            for item in migrated.get("completed_actions", [])
        ):
            raise ProtocolError("completed_actions must contain objects")
        for item in migrated.get("completed_actions", []):
            if item.get("requires_external_result") is True:
                item.setdefault(
                    "external_lifecycle_state", "legacy_unverified"
                )
                item.setdefault(
                    "external_lifecycle_history",
                    [
                        {
                            "state": "legacy_unverified",
                            "at": str(item.get("completed_at") or "legacy-v2"),
                        }
                    ],
                )
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
    for item in migrated.get("completed_actions", []):
        if isinstance(item, dict) and item.get("requires_external_result") is True:
            item.setdefault("external_lifecycle_state", "legacy_unverified")
            item.setdefault(
                "external_lifecycle_history",
                [
                    {
                        "state": "legacy_unverified",
                        "at": str(item.get("completed_at") or "legacy-v1"),
                    }
                ],
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
    if action.get("requires_external_result") is True:
        lifecycle_state = item.get("external_lifecycle_state")
        if lifecycle_state not in EXTERNAL_RESULT_LIFECYCLE_STATES:
            raise ProtocolError(f"{label} has invalid external lifecycle state")
        history = item.get("external_lifecycle_history")
        if not isinstance(history, list) or not history:
            raise ProtocolError(f"{label} requires external lifecycle history")
        if any(
            not isinstance(entry, dict)
            or entry.get("state") not in EXTERNAL_RESULT_LIFECYCLE_STATES
            or not str(entry.get("at") or "")
            for entry in history
        ):
            raise ProtocolError(f"{label} external lifecycle history is invalid")
        if history[-1].get("state") != lifecycle_state:
            raise ProtocolError(f"{label} lifecycle history is not current")
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
            lifecycle_state = item.get("external_lifecycle_state")
            if lifecycle_state not in (
                EXTERNAL_RESULT_LIFECYCLE_STATES
                | EXTERNAL_RESULT_COMPATIBILITY_STATES
            ):
                raise ProtocolError(
                    f"completed_actions[{index}] has invalid external lifecycle state"
                )
            if lifecycle_state == "result_applied":
                evidence = item.get("evidence")
                authorized_runtime_result = item.get("kind") in (
                    AUTHORIZED_RUNTIME_ACTION_KINDS
                )
                if not authorized_runtime_result and (
                    not isinstance(evidence, dict)
                    or not str(evidence.get("result_id") or "")
                ):
                    raise ProtocolError(
                        f"completed_actions[{index}] requires structured result evidence"
                    )
            elif not str(item.get("evidence") or "").strip():
                raise ProtocolError(
                    f"completed_actions[{index}] requires legacy result evidence"
                )
        completed_seen.add(action_id)


def validate_scheduler_state(state: dict[str, Any]) -> None:
    if state.get("schema_version") == 1:
        migrate_scheduler_state(state)
        return
    _validate_scheduler_state_v2(state)


def _git_observe(root: Path, *arguments: str) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _repository_git_high_water(root: Path) -> dict[str, Any]:
    if not (root / ".git").exists():
        return {
            "status": "not_repository",
            "branch": None,
            "head_sha": None,
            "dirty": None,
            "dirty_paths": [],
            "error": None,
        }
    inside_code, inside, inside_error = _git_observe(
        root, "rev-parse", "--is-inside-work-tree"
    )
    if inside_code != 0 or inside.casefold() != "true":
        return {
            "status": "not_repository",
            "branch": None,
            "head_sha": None,
            "dirty": None,
            "dirty_paths": [],
            "error": inside_error or None,
        }
    _, branch, _ = _git_observe(root, "branch", "--show-current")
    head_code, head_sha, head_error = _git_observe(root, "rev-parse", "HEAD")
    _, remote, _ = _git_observe(root, "remote", "get-url", "origin")
    _, status_text, _ = _git_observe(
        root, "status", "--porcelain=v1", "--untracked-files=all"
    )
    dirty_lines = [line for line in status_text.splitlines() if line]
    dirty_paths = sorted(
        {
            line[3:].split(" -> ")[-1]
            for line in dirty_lines
            if len(line) > 3
        }
    )
    upstream_code, upstream, _ = _git_observe(
        root, "rev-parse", "--abbrev-ref", "@{u}"
    )
    ahead = behind = None
    if upstream_code == 0 and upstream:
        parity_code, parity, _ = _git_observe(
            root, "rev-list", "--left-right", "--count", f"HEAD...{upstream}"
        )
        if parity_code == 0:
            parts = parity.split()
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                ahead, behind = (int(parts[0]), int(parts[1]))
    _, commit_time, _ = _git_observe(root, "show", "-s", "--format=%cI", "HEAD")
    _, commit_count, _ = _git_observe(root, "rev-list", "--count", "HEAD")
    operation = "none"
    for marker, name in (
        ("MERGE_HEAD", "merge"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
    ):
        marker_code, marker_path, _ = _git_observe(
            root, "rev-parse", "--git-path", marker
        )
        observed_marker = Path(marker_path)
        if not observed_marker.is_absolute():
            observed_marker = root / observed_marker
        if marker_code == 0 and marker_path and observed_marker.is_file():
            operation = name
            break
        if marker_code == 0 and marker_path and observed_marker.is_dir():
            operation = name
            break
    return {
        "status": "present" if head_code == 0 else "unborn",
        "branch": branch or None,
        "head_sha": head_sha.lower() if head_code == 0 else None,
        "head_commit_time": commit_time or None,
        "commit_count": int(commit_count) if commit_count.isdigit() else None,
        "remote_origin": remote or None,
        "upstream": upstream if upstream_code == 0 and upstream else None,
        "ahead": ahead,
        "behind": behind,
        "dirty": bool(dirty_lines),
        "dirty_paths": dirty_paths,
        "operation": operation,
        "error": head_error or None,
    }


def _authority_file_high_water(root: Path, relative_path: Path) -> dict[str, Any]:
    portable = relative_path.as_posix()
    candidate = root / relative_path
    if not (root / ".git").exists():
        return {
            "path": portable,
            "exists": candidate.is_file(),
            "modified_time_ns": (
                candidate.stat().st_mtime_ns if candidate.is_file() else None
            ),
            "is_dirty": None,
            "git_blob_sha": None,
            "last_commit_sha": None,
            "last_commit_time": None,
        }
    status_code, status_text, _ = _git_observe(
        root, "status", "--porcelain=v1", "--", portable
    )
    log_code, log_text, _ = _git_observe(
        root, "log", "-1", "--format=%H%x09%cI", "--", portable
    )
    stage_code, stage_text, _ = _git_observe(
        root, "ls-files", "--stage", "--", portable
    )
    last_commit_sha = last_commit_time = None
    if log_code == 0 and log_text:
        log_parts = log_text.split("\t", 1)
        last_commit_sha = log_parts[0].lower()
        last_commit_time = log_parts[1] if len(log_parts) == 2 else None
    git_blob_sha = None
    if stage_code == 0 and stage_text:
        stage_parts = stage_text.split()
        if len(stage_parts) >= 2:
            git_blob_sha = stage_parts[1].lower()
    return {
        "path": portable,
        "exists": candidate.is_file(),
        "modified_time_ns": candidate.stat().st_mtime_ns if candidate.is_file() else None,
        "is_dirty": bool(status_text) if status_code == 0 else None,
        "git_blob_sha": git_blob_sha,
        "last_commit_sha": last_commit_sha,
        "last_commit_time": last_commit_time,
    }


def collect_authority_signals(
    registry: dict[str, Any],
    hosts: dict[str, Any],
    adapter: dict[str, Any],
) -> list[dict[str, Any]]:
    """Collect registered authority plus repository/file high-water marks."""
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
        if not repository_id or not isinstance(configured, list):
            continue
        root_value = roots.get(repository_id)
        root = Path(str(root_value)).resolve() if root_value else None
        sources: list[dict[str, Any]] = []
        high_water_marks: list[dict[str, Any]] = []
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
                high_water_marks.append(
                    _authority_file_high_water(root, relative_path)
                )
            sources.append(source)
        signal_payload = {
            "repository_id": repository_id,
            "root": str(root) if root is not None else None,
            "sources": sources,
            "authority_watch_configured": bool(configured),
            "git": (
                _repository_git_high_water(root)
                if root is not None and root.is_dir()
                else {
                    "status": "root_unavailable",
                    "branch": None,
                    "head_sha": None,
                    "dirty": None,
                    "dirty_paths": [],
                }
            ),
            "high_water_marks": high_water_marks,
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
        "resolve_mission_value_gate",
    "return_worker_result",
    "return_authorized_runtime_recovery_result",
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


def _supervisor_reconciliation_route(
    registry: dict[str, Any],
    adapter: dict[str, Any],
    repository_id: str,
    lane: str,
) -> dict[str, Any]:
    """Return the exact Supervisor route for a control-plane reconciliation.

    Frontier and project-context reconciliation decide which durable project
    state is current.  Completing that decision as a local observation can
    permanently suppress the action while leaving the ledger unchanged.  The
    exact repository/lane Supervisor therefore owns the semantic result.
    """
    supervisor = _supervisor_binding_for(registry, repository_id, lane)
    recipient_thread_id = (
        supervisor.get("supervisor_thread_id")
        if isinstance(supervisor, dict)
        and supervisor.get("binding_status") == "active"
        else None
    )
    return {
        "repository_id": repository_id,
        "mission_id": None,
        "attempt_id": None,
        "supervision_lane": lane,
        "recipient_kind": "supervisor",
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
    actor_task_id: str | None = None,
) -> dict[str, Any]:
    """Register one exact Supervisor-authorized, fixed-handler recovery."""
    require_primary_coordinator_writer(
        coordinator_state, actor_task_id=actor_task_id
    )
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
            "status_matrix": copy.deepcopy(
                AUTHORIZED_RUNTIME_PROBE_STATUS_MATRIX
            ),
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


def _frontier_action_context(
    action: dict[str, Any],
    registry: dict[str, Any],
    missions: Iterable[dict[str, Any]],
) -> tuple[str, str, dict[str, Any] | None]:
    repository_id = _action_repository_id(action)
    route = action.get("payload", {}).get("route", {})
    lane = str(route.get("supervision_lane") or "") if isinstance(route, dict) else ""
    if not lane:
        lane = next(
            (
                str(item.get("default_supervision_lane") or "default")
                for item in registry.get("repositories", [])
                if isinstance(item, dict)
                and item.get("repository_id") == repository_id
            ),
            "default",
        )
    selection = action.get("payload", {}).get("selection", {})
    selection = selection if isinstance(selection, dict) else {}
    mission_id = selection.get("mission_id")
    attempt_id = selection.get("attempt_id")
    mission = next(
        (
            item
            for item in missions
            if isinstance(item, dict)
            and item.get("repository_id") == repository_id
            and (mission_id is None or item.get("mission_id") == mission_id)
            and (
                attempt_id is None
                or str(item.get("attempt_id") or "") == str(attempt_id or "")
            )
        ),
        None,
    )
    expected_artifact = None
    if isinstance(mission, dict) and isinstance(mission.get("active_artifact"), dict):
        expected_artifact = copy.deepcopy(mission["active_artifact"])
    card = action.get("payload", {}).get("card")
    if isinstance(card, dict):
        artifact = card.get("artifact") or card.get("related_artifact")
        if isinstance(artifact, dict):
            expected_artifact = copy.deepcopy(artifact)
        elif isinstance(artifact, str) and artifact:
            expected_artifact = {"artifact_id": artifact}
        if (
            card.get("historical_review") is True
            and isinstance(expected_artifact, dict)
        ):
            expected_artifact["historical_review"] = True
    return repository_id, lane, expected_artifact


def _bind_frontier_certificate_to_action(
    action: dict[str, Any], certificate: dict[str, Any]
) -> dict[str, Any]:
    bound = copy.deepcopy(action)
    payload = bound.setdefault("payload", {})
    payload["frontier_certificate"] = copy.deepcopy(certificate)
    identity = {
        "kind": bound["kind"],
        "payload": _semantic_scheduler_value(payload),
    }
    bound["action_id"] = canonical_json_hash(identity)[:32]
    return bound


def _bind_supervisor_context_to_action(
    action: dict[str, Any], envelope: dict[str, Any]
) -> dict[str, Any]:
    bound = copy.deepcopy(action)
    payload = bound.setdefault("payload", {})
    payload["supervisor_context_envelope"] = copy.deepcopy(envelope)
    identity = {
        "kind": bound["kind"],
        "payload": _semantic_scheduler_value(payload),
    }
    bound["action_id"] = canonical_json_hash(identity)[:32]
    return bound


def build_coordinator_plan(
    registry: dict[str, Any],
    hosts: dict[str, Any],
    adapter: dict[str, Any],
    missions: Iterable[dict[str, Any]],
    coordinator_state: dict[str, Any],
    scheduler_state: dict[str, Any],
    *,
    authority_signals: Iterable[dict[str, Any]] | None = None,
    frontier_state: dict[str, Any] | None = None,
    project_context_state: dict[str, Any] | None = None,
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
    frontier_view = (
        migrate_frontier_state(frontier_state, repository_order)
        if frontier_state is not None
        else None
    )
    project_context_view = (
        migrate_project_context_state(project_context_state, repository_order)
        if project_context_state is not None
        else None
    )
    effective_context_safety_mode = (
        effective_project_context_safety_mode(
            project_context_view,
            frontier_view,
            signals,
            repository_order,
        )
        if project_context_view is not None and frontier_view is not None
        else None
    )
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
            "frontier": (
                _semantic_scheduler_value(frontier_view)
                if frontier_view is not None
                else None
            ),
            "project_context": (
                _semantic_scheduler_value(project_context_view)
                if project_context_view is not None
                else None
            ),
        }
    )
    completed_ids = {
        str(item.get("action_id") or "")
        for item in scheduler_state.get("completed_actions", [])
        if isinstance(item, dict)
    }
    candidates: list[dict[str, Any]] = []
    frontier_gate_report: list[dict[str, Any]] = []
    project_context_gate_report: list[dict[str, Any]] = []
    if frontier_view is not None:
        lane_by_repository: dict[str, set[str]] = {
            repository_id: {
                next(
                    (
                        str(item.get("default_supervision_lane") or "default")
                        for item in registry.get("repositories", [])
                        if isinstance(item, dict)
                        and item.get("repository_id") == repository_id
                    ),
                    "default",
                )
            }
            for repository_id in repository_order
        }
        for mission in mission_list:
            repository_id = str(mission.get("repository_id") or "")
            if repository_id in lane_by_repository:
                lane_by_repository[repository_id].add(
                    str(mission.get("supervision_lane") or "default")
                )
        for repository_id, lanes in lane_by_repository.items():
            for lane in sorted(lanes):
                decision = frontier_gate_decision(
                    frontier_view,
                    repository_id,
                    lane,
                    action_kind="advance_mission",
                    expected_artifact=None,
                    authority_signal=signal_by_repository.get(repository_id),
                )
                frontier_gate_report.append(
                    {
                        "repository_id": repository_id,
                        "lane_id": lane,
                        **copy.deepcopy(decision),
                    }
                )
                if decision["classification"] not in {
                    "FRONTIER_CERTIFIED",
                    "FRONTIER_RECONCILED_NO_ACTIVE_CANDIDATE",
                }:
                    route = _supervisor_reconciliation_route(
                        registry, adapter, repository_id, lane
                    )
                    action = _scheduler_action(
                        "reconcile_repository_frontier",
                        {
                            "repository_id": repository_id,
                            "lane_id": lane,
                            "route": route,
                            "reasons": decision["reasons"],
                            "authority_signal": signal_by_repository.get(
                                repository_id
                            ),
                            "current_record": copy.deepcopy(
                                frontier_view.get("records", {}).get(
                                    _frontier_key(repository_id, lane)
                                )
                            ),
                            "safety_mode": frontier_view["safety_mode"],
                        },
                        priority=5,
                        stable_order=repository_order[repository_id],
                        requires_external_result=(
                            route["recipient_thread_id"] is not None
                        ),
                    )
                    if action["action_id"] not in completed_ids:
                        candidates.append(action)
    if project_context_view is not None and frontier_view is not None:
        for repository_id in repository_order:
            context_record = project_context_view.get("contexts", {}).get(
                repository_id
            )
            default_lane = next(
                (
                    str(item.get("default_supervision_lane") or "default")
                    for item in registry.get("repositories", [])
                    if isinstance(item, dict)
                    and item.get("repository_id") == repository_id
                ),
                "default",
            )
            active_lanes = (
                list(context_record.get("active_lanes", []))
                if isinstance(context_record, dict)
                else []
            )
            lane = str(active_lanes[0]) if active_lanes else default_lane
            decision = project_context_gate_decision(
                project_context_view,
                frontier_view,
                repository_id,
                lane,
                action_kind="advance_mission",
                authority_signal=signal_by_repository.get(repository_id),
            )
            project_context_gate_report.append(
                {
                    "repository_id": repository_id,
                    "lane_id": lane,
                    "action_kind": "advance_mission",
                    **copy.deepcopy(decision),
                }
            )
            if decision["classification"] != "PROJECT_CONTEXT_CERTIFIED":
                route = _supervisor_reconciliation_route(
                    registry, adapter, repository_id, lane
                )
                current_lane_frontiers = sorted(
                    (
                        copy.deepcopy(record)
                        for record in frontier_view.get("records", {}).values()
                        if isinstance(record, dict)
                        and record.get("repository_id") == repository_id
                    ),
                    key=lambda item: str(item.get("lane_id") or ""),
                )
                action = _scheduler_action(
                    "reconcile_project_context",
                    {
                        "repository_id": repository_id,
                        "lane_id": lane,
                        "route": route,
                        "reasons": decision["reasons"],
                        "authority_signal": signal_by_repository.get(
                            repository_id
                        ),
                        "current_context": copy.deepcopy(context_record),
                        "current_lane_frontiers": current_lane_frontiers,
                        "project_context_safety_mode": (
                            effective_context_safety_mode
                        ),
                        "reconciliation_contract": {
                            "schema": "project-context-result.v1",
                            "project_context_event_schema": (
                                "project-context-record.v1"
                            ),
                            "apply_command": (
                                "coordinator-action-apply-project-context-result"
                            ),
                            "exact_external_result_required": True,
                            "required_scope": [
                                "north_star",
                                "roadmap",
                                "current_bottleneck",
                                "completion_definition",
                                "every_active_lane_frontier_event_id",
                                "decisions_since_prior",
                                "evidence_manifest",
                                "omitted_evidence",
                                "current_authority_fingerprint",
                            ],
                            "inference_boundary": (
                                "do not choose current state by timestamp, "
                                "chat recency, filename, or portfolio order"
                            ),
                        },
                    },
                    priority=6,
                    stable_order=repository_order[repository_id],
                    requires_external_result=(
                        route["recipient_thread_id"] is not None
                    ),
                )
                if action["action_id"] not in completed_ids:
                    candidates.append(action)
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
        if authority_signal is not None and authority_signal.get(
            "authority_watch_configured"
        ) is not False:
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
            frontier_state=frontier_view,
            authority_signals=signals,
        )
        if selection.get("classification") != "NEXT_ACTIONABLE_REPOSITORY_SELECTED":
            break
        repository_id = str(selection.get("repository_id") or "")
        kind = _selection_action_kind(selection)
        is_successor = kind == "request_next_mission"
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
        value_gate_issue = (
            _mission_value_gate_issue(selected_mission)
            if kind in {"advance_mission", "dispatch_work_order"}
            else None
        )
        blocked_kind = kind if value_gate_issue else None
        if value_gate_issue:
            kind = "resolve_mission_value_gate"
        route = _selection_route(
            selection,
            registry=registry,
            hosts=hosts,
            adapter=adapter,
            missions=mission_list,
            kind=kind,
        )
        action_revision = canonical_json_hash(
            {
                "selection": selection,
                "mission": _semantic_scheduler_value(selected_mission),
                "value_gate_issue": value_gate_issue,
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
                **(
                    {
                        "mission_value_gate": {
                            "classification": "MISSION_VALUE_GATE_REQUIRED",
                            "issue": value_gate_issue,
                            "blocked_action_kind": blocked_kind,
                            "allowed_resolutions": [
                                "admit_valid_value_contract",
                                "return_no_work",
                                "park_mission",
                            ],
                            "admission_event": "value_contract_admitted",
                        }
                    }
                    if value_gate_issue
                    else {}
                ),
            },
            priority=(50 if is_successor else 10 + int(selection.get("selection_priority", 6))),
            stable_order=repository_order.get(repository_id, 999999),
            requires_external_result=kind
            in {
                "route_user_response",
                "await_supervisor_verdict",
                "await_supervisor_work_order",
                "resolve_mission_value_gate",
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

    if frontier_view is not None:
        gated_candidates: list[dict[str, Any]] = []
        for action in candidates:
            repository_id, lane, expected_artifact = _frontier_action_context(
                action, registry, mission_list
            )
            if not repository_id:
                gated_candidates.append(action)
                continue
            decision = frontier_gate_decision(
                frontier_view,
                repository_id,
                lane,
                action_kind=str(action.get("kind") or ""),
                expected_artifact=expected_artifact,
                authority_signal=signal_by_repository.get(repository_id),
            )
            if decision["classification"] in {
                "FRONTIER_RECONCILIATION_ALLOWED",
                "FRONTIER_TRANSPORT_ALLOWED",
            }:
                gated_candidates.append(action)
            elif decision["classification"] in {
                "FRONTIER_CERTIFIED",
                "FRONTIER_HISTORICAL_REVIEW_ALLOWED",
            }:
                selection = action.get("payload", {}).get("selection", {})
                selection = selection if isinstance(selection, dict) else {}
                selected = next(
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
                if isinstance(selected, dict) and selected.get("value_contract") is not None:
                    try:
                        validate_mission_value_contract_frontier(
                            selected["value_contract"], decision["certificate"]
                        )
                    except ProtocolError as exc:
                        frontier_gate_report.append(
                            {
                                "repository_id": repository_id,
                                "lane_id": lane,
                                "action_kind": action.get("kind"),
                                "classification": "FRONTIER_RECONCILIATION_REQUIRED",
                                "reasons": [str(exc)],
                                "certificate": None,
                            }
                        )
                        continue
                gated_candidates.append(
                    _bind_frontier_certificate_to_action(
                        action, decision["certificate"]
                    )
                )
            elif (
                decision["classification"]
                == "FRONTIER_RECONCILED_NO_ACTIVE_CANDIDATE"
                and action.get("kind") == "reconcile_project_context"
            ):
                # A reducer-applied `none` result is a resolved frontier. It
                # authorizes only the mandatory context continuation; absence
                # alone must never authorize ordinary repository work.
                gated_candidates.append(action)
            else:
                frontier_gate_report.append(
                    {
                        "repository_id": repository_id,
                        "lane_id": lane,
                        "action_kind": action.get("kind"),
                        **copy.deepcopy(decision),
                    }
                )
        candidates = gated_candidates

    if project_context_view is not None and frontier_view is not None:
        context_gated_candidates: list[dict[str, Any]] = []
        for action in candidates:
            repository_id, lane, _ = _frontier_action_context(
                action, registry, mission_list
            )
            if not repository_id:
                context_gated_candidates.append(action)
                continue
            decision = project_context_gate_decision(
                project_context_view,
                frontier_view,
                repository_id,
                lane,
                action_kind=str(action.get("kind") or ""),
                authority_signal=signal_by_repository.get(repository_id),
            )
            if decision["classification"] in {
                "PROJECT_CONTEXT_RECONCILIATION_ALLOWED",
                "PROJECT_CONTEXT_NOT_REQUIRED",
            }:
                context_gated_candidates.append(action)
            elif decision["classification"] == "PROJECT_CONTEXT_CERTIFIED":
                context_gated_candidates.append(
                    _bind_supervisor_context_to_action(
                        action, decision["envelope"]
                    )
                )
            else:
                project_context_gate_report.append(
                    {
                        "repository_id": repository_id,
                        "lane_id": lane,
                        "action_kind": action.get("kind"),
                        **copy.deepcopy(decision),
                    }
                )
        candidates = context_gated_candidates

    # Frontier certificates and project-context envelopes participate in the
    # final action identity. Completed IDs therefore have to be filtered after
    # both bindings, not only against the earlier unbound candidate identity.
    candidates = [
        action
        for action in candidates
        if str(action.get("action_id") or "") not in completed_ids
    ]

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
            "primary_writer_task_id": str(
                coordinator_state.get("coordinator_task", {}).get("task_id") or ""
            ),
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
            "frontier_revision": (
                frontier_view.get("revision")
                if frontier_view is not None
                else None
            ),
            "frontier_safety_mode": (
                frontier_view.get("safety_mode")
                if frontier_view is not None
                else None
            ),
            "frontier_gate": frontier_gate_report,
            "project_context_revision": (
                project_context_view.get("revision")
                if project_context_view is not None
                else None
            ),
            "project_context_safety_mode": (
                effective_context_safety_mode
            ),
            "project_context_gate": project_context_gate_report,
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
    actor_task_id: str | None = None,
) -> dict[str, Any]:
    """Prepare the fixed deny-read state repair without running any probe."""
    actor = require_primary_coordinator_writer(
        coordinator_state, actor_task_id=actor_task_id
    )
    _ensure_scheduler_state_v2(scheduler_state)
    active = scheduler_state.get("scheduler_claim")
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        completed = _runtime_completion(scheduler_state, action_id)
        if isinstance(completed, dict):
            _bind_or_validate_scheduler_record_owner(completed, actor)
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
    _bind_or_validate_scheduler_record_owner(active, actor)
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
            actor_task_id=actor,
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
            actor_task_id=actor,
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
        actor_task_id=actor,
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
    actor_task_id: str | None = None,
) -> dict[str, Any]:
    """Restore the byte-exact original while preserving backup/quarantine."""
    actor = require_primary_coordinator_writer(
        coordinator_state, actor_task_id=actor_task_id
    )
    _ensure_scheduler_state_v2(scheduler_state)
    active = scheduler_state.get("scheduler_claim")
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        completed = _runtime_completion(scheduler_state, action_id)
        if isinstance(completed, dict):
            _bind_or_validate_scheduler_record_owner(completed, actor)
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
    _bind_or_validate_scheduler_record_owner(active, actor)
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
            actor_task_id=actor,
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
        actor_task_id=actor,
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
    actor_task_id: str | None = None,
) -> dict[str, Any]:
    """Apply one scheduler-completed runtime transition missing from the ledger."""
    actor = require_primary_coordinator_writer(
        coordinator_state, actor_task_id=actor_task_id
    )
    _ensure_scheduler_state_v2(scheduler_state)
    active = scheduler_state.get("scheduler_claim")
    if not isinstance(active, dict) or active.get("action_id") != action_id:
        completed = _runtime_completion(scheduler_state, action_id)
        if isinstance(completed, dict) and completed.get("outcome") == "reconciled":
            _bind_or_validate_scheduler_record_owner(completed, actor)
            return {
                "classification": "AUTHORIZED_RUNTIME_COMPLETION_ALREADY_RECONCILED",
                "action_id": action_id,
                "deduplicated": True,
            }
        raise ProtocolError("exact authorized runtime reconciliation claim is required")
    action = active.get("action", {})
    if action.get("kind") != "reconcile_authorized_runtime_completion":
        raise ProtocolError("claimed action is not runtime completion reconciliation")
    _bind_or_validate_scheduler_record_owner(active, actor)
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
        actor_task_id=actor,
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


def _bind_or_validate_scheduler_record_owner(
    record: dict[str, Any], actor_task_id: str | None
) -> str:
    actor = str(
        actor_task_id
        if actor_task_id is not None
        else os.environ.get("CODEX_THREAD_ID", "")
    ).strip()
    if not actor:
        raise ProtocolError("Coordinator mutation requires an exact actor task ID")
    owner = str(record.get("owner_task_id") or "").strip()
    if owner and owner != actor:
        raise ProtocolError(
            "COORDINATOR_WRITER_OWNERSHIP_MISMATCH: action belongs to another task"
        )
    if not owner:
        record["owner_task_id"] = actor
    return actor


def _set_external_lifecycle(
    record: dict[str, Any],
    lifecycle_state: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    if lifecycle_state not in EXTERNAL_RESULT_LIFECYCLE_STATES:
        raise ProtocolError("invalid external result lifecycle state")
    if record.get("action", {}).get("requires_external_result") is not True:
        raise ProtocolError("external lifecycle requires an external action")
    current = str(record.get("external_lifecycle_state") or "created")
    if current == lifecycle_state:
        return
    terminal = {
        "result_applied",
        "failed",
        "stale_result_quarantined",
        "cancelled",
    }
    if current in terminal:
        raise ProtocolError("external result lifecycle is already terminal")
    transitions = {
        "created": {"dispatched", "cancelled", "failed"},
        "dispatched": {
            "delivery_acknowledged",
            "result_received",
            "failed",
            "cancelled",
        },
        "delivery_acknowledged": {
            "result_received",
            "failed",
            "cancelled",
        },
        "result_received": {
            "result_parsed",
            "failed",
            "stale_result_quarantined",
        },
        "result_parsed": {
            "result_validated",
            "failed",
            "stale_result_quarantined",
        },
        "result_validated": {
            "result_applied",
            "failed",
            "stale_result_quarantined",
        },
    }
    if lifecycle_state not in transitions.get(current, set()):
        raise ProtocolError(
            f"external lifecycle {current} cannot transition to {lifecycle_state}"
        )
    entry = {"state": lifecycle_state, "at": utc_now()}
    if details:
        entry["details"] = copy.deepcopy(details)
    history = record.setdefault(
        "external_lifecycle_history",
        [{"state": current, "at": str(record.get("claimed_at") or "legacy-v2")}],
    )
    history.append(entry)
    record["external_lifecycle_state"] = lifecycle_state


def claim_coordinator_action(
    scheduler_state: dict[str, Any],
    plan: dict[str, Any],
    action_id: str,
    *,
    owner_task_id: str | None = None,
) -> dict[str, Any]:
    _ensure_scheduler_state_v2(scheduler_state)
    actor = str(
        owner_task_id
        if owner_task_id is not None
        else os.environ.get("CODEX_THREAD_ID", "")
    ).strip()
    if not actor:
        raise ProtocolError("Coordinator mutation requires an exact actor task ID")
    primary_writer = str(plan.get("primary_writer_task_id") or "").strip()
    if not primary_writer:
        raise ProtocolError(
            "PRIMARY_COORDINATOR_WRITER_UNBOUND: plan lacks an exact primary writer"
        )
    if actor != primary_writer:
        raise ProtocolError(
            "READ_ONLY_NON_COORDINATOR_TASK: scheduler claim is reserved for "
            "the plan's bound primary Coordinator"
        )
    active = scheduler_state.get("scheduler_claim")
    if isinstance(active, dict):
        if active.get("action_id") == action_id:
            _bind_or_validate_scheduler_record_owner(active, actor)
            return {
                "classification": "COORDINATOR_ACTION_ALREADY_CLAIMED",
                "action_id": action_id,
                "deduplicated": True,
            }
        raise ProtocolError("another short-lived Coordinator scheduler claim is active")
    lease_index = _route_lease_index(scheduler_state, action_id)
    if lease_index is not None:
        _bind_or_validate_scheduler_record_owner(
            scheduler_state["route_leases"][lease_index], actor
        )
        return {
            "classification": "COORDINATOR_ACTION_ALREADY_WAITING",
            "action_id": action_id,
            "deduplicated": True,
        }
    next_action = plan.get("next_action")
    action = next(
        (
            item
            for item in plan.get("ready_actions", [])
            if isinstance(item, dict) and item.get("action_id") == action_id
        ),
        None,
    )
    if not isinstance(next_action, dict) or not isinstance(action, dict):
        raise ProtocolError("scheduler action is stale, capacity-limited, or not next")
    if (
        action.get("priority") != next_action.get("priority")
        and not _is_applied_frontier_context_continuation(
            action, scheduler_state
        )
    ):
        raise ProtocolError(
            "scheduler action is not in the current highest-priority ready set"
        )
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
            "owner_task_id": actor,
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
    *,
    actor_task_id: str | None = None,
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
    _bind_or_validate_scheduler_record_owner(active, actor_task_id)
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
    actor_task_id: str | None = None,
) -> dict[str, Any]:
    """Move a sent claim to a route lease, releasing the global scheduler."""
    _ensure_scheduler_state_v2(scheduler_state)
    lease_index = _route_lease_index(scheduler_state, action_id)
    if lease_index is not None:
        lease = scheduler_state["route_leases"][lease_index]
        _bind_or_validate_scheduler_record_owner(lease, actor_task_id)
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
    _bind_or_validate_scheduler_record_owner(active, actor_task_id)
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
    _set_external_lifecycle(
        record,
        "dispatched",
        details={
            "recipient_thread_id": recipient_thread_id,
            "packet_sha256": packet_sha256.lower(),
            "after_cursor": after_cursor,
        },
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


def acknowledge_coordinator_action_delivery(
    scheduler_state: dict[str, Any],
    action_id: str,
    delivery_ack_id: str,
    *,
    actor_task_id: str | None = None,
) -> dict[str, Any]:
    """Record transport acknowledgement without closing semantic work."""
    _ensure_scheduler_state_v2(scheduler_state)
    lease_index = _route_lease_index(scheduler_state, action_id)
    if lease_index is None:
        raise ProtocolError("delivery acknowledgement requires an exact route lease")
    lease = scheduler_state["route_leases"][lease_index]
    _bind_or_validate_scheduler_record_owner(lease, actor_task_id)
    acknowledgement = str(delivery_ack_id or "").strip()
    if not acknowledgement:
        raise ProtocolError("delivery acknowledgement requires an exact receipt id")
    existing = str(lease.get("delivery_ack_id") or "")
    if existing:
        if existing != acknowledgement:
            raise ProtocolError("conflicting delivery acknowledgement replay")
        return {
            "classification": "COORDINATOR_DELIVERY_ALREADY_ACKNOWLEDGED",
            "action_id": action_id,
            "delivery_ack_id": acknowledgement,
            "lifecycle_state": lease.get("external_lifecycle_state"),
            "deduplicated": True,
        }
    _set_external_lifecycle(
        lease,
        "delivery_acknowledged",
        details={"delivery_ack_id": acknowledgement},
    )
    lease["delivery_ack_id"] = acknowledgement
    scheduler_state["revision"] = int(scheduler_state.get("revision", 0)) + 1
    _sync_legacy_active_claim_view(scheduler_state)
    return {
        "classification": "COORDINATOR_DELIVERY_ACKNOWLEDGED",
        "action_id": action_id,
        "delivery_ack_id": acknowledgement,
        "lifecycle_state": "delivery_acknowledged",
        "semantic_result_applied": False,
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
    statuses = {
        "probe_a": probe_a.get("status") if isinstance(probe_a, dict) else None,
        "postcheck": (
            postcheck.get("status") if isinstance(postcheck, dict) else None
        ),
        "probe_b": probe_b.get("status") if isinstance(probe_b, dict) else None,
        "runtime_doctor": (
            doctor.get("status") if isinstance(doctor, dict) else None
        ),
    }
    if statuses != AUTHORIZED_RUNTIME_PROBE_STATUS_MATRIX.get(outcome):
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
    evidence: Any = None,
    coordinator_state: dict[str, Any] | None = None,
    actor_task_id: str | None = None,
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
            _bind_or_validate_scheduler_record_owner(
                existing_completion, actor_task_id
            )
            if existing_completion.get("outcome") != outcome or (
                existing_completion.get("evidence") != evidence
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
    _bind_or_validate_scheduler_record_owner(active, actor_task_id)
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
    authorized_external_applied = False
    if requires_external_result and action_kind in AUTHORIZED_RUNTIME_ACTION_KINDS:
        _apply_authorized_runtime_completion(
            coordinator_state or {}, active, action_id, outcome, evidence
        )
        for lifecycle_state in (
            "result_received",
            "result_parsed",
            "result_validated",
            "result_applied",
        ):
            _set_external_lifecycle(
                active,
                lifecycle_state,
                details={
                    "authorized_runtime_receipt": str(evidence or ""),
                    "outcome": outcome,
                },
            )
        authorized_external_applied = True
    if requires_external_result and source != "route_lease":
        raise ProtocolError(
            "external Coordinator action cannot complete before exact send receipt"
        )
    if requires_external_result:
        if active.get("external_lifecycle_state") != "result_applied":
            raise ProtocolError(
                "external Coordinator action cannot complete before result_applied"
            )
        if (
            action_kind not in AUTHORIZED_RUNTIME_ACTION_KINDS
            and (
                not isinstance(evidence, dict)
                or not str(evidence.get("result_id") or "")
            )
        ):
            raise ProtocolError(
                "external Coordinator action completion requires structured result evidence"
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
    if coordinator_state is not None and not authorized_external_applied:
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
        "owner_task_id": active.get("owner_task_id"),
        "external_lifecycle_state": active.get("external_lifecycle_state"),
        "external_lifecycle_history": copy.deepcopy(
            active.get("external_lifecycle_history")
        ),
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


def _external_result_evidence(result: dict[str, Any]) -> dict[str, Any]:
    event = result["frontier_event"]
    return {
        "result_id": result["result_id"],
        "source_thread_id": result["source_thread_id"],
        "source_turn_id": result["source_turn_id"],
        "source_message_id": result["source_message_id"],
        "disposition": result["disposition"],
        "frontier_event_id": event["frontier_event_id"],
        "frontier_epoch": event["frontier_epoch"],
        "authority_fingerprint": result["authority_signal"][
            "authority_fingerprint"
        ],
        "result_sha256": canonical_json_hash(result),
    }


def _close_terminal_external_route(
    scheduler_state: dict[str, Any],
    lease_index: int,
    *,
    outcome: str,
    evidence: dict[str, Any],
) -> None:
    """Close a failed/quarantined external route without treating it as applied."""
    lease = scheduler_state["route_leases"][lease_index]
    lifecycle_state = str(lease.get("external_lifecycle_state") or "")
    if lifecycle_state not in {
        "failed",
        "stale_result_quarantined",
        "cancelled",
    }:
        raise ProtocolError("terminal external route requires a terminal lifecycle")
    action = lease.get("action", {})
    record = {
        "action_id": lease.get("action_id"),
        "kind": action.get("kind"),
        "action": copy.deepcopy(action),
        "requires_external_result": True,
        "state_fingerprint": lease.get("state_fingerprint"),
        "outcome": outcome,
        "evidence": copy.deepcopy(evidence),
        "repository_id": lease.get("repository_id"),
        "route_class": lease.get("route_class"),
        "recipient_thread_id": lease.get("recipient_thread_id"),
        "packet_sha256": lease.get("packet_sha256"),
        "delivery_token": lease.get("delivery_token"),
        "after_cursor": lease.get("after_cursor"),
        "owner_task_id": lease.get("owner_task_id"),
        "external_lifecycle_state": lifecycle_state,
        "external_lifecycle_history": copy.deepcopy(
            lease.get("external_lifecycle_history")
        ),
        "completed_at": utc_now(),
    }
    scheduler_state.setdefault("completed_actions", []).append(record)
    del scheduler_state["route_leases"][lease_index]
    scheduler_state["revision"] = int(scheduler_state.get("revision", 0)) + 1
    _sync_legacy_active_claim_view(scheduler_state)


def _validate_external_result_identity(
    lease: dict[str, Any],
    result: Any,
    observed_authority_signal: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ProtocolError("external result must be an object")
    required = (
        "schema_version",
        "result_id",
        "action_id",
        "repository_id",
        "lane_id",
        "source_actor",
        "source_thread_id",
        "source_turn_id",
        "source_message_id",
        "disposition",
        "based_on_frontier_epoch",
        "frontier_event",
        "authority_signal",
        "mission_id",
        "attempt_id",
        "mission_before_sha256",
        "mission_after",
    )
    missing = [field for field in required if field not in result]
    if missing:
        raise ProtocolError("external result missing: " + ", ".join(missing))
    if result.get("schema_version") != 1:
        raise ProtocolError("unsupported external result schema")
    for field in (
        "result_id",
        "action_id",
        "repository_id",
        "lane_id",
        "source_thread_id",
        "source_turn_id",
        "source_message_id",
    ):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise ProtocolError(f"external result requires {field}")
    action = lease.get("action", {})
    context_envelope = action.get("payload", {}).get(
        "supervisor_context_envelope"
    )
    if isinstance(context_envelope, dict):
        for field in (
            "supervisor_context_envelope_id",
            "based_on_project_context_revision",
        ):
            if field not in result:
                raise ProtocolError(
                    f"external result requires {field} for a context-bound action"
                )
        if result.get("supervisor_context_envelope_id") != context_envelope.get(
            "envelope_id"
        ):
            raise ProtocolError("external result Supervisor context identity mismatch")
        if result.get("based_on_project_context_revision") != context_envelope.get(
            "project_context_revision"
        ):
            raise ProtocolError("external result project context revision mismatch")
    route = action.get("payload", {}).get("route", {})
    expected = {
        "action_id": lease.get("action_id"),
        "repository_id": lease.get("repository_id")
        or route.get("repository_id"),
        "lane_id": route.get("supervision_lane"),
        "source_thread_id": lease.get("recipient_thread_id"),
    }
    for field, value in expected.items():
        if result.get(field) != value:
            raise ProtocolError(f"external result {field} identity mismatch")
    action_mission_id = str(route.get("mission_id") or "")
    action_attempt_id = str(route.get("attempt_id") or "")
    result_mission_id = str(result.get("mission_id") or "")
    result_attempt_id = str(result.get("attempt_id") or "")
    if action_mission_id and result_mission_id != action_mission_id:
        raise ProtocolError("external result mission_id identity mismatch")
    if action_attempt_id and result_attempt_id != action_attempt_id:
        raise ProtocolError("external result attempt_id identity mismatch")
    if bool(result_mission_id) != bool(result_attempt_id):
        raise ProtocolError("external result Mission identity is incomplete")
    if result.get("source_actor") not in FRONTIER_SOURCE_ACTORS:
        raise ProtocolError("external result source_actor is invalid")
    recipient_kind = str(route.get("recipient_kind") or "")
    expected_source_actor = {
        "supervisor": "supervisor",
        "worker": "worker",
    }.get(recipient_kind)
    if expected_source_actor and result.get("source_actor") != expected_source_actor:
        raise ProtocolError(
            "external result source_actor does not match route recipient"
        )
    if result.get("disposition") not in FRONTIER_DISPOSITIONS:
        raise ProtocolError("external result disposition is invalid")
    if not isinstance(result.get("based_on_frontier_epoch"), int):
        raise ProtocolError("external result frontier epoch binding is invalid")
    event = result.get("frontier_event")
    validate_frontier_record(event)
    event_bindings = {
        "repository_id": result["repository_id"],
        "lane_id": result["lane_id"],
        "source_actor": result["source_actor"],
        "source_message_id": result["source_message_id"],
        "source_result_id": result["result_id"],
        "disposition": result["disposition"],
        "based_on_frontier_epoch": result["based_on_frontier_epoch"],
    }
    for field, value in event_bindings.items():
        if event.get(field) != value:
            raise ProtocolError(f"external result frontier_event {field} mismatch")
    authority_signal = result.get("authority_signal")
    if not isinstance(authority_signal, dict) or authority_signal.get(
        "repository_id"
    ) != result["repository_id"]:
        raise ProtocolError("external result authority signal mismatch")
    if not re.fullmatch(
        r"[0-9a-fA-F]{64}",
        str(authority_signal.get("authority_fingerprint") or ""),
    ):
        raise ProtocolError("external result authority fingerprint is invalid")
    expected_authority_hash = canonical_json_hash(
        {
            key: value
            for key, value in authority_signal.items()
            if key != "authority_fingerprint"
        }
    )
    if authority_signal["authority_fingerprint"] != expected_authority_hash:
        raise ProtocolError("external result authority fingerprint does not match")
    if not isinstance(observed_authority_signal, dict):
        raise ProtocolError(
            "external result requires an independently observed authority signal"
        )
    validate_authority_signal_liveness(observed_authority_signal)
    if authority_signal != observed_authority_signal:
        raise ProtocolError(
            "external result authority signal does not match current observation"
        )
    git = authority_signal.get("git")
    if not isinstance(git, dict):
        raise ProtocolError("external result requires Git high-water state")
    if event.get("branch") is not None and git.get("branch") != event.get("branch"):
        raise ProtocolError("external result branch binding mismatch")
    if event.get("head_sha") is not None and git.get("head_sha") != event.get(
        "head_sha"
    ):
        raise ProtocolError("external result head binding mismatch")
    return result


def _validated_mission_replacement(
    missions: list[dict[str, Any]],
    lease: dict[str, Any],
    result: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None]:
    repository_id, action_mission_id, action_attempt_id = _action_mission_identity(
        lease.get("action", {})
    )
    mission_id = str(result.get("mission_id") or action_mission_id or "")
    attempt_id = str(result.get("attempt_id") or action_attempt_id or "")
    if not mission_id and not attempt_id:
        if lease.get("action", {}).get("kind") in {
            "route_direction_update",
            "route_project_question",
            "reconcile_repository_frontier",
        } and all(
            result.get(field) is None
            for field in (
                "mission_id",
                "attempt_id",
                "mission_before_sha256",
                "mission_after",
            )
        ):
            return None, None
        raise ProtocolError("external result requires one exact Mission")
    matches = [
        (index, mission)
        for index, mission in enumerate(missions)
        if isinstance(mission, dict)
        and str(mission.get("repository_id") or "") == repository_id
        and str(mission.get("mission_id") or "") == mission_id
        and str(mission.get("attempt_id") or "") == attempt_id
    ]
    if len(matches) != 1:
        raise ProtocolError("external result requires one exact Mission")
    index, current = matches[0]
    if canonical_json_hash(current) != result.get("mission_before_sha256"):
        raise ProtocolError("external result Mission CAS mismatch")
    replacement = copy.deepcopy(result.get("mission_after"))
    if not isinstance(replacement, dict):
        raise ProtocolError("external result mission_after must be an object")
    for field, expected in (
        ("repository_id", repository_id),
        ("mission_id", mission_id),
        ("attempt_id", attempt_id),
    ):
        if str(replacement.get(field) or "") != expected:
            raise ProtocolError(f"external result Mission {field} mismatch")
    prior_events = current.get("events")
    next_events = replacement.get("events")
    if (
        not isinstance(prior_events, list)
        or not isinstance(next_events, list)
        or next_events[: len(prior_events)] != prior_events
        or len(next_events) <= len(prior_events)
    ):
        raise ProtocolError("external result Mission history must append evidence")
    validate_mission(replacement)
    return index, replacement


def _apply_coordinator_input_result(
    coordinator_state: dict[str, Any] | None,
    lease: dict[str, Any],
    result: dict[str, Any],
) -> None:
    action = lease.get("action", {})
    action_kind = str(action.get("kind") or "")
    if action_kind not in {
        "route_direction_update",
        "route_project_question",
        "route_user_response",
    }:
        return
    if not isinstance(coordinator_state, dict):
        raise ProtocolError(
            "Coordinator input result requires exact Coordinator state"
        )
    validate_coordinator_state(coordinator_state)
    input_disposition = str(result.get("input_disposition") or "")
    if input_disposition not in {
        "ADOPTED",
        "DEFERRED",
        "REJECTED",
        "NEEDS_CLARIFICATION",
        "SUPERSEDED",
    }:
        raise ProtocolError(
            "Coordinator input result requires a semantic input disposition"
        )
    resolution = {
        "state": "result_applied",
        "input_disposition": input_disposition,
        "result_id": result["result_id"],
        "source_thread_id": result["source_thread_id"],
        "source_turn_id": result["source_turn_id"],
        "source_message_id": result["source_message_id"],
        "frontier_event_id": result["frontier_event"]["frontier_event_id"],
        "frontier_epoch": result["frontier_event"]["frontier_epoch"],
        "result_applied_at": utc_now(),
    }
    if action_kind in {"route_direction_update", "route_project_question"}:
        action_event = action.get("payload", {}).get("event", {})
        event_id = str(action_event.get("event_id") or "")
        pending = coordinator_state.get("pending_user_events", [])
        matches = [
            (index, item)
            for index, item in enumerate(pending)
            if isinstance(item, dict) and item.get("event_id") == event_id
        ]
        if len(matches) != 1:
            raise ProtocolError(
                "Coordinator input result requires exact pending event"
            )
        index, event = matches[0]
        if event.get("repository_id") != result["repository_id"]:
            raise ProtocolError("Coordinator input result repository mismatch")
        if event.get("state") not in {"queued", "delivery_acknowledged"}:
            raise ProtocolError("Coordinator input event is not pending")
        if event.get("recipient_thread_id") not in {
            None,
            result["source_thread_id"],
        }:
            raise ProtocolError("Coordinator input recipient identity mismatch")
        routed_event = {**copy.deepcopy(event), **resolution}
        del pending[index]
        coordinator_state.setdefault("routed_user_events", []).append(
            routed_event
        )
    else:
        pending = coordinator_state.get("pending_user_responses", [])
        matches = [
            (index, item)
            for index, item in enumerate(pending)
            if isinstance(item, dict)
            and item.get("repository_id") == result["repository_id"]
            and str(item.get("mission_id") or "")
            == str(result.get("mission_id") or "")
            and str(item.get("attempt_id") or "")
            == str(result.get("attempt_id") or "")
        ]
        if len(matches) != 1:
            raise ProtocolError(
                "Coordinator input result requires exact pending user response"
            )
        index, response = matches[0]
        if response.get("recipient_thread_id") != result["source_thread_id"]:
            raise ProtocolError("USER_RESPONSE result recipient mismatch")
        routed_response = {
            **copy.deepcopy(response),
            **resolution,
            "response_state": "resolved",
        }
        del pending[index]
        coordinator_state.setdefault("routed_user_responses", []).append(
            routed_response
        )
    validate_coordinator_state(coordinator_state)


def _refresh_portfolio_after_external_result(
    portfolio: dict[str, Any],
    scheduler_state: dict[str, Any],
    frontier_state: dict[str, Any],
    missions: list[dict[str, Any]],
    authority_signals: Iterable[dict[str, Any]],
    *,
    project_context_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signals = list(authority_signals)
    refreshed = migrate_portfolio_to_frontier_v3(
        portfolio, frontier_state, signals
    )
    scheduler, routes = _active_scheduler_delivery_routes(scheduler_state)
    refreshed["scheduler_revision"] = scheduler["revision"]
    refreshed["active_routes"] = [
        _scheduler_route_portfolio_projection(route) for route in routes
    ]
    refreshed["active_route_count"] = len(routes)
    refreshed["concurrency_limit"] = scheduler["concurrency_limit"]
    refreshed["execution_state"] = "WAITING_EXTERNAL" if routes else "IDLE"
    route_by_repository: dict[str, list[dict[str, Any]]] = {}
    for route in routes:
        route_by_repository.setdefault(str(route.get("repository_id") or ""), []).append(
            route
        )
    next_user_action = refreshed.get("next_user_action")
    waiting_user_repository_id = (
        str(next_user_action.get("repository_id") or "")
        if isinstance(next_user_action, dict)
        else ""
    )
    mission_by_repository = {
        str(item.get("repository_id") or ""): item
        for item in missions
        if isinstance(item, dict)
    }
    for row in refreshed.get("repositories", []):
        repository_id = str(row.get("repository_id") or "")
        repository_routes = route_by_repository.get(repository_id, [])
        mission = mission_by_repository.get(repository_id)
        preserve_waiting_user = (
            repository_id == waiting_user_repository_id
            and row.get("state") == "WAITING_USER"
        )
        if repository_routes:
            row["route_owner"] = " | ".join(
                f"{route.get('action_id')} / {route.get('recipient_thread_id')} / {route.get('observer_kind')}"
                for route in repository_routes
            )
            if not preserve_waiting_user:
                row["state"] = "WAITING_EXTERNAL"
        else:
            row["route_owner"] = "No active external route; exact result applied."
            if preserve_waiting_user:
                continue
            if row.get("frontier_disposition") == "none":
                row["state"] = "READY"
                row["why"] = (
                    "The current frontier certifies that no active candidate exists."
                )
                row["next_move"] = (
                    "Reconcile exact project context; do not authorize ordinary work from absence."
                )
                row["route_owner"] = (
                    "No active external route; project-context continuation is required."
                )
            elif row.get("frontier_status") != "verified":
                row["state"] = "READY"
                row["why"] = (
                    "The external route ended without a current certified frontier."
                )
                row["next_move"] = (
                    "Reconcile repository evidence and issue a FrontierCertificate."
                )
                row["route_owner"] = (
                    "No active external route; frontier reconciliation is required."
                )
            elif isinstance(mission, dict) and mission.get("state") == "COMPLETE":
                row["state"] = "PROJECT_COMPLETE"
                row["progress"] = {
                    "current_stage": "NEXT_ROUTE",
                    "completed_stages": list(PORTFOLIO_STAGE_ORDER),
                }
                row["next_move"] = "Reconcile and certify any later frontier event."
    refreshed["frontier_revision"] = frontier_state["revision"]
    refreshed["frontier_safety_mode"] = frontier_state["safety_mode"]
    if project_context_state is not None:
        refreshed = migrate_portfolio_to_project_context_v4(
            refreshed,
            project_context_state,
            frontier_state,
            signals,
        )
        validate_portfolio_project_context_consistency(
            refreshed,
            project_context_state,
            frontier_state,
            signals,
        )
    refreshed["semantic_fingerprint"] = portfolio_semantic_fingerprint(refreshed)
    return refreshed


def _validate_project_context_external_result(
    lease: dict[str, Any],
    result: Any,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ProtocolError("project context result must be an object")
    required = (
        "schema_version",
        "result_id",
        "action_id",
        "repository_id",
        "lane_id",
        "source_actor",
        "source_thread_id",
        "source_turn_id",
        "source_message_id",
        "based_on_project_context_revision",
        "project_context_event",
    )
    missing = [field for field in required if field not in result]
    if missing:
        raise ProtocolError(
            "project context result missing: " + ", ".join(missing)
        )
    if result.get("schema_version") != PROJECT_CONTEXT_STATE_VERSION:
        raise ProtocolError("unsupported project context result schema")
    for field in (
        "result_id",
        "action_id",
        "repository_id",
        "lane_id",
        "source_thread_id",
        "source_turn_id",
        "source_message_id",
    ):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise ProtocolError(f"project context result requires {field}")
    if result.get("source_actor") != "supervisor":
        raise ProtocolError(
            "project context reconciliation result must come from the exact Supervisor"
        )
    based_on = result.get("based_on_project_context_revision")
    if not isinstance(based_on, int) or based_on < 0:
        raise ProtocolError(
            "project context result based-on revision must be non-negative"
        )
    action = lease.get("action", {})
    if action.get("kind") != "reconcile_project_context":
        raise ProtocolError(
            "project context result requires a reconcile_project_context route"
        )
    if action.get("requires_external_result") is not True:
        raise ProtocolError("project context result requires an external route")
    route = action.get("payload", {}).get("route", {})
    expected = {
        "action_id": lease.get("action_id"),
        "repository_id": route.get("repository_id"),
        "lane_id": route.get("supervision_lane"),
        "source_thread_id": route.get("recipient_thread_id"),
    }
    for field, value in expected.items():
        if result.get(field) != value:
            raise ProtocolError(f"project context result {field} mismatch")
    event = result.get("project_context_event")
    validate_project_context_record(event)
    for field in ("repository_id", "source_actor", "source_message_id"):
        if event.get(field) != result.get(field):
            raise ProtocolError(
                f"project context event {field} does not match exact result"
            )
    if event.get("based_on_project_context_revision") != based_on:
        raise ProtocolError(
            "project context event based-on revision does not match result"
        )
    return copy.deepcopy(result)


def _project_context_result_evidence(result: dict[str, Any]) -> dict[str, Any]:
    event = result["project_context_event"]
    return {
        "result_id": result["result_id"],
        "source_thread_id": result["source_thread_id"],
        "source_turn_id": result["source_turn_id"],
        "source_message_id": result["source_message_id"],
        "project_context_event_id": event["project_context_event_id"],
        "project_context_revision": event["project_context_revision"],
        "authority_fingerprint": event["authority_fingerprint"],
        "result_sha256": canonical_json_hash(result),
    }


def apply_project_context_result_transaction(
    scheduler_state: dict[str, Any],
    project_context_state: dict[str, Any],
    frontier_state: dict[str, Any],
    missions: list[dict[str, Any]],
    portfolio: dict[str, Any],
    action_id: str,
    result: dict[str, Any],
    *,
    authority_signals: Iterable[dict[str, Any]],
    actor_task_id: str | None = None,
) -> dict[str, Any]:
    """Apply one exact Supervisor context result and close only its route."""
    scheduler_work = migrate_scheduler_state(scheduler_state)
    context_work = copy.deepcopy(project_context_state)
    frontier_work = copy.deepcopy(frontier_state)
    portfolio_work = copy.deepcopy(portfolio)
    signals = list(authority_signals)
    lease_index = _route_lease_index(scheduler_work, action_id)
    if lease_index is None:
        existing = next(
            (
                item
                for item in scheduler_work.get("completed_actions", [])
                if isinstance(item, dict) and item.get("action_id") == action_id
            ),
            None,
        )
        evidence = (
            existing.get("evidence") if isinstance(existing, dict) else None
        )
        if (
            isinstance(existing, dict)
            and existing.get("kind") == "reconcile_project_context"
            and existing.get("outcome") == "result_applied"
            and isinstance(evidence, dict)
            and evidence.get("result_id") == result.get("result_id")
            and evidence.get("result_sha256") == canonical_json_hash(result)
        ):
            return {
                "classification": "PROJECT_CONTEXT_RESULT_ALREADY_APPLIED",
                "action_id": action_id,
                "result_id": result.get("result_id"),
                "deduplicated": True,
            }
        raise ProtocolError("exact project context route lease is required")
    lease = scheduler_work["route_leases"][lease_index]
    _bind_or_validate_scheduler_record_owner(lease, actor_task_id)
    validated = _validate_project_context_external_result(lease, result)
    result_id = validated["result_id"]
    for lifecycle_state in ("result_received", "result_parsed"):
        _set_external_lifecycle(
            lease,
            lifecycle_state,
            details={"result_id": result_id},
        )
    signal = next(
        (
            item
            for item in signals
            if isinstance(item, dict)
            and item.get("repository_id") == validated["repository_id"]
        ),
        None,
    )
    event = validated["project_context_event"]
    validate_project_context_event_against_observations(
        event, frontier_work, signal
    )
    current = context_work.get("contexts", {}).get(validated["repository_id"])
    current_revision = (
        int(current.get("project_context_revision", 0))
        if isinstance(current, dict)
        else 0
    )
    if validated["based_on_project_context_revision"] != current_revision:
        raise ProtocolError("project context result revision is stale")
    _set_external_lifecycle(
        lease,
        "result_validated",
        details={"result_id": result_id},
    )
    applied = apply_project_context_event(context_work, event)
    if applied["classification"] not in {
        "PROJECT_CONTEXT_EVENT_APPLIED",
        "PROJECT_CONTEXT_EVENT_ALREADY_APPLIED",
    }:
        raise ProtocolError(
            "project context result did not advance the context ledger: "
            + str(applied["classification"])
        )
    evidence = _project_context_result_evidence(validated)
    _set_external_lifecycle(
        lease,
        "result_applied",
        details={
            "result_id": result_id,
            "project_context_event_id": event["project_context_event_id"],
        },
    )
    complete_coordinator_action(
        scheduler_work,
        action_id,
        "result_applied",
        evidence=evidence,
        actor_task_id=actor_task_id,
    )
    portfolio_work = _refresh_portfolio_after_external_result(
        portfolio_work,
        scheduler_work,
        frontier_work,
        missions,
        signals,
        project_context_state=context_work,
    )
    validate_project_context_state(context_work)
    _validate_scheduler_state_v2(scheduler_work)
    validate_portfolio_scheduler_consistency(portfolio_work, scheduler_work)
    validate_portfolio_frontier_consistency(
        portfolio_work, frontier_work, signals
    )
    validate_portfolio_project_context_consistency(
        portfolio_work, context_work, frontier_work, signals
    )
    scheduler_state.clear()
    scheduler_state.update(scheduler_work)
    project_context_state.clear()
    project_context_state.update(context_work)
    portfolio.clear()
    portfolio.update(portfolio_work)
    return {
        "classification": "PROJECT_CONTEXT_RESULT_APPLIED",
        "action_id": action_id,
        "result_id": result_id,
        "project_context_revision": event["project_context_revision"],
        "project_context_event_id": event["project_context_event_id"],
        "deduplicated": False,
    }


def apply_project_context_result_transaction_files(
    *,
    scheduler_path: Path | str,
    project_context_path: Path | str,
    frontier_path: Path | str,
    missions_dir: Path | str,
    portfolio_path: Path | str,
    journal_dir: Path | str,
    action_id: str,
    result: dict[str, Any],
    authority_signals: Iterable[dict[str, Any]],
    actor_task_id: str | None = None,
) -> dict[str, Any]:
    """Persist the context ledger, route completion, and portfolio atomically."""
    result_id = str(result.get("result_id") or "")
    if not result_id:
        raise ProtocolError("project context transaction requires result_id")
    transaction_id = canonical_json_hash(
        {
            "kind": "project_context_result",
            "action_id": action_id,
            "result_id": result_id,
        }
    )
    journal_path = Path(journal_dir) / f"{transaction_id}.json"
    result_sha256 = canonical_json_hash(result)

    def persist_desired(journal: dict[str, Any]) -> int:
        desired = journal.get("desired_state")
        if not isinstance(desired, dict):
            raise ProtocolError(
                "project context transaction journal lacks desired state"
            )
        writes = (
            (Path(project_context_path), desired["project_context"]),
            (Path(scheduler_path), desired["scheduler"]),
            (Path(portfolio_path), desired["portfolio"]),
        )
        for target, value in writes:
            atomic_write_json(target, value)
        return len(writes)

    if journal_path.is_file():
        journal = load_json(journal_path)
        if journal.get("result_sha256") != result_sha256:
            raise ProtocolError("project context transaction result replay mismatch")
        writes = persist_desired(journal)
        journal["state"] = "applied"
        journal["replayed_at"] = utc_now()
        atomic_write_json(journal_path, journal)
        return {
            "classification": "PROJECT_CONTEXT_RESULT_TRANSACTION_REPLAYED",
            "transaction_id": transaction_id,
            "result_id": result_id,
            "write_count": writes,
            "deduplicated": True,
        }

    scheduler = load_scheduler_state(scheduler_path)
    missions = load_missions(missions_dir)
    frontier = load_frontier_state(
        frontier_path,
        (str(item.get("repository_id") or "") for item in missions),
    )
    context = load_project_context_state(
        project_context_path,
        {
            str(item.get("repository_id") or "") for item in missions
        }
        | {str(result.get("repository_id") or "")},
    )
    portfolio = load_json(portfolio_path)
    outcome = apply_project_context_result_transaction(
        scheduler,
        context,
        frontier,
        missions,
        portfolio,
        action_id,
        result,
        authority_signals=authority_signals,
        actor_task_id=actor_task_id,
    )
    journal = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "state": "prepared",
        "action_id": action_id,
        "result_id": result_id,
        "result_sha256": result_sha256,
        "prepared_at": utc_now(),
        "desired_state": {
            "project_context": context,
            "scheduler": scheduler,
            "portfolio": portfolio,
        },
    }
    atomic_write_json(journal_path, journal)
    writes = persist_desired(journal)
    journal["state"] = "applied"
    journal["applied_at"] = utc_now()
    atomic_write_json(journal_path, journal)
    return {
        **outcome,
        "classification": "PROJECT_CONTEXT_RESULT_TRANSACTION_APPLIED",
        "transaction_id": transaction_id,
        "write_count": writes,
    }


def apply_external_result_transaction(
    scheduler_state: dict[str, Any],
    frontier_state: dict[str, Any],
    missions: list[dict[str, Any]],
    portfolio: dict[str, Any],
    action_id: str,
    result: dict[str, Any],
    *,
    observed_authority_signal: dict[str, Any] | None = None,
    project_context_state: dict[str, Any] | None = None,
    coordinator_state: dict[str, Any] | None = None,
    actor_task_id: str | None = None,
) -> dict[str, Any]:
    """Atomically reduce one exact result across all semantic projections."""
    _ensure_scheduler_state_v2(scheduler_state)
    validate_frontier_state(frontier_state)
    if project_context_state is not None:
        validate_project_context_state(project_context_state)
    result_id = str(result.get("result_id") or "") if isinstance(result, dict) else ""
    result_hash = canonical_json_hash(result) if isinstance(result, dict) else ""
    prior = next(
        (
            item
            for item in frontier_state.get("applied_results", [])
            if isinstance(item, dict) and item.get("result_id") == result_id
        ),
        None,
    )
    if isinstance(prior, dict):
        if prior.get("result_sha256") != result_hash:
            raise ProtocolError("conflicting external result replay")
        completion = next(
            (
                item
                for item in scheduler_state.get("completed_actions", [])
                if isinstance(item, dict) and item.get("action_id") == action_id
            ),
            None,
        )
        if not isinstance(completion, dict) or completion.get(
            "external_lifecycle_state"
        ) != "result_applied":
            raise ProtocolError("applied external result lacks scheduler completion")
        return {
            "classification": "EXTERNAL_RESULT_ALREADY_APPLIED",
            "action_id": action_id,
            "result_id": result_id,
            "deduplicated": True,
        }
    scheduler_work = copy.deepcopy(scheduler_state)
    frontier_work = copy.deepcopy(frontier_state)
    missions_work = copy.deepcopy(missions)
    portfolio_work = copy.deepcopy(portfolio)
    coordinator_work = (
        copy.deepcopy(coordinator_state)
        if isinstance(coordinator_state, dict)
        else None
    )
    lease_index = _route_lease_index(scheduler_work, action_id)
    if lease_index is None:
        raise ProtocolError("external result requires an exact route lease")
    lease = scheduler_work["route_leases"][lease_index]
    _bind_or_validate_scheduler_record_owner(lease, actor_task_id)
    try:
        validated_result = _validate_external_result_identity(
            lease, result, observed_authority_signal
        )
        _set_external_lifecycle(
            lease,
            "result_received",
            details={"result_id": validated_result["result_id"]},
        )
        _set_external_lifecycle(
            lease,
            "result_parsed",
            details={"result_sha256": result_hash},
        )
    except ProtocolError as exc:
        if lease.get("external_lifecycle_state") in {
            "dispatched",
            "delivery_acknowledged",
            "result_received",
            "result_parsed",
        }:
            _set_external_lifecycle(
                lease, "failed", details={"reason": str(exc)}
            )
            frontier_work["failed_results"].append(
                {
                    "result_id": result_id or None,
                    "action_id": action_id,
                    "result_sha256": result_hash or None,
                    "reason": str(exc),
                    "recorded_at": utc_now(),
                }
            )
            frontier_work["revision"] += 1
            failure_evidence = {
                "result_id": result_id or None,
                "result_sha256": result_hash or None,
                "reason": str(exc),
            }
            _close_terminal_external_route(
                scheduler_work,
                lease_index,
                outcome="external_result_failed",
                evidence=failure_evidence,
            )
            portfolio_work = _refresh_portfolio_after_external_result(
                portfolio_work,
                scheduler_work,
                frontier_work,
                missions_work,
                (
                    [observed_authority_signal]
                    if isinstance(observed_authority_signal, dict)
                    else []
                ),
                project_context_state=project_context_state,
            )
            validate_frontier_state(frontier_work)
            _validate_scheduler_state_v2(scheduler_work)
            validate_portfolio_scheduler_consistency(
                portfolio_work, scheduler_work
            )
            validate_portfolio_frontier_consistency(
                portfolio_work,
                frontier_work,
                (
                    [observed_authority_signal]
                    if isinstance(observed_authority_signal, dict)
                    else []
                ),
            )
            scheduler_state.clear()
            scheduler_state.update(scheduler_work)
            frontier_state.clear()
            frontier_state.update(frontier_work)
            portfolio.clear()
            portfolio.update(portfolio_work)
        return {
            "classification": "EXTERNAL_RESULT_FAILED",
            "action_id": action_id,
            "result_id": result_id or None,
            "reason": str(exc),
            "deduplicated": False,
        }
    context_envelope = lease.get("action", {}).get("payload", {}).get(
        "supervisor_context_envelope"
    )
    if isinstance(context_envelope, dict):
        context_stale_reason: str | None = None
        try:
            if project_context_state is None:
                raise ProtocolError(
                    "context-bound external result requires project context state"
                )
            if not isinstance(observed_authority_signal, dict):
                raise ProtocolError(
                    "context-bound external result requires current authority"
                )
            validate_supervisor_context_result_binding(
                context_envelope,
                project_context_state,
                frontier_work,
                expected_action_kind=str(
                    lease.get("action", {}).get("kind") or ""
                ),
            )
        except ProtocolError as exc:
            context_stale_reason = str(exc)
        if context_stale_reason is not None:
            repository_id = str(result.get("repository_id") or "")
            current_context = project_context_state.get("contexts", {}).get(
                repository_id
            ) if isinstance(project_context_state, dict) else None
            expected_context_revision = (
                current_context.get("project_context_revision")
                if isinstance(current_context, dict)
                else None
            )
            based_on_context_revision = result.get(
                "based_on_project_context_revision"
            )
            _set_external_lifecycle(
                lease,
                "stale_result_quarantined",
                details={
                    "result_id": result_id,
                    "reason": context_stale_reason,
                    "expected_project_context_revision": expected_context_revision,
                    "based_on_project_context_revision": based_on_context_revision,
                },
            )
            frontier_work["quarantined_results"].append(
                {
                    "result_id": result_id,
                    "action_id": action_id,
                    "repository_id": repository_id,
                    "lane_id": result.get("lane_id"),
                    "result_sha256": result_hash,
                    "reason": context_stale_reason,
                    "expected_project_context_revision": expected_context_revision,
                    "based_on_project_context_revision": based_on_context_revision,
                    "recorded_at": utc_now(),
                }
            )
            frontier_work["revision"] += 1
            quarantine_evidence = {
                "result_id": result_id,
                "result_sha256": result_hash,
                "reason": context_stale_reason,
                "expected_project_context_revision": expected_context_revision,
                "based_on_project_context_revision": based_on_context_revision,
            }
            _close_terminal_external_route(
                scheduler_work,
                lease_index,
                outcome="stale_project_context_result_quarantined",
                evidence=quarantine_evidence,
            )
            portfolio_work = _refresh_portfolio_after_external_result(
                portfolio_work,
                scheduler_work,
                frontier_work,
                missions_work,
                [result["authority_signal"]],
                project_context_state=project_context_state,
            )
            validate_frontier_state(frontier_work)
            _validate_scheduler_state_v2(scheduler_work)
            validate_portfolio_scheduler_consistency(
                portfolio_work, scheduler_work
            )
            validate_portfolio_frontier_consistency(
                portfolio_work, frontier_work, [result["authority_signal"]]
            )
            scheduler_state.clear()
            scheduler_state.update(scheduler_work)
            frontier_state.clear()
            frontier_state.update(frontier_work)
            portfolio.clear()
            portfolio.update(portfolio_work)
            return {
                "classification": "STALE_PROJECT_CONTEXT_RESULT_QUARANTINED",
                "action_id": action_id,
                "result_id": result_id,
                "expected_project_context_revision": expected_context_revision,
                "deduplicated": False,
            }
    key = _frontier_key(result["repository_id"], result["lane_id"])
    current = frontier_work["records"].get(key)
    current_epoch = int(current.get("frontier_epoch", 0)) if current else 0
    if result["based_on_frontier_epoch"] != current_epoch:
        _set_external_lifecycle(
            lease,
            "stale_result_quarantined",
            details={
                "result_id": result_id,
                "expected_frontier_epoch": current_epoch,
                "based_on_frontier_epoch": result["based_on_frontier_epoch"],
            },
        )
        frontier_work["quarantined_results"].append(
            {
                "result_id": result_id,
                "action_id": action_id,
                "repository_id": result["repository_id"],
                "lane_id": result["lane_id"],
                "result_sha256": result_hash,
                "expected_frontier_epoch": current_epoch,
                "based_on_frontier_epoch": result["based_on_frontier_epoch"],
                "recorded_at": utc_now(),
            }
        )
        frontier_work["revision"] += 1
        quarantine_evidence = {
            "result_id": result_id,
            "result_sha256": result_hash,
            "expected_frontier_epoch": current_epoch,
            "based_on_frontier_epoch": result["based_on_frontier_epoch"],
        }
        _close_terminal_external_route(
            scheduler_work,
            lease_index,
            outcome="stale_result_quarantined",
            evidence=quarantine_evidence,
        )
        portfolio_work = _refresh_portfolio_after_external_result(
            portfolio_work,
            scheduler_work,
            frontier_work,
            missions_work,
            [result["authority_signal"]],
            project_context_state=project_context_state,
        )
        validate_frontier_state(frontier_work)
        _validate_scheduler_state_v2(scheduler_work)
        validate_portfolio_scheduler_consistency(portfolio_work, scheduler_work)
        validate_portfolio_frontier_consistency(
            portfolio_work, frontier_work, [result["authority_signal"]]
        )
        scheduler_state.clear()
        scheduler_state.update(scheduler_work)
        frontier_state.clear()
        frontier_state.update(frontier_work)
        portfolio.clear()
        portfolio.update(portfolio_work)
        return {
            "classification": "STALE_RESULT_QUARANTINED",
            "action_id": action_id,
            "result_id": result_id,
            "expected_frontier_epoch": current_epoch,
            "deduplicated": False,
        }
    mission_index, replacement = _validated_mission_replacement(
        missions_work, lease, result
    )
    _set_external_lifecycle(
        lease,
        "result_validated",
        details={"result_id": result_id},
    )
    frontier_application = apply_frontier_event(
        frontier_work, result["frontier_event"]
    )
    if frontier_application["classification"] != "FRONTIER_EVENT_APPLIED":
        _set_external_lifecycle(
            lease,
            "stale_result_quarantined",
            details={
                "result_id": result_id,
                "frontier_classification": frontier_application[
                    "classification"
                ],
            },
        )
        frontier_work["quarantined_results"].append(
            {
                "result_id": result_id,
                "action_id": action_id,
                "repository_id": result["repository_id"],
                "lane_id": result["lane_id"],
                "result_sha256": result_hash,
                "frontier_classification": frontier_application[
                    "classification"
                ],
                "recorded_at": utc_now(),
            }
        )
        frontier_work["revision"] += 1
        quarantine_evidence = {
            "result_id": result_id,
            "result_sha256": result_hash,
            "frontier_classification": frontier_application[
                "classification"
            ],
        }
        _close_terminal_external_route(
            scheduler_work,
            lease_index,
            outcome="stale_result_quarantined",
            evidence=quarantine_evidence,
        )
        portfolio_work = _refresh_portfolio_after_external_result(
            portfolio_work,
            scheduler_work,
            frontier_work,
            missions_work,
            [result["authority_signal"]],
            project_context_state=project_context_state,
        )
        validate_frontier_state(frontier_work)
        _validate_scheduler_state_v2(scheduler_work)
        validate_portfolio_scheduler_consistency(portfolio_work, scheduler_work)
        validate_portfolio_frontier_consistency(
            portfolio_work, frontier_work, [result["authority_signal"]]
        )
        scheduler_state.clear()
        scheduler_state.update(scheduler_work)
        frontier_state.clear()
        frontier_state.update(frontier_work)
        portfolio.clear()
        portfolio.update(portfolio_work)
        return {
            "classification": "STALE_RESULT_QUARANTINED",
            "action_id": action_id,
            "result_id": result_id,
            "deduplicated": False,
        }
    certificate = None
    if result["disposition"] in FRONTIER_ADVANCE_DISPOSITIONS:
        certificate = issue_frontier_certificate(
            frontier_work,
            result["repository_id"],
            result["lane_id"],
            result["authority_signal"],
        )
    if mission_index is not None and replacement is not None:
        replacement["frontier_certificate"] = copy.deepcopy(certificate)
        replacement["active_artifact"] = (
            {
                "artifact_id": certificate.get("artifact_id"),
                "artifact_revision": certificate.get("artifact_revision"),
                "artifact_sha256": certificate.get("artifact_sha256"),
            }
            if isinstance(certificate, dict)
            else None
        )
        missions_work[mission_index] = replacement
    _apply_coordinator_input_result(coordinator_work, lease, result)
    evidence = _external_result_evidence(result)
    _set_external_lifecycle(
        lease,
        "result_applied",
        details={
            "result_id": result_id,
            "frontier_event_id": result["frontier_event"]["frontier_event_id"],
        },
    )
    complete_coordinator_action(
        scheduler_work,
        action_id,
        "result_applied",
        evidence=evidence,
        actor_task_id=actor_task_id,
    )
    frontier_work["applied_results"].append(
        {
            **copy.deepcopy(evidence),
            "action_id": action_id,
            "result_sha256": result_hash,
            "applied_at": utc_now(),
        }
    )
    frontier_work["revision"] += 1
    portfolio_work = _refresh_portfolio_after_external_result(
        portfolio_work,
        scheduler_work,
        frontier_work,
        missions_work,
        [result["authority_signal"]],
        project_context_state=project_context_state,
    )
    validate_frontier_state(frontier_work)
    _validate_scheduler_state_v2(scheduler_work)
    for mission in missions_work:
        validate_mission(mission)
    if coordinator_work is not None:
        validate_coordinator_state(coordinator_work)
    validate_portfolio_scheduler_consistency(portfolio_work, scheduler_work)
    validate_portfolio_frontier_consistency(
        portfolio_work, frontier_work, [result["authority_signal"]]
    )
    scheduler_state.clear()
    scheduler_state.update(scheduler_work)
    frontier_state.clear()
    frontier_state.update(frontier_work)
    missions.clear()
    missions.extend(missions_work)
    portfolio.clear()
    portfolio.update(portfolio_work)
    if coordinator_state is not None and coordinator_work is not None:
        coordinator_state.clear()
        coordinator_state.update(coordinator_work)
    return {
        "classification": "EXTERNAL_RESULT_APPLIED",
        "action_id": action_id,
        "result_id": result_id,
        "frontier_epoch": result["frontier_event"]["frontier_epoch"],
        "certificate_id": (
            certificate["certificate_id"]
            if isinstance(certificate, dict)
            else None
        ),
        "deduplicated": False,
    }


def apply_external_result_transaction_files(
    *,
    scheduler_path: Path | str,
    frontier_path: Path | str,
    project_context_path: Path | str | None = None,
    missions_dir: Path | str,
    portfolio_path: Path | str,
    journal_dir: Path | str,
    action_id: str,
    result: dict[str, Any],
    observed_authority_signal: dict[str, Any] | None = None,
    coordinator_path: Path | str | None = None,
    actor_task_id: str | None = None,
    failure_after_write: int | None = None,
) -> dict[str, Any]:
    """Persist a replayable write-ahead transaction across state projections."""
    result_id = str(result.get("result_id") or "")
    if not result_id:
        raise ProtocolError("external result transaction requires result_id")
    transaction_id = canonical_json_hash(
        {"action_id": action_id, "result_id": result_id}
    )
    journal_path = Path(journal_dir) / f"{transaction_id}.json"
    result_sha256 = canonical_json_hash(result)

    def persist_desired(journal: dict[str, Any]) -> int:
        desired = journal.get("desired_state")
        if not isinstance(desired, dict):
            raise ProtocolError("frontier transaction journal lacks desired state")
        writes: list[tuple[Path, Any]] = [
            (Path(frontier_path), desired["frontier"]),
        ]
        mission_outputs = desired.get("missions", [])
        if not isinstance(mission_outputs, list):
            raise ProtocolError("frontier transaction journal missions are invalid")
        writes.extend(
            (Path(str(item["path"])), item["value"])
            for item in mission_outputs
            if isinstance(item, dict)
        )
        writes.extend(
            [
                (Path(scheduler_path), desired["scheduler"]),
                (Path(portfolio_path), desired["portfolio"]),
            ]
        )
        coordinator_output = desired.get("coordinator")
        if isinstance(coordinator_output, dict):
            writes.insert(
                -1,
                (
                    Path(str(coordinator_output["path"])),
                    coordinator_output["value"],
                ),
            )
        completed_writes = 0
        for target, value in writes:
            atomic_write_json(target, value)
            completed_writes += 1
            if failure_after_write == completed_writes:
                raise OSError("injected frontier transaction interruption")
        return completed_writes

    if journal_path.is_file():
        journal = load_json(journal_path)
        if journal.get("result_sha256") != result_sha256:
            raise ProtocolError("frontier transaction result replay mismatch")
        writes = persist_desired(journal)
        journal["state"] = "applied"
        journal["replayed_at"] = utc_now()
        atomic_write_json(journal_path, journal)
        return {
            "classification": "EXTERNAL_RESULT_TRANSACTION_REPLAYED",
            "transaction_id": transaction_id,
            "result_id": result_id,
            "write_count": writes,
            "deduplicated": True,
        }

    scheduler = load_scheduler_state(scheduler_path)
    mission_directory = Path(missions_dir)
    mission_paths = sorted(mission_directory.glob("*.json"))
    missions = [load_json(path) for path in mission_paths]
    portfolio = load_json(portfolio_path)
    coordinator = (
        load_json(coordinator_path) if coordinator_path is not None else None
    )
    frontier = load_frontier_state(
        frontier_path,
        (str(item.get("repository_id") or "") for item in missions),
    )
    project_context = (
        load_project_context_state(
            project_context_path,
            {
                str(item.get("repository_id") or "") for item in missions
            }
            | {str(result.get("repository_id") or "")},
        )
        if project_context_path is not None
        else None
    )
    original_missions = copy.deepcopy(missions)
    outcome = apply_external_result_transaction(
        scheduler,
        frontier,
        missions,
        portfolio,
        action_id,
        result,
        observed_authority_signal=observed_authority_signal,
        project_context_state=project_context,
        coordinator_state=coordinator,
        actor_task_id=actor_task_id,
    )
    mission_outputs: list[dict[str, Any]] = []
    for index, mission in enumerate(missions):
        if index < len(mission_paths):
            target = mission_paths[index]
            if index < len(original_missions) and mission == original_missions[index]:
                continue
        else:
            target = _mission_path(mission_directory, mission)
        mission_outputs.append({"path": str(target), "value": mission})
    journal = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "state": "prepared",
        "action_id": action_id,
        "result_id": result_id,
        "result_sha256": result_sha256,
        "prepared_at": utc_now(),
        "desired_state": {
            "frontier": frontier,
            "missions": mission_outputs,
            "scheduler": scheduler,
            "portfolio": portfolio,
            "coordinator": (
                {"path": str(coordinator_path), "value": coordinator}
                if coordinator_path is not None and coordinator is not None
                else None
            ),
        },
    }
    atomic_write_json(journal_path, journal)
    writes = persist_desired(journal)
    journal["state"] = "applied"
    journal["applied_at"] = utc_now()
    atomic_write_json(journal_path, journal)
    return {
        **outcome,
        "classification": "EXTERNAL_RESULT_TRANSACTION_APPLIED",
        "transaction_id": transaction_id,
        "write_count": writes,
        "journal_path": str(journal_path),
    }


def audit_frontier_state(
    registry: dict[str, Any],
    missions: Iterable[dict[str, Any]],
    frontier_state: dict[str, Any],
    authority_signals: Iterable[dict[str, Any]],
    *,
    portfolio: dict[str, Any] | None = None,
    scheduler_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repository_ids = [
        str(item.get("repository_id") or "")
        for item in registry.get("repositories", [])
        if isinstance(item, dict) and item.get("repository_id")
    ]
    frontier = migrate_frontier_state(frontier_state, repository_ids)
    signals = list(authority_signals)
    signal_by_repository = {
        str(item.get("repository_id") or ""): item
        for item in signals
        if isinstance(item, dict) and item.get("repository_id")
    }
    mission_list = [item for item in missions if isinstance(item, dict)]
    findings: list[dict[str, Any]] = []
    for repository in registry.get("repositories", []):
        if not isinstance(repository, dict):
            continue
        repository_id = str(repository.get("repository_id") or "")
        lanes = {
            str(repository.get("default_supervision_lane") or "default"),
            *(
                str(item.get("supervision_lane") or "default")
                for item in mission_list
                if item.get("repository_id") == repository_id
            ),
        }
        for lane in sorted(lanes):
            decision = frontier_gate_decision(
                frontier,
                repository_id,
                lane,
                action_kind="advance_mission",
                expected_artifact=None,
                authority_signal=signal_by_repository.get(repository_id),
            )
            if decision["classification"] != "FRONTIER_CERTIFIED":
                current = frontier.get("records", {}).get(
                    _frontier_key(repository_id, lane)
                )
                findings.append(
                    {
                        "repository_id": repository_id,
                        "lane_id": lane,
                        "classification": "RECONCILIATION_REQUIRED",
                        "reasons": decision["reasons"],
                        "recommended_reconciliation_event": {
                            "kind": "frontier_reconciliation_event",
                            "based_on_frontier_epoch": (
                                int(current.get("frontier_epoch", 0))
                                if isinstance(current, dict)
                                else 0
                            ),
                            "required_evidence": [
                                "current independent Git/authority observation",
                                "exact artifact or null-frontier disposition lineage",
                                "exact source message/result identity",
                            ],
                            "candidate_event_generated": False,
                        },
                    }
                )
    scheduler: dict[str, Any] | None = None
    if isinstance(scheduler_state, dict):
        scheduler = migrate_scheduler_state(scheduler_state)
        _validate_scheduler_state_v2(scheduler)
        _, active_routes = _active_scheduler_delivery_routes(scheduler)
        for route in active_routes:
            if route.get("action", {}).get("requires_external_result") is True:
                findings.append(
                    {
                        "repository_id": route.get("repository_id"),
                        "action_id": route.get("action_id"),
                        "classification": "UNAPPLIED_EXTERNAL_RESULT",
                        "external_lifecycle_state": route.get(
                            "external_lifecycle_state"
                        ),
                        "recommended_reconciliation_event": {
                            "kind": "apply_exact_external_result_or_continue_observation",
                            "delivery_token": route.get("delivery_token"),
                            "candidate_event_generated": False,
                        },
                    }
                )
    for item in frontier.get("quarantined_results", []):
        if isinstance(item, dict):
            findings.append(
                {
                    "repository_id": item.get("repository_id"),
                    "action_id": item.get("action_id"),
                    "result_id": item.get("result_id"),
                    "classification": "QUARANTINED_RESULT_AUDIT",
                    "recommended_reconciliation_event": {
                        "kind": "observe_current_frontier_without_replaying_quarantine",
                        "candidate_event_generated": False,
                    },
                }
            )
    portfolio_status = "not_supplied"
    if isinstance(portfolio, dict):
        try:
            validate_portfolio_frontier_consistency(portfolio, frontier, signals)
            if scheduler is not None:
                validate_portfolio_scheduler_consistency(portfolio, scheduler)
            portfolio_status = "consistent"
        except ProtocolError as exc:
            portfolio_status = "reconciliation_required"
            findings.append(
                {
                    "classification": "PORTFOLIO_RECONCILIATION_REQUIRED",
                    "reasons": [str(exc)],
                }
            )
    return {
        "classification": (
            "FRONTIER_AUDIT_CLEAR"
            if not findings
            else "FRONTIER_RECONCILIATION_REQUIRED"
        ),
        "schema_version": FRONTIER_STATE_VERSION,
        "safety_mode": frontier["safety_mode"],
        "frontier_revision": frontier["revision"],
        "repository_count": len(repository_ids),
        "verified_frontier_count": len(frontier["records"]),
        "quarantined_result_count": len(frontier["quarantined_results"]),
        "portfolio_status": portfolio_status,
        "findings": findings,
        "dry_run": True,
        "mutated": False,
    }


def validate_project_context_event_against_observations(
    candidate: dict[str, Any],
    frontier_state: dict[str, Any],
    authority_signal: dict[str, Any] | None,
) -> None:
    """Require one context event to name the exact observed project frontier."""
    validate_project_context_record(candidate)
    validate_frontier_state(frontier_state)
    repository_id = str(candidate["repository_id"])
    if not isinstance(authority_signal, dict):
        raise ProtocolError("project context event authority signal is missing")
    validate_authority_signal_liveness(authority_signal)
    if authority_signal.get("repository_id") != repository_id:
        raise ProtocolError("project context event authority repository mismatch")
    if candidate.get("authority_fingerprint") != authority_signal.get(
        "authority_fingerprint"
    ):
        raise ProtocolError("project context event authority fingerprint is stale")
    if candidate.get("authority_revision") != authority_signal.get("git", {}).get(
        "head_sha"
    ):
        raise ProtocolError("project context event authority revision is stale")
    for lane_id, event_id in candidate["lane_frontier_event_ids"].items():
        frontier = frontier_state.get("records", {}).get(
            _frontier_key(repository_id, lane_id)
        )
        if not isinstance(frontier, dict):
            raise ProtocolError(
                f"project context event active lane is missing: {lane_id}"
            )
        if frontier.get("frontier_event_id") != event_id:
            raise ProtocolError(
                f"project context event active lane is stale: {lane_id}"
            )


def audit_project_context_state(
    registry: dict[str, Any],
    project_context_state: dict[str, Any],
    frontier_state: dict[str, Any],
    authority_signals: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Read-only project-wide context and cross-lane frontier audit."""
    repository_ids = [
        str(item.get("repository_id") or "")
        for item in registry.get("repositories", [])
        if isinstance(item, dict) and item.get("repository_id")
    ]
    context = migrate_project_context_state(
        project_context_state, repository_ids
    )
    frontier = migrate_frontier_state(frontier_state, repository_ids)
    signal_by_repository = {
        str(item.get("repository_id") or ""): item
        for item in authority_signals
        if isinstance(item, dict) and item.get("repository_id")
    }
    findings: list[dict[str, Any]] = []
    for repository in registry.get("repositories", []):
        if not isinstance(repository, dict):
            continue
        repository_id = str(repository.get("repository_id") or "")
        record = context.get("contexts", {}).get(repository_id)
        lane = (
            str(record.get("active_lanes", [""])[0])
            if isinstance(record, dict) and record.get("active_lanes")
            else str(repository.get("default_supervision_lane") or "default")
        )
        decision = project_context_gate_decision(
            context,
            frontier,
            repository_id,
            lane,
            action_kind="advance_mission",
            authority_signal=signal_by_repository.get(repository_id),
        )
        if decision["classification"] != "PROJECT_CONTEXT_CERTIFIED":
            findings.append(
                {
                    "repository_id": repository_id,
                    "lane_id": lane,
                    "classification": "PROJECT_CONTEXT_RECONCILIATION_REQUIRED",
                    "repository_status": context.get(
                        "repository_status", {}
                    ).get(repository_id, "legacy_unverified"),
                    "reasons": decision["reasons"],
                    "recommended_reconciliation_event": {
                        "kind": "project_context_reconciliation_event",
                        "based_on_project_context_revision": (
                            int(record.get("project_context_revision", 0))
                            if isinstance(record, dict)
                            else 0
                        ),
                        "required_evidence": [
                            "current north star and completion definition",
                            "current roadmap position and bottleneck",
                            "every active lane frontier event ID",
                            "decisions, omitted evidence, and retired artifacts",
                            "current independent authority observation",
                        ],
                        "candidate_event_generated": False,
                    },
                }
            )
    return {
        "classification": (
            "PROJECT_CONTEXT_AUDIT_CLEAR"
            if not findings
            else "PROJECT_CONTEXT_RECONCILIATION_REQUIRED"
        ),
        "schema_version": PROJECT_CONTEXT_STATE_VERSION,
        "safety_mode": effective_project_context_safety_mode(
            context, frontier, signal_by_repository.values(), repository_ids
        ),
        "project_context_revision": context["revision"],
        "repository_count": len(repository_ids),
        "verified_context_count": sum(
            1
            for status in context["repository_status"].values()
            if status == "verified"
        ),
        "findings": findings,
        "dry_run": True,
        "mutated": False,
    }


def release_coordinator_action(
    scheduler_state: dict[str, Any],
    action_id: str,
    reason: str,
    *,
    actor_task_id: str | None = None,
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
            _bind_or_validate_scheduler_record_owner(
                existing_release, actor_task_id
            )
            if existing_release.get("reason") != reason:
                raise ProtocolError("conflicting released action replay")
            return {
                "classification": "COORDINATOR_ACTION_ALREADY_RELEASED",
                "action_id": action_id,
                "reason": reason,
                "deduplicated": True,
            }
        raise ProtocolError("exact active Coordinator scheduler claim is required")
    _bind_or_validate_scheduler_record_owner(active, actor_task_id)
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
            "owner_task_id": active.get("owner_task_id"),
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
    """Record delivery only; a later Supervisor result advances the Mission."""
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

    if matched.get("response_state") == "delivery_acknowledged":
        return {
            "classification": "USER_RESPONSE_DELIVERY_ALREADY_ACKNOWLEDGED",
            "response_id": response_id,
            "recipient_thread_id": matched["recipient_thread_id"],
            "mission_state": mission["state"],
            "semantic_result_applied": False,
            "deduplicated": True,
        }
    matched["response_state"] = "delivery_acknowledged"
    matched["delivery_acknowledged_at"] = utc_now()
    mission["user_response_ready"] = False
    return {
        "classification": "USER_RESPONSE_DELIVERY_ACKNOWLEDGED",
        "response_id": response_id,
        "recipient_thread_id": matched["recipient_thread_id"],
        "mission_state": mission["state"],
        "semantic_result_applied": False,
        "deduplicated": False,
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


def validate_mission_value_contract(contract: Any) -> None:
    """Fail closed when a proposed Mission has no short path back to value."""
    if not isinstance(contract, dict):
        raise ProtocolError(
            "MISSION_VALUE_GATE: new live Missions require a value_contract"
        )
    if contract.get("contract_version") != MISSION_VALUE_CONTRACT_VERSION:
        raise ProtocolError("MISSION_VALUE_GATE: unsupported value_contract version")
    required = (
        "authority_source",
        "authority_revision",
        "authority_fingerprint",
        "authority_next_action",
        "north_star",
        "current_bottleneck",
        "current_gate",
        "gate_delta",
        "expected_authority_state_after",
        "expected_user_value",
        "smallest_deliverable",
        "next_consumer",
        "reuse_or_integration",
        "existing_artifact_reused",
        "creates_new_artifact",
        "new_source_story_form_or_candidate",
        "advances_current_next_action",
        "adoption_test",
        "kill_condition",
        "objective_fit",
        "work_class",
        "max_worker_turns",
        "genre_or_domain_shift",
        "out_of_scope",
    )
    _required(contract, required, "value_contract")
    if not re.fullmatch(
        r"[0-9a-fA-F]{64}", str(contract.get("authority_fingerprint") or "")
    ):
        raise ProtocolError(
            "MISSION_VALUE_GATE: authority_fingerprint must be exact SHA-256"
        )
    if contract.get("objective_fit") not in MISSION_OBJECTIVE_FITS:
        raise ProtocolError("MISSION_VALUE_GATE: invalid objective_fit")
    work_class = str(contract.get("work_class") or "")
    if work_class not in MISSION_WORK_CLASSES:
        raise ProtocolError("MISSION_VALUE_GATE: invalid work_class")
    if contract.get("advances_current_next_action") is not True:
        raise ProtocolError(
            "MISSION_VALUE_GATE: Mission must directly advance authority_next_action"
        )
    if work_class == "quick_win" and contract.get("objective_fit") != "direct":
        raise ProtocolError(
            "MISSION_VALUE_GATE: quick_win must have direct objective_fit"
        )
    max_turns = contract.get("max_worker_turns")
    if not isinstance(max_turns, int) or not (1 <= max_turns <= 2):
        raise ProtocolError(
            "MISSION_VALUE_GATE: max_worker_turns must be one or two"
        )
    out_of_scope = contract.get("out_of_scope")
    if not isinstance(out_of_scope, list) or not out_of_scope or not all(
        isinstance(item, str) and item.strip() for item in out_of_scope
    ):
        raise ProtocolError(
            "MISSION_VALUE_GATE: out_of_scope must be a non-empty string list"
        )
    if work_class != "quick_win" and not str(
        contract.get("why_not_smaller") or ""
    ).strip():
        raise ProtocolError(
            "MISSION_VALUE_GATE: non-quick work requires why_not_smaller"
        )
    if max_turns == 2 and not str(contract.get("why_not_one_turn") or "").strip():
        raise ProtocolError(
            "MISSION_VALUE_GATE: two-turn work requires why_not_one_turn"
        )
    if work_class != "quick_win" and not str(
        contract.get("quick_win_unavailable_evidence") or ""
    ).strip():
        raise ProtocolError(
            "MISSION_VALUE_GATE: non-quick work requires evidence that no "
            "smaller current-gate move is available"
        )
    creates_new_artifact = contract.get("creates_new_artifact") is True
    changes_content_lane = bool(
        contract.get("new_source_story_form_or_candidate")
    )
    if creates_new_artifact and not str(
        contract.get("new_artifact_justification") or ""
    ).strip():
        raise ProtocolError(
            "MISSION_VALUE_GATE: new artifact creation requires justification"
        )
    if (
        not creates_new_artifact
        and contract.get("existing_artifact_reused") is not True
    ):
        raise ProtocolError(
            "MISSION_VALUE_GATE: quick path must reuse the existing artifact"
        )
    needs_explicit_authority = (
        bool(contract.get("genre_or_domain_shift"))
        or creates_new_artifact
        or changes_content_lane
        or work_class == "strategic_bet"
        or contract.get("objective_fit") == "exploratory"
    )
    if needs_explicit_authority:
        if contract.get("explicit_user_authorized") is not True or not str(
            contract.get("user_authorization_evidence") or ""
        ).strip():
            raise ProtocolError(
                "MISSION_VALUE_GATE: new artifacts, source/story/form/candidate "
                "changes, genre/domain shifts, exploratory work, and strategic "
                "bets require explicit user authorization evidence"
            )


def validate_mission_value_contract_frontier(
    contract: Any, certificate: dict[str, Any]
) -> None:
    validate_mission_value_contract(contract)
    if not isinstance(certificate, dict):
        raise ProtocolError("MISSION_VALUE_GATE: frontier certificate is required")
    if contract.get("authority_fingerprint") != certificate.get(
        "authority_fingerprint"
    ):
        raise ProtocolError(
            "MISSION_VALUE_GATE: authority fingerprint is stale relative to frontier"
        )
    if (
        contract.get("existing_artifact_reused") is True
        and not certificate.get("artifact_id")
    ):
        raise ProtocolError(
            "MISSION_VALUE_GATE: reuse claim lacks a certified current artifact"
        )
    if certificate.get("disposition") not in FRONTIER_ADVANCE_DISPOSITIONS:
        raise ProtocolError(
            "MISSION_VALUE_GATE: current frontier is not advanceable"
        )


def require_new_mission_value_contract(payload: dict[str, Any]) -> None:
    validate_mission_value_contract(payload.get("value_contract"))


def _mission_value_gate_issue(mission: Any) -> str | None:
    if not isinstance(mission, dict):
        return "selected Mission record is missing"
    try:
        validate_mission_value_contract(mission.get("value_contract"))
    except ProtocolError as exc:
        return str(exc)
    return None


def admit_mission_value_contract(
    mission: dict[str, Any], contract: Any
) -> dict[str, Any]:
    """Attach one immutable value contract to a legacy live Mission."""
    validate_mission_value_contract(contract)
    admitted = copy.deepcopy(contract)
    fingerprint = canonical_json_hash(admitted)
    existing = mission.get("value_contract")
    if existing is not None:
        validate_mission_value_contract(existing)
        if canonical_json_hash(existing) != fingerprint:
            raise ProtocolError(
                "MISSION_VALUE_GATE: an admitted value_contract cannot be replaced"
            )
        return {
            "classification": "MISSION_VALUE_CONTRACT_ALREADY_ADMITTED",
            "value_contract_fingerprint": fingerprint,
            "state": mission.get("state"),
        }
    mission["value_contract"] = admitted
    _record_state(
        mission,
        str(mission.get("state") or ""),
        {
            "event_kind": "MISSION_VALUE_CONTRACT_ADMITTED",
            "value_contract_fingerprint": fingerprint,
        },
    )
    return {
        "classification": "MISSION_VALUE_CONTRACT_ADMITTED",
        "value_contract_fingerprint": fingerprint,
        "state": mission.get("state"),
    }


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
    mission = {
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
    if "value_contract" in payload:
        validate_mission_value_contract(payload["value_contract"])
        mission["value_contract"] = copy.deepcopy(payload["value_contract"])
    return mission


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
    if "value_contract" in next_payload:
        payload["value_contract"] = copy.deepcopy(next_payload["value_contract"])
    return new_mission(payload)


def validate_mission(mission: dict[str, Any]) -> None:
    if mission.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError("unsupported Mission schema")
    if mission.get("mission_status") not in MISSION_STATUSES:
        raise ProtocolError("invalid mission_status")
    if mission.get("review_status") not in REVIEW_STATUSES:
        raise ProtocolError("invalid review_status")
    if "value_contract" in mission:
        validate_mission_value_contract(mission["value_contract"])
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


def require_primary_coordinator_writer(
    coordinator_state: dict[str, Any],
    *,
    actor_task_id: str | None = None,
) -> str:
    """Return the bound primary task ID or fail closed for live mutations.

    Codex supplies ``CODEX_THREAD_ID`` to every task process.  The persisted
    Coordinator binding is therefore an operational writer fence: repair,
    audit, and status tasks may read the plan, but they cannot claim actions or
    mutate the live Coordinator ledgers merely by supplying the primary task's
    ID as a command-line argument.
    """
    validate_coordinator_state(coordinator_state)
    binding = coordinator_state.get("coordinator_task")
    if not isinstance(binding, dict):
        raise ProtocolError(
            "PRIMARY_COORDINATOR_WRITER_UNBOUND: coordinator_task is missing"
        )
    bound_task_id = str(binding.get("task_id") or "").strip()
    if (
        binding.get("scope") != "all_repositories"
        or binding.get("binding_status") != "active"
        or not bound_task_id
    ):
        raise ProtocolError(
            "PRIMARY_COORDINATOR_WRITER_UNBOUND: an active exact task binding "
            "is required before live mutation"
        )
    actor = str(
        actor_task_id
        if actor_task_id is not None
        else os.environ.get("CODEX_THREAD_ID", "")
    ).strip()
    if not actor:
        raise ProtocolError(
            "PRIMARY_COORDINATOR_WRITER_UNKNOWN: CODEX_THREAD_ID is required"
        )
    if actor != bound_task_id:
        raise ProtocolError(
            "READ_ONLY_NON_COORDINATOR_TASK: live Coordinator mutation is "
            "reserved for the bound primary Coordinator task"
        )
    return actor


def rebind_primary_coordinator_writer(
    coordinator_state: dict[str, Any],
    scheduler_state: dict[str, Any],
    *,
    expected_current_task_id: str,
    new_task_id: str,
    reason: str,
    confirmation: str,
    actor_task_id: str | None = None,
) -> dict[str, Any]:
    """Perform one explicit idle-only primary writer generation change."""
    if confirmation != PRIMARY_WRITER_REBIND_CONFIRMATION:
        raise ProtocolError("primary writer rebind confirmation is missing")
    actor = str(
        actor_task_id
        if actor_task_id is not None
        else os.environ.get("CODEX_THREAD_ID", "")
    ).strip()
    requested = str(new_task_id or "").strip()
    if not actor or actor != requested:
        raise ProtocolError(
            "primary writer rebind must bind the exact current CODEX_THREAD_ID"
        )
    if not str(reason or "").strip():
        raise ProtocolError("primary writer rebind requires an explicit reason")
    _ensure_scheduler_state_v2(scheduler_state)
    if isinstance(scheduler_state.get("scheduler_claim"), dict) or any(
        isinstance(item, dict) for item in scheduler_state.get("route_leases", [])
    ):
        raise ProtocolError(
            "PRIMARY_COORDINATOR_REBIND_NOT_IDLE: claim or route lease is active"
        )
    recovery_phases = {
        "EFFECT_INTENT",
        "EFFECT_PREPARED",
        "REPAIR_PREPARED",
        "ROLLBACK_REQUIRED",
        "RESULT_READY",
    }
    if any(
        isinstance(item, dict) and item.get("phase") in recovery_phases
        for item in coordinator_state.get("authorized_runtime_actions", [])
    ):
        raise ProtocolError(
            "PRIMARY_COORDINATOR_REBIND_NOT_IDLE: runtime recovery is unfinished"
        )
    binding = coordinator_state.setdefault("coordinator_task", {})
    current = str(binding.get("task_id") or "").strip()
    expected = str(expected_current_task_id or "").strip()
    normalized_expected = "" if expected == "UNBOUND" else expected
    if current != normalized_expected:
        raise ProtocolError("primary writer rebind expected-current identity mismatch")
    if current == requested:
        return {
            "classification": "PRIMARY_COORDINATOR_WRITER_ALREADY_BOUND",
            "previous_task_id": current or None,
            "new_task_id": requested,
            "deduplicated": True,
        }
    rebound_at = utc_now()
    binding.update(
        {
            "scope": "all_repositories",
            "task_id": requested,
            "binding_status": "active",
            "previous_task_id": current or None,
            "rebound_at": rebound_at,
            "rebind_reason": str(reason).strip(),
        }
    )
    return {
        "classification": "PRIMARY_COORDINATOR_WRITER_REBOUND",
        "previous_task_id": current or None,
        "new_task_id": requested,
        "rebound_at": rebound_at,
        "deduplicated": False,
    }


def _same_resolved_path(left: Path | str, right: Path | str) -> bool:
    return Path(left).resolve(strict=False) == Path(right).resolve(strict=False)


def _path_is_within(path: Path | str, root: Path | str) -> bool:
    try:
        Path(path).resolve(strict=False).relative_to(Path(root).resolve(strict=False))
        return True
    except ValueError:
        return False


def _require_exact_cli_path(
    args: argparse.Namespace, argument: str, expected: Path
) -> None:
    observed = getattr(args, argument, None)
    if observed is None or not _same_resolved_path(observed, expected):
        raise ProtocolError(
            "CANONICAL_LIVE_STATE_REQUIRED: "
            f"--{argument.replace('_', '-')} must be {expected}"
        )


def _live_mutation_targets(args: argparse.Namespace) -> list[Path]:
    names_by_command = {
        "resolve": ("registry", "hosts"),
        "compile-work-order": ("output",),
        "migrate": ("output", "report"),
        "migrate-coordinator-ux": ("registry", "coordinator_state", "report"),
        "migrate-scheduler-state": ("output",),
        "mission-init": ("missions_dir",),
        "mission-event": ("mission",),
        "mission-blocker-contract": ("mission",),
        "mission-continue": ("missions_dir",),
        "terminal": ("terminal_dir", "ledger"),
        "portfolio-render": ("input", "output"),
        "coordinator-action-claim": ("scheduler_state",),
        "coordinator-action-prepare": ("scheduler_state",),
        "coordinator-action-sent": ("scheduler_state",),
        "coordinator-action-delivery-ack": ("scheduler_state",),
        "coordinator-action-apply-result": (
            "coordinator_state",
            "scheduler_state",
            "frontier_state",
            "missions_dir",
            "portfolio",
            "journal_dir",
        ),
        "coordinator-action-apply-project-context-result": (
            "coordinator_state",
            "scheduler_state",
            "project_context_state",
            "portfolio",
            "journal_dir",
        ),
        "project-context-apply-event": ("project_context_state",),
        "coordinator-action-complete": ("scheduler_state", "coordinator_state"),
        "coordinator-action-release": ("scheduler_state",),
        "authorized-runtime-action-register": ("coordinator_state",),
        "authorized-runtime-action-execute": (
            "coordinator_state",
            "scheduler_state",
        ),
        "authorized-runtime-action-rollback": (
            "coordinator_state",
            "scheduler_state",
        ),
        "authorized-runtime-action-reconcile-completion": (
            "coordinator_state",
            "scheduler_state",
        ),
        "queue-coordinator-event": ("coordinator_state",),
        "ack-coordinator-event": ("coordinator_state",),
        "route-user-response": ("mission", "coordinator_state"),
        "ack-user-response": ("mission", "coordinator_state"),
    }
    return [
        Path(value)
        for name in names_by_command.get(args.command, ())
        if (value := getattr(args, name, None)) is not None
    ]


def _require_canonical_live_mutation_paths(args: argparse.Namespace) -> None:
    """Bind live writes to the installed authority set, not caller clones.

    This is a cooperative operational fence.  It prevents ordinary repair,
    audit, or report tasks from pointing a mutating CLI at a cloned authority
    file while writing a real scheduler or Mission ledger.  It is not a
    security boundary against a same-user process that writes files directly.
    """
    runtime_commands = {
        "authorized-runtime-action-register",
        "authorized-runtime-action-execute",
        "authorized-runtime-action-rollback",
        "authorized-runtime-action-reconcile-completion",
    }
    if args.command in runtime_commands:
        _require_exact_cli_path(args, "coordinator_state", DEFAULT_COORDINATOR_STATE)
        _require_exact_cli_path(args, "scheduler_state", DEFAULT_SCHEDULER_STATE)
        if args.command == "authorized-runtime-action-register":
            _require_exact_cli_path(args, "registry", DEFAULT_BINDINGS)
            _require_exact_cli_path(args, "hosts", DEFAULT_HOSTS)
            _require_exact_cli_path(args, "adapter", DEFAULT_ADAPTER)
        return

    live_root = SKILL_ROOT / "state"
    if not any(_path_is_within(path, live_root) for path in _live_mutation_targets(args)):
        return

    if hasattr(args, "coordinator_state"):
        _require_exact_cli_path(args, "coordinator_state", DEFAULT_COORDINATOR_STATE)
    required_by_command = {
        "resolve": {
            "registry": DEFAULT_BINDINGS,
            "hosts": DEFAULT_HOSTS,
            "adapter": DEFAULT_ADAPTER,
        },
        "compile-work-order": {"mission": DEFAULT_MISSIONS},
        "migrate-coordinator-ux": {
            "registry": DEFAULT_BINDINGS,
            "coordinator_state": DEFAULT_COORDINATOR_STATE,
        },
        "migrate-scheduler-state": {
            "input": DEFAULT_SCHEDULER_STATE,
            "output": DEFAULT_SCHEDULER_STATE,
        },
        "portfolio-render": {
            "input": DEFAULT_PORTFOLIO_JSON,
            "output": DEFAULT_PORTFOLIO_MARKDOWN,
            "scheduler_state": DEFAULT_SCHEDULER_STATE,
            "frontier_state": DEFAULT_FRONTIER_STATE,
            "project_context_state": DEFAULT_PROJECT_CONTEXT_STATE,
            "registry": DEFAULT_BINDINGS,
            "hosts": DEFAULT_HOSTS,
            "adapter": DEFAULT_ADAPTER,
        },
        "terminal": {"mission": DEFAULT_MISSIONS},
        "route-user-response": {"mission": DEFAULT_MISSIONS},
        "ack-user-response": {"mission": DEFAULT_MISSIONS},
        "coordinator-action-claim": {
            "registry": DEFAULT_BINDINGS,
            "hosts": DEFAULT_HOSTS,
            "adapter": DEFAULT_ADAPTER,
            "missions_dir": DEFAULT_MISSIONS,
            "scheduler_state": DEFAULT_SCHEDULER_STATE,
            "frontier_state": DEFAULT_FRONTIER_STATE,
            "project_context_state": DEFAULT_PROJECT_CONTEXT_STATE,
        },
        "coordinator-action-prepare": {
            "scheduler_state": DEFAULT_SCHEDULER_STATE
        },
        "coordinator-action-sent": {"scheduler_state": DEFAULT_SCHEDULER_STATE},
        "coordinator-action-delivery-ack": {
            "scheduler_state": DEFAULT_SCHEDULER_STATE
        },
        "coordinator-action-apply-result": {
            "coordinator_state": DEFAULT_COORDINATOR_STATE,
            "scheduler_state": DEFAULT_SCHEDULER_STATE,
            "frontier_state": DEFAULT_FRONTIER_STATE,
            "project_context_state": DEFAULT_PROJECT_CONTEXT_STATE,
            "missions_dir": DEFAULT_MISSIONS,
            "portfolio": DEFAULT_PORTFOLIO_JSON,
            "journal_dir": DEFAULT_FRONTIER_JOURNAL,
        },
        "coordinator-action-apply-project-context-result": {
            "coordinator_state": DEFAULT_COORDINATOR_STATE,
            "registry": DEFAULT_BINDINGS,
            "hosts": DEFAULT_HOSTS,
            "adapter": DEFAULT_ADAPTER,
            "scheduler_state": DEFAULT_SCHEDULER_STATE,
            "frontier_state": DEFAULT_FRONTIER_STATE,
            "project_context_state": DEFAULT_PROJECT_CONTEXT_STATE,
            "missions_dir": DEFAULT_MISSIONS,
            "portfolio": DEFAULT_PORTFOLIO_JSON,
            "journal_dir": DEFAULT_FRONTIER_JOURNAL,
        },
        "project-context-apply-event": {
            "coordinator_state": DEFAULT_COORDINATOR_STATE,
            "registry": DEFAULT_BINDINGS,
            "hosts": DEFAULT_HOSTS,
            "adapter": DEFAULT_ADAPTER,
            "frontier_state": DEFAULT_FRONTIER_STATE,
            "project_context_state": DEFAULT_PROJECT_CONTEXT_STATE,
        },
        "coordinator-action-complete": {
            "scheduler_state": DEFAULT_SCHEDULER_STATE
        },
        "coordinator-action-release": {
            "scheduler_state": DEFAULT_SCHEDULER_STATE
        },
    }
    for name, expected in required_by_command.get(args.command, {}).items():
        observed = getattr(args, name, None)
        if name == "mission" and expected == DEFAULT_MISSIONS:
            if observed is None or not _path_is_within(observed, expected):
                raise ProtocolError(
                    "CANONICAL_LIVE_STATE_REQUIRED: Mission must be in the "
                    f"canonical ledger {expected}"
                )
        else:
            _require_exact_cli_path(args, name, expected)


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
    options = {"indent": 2, "sort_keys": True}
    rendered = json.dumps(value, ensure_ascii=False, **options)
    output_encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        rendered.encode(output_encoding)
    except (LookupError, UnicodeEncodeError):
        # Windows task shells may expose cp932 even though persisted state is
        # UTF-8. Keep stdout machine-readable instead of crashing on a path or
        # verdict containing an otherwise valid Unicode character.
        rendered = json.dumps(value, ensure_ascii=True, **options)
    sys.stdout.write(rendered + "\n")


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


def _authorized_cli_writer(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    _require_canonical_live_mutation_paths(args)
    coordinator_state = load_json(args.coordinator_state)
    actor_task_id = require_primary_coordinator_writer(coordinator_state)
    supplied_owner = str(getattr(args, "owner_task_id", None) or "").strip()
    if supplied_owner and supplied_owner != actor_task_id:
        raise ProtocolError(
            "owner-task-id must match the current CODEX_THREAD_ID; caller "
            "identity cannot be delegated"
        )
    return coordinator_state, actor_task_id


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
    resolve.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    resolve.add_argument(
        "--project-context-state",
        type=Path,
        default=DEFAULT_PROJECT_CONTEXT_STATE,
    )
    resolve.add_argument("--dry-run", action="store_true")

    sub.add_parser("contract")

    compile_parser = sub.add_parser("compile-work-order")
    compile_parser.add_argument("--mission", required=True, type=Path)
    compile_parser.add_argument("--output", required=True, type=Path)
    compile_parser.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    compile_parser.add_argument("--dry-run", action="store_true")

    migrate = sub.add_parser("migrate")
    migrate.add_argument("--legacy", required=True, type=Path)
    migrate.add_argument("--output", required=True, type=Path)
    migrate.add_argument("--report", required=True, type=Path)
    migrate.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    migrate.add_argument("--dry-run", action="store_true")

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
    migrate_scheduler.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    migrate_scheduler.add_argument("--dry-run", action="store_true")

    writer_rebind = sub.add_parser("coordinator-writer-rebind")
    writer_rebind.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    writer_rebind.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    writer_rebind.add_argument("--expected-current-task-id", required=True)
    writer_rebind.add_argument("--new-task-id", required=True)
    writer_rebind.add_argument("--reason", required=True)
    writer_rebind.add_argument("--confirm", required=True)
    writer_rebind.add_argument("--dry-run", action="store_true")

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
    validate.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    validate.add_argument(
        "--project-context-state",
        type=Path,
        default=DEFAULT_PROJECT_CONTEXT_STATE,
    )

    mission_init = sub.add_parser("mission-init")
    mission_init.add_argument("--payload", required=True, type=Path)
    mission_init.add_argument("--missions-dir", type=Path, default=DEFAULT_MISSIONS)
    mission_init.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )

    mission_event = sub.add_parser("mission-event")
    mission_event.add_argument("--mission", required=True, type=Path)
    mission_event.add_argument("--event", required=True)
    mission_event.add_argument("--payload", type=Path)
    mission_event.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )

    mission_blocker_contract = sub.add_parser("mission-blocker-contract")
    mission_blocker_contract.add_argument("--mission", required=True, type=Path)
    mission_blocker_contract.add_argument("--payload", required=True, type=Path)
    mission_blocker_contract.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )

    mission_continue = sub.add_parser("mission-continue")
    mission_continue.add_argument("--prior", required=True, type=Path)
    mission_continue.add_argument("--payload", required=True, type=Path)
    mission_continue.add_argument(
        "--missions-dir", type=Path, default=DEFAULT_MISSIONS
    )
    mission_continue.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )

    terminal = sub.add_parser("terminal")
    terminal.add_argument("--mission", required=True, type=Path)
    terminal.add_argument("--terminal-dir", type=Path, default=DEFAULT_TERMINALS)
    terminal.add_argument(
        "--ledger", type=Path, default=DEFAULT_NOTIFICATION_LEDGER
    )
    terminal.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
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
    coordinator_status.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    coordinator_status.add_argument(
        "--project-context-state",
        type=Path,
        default=DEFAULT_PROJECT_CONTEXT_STATE,
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
    portfolio_render.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    portfolio_render.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    portfolio_render.add_argument(
        "--project-context-state",
        type=Path,
        default=DEFAULT_PROJECT_CONTEXT_STATE,
    )
    portfolio_render.add_argument(
        "--registry", type=Path, default=DEFAULT_BINDINGS
    )
    portfolio_render.add_argument("--hosts", type=Path, default=DEFAULT_HOSTS)
    portfolio_render.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
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
    coordinator_plan.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    coordinator_plan.add_argument(
        "--project-context-state",
        type=Path,
        default=DEFAULT_PROJECT_CONTEXT_STATE,
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
    coordinator_claim.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    coordinator_claim.add_argument(
        "--project-context-state",
        type=Path,
        default=DEFAULT_PROJECT_CONTEXT_STATE,
    )
    coordinator_claim.add_argument("--dry-run", action="store_true")

    coordinator_prepare = sub.add_parser("coordinator-action-prepare")
    coordinator_prepare.add_argument("--action-id", required=True)
    coordinator_prepare.add_argument("--recipient-thread-id", required=True)
    coordinator_prepare.add_argument("--packet-sha256", required=True)
    coordinator_prepare.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_prepare.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
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
    coordinator_sent.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    coordinator_sent.add_argument("--dry-run", action="store_true")

    coordinator_delivery_ack = sub.add_parser(
        "coordinator-action-delivery-ack"
    )
    coordinator_delivery_ack.add_argument("--action-id", required=True)
    coordinator_delivery_ack.add_argument("--delivery-ack-id", required=True)
    coordinator_delivery_ack.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_delivery_ack.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    coordinator_delivery_ack.add_argument("--dry-run", action="store_true")

    coordinator_apply_result = sub.add_parser(
        "coordinator-action-apply-result"
    )
    coordinator_apply_result.add_argument("--action-id", required=True)
    coordinator_apply_result.add_argument("--result", required=True, type=Path)
    coordinator_apply_result.add_argument(
        "--registry", type=Path, default=DEFAULT_BINDINGS
    )
    coordinator_apply_result.add_argument(
        "--hosts", type=Path, default=DEFAULT_HOSTS
    )
    coordinator_apply_result.add_argument(
        "--adapter", type=Path, default=DEFAULT_ADAPTER
    )
    coordinator_apply_result.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_apply_result.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    coordinator_apply_result.add_argument(
        "--project-context-state",
        type=Path,
        default=DEFAULT_PROJECT_CONTEXT_STATE,
    )
    coordinator_apply_result.add_argument(
        "--missions-dir", type=Path, default=DEFAULT_MISSIONS
    )
    coordinator_apply_result.add_argument(
        "--portfolio", type=Path, default=DEFAULT_PORTFOLIO_JSON
    )
    coordinator_apply_result.add_argument(
        "--journal-dir", type=Path, default=DEFAULT_FRONTIER_JOURNAL
    )
    coordinator_apply_result.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    coordinator_apply_result.add_argument("--dry-run", action="store_true")

    coordinator_apply_context_result = sub.add_parser(
        "coordinator-action-apply-project-context-result"
    )
    coordinator_apply_context_result.add_argument("--action-id", required=True)
    coordinator_apply_context_result.add_argument(
        "--result", required=True, type=Path
    )
    coordinator_apply_context_result.add_argument(
        "--registry", type=Path, default=DEFAULT_BINDINGS
    )
    coordinator_apply_context_result.add_argument(
        "--hosts", type=Path, default=DEFAULT_HOSTS
    )
    coordinator_apply_context_result.add_argument(
        "--adapter", type=Path, default=DEFAULT_ADAPTER
    )
    coordinator_apply_context_result.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_apply_context_result.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    coordinator_apply_context_result.add_argument(
        "--project-context-state",
        type=Path,
        default=DEFAULT_PROJECT_CONTEXT_STATE,
    )
    coordinator_apply_context_result.add_argument(
        "--missions-dir", type=Path, default=DEFAULT_MISSIONS
    )
    coordinator_apply_context_result.add_argument(
        "--portfolio", type=Path, default=DEFAULT_PORTFOLIO_JSON
    )
    coordinator_apply_context_result.add_argument(
        "--journal-dir", type=Path, default=DEFAULT_FRONTIER_JOURNAL
    )
    coordinator_apply_context_result.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    coordinator_apply_context_result.add_argument(
        "--dry-run", action="store_true"
    )

    coordinator_complete = sub.add_parser("coordinator-action-complete")
    coordinator_complete.add_argument("--action-id", required=True)
    coordinator_complete.add_argument("--outcome", required=True)
    coordinator_complete.add_argument("--evidence")
    coordinator_complete.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_complete.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    coordinator_complete.add_argument("--dry-run", action="store_true")

    coordinator_release = sub.add_parser("coordinator-action-release")
    coordinator_release.add_argument("--action-id", required=True)
    coordinator_release.add_argument("--reason", required=True)
    coordinator_release.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    coordinator_release.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    coordinator_release.add_argument("--dry-run", action="store_true")

    frontier_audit = sub.add_parser("frontier-audit")
    frontier_audit.add_argument("--registry", type=Path, default=DEFAULT_BINDINGS)
    frontier_audit.add_argument("--hosts", type=Path, default=DEFAULT_HOSTS)
    frontier_audit.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    frontier_audit.add_argument(
        "--missions-dir", type=Path, default=DEFAULT_MISSIONS
    )
    frontier_audit.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    frontier_audit.add_argument(
        "--portfolio", type=Path, default=DEFAULT_PORTFOLIO_JSON
    )
    frontier_audit.add_argument(
        "--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE
    )
    frontier_audit.add_argument("--dry-run", action="store_true", default=True)

    project_context_audit = sub.add_parser("project-context-audit")
    project_context_audit.add_argument(
        "--registry", type=Path, default=DEFAULT_BINDINGS
    )
    project_context_audit.add_argument(
        "--hosts", type=Path, default=DEFAULT_HOSTS
    )
    project_context_audit.add_argument(
        "--adapter", type=Path, default=DEFAULT_ADAPTER
    )
    project_context_audit.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    project_context_audit.add_argument(
        "--project-context-state",
        type=Path,
        default=DEFAULT_PROJECT_CONTEXT_STATE,
    )
    project_context_audit.add_argument(
        "--dry-run", action="store_true", default=True
    )

    project_context_apply = sub.add_parser("project-context-apply-event")
    project_context_apply.add_argument("--event", required=True, type=Path)
    project_context_apply.add_argument(
        "--registry", type=Path, default=DEFAULT_BINDINGS
    )
    project_context_apply.add_argument(
        "--hosts", type=Path, default=DEFAULT_HOSTS
    )
    project_context_apply.add_argument(
        "--adapter", type=Path, default=DEFAULT_ADAPTER
    )
    project_context_apply.add_argument(
        "--frontier-state", type=Path, default=DEFAULT_FRONTIER_STATE
    )
    project_context_apply.add_argument(
        "--project-context-state",
        type=Path,
        default=DEFAULT_PROJECT_CONTEXT_STATE,
    )
    project_context_apply.add_argument(
        "--coordinator-state", type=Path, default=DEFAULT_COORDINATOR_STATE
    )
    project_context_apply.add_argument("--dry-run", action="store_true")

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
                        frontier_state=load_frontier_state(
                            args.frontier_state,
                            (
                                str(item.get("repository_id") or "")
                                for item in registry.get("repositories", [])
                                if isinstance(item, dict)
                            ),
                        ),
                        project_context_state=load_project_context_state(
                            args.project_context_state,
                            (
                                str(item.get("repository_id") or "")
                                for item in registry.get("repositories", [])
                                if isinstance(item, dict)
                            ),
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
                    _authorized_cli_writer(args)
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
            _authorized_cli_writer(args)
            packet = compile_work_order(load_json(args.mission))
            if not args.dry_run:
                atomic_write_json(args.output, packet)
            _json_stdout(
                {
                    "classification": "COMPILED",
                    "output": str(args.output),
                    "packet_sha256": packet["packet_sha256"],
                    "dry_run": bool(args.dry_run),
                }
            )
            return 0

        if args.command == "migrate":
            _authorized_cli_writer(args)
            registry, report = migrate_legacy_registry(load_json(args.legacy))
            if not args.dry_run:
                atomic_write_json(args.output, registry)
                atomic_write_json(args.report, report)
            report["dry_run"] = bool(args.dry_run)
            _json_stdout(report)
            return 0

        if args.command == "coordinator-writer-rebind":
            _require_exact_cli_path(
                args, "coordinator_state", DEFAULT_COORDINATOR_STATE
            )
            _require_exact_cli_path(
                args, "scheduler_state", DEFAULT_SCHEDULER_STATE
            )
            coordinator_state = load_json(args.coordinator_state)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            result = rebind_primary_coordinator_writer(
                coordinator_state,
                scheduler_state,
                expected_current_task_id=args.expected_current_task_id,
                new_task_id=args.new_task_id,
                reason=args.reason,
                confirmation=args.confirm,
            )
            if not args.dry_run:
                validate_coordinator_state(coordinator_state)
                atomic_write_json(args.coordinator_state, coordinator_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "migrate-coordinator-ux":
            if not args.dry_run:
                _authorized_cli_writer(args)
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
            if not args.dry_run:
                _authorized_cli_writer(args)
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
                frontier_state=load_frontier_state(
                    args.frontier_state,
                    (
                        str(item.get("repository_id") or "")
                        for item in registry.get("repositories", [])
                        if isinstance(item, dict)
                    ),
                ),
                project_context_state=load_project_context_state(
                    args.project_context_state,
                    (
                        str(item.get("repository_id") or "")
                        for item in registry.get("repositories", [])
                        if isinstance(item, dict)
                    ),
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

        if args.command == "frontier-audit":
            registry = load_json(args.registry)
            hosts = load_json(args.hosts)
            adapter = load_json(args.adapter)
            validate_registry(registry, hosts)
            missions = load_missions(args.missions_dir)
            signals = collect_authority_signals(registry, hosts, adapter)
            frontier_state = load_frontier_state(
                args.frontier_state,
                (
                    str(item.get("repository_id") or "")
                    for item in registry.get("repositories", [])
                    if isinstance(item, dict)
                ),
            )
            portfolio = load_json(args.portfolio) if args.portfolio.is_file() else None
            scheduler_state = (
                load_scheduler_state(args.scheduler_state)
                if args.scheduler_state.is_file()
                else None
            )
            result = audit_frontier_state(
                registry,
                missions,
                frontier_state,
                signals,
                portfolio=portfolio,
                scheduler_state=scheduler_state,
            )
            _json_stdout(result)
            return 0 if result["classification"] == "FRONTIER_AUDIT_CLEAR" else 2

        if args.command == "project-context-audit":
            registry = load_json(args.registry)
            hosts = load_json(args.hosts)
            adapter = load_json(args.adapter)
            validate_registry(registry, hosts)
            repository_ids = [
                str(item.get("repository_id") or "")
                for item in registry.get("repositories", [])
                if isinstance(item, dict) and item.get("repository_id")
            ]
            result = audit_project_context_state(
                registry,
                load_project_context_state(
                    args.project_context_state, repository_ids
                ),
                load_frontier_state(args.frontier_state, repository_ids),
                collect_authority_signals(registry, hosts, adapter),
            )
            _json_stdout(result)
            return (
                0
                if result["classification"] == "PROJECT_CONTEXT_AUDIT_CLEAR"
                else 2
            )

        if args.command == "project-context-apply-event":
            if not args.dry_run:
                _authorized_cli_writer(args)
            registry = load_json(args.registry)
            hosts = load_json(args.hosts)
            adapter = load_json(args.adapter)
            validate_registry(registry, hosts)
            repository_ids = [
                str(item.get("repository_id") or "")
                for item in registry.get("repositories", [])
                if isinstance(item, dict) and item.get("repository_id")
            ]
            candidate = load_json(args.event)
            signals = collect_authority_signals(registry, hosts, adapter)
            signal = next(
                (
                    item
                    for item in signals
                    if item.get("repository_id") == candidate.get("repository_id")
                ),
                None,
            )
            frontier_state = load_frontier_state(
                args.frontier_state, repository_ids
            )
            validate_project_context_event_against_observations(
                candidate, frontier_state, signal
            )
            project_context_state = load_project_context_state(
                args.project_context_state, repository_ids
            )
            result = apply_project_context_event(
                project_context_state, candidate
            )
            if (
                not args.dry_run
                and result["classification"]
                == "PROJECT_CONTEXT_EVENT_APPLIED"
            ):
                atomic_write_json(
                    args.project_context_state, project_context_state
                )
            result["dry_run"] = bool(args.dry_run)
            result["mutated"] = bool(
                not args.dry_run
                and result["classification"]
                == "PROJECT_CONTEXT_EVENT_APPLIED"
            )
            _json_stdout(result)
            return 0

        if args.command == "authorized-runtime-action-register":
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
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
                actor_task_id=actor_task_id,
            )
            validate_coordinator_state(coordinator_state)
            if not args.dry_run:
                atomic_write_json(args.coordinator_state, coordinator_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "mission-init":
            _authorized_cli_writer(args)
            mission_payload = load_json(args.payload)
            require_new_mission_value_contract(mission_payload)
            mission = new_mission(mission_payload)
            path = _mission_path(args.missions_dir, mission)
            if path.exists():
                raise ProtocolError(f"Mission attempt already exists: {path}")
            atomic_write_json(path, mission)
            _json_stdout({"classification": "MISSION_CREATED", "path": str(path)})
            return 0

        if args.command == "mission-event":
            _authorized_cli_writer(args)
            mission = load_json(args.mission)
            payload = load_json(args.payload) if args.payload else {}
            event = args.event
            event_result: dict[str, Any] | None = None
            if event == "value_contract_admitted":
                event_result = admit_mission_value_contract(
                    mission, payload.get("value_contract")
                )
            elif event in {name for _, name in LINEAR_EVENTS}:
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
                event_result
                or {"classification": "MISSION_UPDATED", "state": mission["state"]}
            )
            return 0

        if args.command == "mission-blocker-contract":
            _authorized_cli_writer(args)
            _json_stdout(persist_blocked_contract(args.mission, args.payload))
            return 0

        if args.command == "mission-continue":
            _authorized_cli_writer(args)
            prior = load_json(args.prior)
            continuation_payload = load_json(args.payload)
            require_new_mission_value_contract(continuation_payload)
            next_mission = start_continuation(prior, continuation_payload)
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
            if not args.dry_run:
                _authorized_cli_writer(args)
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
            if not args.dry_run:
                _authorized_cli_writer(args)
            portfolio = load_json(args.input)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            validate_portfolio_scheduler_consistency(
                portfolio, scheduler_state
            )
            registry = load_json(args.registry)
            hosts = load_json(args.hosts)
            adapter = load_json(args.adapter)
            repository_ids = [
                str(item.get("repository_id") or "")
                for item in registry.get("repositories", [])
                if isinstance(item, dict) and item.get("repository_id")
            ]
            frontier_state = load_frontier_state(
                args.frontier_state,
                repository_ids,
            )
            project_context_state = load_project_context_state(
                args.project_context_state, repository_ids
            )
            signals = collect_authority_signals(registry, hosts, adapter)
            portfolio = migrate_portfolio_to_project_context_v4(
                portfolio,
                project_context_state,
                frontier_state,
                signals,
            )
            validate_portfolio_scheduler_consistency(portfolio, scheduler_state)
            validate_portfolio_frontier_consistency(
                portfolio,
                frontier_state,
                signals,
            )
            validate_portfolio_project_context_consistency(
                portfolio,
                project_context_state,
                frontier_state,
                signals,
            )
            rendered = render_portfolio_markdown(portfolio)
            if not args.dry_run:
                atomic_write_json(args.input, portfolio)
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
                    "schema_version": portfolio.get("schema_version"),
                    "project_context_revision": portfolio.get(
                        "project_context_revision"
                    ),
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
                frontier_state=load_frontier_state(
                    args.frontier_state,
                    (
                        str(item.get("repository_id") or "")
                        for item in registry.get("repositories", [])
                        if isinstance(item, dict)
                    ),
                ),
                project_context_state=load_project_context_state(
                    args.project_context_state,
                    (
                        str(item.get("repository_id") or "")
                        for item in registry.get("repositories", [])
                        if isinstance(item, dict)
                    ),
                ),
            )
            if args.command == "coordinator-action-claim":
                coordinator_state, actor_task_id = _authorized_cli_writer(args)
                supplied_owner = str(args.owner_task_id or "").strip()
                if supplied_owner and supplied_owner != actor_task_id:
                    raise ProtocolError(
                        "owner-task-id must match the current CODEX_THREAD_ID; "
                        "caller identity cannot be delegated"
                    )
                result = claim_coordinator_action(
                    scheduler_state,
                    plan,
                    args.action_id,
                    owner_task_id=actor_task_id,
                )
                if not args.dry_run:
                    atomic_write_json(args.scheduler_state, scheduler_state)
                result["dry_run"] = bool(args.dry_run)
                _json_stdout(result)
            else:
                _json_stdout(plan)
            return 0

        if args.command == "coordinator-action-sent":
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            result = mark_coordinator_action_sent(
                scheduler_state,
                args.action_id,
                args.recipient_thread_id,
                packet_sha256=args.packet_sha256,
                after_cursor=args.after_cursor,
                actor_task_id=actor_task_id,
            )
            if not args.dry_run:
                atomic_write_json(args.scheduler_state, scheduler_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "coordinator-action-delivery-ack":
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            result = acknowledge_coordinator_action_delivery(
                scheduler_state,
                args.action_id,
                args.delivery_ack_id,
                actor_task_id=actor_task_id,
            )
            if not args.dry_run:
                atomic_write_json(args.scheduler_state, scheduler_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "coordinator-action-apply-result":
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
            result_payload = load_json(args.result)
            registry = load_json(args.registry)
            hosts = load_json(args.hosts)
            adapter = load_json(args.adapter)
            validate_registry(registry, hosts)
            observed_authority_signal = next(
                (
                    signal
                    for signal in collect_authority_signals(
                        registry, hosts, adapter
                    )
                    if signal.get("repository_id")
                    == result_payload.get("repository_id")
                ),
                None,
            )
            if args.dry_run:
                scheduler_state = load_scheduler_state(args.scheduler_state)
                missions = load_missions(args.missions_dir)
                frontier_state = load_frontier_state(
                    args.frontier_state,
                    (
                        str(item.get("repository_id") or "")
                        for item in missions
                        if isinstance(item, dict)
                    ),
                )
                portfolio = load_json(args.portfolio)
                project_context_state = load_project_context_state(
                    args.project_context_state,
                    (
                        str(item.get("repository_id") or "")
                        for item in registry.get("repositories", [])
                        if isinstance(item, dict)
                    ),
                )
                result = apply_external_result_transaction(
                    scheduler_state,
                    frontier_state,
                    missions,
                    portfolio,
                    args.action_id,
                    result_payload,
                    observed_authority_signal=observed_authority_signal,
                    project_context_state=project_context_state,
                    coordinator_state=coordinator_state,
                    actor_task_id=actor_task_id,
                )
            else:
                result = apply_external_result_transaction_files(
                    scheduler_path=args.scheduler_state,
                    frontier_path=args.frontier_state,
                    project_context_path=args.project_context_state,
                    missions_dir=args.missions_dir,
                    portfolio_path=args.portfolio,
                    journal_dir=args.journal_dir,
                    action_id=args.action_id,
                    result=result_payload,
                    observed_authority_signal=observed_authority_signal,
                    coordinator_path=args.coordinator_state,
                    actor_task_id=actor_task_id,
                )
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "coordinator-action-apply-project-context-result":
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
            result_payload = load_json(args.result)
            registry = load_json(args.registry)
            hosts = load_json(args.hosts)
            adapter = load_json(args.adapter)
            validate_registry(registry, hosts)
            signals = collect_authority_signals(registry, hosts, adapter)
            if args.dry_run:
                scheduler_state = load_scheduler_state(args.scheduler_state)
                missions = load_missions(args.missions_dir)
                frontier_state = load_frontier_state(
                    args.frontier_state,
                    (
                        str(item.get("repository_id") or "")
                        for item in registry.get("repositories", [])
                        if isinstance(item, dict)
                    ),
                )
                project_context_state = load_project_context_state(
                    args.project_context_state,
                    (
                        str(item.get("repository_id") or "")
                        for item in registry.get("repositories", [])
                        if isinstance(item, dict)
                    ),
                )
                portfolio = load_json(args.portfolio)
                result = apply_project_context_result_transaction(
                    scheduler_state,
                    project_context_state,
                    frontier_state,
                    missions,
                    portfolio,
                    args.action_id,
                    result_payload,
                    authority_signals=signals,
                    actor_task_id=actor_task_id,
                )
            else:
                result = apply_project_context_result_transaction_files(
                    scheduler_path=args.scheduler_state,
                    project_context_path=args.project_context_state,
                    frontier_path=args.frontier_state,
                    missions_dir=args.missions_dir,
                    portfolio_path=args.portfolio,
                    journal_dir=args.journal_dir,
                    action_id=args.action_id,
                    result=result_payload,
                    authority_signals=signals,
                    actor_task_id=actor_task_id,
                )
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "coordinator-action-prepare":
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            result = prepare_coordinator_action_delivery(
                scheduler_state,
                args.action_id,
                args.recipient_thread_id,
                args.packet_sha256,
                actor_task_id=actor_task_id,
            )
            if not args.dry_run:
                atomic_write_json(args.scheduler_state, scheduler_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "coordinator-action-complete":
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            result = complete_coordinator_action(
                scheduler_state,
                args.action_id,
                args.outcome,
                evidence=args.evidence,
                coordinator_state=coordinator_state,
                actor_task_id=actor_task_id,
            )
            if not args.dry_run:
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
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
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
                    actor_task_id=actor_task_id,
                )
            elif args.command == "authorized-runtime-action-rollback":
                result = rollback_authorized_runtime_action(
                    coordinator_state,
                    scheduler_state,
                    args.action_id,
                    dry_run=args.dry_run,
                    persist_state=(None if args.dry_run else persist_runtime_state),
                    actor_task_id=actor_task_id,
                )
            else:
                result = reconcile_authorized_runtime_completion(
                    coordinator_state,
                    scheduler_state,
                    args.action_id,
                    dry_run=args.dry_run,
                    persist_state=(None if args.dry_run else persist_runtime_state),
                    actor_task_id=actor_task_id,
                )
            if not args.dry_run:
                persist_runtime_state()
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "coordinator-action-release":
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
            scheduler_state = load_scheduler_state(args.scheduler_state)
            result = release_coordinator_action(
                scheduler_state,
                args.action_id,
                args.reason,
                actor_task_id=actor_task_id,
            )
            if not args.dry_run:
                atomic_write_json(args.scheduler_state, scheduler_state)
            result["dry_run"] = bool(args.dry_run)
            _json_stdout(result)
            return 0

        if args.command == "queue-coordinator-event":
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
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
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
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
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
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
            coordinator_state, actor_task_id = _authorized_cli_writer(args)
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
