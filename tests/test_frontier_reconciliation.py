from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import supervise_repo_loop as loop  # noqa: E402
from test_coordinator_only_ux import (  # noqa: E402
    REPO_A,
    REPO_B,
    fixture,
    mission as coordinator_mission,
)


LANE = "default"
OWNER = "primary-coordinator"
SUPERVISOR = "supervisor-thread-a"


def frontier_event(
    *,
    epoch: int,
    based_on: int,
    artifact_id: str | None,
    actor: str,
    disposition: str = "accepted",
    token: str,
    supersedes: list[str] | None = None,
    branch: str | None = "main",
    head_sha: str | None = "a" * 40,
) -> dict:
    return {
        "repository_id": REPO_A,
        "lane_id": LANE,
        "frontier_epoch": epoch,
        "frontier_event_id": f"frontier-{token}",
        "artifact_id": artifact_id,
        "artifact_revision": f"revision-{token}" if artifact_id else None,
        "artifact_sha256": loop.sha256_text(f"artifact-{token}") if artifact_id else None,
        "branch": branch,
        "head_sha": head_sha,
        "disposition": disposition,
        "source_actor": actor,
        "source_message_id": f"message-{token}",
        "source_result_id": f"result-{token}",
        "based_on_frontier_epoch": based_on,
        "supersedes_event_ids": list(supersedes or []),
        "recorded_at": f"2026-08-02T00:{epoch:02d}:00Z",
    }


def authority_signal(record: dict, *, branch: str | None = None, head: str | None = None) -> dict:
    payload = {
        "repository_id": record["repository_id"],
        "root": "C:/synthetic/repository",
        "sources": [],
        "authority_watch_configured": False,
        "git": {
            "status": "present",
            "branch": record["branch"] if branch is None else branch,
            "head_sha": record["head_sha"] if head is None else head,
            "dirty": False,
            "dirty_paths": [],
        },
        "high_water_marks": [],
    }
    payload["authority_fingerprint"] = loop.canonical_json_hash(payload)
    return payload


def current_frontier(state: dict) -> dict:
    return state["records"][f"{REPO_A}|{LANE}"]


def mission_record(state: str = "SUPERVISOR_ADJUDICATION_REQUESTED") -> dict:
    return {
        "schema_version": 2,
        "repository_id": REPO_A,
        "launch_set_id": "launch-frontier",
        "mission_id": "mission-frontier",
        "attempt_id": "attempt-1",
        "worker_task_id": "worker-a",
        "host_id": "host-a",
        "supervisor_thread_id": SUPERVISOR,
        "supervision_lane": LANE,
        "mode": "coordinator",
        "state": state,
        "mission_status": "running",
        "review_status": "pending",
        "review_policy": {"gate": "none", "depth": "light", "stage": "mission"},
        "external_effects": loop.default_external_effects(),
        "dispatch_keys": [],
        "returned_report_hashes": [],
        "completed_worker_turns": 1,
        "safety_ceiling": 8,
        "events": [{"state": state, "at": "2026-08-02T00:00:00Z"}],
        "created_at": "2026-08-02T00:00:00Z",
        "updated_at": "2026-08-02T00:00:00Z",
    }


def portfolio_v2() -> dict:
    return {
        "schema_version": 2,
        "semantic_fingerprint": "f" * 64,
        "coordinator_availability": "AVAILABLE",
        "execution_state": "WAITING_EXTERNAL",
        "scheduler_revision": 1,
        "active_route_count": 1,
        "concurrency_limit": 3,
        "active_routes": [
            {
                "repository_id": REPO_A,
                "action_id": "action-frontier",
                "recipient_thread_id": SUPERVISOR,
                "delivery_token": "d" * 64,
                "after_cursor": "cursor-1",
                "status": "waiting",
                "observer_kind": "chatgpt_poll",
            }
        ],
        "next_user_action": None,
        "repositories": [
            {
                "repository_id": REPO_A,
                "project_name": "Synthetic frontier project",
                "state": "WAITING_EXTERNAL",
                "progress": {"current_stage": "SUPERVISOR", "completed_stages": ["MISSION", "WORK_ORDER", "WORKER", "WORKER_REPORT"]},
                "roadmap": {
                    "overall_position": "frontier reconciliation",
                    "completed_blocks": ["synthetic reproduction"],
                    "current_block": "Supervisor result",
                    "next_blocks": ["apply exact frontier"],
                    "completion_definition": "current result applied",
                    "next_gate": "frontier certificate",
                },
                "why": "An exact external result is pending.",
                "owner": "Coordinator",
                "next_move": "Apply the exact result transaction.",
                "route_owner": f"action-frontier / {SUPERVISOR} / chatgpt_poll",
                "stop": None,
            }
        ],
    }


def scheduler_with_waiting_route() -> dict:
    state = loop.default_scheduler_state()
    state["revision"] = 1
    state["route_leases"] = [
        {
            "action_id": "action-frontier",
            "action": {
                "action_id": "action-frontier",
                "kind": "await_supervisor_verdict",
                "priority": 10,
                "stable_order": 0,
                "requires_external_result": True,
                "payload": {
                    "route": {
                        "repository_id": REPO_A,
                        "mission_id": "mission-frontier",
                        "attempt_id": "attempt-1",
                        "supervision_lane": LANE,
                        "recipient_kind": "supervisor",
                        "recipient_thread_id": SUPERVISOR,
                        "observer_kind": "chatgpt_poll",
                    }
                },
            },
            "status": "waiting",
            "external_lifecycle_state": "dispatched",
            "external_lifecycle_history": [
                {"state": "created", "at": "2026-08-02T00:00:00Z"},
                {"state": "dispatched", "at": "2026-08-02T00:01:00Z"},
            ],
            "state_fingerprint": "state-frontier",
            "owner_task_id": OWNER,
            "repository_id": REPO_A,
            "mission_id": "mission-frontier",
            "attempt_id": "attempt-1",
            "route_class": "execution",
            "observer_kind": "chatgpt_poll",
            "recipient_thread_id": SUPERVISOR,
            "packet_sha256": "c" * 64,
            "delivery_token": "d" * 64,
            "after_cursor": "cursor-1",
            "sent_at": "2026-08-02T00:01:00Z",
            "leased_at": "2026-08-02T00:01:00Z",
        }
    ]
    state["active_claim"] = copy.deepcopy(state["route_leases"][0])
    return state


def external_result(event: dict, *, result_id: str = "external-result-1") -> dict:
    before = mission_record()
    after = copy.deepcopy(before)
    loop.apply_supervisor_verdict(after, "complete")
    bound_event = copy.deepcopy(event)
    bound_event["source_result_id"] = result_id
    return {
        "schema_version": 1,
        "result_id": result_id,
        "action_id": "action-frontier",
        "repository_id": REPO_A,
        "lane_id": LANE,
        "source_actor": "supervisor",
        "source_thread_id": SUPERVISOR,
        "source_turn_id": "turn-1",
        "source_message_id": bound_event["source_message_id"],
        "disposition": bound_event["disposition"],
        "based_on_frontier_epoch": bound_event["based_on_frontier_epoch"],
        "frontier_event": bound_event,
        "authority_signal": authority_signal(bound_event),
        "mission_id": before["mission_id"],
        "attempt_id": before["attempt_id"],
        "mission_before_sha256": loop.canonical_json_hash(before),
        "mission_after": after,
    }


class FrontierReconciliationRegressionTests(unittest.TestCase):
    def test_FR_CLIP_01_later_subaru_acceptance_cannot_regress_to_s1(self) -> None:
        state = loop.default_frontier_state([REPO_A])
        s1 = frontier_event(epoch=1, based_on=0, artifact_id="clip-s1", actor="supervisor", token="s1")
        subaru = frontier_event(epoch=2, based_on=1, artifact_id="clip-subaru", actor="supervisor", token="subaru", supersedes=[s1["frontier_event_id"]])
        stale_repromotion = frontier_event(epoch=3, based_on=2, artifact_id="clip-s1", actor="coordinator", token="s1-reprompt")
        loop.apply_frontier_event(state, s1)
        loop.apply_frontier_event(state, subaru)
        result = loop.apply_frontier_event(state, stale_repromotion)
        self.assertEqual(result["classification"], "FRONTIER_EVENT_PRECEDENCE_REJECTED")
        self.assertEqual(current_frontier(state)["artifact_id"], "clip-subaru")

    def test_FR_NLM_01_null_frontier_tombstone_blocks_partial_repromotion(self) -> None:
        state = loop.default_frontier_state([REPO_A])
        partial = frontier_event(
            epoch=1,
            based_on=0,
            artifact_id="cue-002-partial",
            actor="supervisor",
            token="partial",
        )
        tombstone = frontier_event(
            epoch=2,
            based_on=1,
            artifact_id=None,
            actor="supervisor",
            disposition="none",
            token="no-eligible-candidate",
            supersedes=[partial["frontier_event_id"]],
        )
        worker_draft = frontier_event(
            epoch=3,
            based_on=2,
            artifact_id="cue-002-partial",
            actor="worker",
            token="worker-repromotion",
        )
        loop.apply_frontier_event(state, partial)
        loop.apply_frontier_event(state, tombstone)
        result = loop.apply_frontier_event(state, worker_draft)
        self.assertEqual(result["classification"], "FRONTIER_EVENT_PRECEDENCE_REJECTED")
        self.assertIsNone(current_frontier(state)["artifact_id"])
        self.assertEqual(current_frontier(state)["disposition"], "none")
        self.assertEqual(state["safety_mode"], loop.FRONTIER_SAFETY_MODE)
        gate = loop.frontier_gate_decision(
            state,
            REPO_A,
            LANE,
            action_kind="present_user_card",
            expected_artifact={"artifact_id": "cue-002-partial"},
            authority_signal=authority_signal(tombstone),
        )
        self.assertEqual(gate["classification"], "FRONTIER_RECONCILIATION_REQUIRED")
        self.assertIn("no_candidate", gate["reasons"])

    def test_FR_FFF_01_delivered_densou_request_waits_for_d0_result_application(self) -> None:
        scheduler = scheduler_with_waiting_route()
        frontier = loop.default_frontier_state([REPO_A])
        missions = [mission_record()]
        portfolio = portfolio_v2()
        loop.acknowledge_coordinator_action_delivery(scheduler, "action-frontier", "delivery-ack-1", actor_task_id=OWNER)
        with self.assertRaisesRegex(loop.ProtocolError, "result_applied"):
            loop.complete_coordinator_action(scheduler, "action-frontier", "delivered", evidence="delivery-ack-1", actor_task_id=OWNER)
        d0 = frontier_event(epoch=1, based_on=0, artifact_id="fff-d0", actor="supervisor", token="fff-d0")
        payload = external_result(d0)
        applied = loop.apply_external_result_transaction(
            scheduler,
            frontier,
            missions,
            portfolio,
            "action-frontier",
            payload,
            observed_authority_signal=payload["authority_signal"],
            actor_task_id=OWNER,
        )
        self.assertEqual(applied["classification"], "EXTERNAL_RESULT_APPLIED")
        self.assertEqual(current_frontier(frontier)["artifact_id"], "fff-d0")
        self.assertEqual(missions[0]["state"], "COMPLETE")
        self.assertEqual(portfolio["repositories"][0]["frontier_certificate"]["artifact_id"], "fff-d0")
        self.assertEqual(scheduler["completed_actions"][0]["external_lifecycle_state"], "result_applied")

    def test_FR_RA_01_human_accepted_normal_360_blocks_old_artifact_repromotion(self) -> None:
        state = loop.default_frontier_state([REPO_A])
        normal = frontier_event(epoch=1, based_on=0, artifact_id="normal-360", actor="human", token="normal-360")
        old = frontier_event(epoch=2, based_on=1, artifact_id="pre-normal-preview", actor="supervisor", token="old-preview")
        loop.apply_frontier_event(state, normal)
        result = loop.apply_frontier_event(state, old)
        self.assertEqual(result["classification"], "FRONTIER_EVENT_PRECEDENCE_REJECTED")
        self.assertEqual(current_frontier(state)["artifact_id"], "normal-360")

    def test_FR_RACE_01_stale_result_is_quarantined_by_epoch_cas(self) -> None:
        scheduler = scheduler_with_waiting_route()
        frontier = loop.default_frontier_state([REPO_A])
        portfolio = portfolio_v2()
        first = frontier_event(epoch=1, based_on=0, artifact_id="artifact-1", actor="supervisor", token="race-1")
        second = frontier_event(epoch=2, based_on=1, artifact_id="artifact-2", actor="supervisor", token="race-2", supersedes=[first["frontier_event_id"]])
        loop.apply_frontier_event(frontier, first)
        loop.apply_frontier_event(frontier, second)
        stale = frontier_event(epoch=2, based_on=1, artifact_id="artifact-stale", actor="supervisor", token="race-stale")
        payload = external_result(stale, result_id="stale-result")
        result = loop.apply_external_result_transaction(
            scheduler,
            frontier,
            [mission_record()],
            portfolio,
            "action-frontier",
            payload,
            observed_authority_signal=payload["authority_signal"],
            actor_task_id=OWNER,
        )
        self.assertEqual(result["classification"], "STALE_EXTERNAL_RESULT_QUARANTINED")
        self.assertEqual(current_frontier(frontier)["artifact_id"], "artifact-2")
        self.assertEqual(frontier["quarantined_results"][0]["result_id"], "stale-result")
        self.assertEqual(scheduler["route_leases"], [])
        self.assertEqual(
            scheduler["completed_actions"][0]["external_lifecycle_state"],
            "stale_result_quarantined",
        )
        self.assertEqual(portfolio["active_route_count"], 0)
        self.assertEqual(
            portfolio["repositories"][0]["frontier_status"],
            "verified",
        )

    def test_FR_ACK_01_delivery_ack_is_not_semantic_completion(self) -> None:
        scheduler = scheduler_with_waiting_route()
        receipt = loop.acknowledge_coordinator_action_delivery(scheduler, "action-frontier", "ack-1", actor_task_id=OWNER)
        self.assertEqual(receipt["lifecycle_state"], "delivery_acknowledged")
        self.assertEqual(len(scheduler["route_leases"]), 1)
        self.assertEqual(scheduler["completed_actions"], [])

    def test_direction_delivery_completes_only_after_atomic_semantic_result(self) -> None:
        registry, _, _, coordinator = fixture()
        queued = loop.queue_coordinator_event(
            coordinator,
            kind="direction_update",
            repository_id=REPO_A,
            raw_text="Park the historical route and restore the current one.",
        )
        scheduler = scheduler_with_waiting_route()
        scheduler["route_leases"][0]["action"]["kind"] = (
            "route_direction_update"
        )
        route = scheduler["route_leases"][0]["action"]["payload"]["route"]
        route["mission_id"] = None
        route["attempt_id"] = None
        scheduler["route_leases"][0]["action"]["payload"]["event"] = copy.deepcopy(
            queued["event"]
        )
        scheduler["active_claim"] = copy.deepcopy(
            scheduler["route_leases"][0]
        )
        loop.acknowledge_coordinator_event_routed(
            coordinator, queued["event_id"], SUPERVISOR
        )
        loop.acknowledge_coordinator_action_delivery(
            scheduler, "action-frontier", "direction-delivery-ack", actor_task_id=OWNER
        )
        frontier = loop.default_frontier_state([REPO_A])
        missions = [mission_record()]
        portfolio = portfolio_v2()
        event = frontier_event(
            epoch=1,
            based_on=0,
            artifact_id="restored-current-artifact",
            actor="supervisor",
            token="direction-result",
        )
        payload = external_result(event, result_id="direction-result")
        payload["input_disposition"] = "ADOPTED"
        result = loop.apply_external_result_transaction(
            scheduler,
            frontier,
            missions,
            portfolio,
            "action-frontier",
            payload,
            observed_authority_signal=payload["authority_signal"],
            coordinator_state=coordinator,
            actor_task_id=OWNER,
        )
        self.assertEqual(result["classification"], "EXTERNAL_RESULT_APPLIED")
        self.assertEqual(coordinator["pending_user_events"], [])
        self.assertEqual(
            coordinator["routed_user_events"][0]["state"], "result_applied"
        )
        self.assertEqual(
            coordinator["routed_user_events"][0]["input_disposition"],
            "ADOPTED",
        )
        self.assertEqual(missions[0]["state"], "COMPLETE")
        self.assertEqual(scheduler["route_leases"], [])

    def test_FR_DUP_01_exact_result_replay_converges_idempotently(self) -> None:
        scheduler = scheduler_with_waiting_route()
        frontier = loop.default_frontier_state([REPO_A])
        missions = [mission_record()]
        portfolio = portfolio_v2()
        event = frontier_event(epoch=1, based_on=0, artifact_id="artifact-dedup", actor="supervisor", token="dedup")
        payload = external_result(event, result_id="dedup-result")
        first = loop.apply_external_result_transaction(
            scheduler,
            frontier,
            missions,
            portfolio,
            "action-frontier",
            payload,
            observed_authority_signal=payload["authority_signal"],
            actor_task_id=OWNER,
        )
        snapshot = copy.deepcopy((scheduler, frontier, missions, portfolio))
        second = loop.apply_external_result_transaction(
            scheduler,
            frontier,
            missions,
            portfolio,
            "action-frontier",
            payload,
            observed_authority_signal=payload["authority_signal"],
            actor_task_id=OWNER,
        )
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual((scheduler, frontier, missions, portfolio), snapshot)

    def test_FR_AUTH_01_authority_signal_includes_git_and_file_high_water_marks(self) -> None:
        registry, hosts, adapter, _ = fixture()
        registry["repositories"][0]["authority_watch"] = ["AUTHORITY.md"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "frontier@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Frontier Test"], cwd=root, check=True)
            authority = root / "AUTHORITY.md"
            authority.write_text("frontier one\n", encoding="utf-8")
            subprocess.run(["git", "add", "AUTHORITY.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "authority one"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            authority.write_text("frontier two\n", encoding="utf-8")
            hosts["hosts"][0]["known_repository_roots"][REPO_A] = str(root)
            signal = next(item for item in loop.collect_authority_signals(registry, hosts, adapter) if item["repository_id"] == REPO_A)
        self.assertEqual(signal["git"]["branch"], "main")
        self.assertRegex(signal["git"]["head_sha"], r"^[0-9a-f]{40}$")
        self.assertTrue(signal["git"]["dirty"])
        self.assertTrue(signal["high_water_marks"][0]["is_dirty"])
        self.assertRegex(signal["high_water_marks"][0]["last_commit_sha"], r"^[0-9a-f]{40}$")

        scheduler = scheduler_with_waiting_route()
        frontier = loop.default_frontier_state([REPO_A])
        missions = [mission_record()]
        before = copy.deepcopy(missions)
        portfolio = portfolio_v2()
        invented_head = "b" * 40
        fake_event = frontier_event(
            epoch=1,
            based_on=0,
            artifact_id="self-declared-artifact",
            actor="supervisor",
            token="invalid-authority",
            branch=signal["git"]["branch"],
            head_sha=invented_head,
        )
        payload = external_result(fake_event, result_id="invalid-authority-result")
        fake_signal = copy.deepcopy(signal)
        fake_signal["git"]["head_sha"] = invented_head
        fake_signal["sources"][0]["path"] = "obsolete/AUTHORITY.md"
        fake_signal["high_water_marks"][0]["path"] = "obsolete/AUTHORITY.md"
        fake_signal["authority_fingerprint"] = loop.canonical_json_hash(
            {
                key: value
                for key, value in fake_signal.items()
                if key != "authority_fingerprint"
            }
        )
        payload["authority_signal"] = fake_signal
        result = loop.apply_external_result_transaction(
            scheduler,
            frontier,
            missions,
            portfolio,
            "action-frontier",
            payload,
            observed_authority_signal=signal,
            actor_task_id=OWNER,
        )
        self.assertEqual(result["classification"], "EXTERNAL_RESULT_FAILED")
        self.assertIn("current observation", result["reason"])
        self.assertEqual(missions, before)
        self.assertEqual(frontier["records"], {})
        self.assertEqual(scheduler["route_leases"], [])
        self.assertEqual(
            scheduler["completed_actions"][0]["external_lifecycle_state"],
            "failed",
        )
        self.assertEqual(portfolio["active_route_count"], 0)

    def test_FR_BRANCH_01_certificate_rejects_old_branch_or_head(self) -> None:
        state = loop.default_frontier_state([REPO_A])
        record = frontier_event(epoch=1, based_on=0, artifact_id="branch-artifact", actor="supervisor", token="branch")
        loop.apply_frontier_event(state, record)
        certificate = loop.issue_frontier_certificate(state, REPO_A, LANE, authority_signal(record))
        with self.assertRaisesRegex(loop.ProtocolError, "branch|head"):
            loop.validate_frontier_certificate(certificate, state, authority_signal(record, branch="feature/new", head="b" * 40))

    def test_FR_HIST_01_historical_artifact_review_card_is_blocked(self) -> None:
        state = loop.default_frontier_state([REPO_A])
        old = frontier_event(epoch=1, based_on=0, artifact_id="old-review", actor="supervisor", token="hist-old")
        current = frontier_event(epoch=2, based_on=1, artifact_id="current-review", actor="supervisor", token="hist-current", supersedes=[old["frontier_event_id"]])
        loop.apply_frontier_event(state, old)
        loop.apply_frontier_event(state, current)
        decision = loop.frontier_gate_decision(state, REPO_A, LANE, action_kind="present_user_card", expected_artifact={"artifact_id": "old-review"}, authority_signal=authority_signal(current))
        self.assertEqual(decision["classification"], "FRONTIER_RECONCILIATION_REQUIRED")
        self.assertIn("historical", decision["reasons"])
        historical = loop.frontier_gate_decision(
            state,
            REPO_A,
            LANE,
            action_kind="present_user_card",
            expected_artifact={
                "artifact_id": old["artifact_id"],
                "artifact_revision": old["artifact_revision"],
                "artifact_sha256": old["artifact_sha256"],
                "historical_review": True,
            },
            authority_signal=authority_signal(current),
        )
        self.assertEqual(
            historical["classification"],
            "FRONTIER_HISTORICAL_REVIEW_ALLOWED",
        )
        self.assertEqual(
            historical["historical_artifact"]["frontier_event_id"],
            old["frontier_event_id"],
        )
        self.assertEqual(current_frontier(state), current)

    def test_FR_PROJ_01_stale_portfolio_frontier_projection_is_rejected(self) -> None:
        state = loop.default_frontier_state([REPO_A])
        record = frontier_event(epoch=1, based_on=0, artifact_id="projection-current", actor="supervisor", token="projection")
        loop.apply_frontier_event(state, record)
        signal = authority_signal(record)
        portfolio = loop.migrate_portfolio_to_frontier_v3(portfolio_v2(), state, [signal])
        portfolio["repositories"][0]["frontier_certificate"]["frontier_event_id"] = "historical-event"
        portfolio["semantic_fingerprint"] = loop.portfolio_semantic_fingerprint(portfolio)
        with self.assertRaisesRegex(loop.ProtocolError, "frontier"):
            loop.validate_portfolio_frontier_consistency(portfolio, state, [signal])

    def test_FR_HUMAN_01_human_rejection_cannot_be_undone_by_lower_authority(self) -> None:
        state = loop.default_frontier_state([REPO_A])
        accepted = frontier_event(epoch=1, based_on=0, artifact_id="human-reviewed", actor="human", token="human-accept")
        authority_laundering = frontier_event(
            epoch=2,
            based_on=1,
            artifact_id="human-reviewed",
            actor="supervisor",
            token="human-authority-laundering",
        )
        rejected = frontier_event(epoch=2, based_on=1, artifact_id="human-reviewed", actor="human", disposition="rejected", token="human-reject", supersedes=[accepted["frontier_event_id"]])
        repromote = frontier_event(epoch=3, based_on=2, artifact_id="human-reviewed", actor="supervisor", disposition="active", token="human-repromote")
        loop.apply_frontier_event(state, accepted)
        laundering_result = loop.apply_frontier_event(
            state, authority_laundering
        )
        self.assertEqual(
            laundering_result["classification"],
            "FRONTIER_EVENT_PRECEDENCE_REJECTED",
        )
        self.assertEqual(current_frontier(state)["source_actor"], "human")
        loop.apply_frontier_event(state, rejected)
        result = loop.apply_frontier_event(state, repromote)
        self.assertEqual(result["classification"], "FRONTIER_EVENT_PRECEDENCE_REJECTED")
        self.assertEqual(current_frontier(state)["disposition"], "rejected")
        self.assertEqual(state["safety_mode"], loop.FRONTIER_SAFETY_MODE)
        self.assertEqual(frontier["artifact_id"] if (frontier := state["retired_artifacts"][0]) else None, "human-reviewed")

    def test_plan_enters_transport_only_reconciliation_until_all_frontiers_certify(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        registry["repositories"][1]["allow_request_next_mission"] = False
        running = coordinator_mission(
            REPO_A, "WORK_ORDER_RECEIVED", mission_id="frontier-plan"
        )
        signals: list[dict] = []
        certified = loop.default_frontier_state([REPO_A, REPO_B])
        for index, repository_id in enumerate((REPO_A, REPO_B), start=1):
            record = frontier_event(
                epoch=1,
                based_on=0,
                artifact_id=f"artifact-{index}",
                actor="supervisor",
                token=f"plan-{index}",
            )
            record["repository_id"] = repository_id
            signal = authority_signal(record)
            signal["authority_watch_configured"] = False
            signal["authority_fingerprint"] = loop.canonical_json_hash(
                {key: value for key, value in signal.items() if key != "authority_fingerprint"}
            )
            signals.append(signal)
            loop.apply_frontier_event(certified, record)
        missing = loop.default_frontier_state([REPO_A, REPO_B])
        blocked_plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [running],
            coordinator,
            loop.default_scheduler_state(),
            authority_signals=signals,
            frontier_state=missing,
        )
        self.assertEqual(
            blocked_plan["next_action"]["kind"],
            "reconcile_repository_frontier",
        )
        running["value_contract"]["authority_fingerprint"] = signals[0][
            "authority_fingerprint"
        ]
        running["active_artifact"] = {
            "artifact_id": "artifact-1",
            "artifact_revision": "revision-plan-1",
            "artifact_sha256": loop.sha256_text("artifact-plan-1"),
        }
        admitted_plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [running],
            coordinator,
            loop.default_scheduler_state(),
            authority_signals=signals,
            frontier_state=certified,
        )
        self.assertNotEqual(
            admitted_plan["next_action"]["kind"],
            "reconcile_repository_frontier",
        )
        self.assertIn(
            "frontier_certificate", admitted_plan["next_action"]["payload"]
        )

    def test_frontier_audit_is_read_only_and_reports_legacy_unverified(self) -> None:
        registry, _, _, _ = fixture()
        state = loop.default_frontier_state([REPO_A, REPO_B])
        before = copy.deepcopy(state)
        scheduler = scheduler_with_waiting_route()
        scheduler_before = copy.deepcopy(scheduler)
        report = loop.audit_frontier_state(
            registry, [], state, [], scheduler_state=scheduler
        )
        self.assertEqual(report["classification"], "FRONTIER_RECONCILIATION_REQUIRED")
        self.assertFalse(report["mutated"])
        self.assertEqual(state, before)
        self.assertEqual(scheduler, scheduler_before)
        self.assertIn(
            "UNAPPLIED_EXTERNAL_RESULT",
            {item["classification"] for item in report["findings"]},
        )

    def test_write_ahead_transaction_recovers_after_interrupted_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                "scheduler": root / "scheduler.json",
                "frontier": root / "frontier.json",
                "missions": root / "missions",
                "portfolio": root / "portfolio.json",
                "journal": root / "journal",
            }
            paths["missions"].mkdir()
            loop.atomic_write_json(paths["scheduler"], scheduler_with_waiting_route())
            loop.atomic_write_json(
                paths["frontier"], loop.default_frontier_state([REPO_A])
            )
            loop.atomic_write_json(paths["missions"] / "mission.json", mission_record())
            loop.atomic_write_json(paths["portfolio"], portfolio_v2())
            event = frontier_event(
                epoch=1,
                based_on=0,
                artifact_id="recovered-artifact",
                actor="supervisor",
                token="recovery",
            )
            payload = external_result(event, result_id="recovered-result")
            with self.assertRaisesRegex(OSError, "injected"):
                loop.apply_external_result_transaction_files(
                    scheduler_path=paths["scheduler"],
                    frontier_path=paths["frontier"],
                    missions_dir=paths["missions"],
                    portfolio_path=paths["portfolio"],
                    journal_dir=paths["journal"],
                    action_id="action-frontier",
                    result=payload,
                    observed_authority_signal=payload["authority_signal"],
                    actor_task_id=OWNER,
                    failure_after_write=2,
                )
            replay = loop.apply_external_result_transaction_files(
                scheduler_path=paths["scheduler"],
                frontier_path=paths["frontier"],
                missions_dir=paths["missions"],
                portfolio_path=paths["portfolio"],
                journal_dir=paths["journal"],
                action_id="action-frontier",
                result=payload,
                observed_authority_signal=payload["authority_signal"],
                actor_task_id=OWNER,
            )
            self.assertEqual(
                replay["classification"],
                "EXTERNAL_RESULT_TRANSACTION_REPLAYED",
            )
            persisted_scheduler = loop.load_scheduler_state(paths["scheduler"])
            self.assertEqual(persisted_scheduler["route_leases"], [])
            self.assertEqual(
                persisted_scheduler["completed_actions"][0][
                    "external_lifecycle_state"
                ],
                "result_applied",
            )


if __name__ == "__main__":
    unittest.main()
