from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import supervise_repo_loop as loop  # noqa: E402
from test_coordinator_only_ux import (  # noqa: E402
    REPO_A,
    REPO_B,
    blocked_contract,
    fixture,
    mission,
)


def supervisor_revision_contract(
    blocked: dict,
    predecessor: dict,
    directory: Path,
    *,
    blocker_id: str,
    introduced_at: str,
    token: str,
) -> tuple[dict, Path]:
    event_id = loop.sha256_text(token)
    evidence_path = directory / f"{token}.json"
    evidence = {
        "event_kind": "SUPERVISOR_PROJECT_QUESTION_VERDICT",
        "event_id": event_id,
        "repository_id": blocked["repository_id"],
        "mission_id": blocked["mission_id"],
        "attempt_id": blocked["attempt_id"],
        "supervisor_thread_id": blocked["supervisor_thread_id"],
    }
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    revision = blocked_contract(blocker_id)
    revision["introduced_by"] = {
        "event": "SUPERVISOR_PROJECT_QUESTION_VERDICT",
        "at": introduced_at,
        "evidence": str(evidence_path),
    }
    revision["baseline_observation_fingerprint"] = event_id
    revision["supersedes_contract_fingerprint"] = loop.canonical_json_hash(
        predecessor
    )
    revision["revision_authority"] = {
        **evidence,
        "evidence_sha256": loop.sha256_file(evidence_path),
    }
    return revision, evidence_path


class ProgressAndStopContractTests(unittest.TestCase):
    def test_T88_blocked_verdict_requires_explainable_contract(self) -> None:
        blocked = mission(
            REPO_A,
            "SUPERVISOR_ADJUDICATION_REQUESTED",
            mission_id="opaque-block",
        )
        with self.assertRaisesRegex(loop.ProtocolError, "non-empty recovery contract"):
            loop.apply_supervisor_verdict(blocked, "blocked", user_packet=None)

    def test_T89_legacy_blocked_frontier_gets_contract_repair_not_probe(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        for repository in registry["repositories"]:
            repository["allow_request_next_mission"] = False
        legacy = mission(REPO_A, "BLOCKED", mission_id="legacy-block")
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [legacy],
            coordinator,
            loop.default_scheduler_state(),
        )
        self.assertEqual(plan["next_action"]["kind"], "repair_blocker_contract")
        self.assertFalse(plan["next_action"]["requires_external_result"])
        self.assertEqual(plan["next_action"]["payload"]["contract_version"], 2)

    def test_T90_worker_result_requires_same_pass_protocol_handoff(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        for repository in registry["repositories"]:
            repository["allow_request_next_mission"] = False
        result = mission(REPO_A, "WORKER_RESULT_RECEIVED", mission_id="handoff")
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [result],
            coordinator,
            loop.default_scheduler_state(),
        )
        self.assertEqual(plan["next_action"]["kind"], "return_worker_result")
        self.assertTrue(plan["protocol_handoff_required"])
        self.assertIn("required_protocol_handoff", plan["checkpoint_blockers"])
        self.assertFalse(plan["cycle_checkpoint_allowed"])

    def test_T91_active_handoff_route_suppresses_duplicate_same_mission(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        for repository in registry["repositories"]:
            repository["allow_request_next_mission"] = False
        result = mission(REPO_A, "WORKER_RESULT_RECEIVED", mission_id="handoff")
        scheduler = loop.default_scheduler_state()
        initial = loop.build_coordinator_plan(
            registry, hosts, adapter, [result], coordinator, scheduler
        )
        action = initial["next_action"]
        action_id = action["action_id"]
        recipient = action["payload"]["route"]["recipient_thread_id"]
        loop.claim_coordinator_action(scheduler, initial, action_id)
        loop.prepare_coordinator_action_delivery(
            scheduler, action_id, recipient, "d" * 64
        )
        loop.mark_coordinator_action_sent(
            scheduler,
            action_id,
            recipient,
            packet_sha256="d" * 64,
            after_cursor="supervisor-cursor-1",
        )
        result["state"] = "SUPERVISOR_ADJUDICATION_REQUESTED"
        waiting = loop.build_coordinator_plan(
            registry, hosts, adapter, [result], coordinator, scheduler
        )
        same_mission_ready = [
            item
            for item in waiting["ready_actions"]
            if loop._action_mission_identity(item) == (REPO_A, "handoff", "attempt-1")
        ]
        self.assertEqual(same_mission_ready, [])
        self.assertTrue(waiting["checkpoint_after_wait_allowed"])

    def test_T92_missing_route_cursor_forbids_checkpoint(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        for repository in registry["repositories"]:
            repository["allow_request_next_mission"] = False
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="cursor")
        scheduler = loop.default_scheduler_state()
        initial = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        action = initial["next_action"]
        recipient = action["payload"]["route"]["recipient_thread_id"]
        loop.claim_coordinator_action(scheduler, initial, action["action_id"])
        loop.prepare_coordinator_action_delivery(
            scheduler, action["action_id"], recipient, "e" * 64
        )
        loop.mark_coordinator_action_sent(
            scheduler,
            action["action_id"],
            recipient,
            packet_sha256="e" * 64,
        )
        waiting = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        self.assertFalse(waiting["route_cursor_complete"])
        self.assertFalse(waiting["checkpoint_after_wait_allowed"])
        self.assertIn("missing_route_cursor", waiting["checkpoint_blockers"])

    def test_T93_portfolio_renderer_is_deterministic_and_explanatory(self) -> None:
        stop = blocked_contract("source-gap")
        portfolio = {
            "schema_version": 2,
            "semantic_fingerprint": "f" * 64,
            "coordinator_availability": "AVAILABLE",
            "execution_state": "DRAINING",
            "active_route_count": 1,
            "concurrency_limit": 3,
            "repositories": [
                {
                    "repository_id": REPO_A,
                    "project_name": "Project A",
                    "state": "RUNNING",
                    "progress": {
                        "current_stage": "SUPERVISOR",
                        "completed_stages": [
                            "MISSION",
                            "WORK_ORDER",
                            "WORKER",
                            "WORKER_REPORT",
                        ],
                    },
                    "why": "Worker Report was sent for adjudication.",
                    "owner": "exact Supervisor",
                    "next_move": "Return one verdict.",
                },
                {
                    "repository_id": REPO_B,
                    "project_name": "Project B",
                    "state": "SYSTEM_BLOCKED",
                    "progress": {
                        "current_stage": "NEXT_ROUTE",
                        "completed_stages": list(loop.PORTFOLIO_STAGE_ORDER[:-1]),
                    },
                    "why": "The source bundle is incomplete.",
                    "owner": "source owner",
                    "next_move": "Supply the named bundle through the Coordinator.",
                    "stop": copy.deepcopy(stop),
                },
            ],
        }
        first = loop.render_portfolio_markdown(portfolio)
        second = loop.render_portfolio_markdown(copy.deepcopy(portfolio))
        self.assertEqual(first, second)
        self.assertIn("```mermaid", first)
        self.assertIn("Project A", first)
        self.assertIn("Qualifies when", first)
        self.assertIn("Does not qualify", first)
        self.assertIn("Diagnostics already completed", first)

    def test_T94_newer_supervisor_contract_revision_is_historical_and_current(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        for repository in registry["repositories"]:
            repository["allow_request_next_mission"] = False
        blocked = mission(REPO_A, "BLOCKED", mission_id="revised-block")
        original = blocked_contract("generic-helper-failure")
        blocked["blocked_contract"] = copy.deepcopy(original)
        scheduler = loop.default_scheduler_state()
        original_plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [blocked], coordinator, scheduler
        )
        original_action_id = original_plan["next_action"]["action_id"]
        loop.claim_coordinator_action(scheduler, original_plan, original_action_id)
        loop.complete_coordinator_action(
            scheduler, original_action_id, "unchanged", evidence="old observation"
        )

        with tempfile.TemporaryDirectory() as temporary:
            revision, _ = supervisor_revision_contract(
                blocked,
                original,
                Path(temporary),
                blocker_id="corrupt-deny-read-state",
                introduced_at="2026-08-01T01:00:00Z",
                token="revision-one",
            )
            loop.record_blocked_contract(blocked, revision)
            loop.record_blocked_contract(blocked, revision)

            self.assertEqual(blocked["blocked_contract"], revision)
            self.assertEqual(len(blocked["blocked_contract_history"]), 1)
            self.assertEqual(
                blocked["blocked_contract_history"][0]["contract"], original
            )
            self.assertEqual(
                blocked["events"][-1]["state"], "BLOCKED_CONTRACT_REVISED"
            )
            plan = loop.build_coordinator_plan(
                registry, hosts, adapter, [blocked], coordinator, scheduler
            )
            self.assertEqual(plan["next_action"]["kind"], "inspect_blocked_recovery")
            self.assertNotEqual(plan["next_action"]["action_id"], original_action_id)
            self.assertEqual(
                plan["next_action"]["payload"]["blocked_packet"]["blocker_id"],
                "corrupt-deny-read-state",
            )

    def test_T95_blocker_revision_rejects_stale_or_wrong_predecessor(self) -> None:
        original = blocked_contract("current")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for index, (failure, timestamp, predecessor) in enumerate(
                (
                    (
                        "current contract fingerprint",
                        "2026-08-01T01:00:00Z",
                        "d" * 64,
                    ),
                    (
                        "newer than the current contract",
                        "2026-07-31T23:00:00Z",
                        loop.canonical_json_hash(original),
                    ),
                )
            ):
                with self.subTest(failure=failure):
                    blocked = mission(REPO_A, "BLOCKED", mission_id="revision-cas")
                    blocked["blocked_contract"] = copy.deepcopy(original)
                    revision, _ = supervisor_revision_contract(
                        blocked,
                        original,
                        directory,
                        blocker_id="candidate",
                        introduced_at=timestamp,
                        token=f"candidate-{index}",
                    )
                    revision["supersedes_contract_fingerprint"] = predecessor
                    with self.assertRaisesRegex(loop.ProtocolError, failure):
                        loop.record_blocked_contract(blocked, revision)

    def test_T96_blocker_revision_is_bound_to_exact_supervisor_evidence(self) -> None:
        blocked = mission(REPO_A, "BLOCKED", mission_id="authority-bound")
        original = blocked_contract("current")
        blocked["blocked_contract"] = copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as temporary:
            revision, _ = supervisor_revision_contract(
                blocked,
                original,
                Path(temporary),
                blocker_id="candidate",
                introduced_at="2026-08-01T01:00:00Z",
                token="authority-bound",
            )
            revision["revision_authority"]["supervisor_thread_id"] = "impostor"
            with self.assertRaisesRegex(loop.ProtocolError, "supervisor_thread_id"):
                loop.record_blocked_contract(blocked, revision)

            revision["revision_authority"]["supervisor_thread_id"] = blocked[
                "supervisor_thread_id"
            ]
            revision["revision_authority"]["event_kind"] = (
                "NOT_A_SUPERVISOR_VERDICT"
            )
            revision["introduced_by"]["event"] = "NOT_A_SUPERVISOR_VERDICT"
            with self.assertRaisesRegex(loop.ProtocolError, "event_kind is not allowed"):
                loop.record_blocked_contract(blocked, revision)

    def test_T97_blocker_history_requires_a_bidirectional_chain(self) -> None:
        original = blocked_contract("current")
        orphan = mission(REPO_A, "BLOCKED", mission_id="orphan")
        orphan["blocked_contract"] = copy.deepcopy(original)
        orphan["blocked_contract"]["supersedes_contract_fingerprint"] = "f" * 64
        with self.assertRaisesRegex(loop.ProtocolError, "initial BLOCKED contract"):
            loop.validate_mission(orphan)

        blocked = mission(REPO_A, "BLOCKED", mission_id="history-chain")
        blocked["blocked_contract"] = copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as temporary:
            revision, _ = supervisor_revision_contract(
                blocked,
                original,
                Path(temporary),
                blocker_id="candidate",
                introduced_at="2026-08-01T01:00:00Z",
                token="history-chain",
            )
            loop.record_blocked_contract(blocked, revision)
            blocked["blocked_contract_history"][0][
                "superseded_by_contract_fingerprint"
            ] = "f" * 64
            with self.assertRaisesRegex(loop.ProtocolError, "successor link"):
                loop.validate_mission(blocked)

    def test_T98_historical_contract_replay_is_a_file_level_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            mission_path = directory / "mission.json"
            first_path = directory / "revision-one.json"
            second_path = directory / "revision-two.json"
            blocked = mission(REPO_A, "BLOCKED", mission_id="durable-replay")
            original = blocked_contract("original")
            blocked["blocked_contract"] = copy.deepcopy(original)
            first, _ = supervisor_revision_contract(
                blocked,
                original,
                directory,
                blocker_id="revision-one",
                introduced_at="2026-08-01T01:00:00Z",
                token="durable-replay-one",
            )
            loop.atomic_write_json(mission_path, blocked)
            loop.atomic_write_json(first_path, first)
            self.assertEqual(
                loop.persist_blocked_contract(mission_path, first_path)[
                    "classification"
                ],
                "BLOCKED_RECOVERY_CONTRACT_REVISED",
            )

            after_first = loop.load_json(mission_path)
            second, _ = supervisor_revision_contract(
                after_first,
                first,
                directory,
                blocker_id="revision-two",
                introduced_at="2026-08-01T02:00:00Z",
                token="durable-replay-two",
            )
            loop.atomic_write_json(second_path, second)
            loop.persist_blocked_contract(mission_path, second_path)
            before_bytes = mission_path.read_bytes()
            before_mtime = mission_path.stat().st_mtime_ns
            replay = loop.persist_blocked_contract(mission_path, first_path)
            self.assertEqual(
                replay["classification"],
                "BLOCKED_RECOVERY_CONTRACT_ALREADY_APPLIED",
            )
            self.assertEqual(replay["replay_location"], "history")
            self.assertEqual(mission_path.read_bytes(), before_bytes)
            self.assertEqual(mission_path.stat().st_mtime_ns, before_mtime)

    def test_T99_parallel_revisions_allow_only_one_parent_successor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            mission_path = directory / "mission.json"
            blocked = mission(REPO_A, "BLOCKED", mission_id="parallel-cas")
            original = blocked_contract("original")
            blocked["blocked_contract"] = copy.deepcopy(original)
            loop.atomic_write_json(mission_path, blocked)
            payloads: list[Path] = []
            for index in range(2):
                revision, _ = supervisor_revision_contract(
                    blocked,
                    original,
                    directory,
                    blocker_id=f"candidate-{index}",
                    introduced_at=f"2026-08-01T0{index + 1}:00:00Z",
                    token=f"parallel-{index}",
                )
                payload_path = directory / f"payload-{index}.json"
                loop.atomic_write_json(payload_path, revision)
                payloads.append(payload_path)

            def apply(path: Path) -> str:
                try:
                    return loop.persist_blocked_contract(mission_path, path)[
                        "classification"
                    ]
                except loop.ProtocolError as exc:
                    return f"ERROR:{exc}"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(apply, payloads))
            self.assertEqual(
                results.count("BLOCKED_RECOVERY_CONTRACT_REVISED"), 1
            )
            self.assertEqual(
                sum("current contract fingerprint" in result for result in results),
                1,
            )
            final = loop.load_json(mission_path)
            loop.validate_mission(final)
            self.assertEqual(len(final["blocked_contract_history"]), 1)

    def test_T100_legacy_revision_can_only_add_exact_authority_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            mission_path = directory / "mission.json"
            payload_path = directory / "authority-enrichment-payload.json"
            blocked = mission(REPO_A, "BLOCKED", mission_id="authority-enrichment")
            original = blocked_contract("original")
            blocked["blocked_contract"] = copy.deepcopy(original)
            enriched, _ = supervisor_revision_contract(
                blocked,
                original,
                directory,
                blocker_id="revised",
                introduced_at="2026-08-01T01:00:00Z",
                token="authority-enrichment",
            )
            legacy_revision = copy.deepcopy(enriched)
            legacy_revision.pop("revision_authority")
            successor_fingerprint = loop.canonical_json_hash(legacy_revision)
            blocked["blocked_contract_history"] = [
                {
                    "contract": copy.deepcopy(original),
                    "contract_fingerprint": loop.canonical_json_hash(original),
                    "superseded_at": "2026-08-01T01:00:01Z",
                    "superseded_by_contract_fingerprint": successor_fingerprint,
                }
            ]
            blocked["blocked_contract"] = legacy_revision
            loop.atomic_write_json(mission_path, blocked)
            loop.atomic_write_json(payload_path, enriched)

            result = loop.persist_blocked_contract(mission_path, payload_path)
            self.assertEqual(
                result["classification"],
                "BLOCKED_RECOVERY_CONTRACT_AUTHORITY_ENRICHED",
            )
            migrated = loop.load_json(mission_path)
            loop.validate_mission(migrated)
            self.assertEqual(migrated["blocked_contract"], enriched)
            self.assertEqual(
                migrated["blocked_contract_history"][-1][
                    "superseded_by_contract_fingerprint"
                ],
                loop.canonical_json_hash(enriched),
            )

            before = mission_path.read_bytes()
            replay = loop.persist_blocked_contract(mission_path, payload_path)
            self.assertEqual(
                replay["classification"],
                "BLOCKED_RECOVERY_CONTRACT_ALREADY_APPLIED",
            )
            self.assertEqual(mission_path.read_bytes(), before)

    def test_T101_route_waits_are_not_misclassified_as_blockers(self) -> None:
        for state in ("WAITING_EXTERNAL", "WAITING_USER"):
            with self.subTest(state=state):
                portfolio = {
                    "schema_version": 2,
                    "semantic_fingerprint": "a" * 64,
                    "coordinator_availability": "AVAILABLE",
                    "execution_state": state,
                    "active_route_count": 1,
                    "concurrency_limit": 3,
                    "repositories": [
                        {
                            "repository_id": REPO_A,
                            "project_name": "Project A",
                            "state": state,
                            "progress": {
                                "current_stage": "NEXT_ROUTE",
                                "completed_stages": list(
                                    loop.PORTFOLIO_STAGE_ORDER[:-1]
                                ),
                            },
                            "why": "An exact external result is pending.",
                            "owner": "exact route recipient",
                            "next_move": "Consume the exact result once.",
                        }
                    ],
                }
                rendered = loop.render_portfolio_markdown(portfolio)
                self.assertIn(state, rendered)
                expected_class = (
                    "current" if state == "WAITING_EXTERNAL" else "parked"
                )
                self.assertIn(f'["NEXT ROUTE"]:::{expected_class}', rendered)

        blocked = copy.deepcopy(portfolio)
        blocked["repositories"][0]["state"] = "SYSTEM_BLOCKED"
        with self.assertRaisesRegex(loop.ProtocolError, "non-empty recovery contract"):
            loop.render_portfolio_markdown(blocked)


if __name__ == "__main__":
    unittest.main()
