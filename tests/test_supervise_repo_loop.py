from __future__ import annotations

import copy
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import supervise_repo_loop as loop  # noqa: E402


THANK_ID = "host-11111111-1111-4111-8111-111111111111"
PLANNER_ID = "host-22222222-2222-4222-8222-222222222222"
REPO_ID = "github.com/example/nlmytgen"
PROJECT_ID = "g-p-development"


def blocked_recovery_contract() -> dict:
    return {
        "contract_version": 2,
        "blocker_id": "runtime-helper",
        "introduced_by": {
            "event": "supervisor-verdict",
            "at": "2026-08-01T00:00:00Z",
            "evidence": "X:\\fixtures\\supervisor-verdict.json",
        },
        "requirement": "The neutral runtime probe must exit 0.",
        "rationale": "The required runtime cannot start before this succeeds.",
        "qualifies_when": ["The registered neutral probe exits 0."],
        "does_not_qualify": ["A text-only retry request."],
        "diagnostics_completed": ["Read-only path access passed."],
        "owner": "runtime owner",
        "next_permitted_probe": "Run one registered probe after a changed signal.",
        "retry_policy": "on_changed_satisfied_signal",
        "input_route": {
            "destination": "Coordinator direction update",
            "format": "probe result with evidence path",
        },
        "baseline_observation_fingerprint": "c" * 64,
    }


def repository_record(
    *,
    repository_id: str = REPO_ID,
    aliases: list[str] | None = None,
    default_lane: str = "video",
) -> dict:
    return {
        "schema_version": 2,
        "repository_id": repository_id,
        "aliases": aliases or ["NLMYTGen"],
        "default_supervision_lane": default_lane,
        "remote_identity": repository_id,
    }


def supervisor_binding(
    *,
    repository_id: str = REPO_ID,
    lane: str = "video",
    supervisor_id: str = "sup-video",
    supervisor_title: str = "NLMYTGen 動画監修",
) -> dict:
    return {
        "schema_version": 2,
        "repository_id": repository_id,
        "supervision_lane": lane,
        "supervisor_project_id": PROJECT_ID,
        "supervisor_thread_id": supervisor_id,
        "expected_supervisor_title": supervisor_title,
        "last_verified_at": "2026-07-27T00:00:00Z",
        "binding_status": "active",
        "allow_create_supervisor_chat": False,
    }


def worker_binding(
    *,
    repository_id: str = REPO_ID,
    worker_id: str = "worker-thank",
    host_id: str = THANK_ID,
    root: str = "C:\\Thank\\NLMYTGen",
) -> dict:
    return {
        "schema_version": 2,
        "repository_id": repository_id,
        "worker_task_id": worker_id,
        "host_id": host_id,
        "root_hint": root,
        "last_verified_at": "2026-07-27T00:00:00Z",
        "binding_status": "active",
        "allow_create_worker_task": False,
    }


def host(
    host_id: str,
    aliases: list[str],
    repository_id: str,
    root: str,
    worker_id: str,
) -> dict:
    return {
        "host_id": host_id,
        "aliases": aliases,
        "app_host_ids": [aliases[0].lower()],
        "workspace_roots": [root],
        "known_repository_roots": {repository_id: root},
        "root_verifications": {
            repository_id: {"root": root, "repository_id": repository_id}
        },
        "available_worker_tasks": {repository_id: worker_id},
        "capabilities": {},
        "last_seen_at": "2026-07-27T00:00:00Z",
    }


def supervisor_thread(
    thread_id: str = "sup-video",
    title: str = "NLMYTGen 動画監修",
) -> dict:
    return {
        "id": thread_id,
        "kind": "chatgpt",
        "project_id": PROJECT_ID,
        "title": title,
        "read_verified": True,
    }


def worker_thread(
    thread_id: str = "worker-thank",
    host_id: str = "thank",
    repository_id: str = REPO_ID,
) -> dict:
    return {
        "id": thread_id,
        "kind": "codex",
        "host_id": host_id,
        "cwd": "fixture-root",
        "repository_id": repository_id,
        "read_verified": True,
    }


def fixture() -> tuple[dict, dict, dict]:
    registry = {
        "schema_version": 2,
        "repositories": [repository_record()],
        "supervisor_bindings": [supervisor_binding()],
        "worker_bindings": [worker_binding()],
    }
    hosts = {
        "schema_version": 2,
        "hosts": [
            host(
                THANK_ID,
                ["thank", "THANK", "local"],
                REPO_ID,
                "C:\\Thank\\NLMYTGen",
                "worker-thank",
            ),
            host(
                PLANNER_ID,
                ["planner007", "PLANNER007"],
                REPO_ID,
                "D:\\Repos\\NLMYTGen",
                "worker-planner",
            ),
        ],
        "private_artifacts": [
            {
                "artifact_id": "review-zip",
                "repository_id": REPO_ID,
                "host_id": THANK_ID,
                "path": "C:\\Thank\\private\\review.zip",
                "sha256": "9" * 64,
                "status": "verified",
                "last_verified_at": "2026-07-27T00:00:00Z",
            }
        ],
    }
    adapter = {
        "schema_version": 1,
        "current_host_alias": "thank",
        "capabilities": {
            "create_codex_thread": True,
            "create_regular_chatgpt_project_chat": False,
        },
        "projects": [],
        "threads": [supervisor_thread(), worker_thread()],
    }
    return registry, hosts, adapter


def resolved(**kwargs) -> dict:
    registry, hosts, adapter = fixture()
    return loop.resolve_launch(
        "NLMYTGen",
        mode=kwargs.pop("mode", "coordinator"),
        registry=kwargs.pop("registry", registry),
        hosts=kwargs.pop("hosts", hosts),
        adapter=kwargs.pop("adapter", adapter),
        **kwargs,
    )


def worker_report_packet() -> dict:
    return {
        "repository_id": REPO_ID,
        "mission_id": "mission-1",
        "attempt_id": 1,
        "worker_task_id": "worker-thank",
        "host_id": THANK_ID,
        "result_classification": "complete",
        "active_artifact": {"id": "artifact-1", "sha256": "1" * 64},
        "verification_summary": {"tests": "pass"},
        "deviations": [],
        "bounded_blocker": None,
        "suggested_decision_type": None,
        "git_state": {"tracked_diff": 0, "index_diff": 0},
        "external_effect_state": loop.default_external_effects(),
        "full_worker_report": "full report",
    }


def mission_to_result() -> tuple[dict, str]:
    mission = loop.new_mission(
        {
            "repository_id": REPO_ID,
            "launch_set_id": "launch-1",
            "mission_id": "mission-1",
            "attempt_id": 1,
            "worker_task_id": "worker-thank",
            "host_id": THANK_ID,
            "supervisor_thread_id": "sup-video",
            "supervision_lane": "video",
        }
    )
    for event in (
        "repository_resolved",
        "host_resolved",
        "bindings_validated",
        "supervisor_work_order_requested",
        "work_order_received",
    ):
        loop.advance_linear(mission, event)
    loop.dispatch_worker(mission, "a" * 64)
    mission, report_hash = loop.receive_worker_result(
        mission, worker_report_packet()
    )
    return mission, report_hash


class SuperviseRepoLoopV2Tests(unittest.TestCase):
    def test_T01_coordinator_never_selects_in_place(self) -> None:
        result = resolved()
        self.assertEqual(result["classification"], "RESOLVED")
        self.assertFalse(result["in_place"])
        self.assertEqual(result["mode"], "coordinator")

    def test_T02_persistent_worker_is_reused(self) -> None:
        result = resolved()
        self.assertEqual(result["worker_task_id"], "worker-thank")
        self.assertFalse(result["create_worker_task"])

    def test_T03_exact_supervisor_binding_is_reused(self) -> None:
        result = resolved()
        self.assertEqual(result["supervisor_thread_id"], "sup-video")
        self.assertEqual(result["expected_supervisor_title"], "NLMYTGen 動画監修")
        self.assertFalse(result["create_supervisor_chat"])

    def test_T04_invalid_development_lane_never_falls_back_to_video(self) -> None:
        registry, hosts, adapter = fixture()
        registry["supervisor_bindings"].append(
            supervisor_binding(
                lane="development",
                supervisor_id="sup-development",
                supervisor_title="NLMYTGen開発監修",
            )
        )
        adapter["threads"].append(
            supervisor_thread("similar-video", "NLMYTGen 動画監修")
        )
        result = loop.resolve_launch(
            "NLMYTGen",
            mode="coordinator",
            lane="development",
            registry=registry,
            hosts=hosts,
            adapter=adapter,
        )
        self.assertEqual(result["classification"], "BINDING_REPAIR_SUPERVISOR")
        self.assertNotEqual(result.get("supervisor_thread_id"), "similar-video")
        self.assertIn("exact_thread_missing", result["supervisor_issues"])

    def test_T05_same_remote_maps_to_current_host_root(self) -> None:
        registry, hosts, adapter = fixture()
        registry["worker_bindings"].append(
            worker_binding(
                worker_id="worker-planner",
                host_id=PLANNER_ID,
                root="D:\\Repos\\NLMYTGen",
            )
        )
        adapter["current_host_alias"] = "planner007"
        adapter["threads"] = [
            supervisor_thread(),
            worker_thread("worker-planner", "planner007"),
        ]
        result = loop.resolve_launch(
            "NLMYTGen",
            mode="coordinator",
            registry=registry,
            hosts=hosts,
            adapter=adapter,
        )
        self.assertEqual(result["root"], "D:\\Repos\\NLMYTGen")
        self.assertEqual(result["repository_id"], REPO_ID)
        self.assertEqual(result["supervisor_thread_id"], "sup-video")
        self.assertEqual(len(registry["supervisor_bindings"]), 1)

    def test_T06_private_artifact_selects_thank_and_transport_stays_pending(self) -> None:
        registry, hosts, adapter = fixture()
        registry["worker_bindings"].append(
            worker_binding(
                worker_id="worker-planner",
                host_id=PLANNER_ID,
                root="D:\\Repos\\NLMYTGen",
            )
        )
        adapter["current_host_alias"] = "planner007"
        adapter["threads"] = [
            supervisor_thread(),
            worker_thread("worker-thank", "thank"),
            worker_thread("worker-planner", "planner007"),
        ]
        result = loop.resolve_launch(
            "NLMYTGen",
            mode="coordinator",
            registry=registry,
            hosts=hosts,
            adapter=adapter,
            private_artifact_id="review-zip",
            external_target_host_id=PLANNER_ID,
        )
        self.assertEqual(result["execution_host_id"], THANK_ID)
        self.assertEqual(result["external_effects"]["transport"], "pending")
        self.assertEqual(result["external_effects"]["recipient_open"], "unverified")

    def test_T07_pending_transport_does_not_block_authorized_work(self) -> None:
        result = resolved(external_target_host_id=PLANNER_ID)
        self.assertEqual(result["classification"], "RESOLVED")
        self.assertIsNone(result["terminal_route"])
        self.assertTrue(result["bindings_validated"])

    def test_T08_no_next_work_order_or_user_route_before_adjudication(self) -> None:
        mission, _ = mission_to_result()
        with self.assertRaises(loop.ProtocolError):
            loop.advance_linear(mission, "work_order_received")
        with self.assertRaises(loop.ProtocolError):
            loop.apply_supervisor_verdict(
                mission, "user_action", user_packet={"action": "do something"}
            )

    def test_T09_reject_must_supersede_before_successor(self) -> None:
        mission, report_hash = mission_to_result()
        loop.request_adjudication(mission, report_hash)
        loop.apply_supervisor_verdict(
            mission,
            "reject",
            next_work_order={"mission_id": "successor", "reason": "new direction"},
        )
        self.assertEqual(mission["review_status"], "rejected")
        self.assertEqual(mission["mission_status"], "superseded")
        successor = loop.start_successor(
            mission,
            {
                "repository_id": REPO_ID,
                "launch_set_id": "launch-1",
                "mission_id": "successor",
                "attempt_id": 1,
            },
        )
        self.assertEqual(successor["state"], "TRIGGERED")

    def test_T10_duplicate_dispatch_is_rejected(self) -> None:
        mission = loop.new_mission(
            {
                "repository_id": REPO_ID,
                "launch_set_id": "launch-1",
                "mission_id": "mission-1",
                "attempt_id": 1,
                "worker_task_id": "worker-thank",
                "host_id": THANK_ID,
                "supervisor_thread_id": "sup-video",
                "supervision_lane": "video",
            }
        )
        for event in (
            "repository_resolved",
            "host_resolved",
            "bindings_validated",
            "supervisor_work_order_requested",
            "work_order_received",
        ):
            loop.advance_linear(mission, event)
        loop.dispatch_worker(mission, "a" * 64)
        with self.assertRaisesRegex(loop.ProtocolError, "duplicate dispatch"):
            loop.dispatch_worker(mission, "a" * 64)

    def test_T11_duplicate_worker_report_return_is_rejected(self) -> None:
        mission, report_hash = mission_to_result()
        loop.request_adjudication(mission, report_hash)
        with self.assertRaisesRegex(loop.ProtocolError, "duplicate Worker Report"):
            loop.request_adjudication(mission, report_hash)

    def test_T12_terminal_notification_is_deduplicated(self) -> None:
        mission, report_hash = mission_to_result()
        loop.request_adjudication(mission, report_hash)
        loop.apply_supervisor_verdict(mission, "complete")
        calls = {"count": 0}

        def notifier(_path: Path, _packet: dict) -> bool:
            calls["count"] += 1
            return True

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = loop.emit_terminal_packet(
                mission,
                terminal_dir=root / "packets",
                ledger_path=root / "ledger.json",
                notifier=notifier,
            )
            second = loop.emit_terminal_packet(
                mission,
                terminal_dir=root / "packets",
                ledger_path=root / "ledger.json",
                notifier=notifier,
            )
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(calls["count"], 1)

    def test_T13_notification_failure_preserves_terminal_packet(self) -> None:
        mission, report_hash = mission_to_result()
        loop.request_adjudication(mission, report_hash)
        loop.apply_supervisor_verdict(
            mission, "blocked", user_packet=blocked_recovery_contract()
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = loop.emit_terminal_packet(
                mission,
                terminal_dir=root / "packets",
                ledger_path=root / "ledger.json",
                notifier=lambda _path, _packet: False,
            )
            self.assertTrue(Path(result["packet_path"]).is_file())
        self.assertEqual(result["notification_status"], "failed")
        self.assertEqual(mission["state"], "BLOCKED")

    def test_T14_alias_launch_needs_no_absolute_path(self) -> None:
        result = resolved()
        self.assertEqual(result["repository_alias"], "NLMYTGen")
        self.assertEqual(result["root"], "C:\\Thank\\NLMYTGen")

    def test_T15_alias_collision_stops_with_minimal_candidates(self) -> None:
        registry, hosts, adapter = fixture()
        other = repository_record(
            repository_id="github.com/example/other", aliases=["NLMYTGen"]
        )
        registry["repositories"].append(other)
        result = loop.resolve_launch(
            "NLMYTGen",
            mode="coordinator",
            registry=registry,
            hosts=hosts,
            adapter=adapter,
        )
        self.assertEqual(result["classification"], "USER_DECISION_ALIAS_COLLISION")
        self.assertEqual(len(result["candidate_repository_ids"]), 2)
        self.assertNotIn("root", result)

    def test_T16_compiler_references_common_policy_and_keeps_delta(self) -> None:
        mission = {
            "repository_id": REPO_ID,
            "launch_set_id": "launch-1",
            "mission_id": "mission-1",
            "attempt_id": 1,
            "authority_revision": "authority@abc",
            "active_artifact": {"id": "artifact-1", "sha256": "1" * 64},
            "canonical_revision": "a" * 40,
            "read_scope": ["docs/state.md"],
            "write_scope": ["src/feature.py"],
            "private_inputs": [{"host_id": THANK_ID, "sha256": "2" * 64}],
            "preserve_delta": ["user-owned.bin"],
            "prohibited_delta": ["release"],
            "acceptance_delta": ["focused test passes"],
            "stop_delta": ["identity drift"],
            "authority_documents": [
                {"path": "docs/state.md", "revision": "a" * 40}
            ],
            "external_effects": loop.default_external_effects(),
        }
        packet = loop.compile_work_order(mission)
        self.assertEqual(
            packet["protocol_ref"]["path"], "references/protocol-v2.md"
        )
        self.assertEqual(packet["mission_delta"]["private_inputs"], mission["private_inputs"])
        serialized = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn("git_safety_policy", serialized)

    def test_T17_missing_supervisor_creation_capability_reports_user_action(
        self,
    ) -> None:
        registry, hosts, adapter = fixture()
        registry["supervisor_bindings"][0]["supervisor_thread_id"] = None
        registry["supervisor_bindings"][0][
            "binding_status"
        ] = "needs_verification"
        adapter["threads"] = [worker_thread()]
        loop.validate_registry(registry, hosts)
        result = loop.resolve_launch(
            "NLMYTGen",
            mode="coordinator",
            registry=registry,
            hosts=hosts,
            adapter=adapter,
        )
        self.assertEqual(
            result["classification"],
            "USER_ACTION_CREATE_OR_BIND_SUPERVISOR_CHAT",
        )
        self.assertFalse(result["creation_capability"])
        self.assertEqual(result["terminal_route"], "USER_ACTION")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "registry.json"
            hosts_path = root / "hosts.json"
            adapter_path = root / "adapter.json"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            hosts_path.write_text(json.dumps(hosts), encoding="utf-8")
            adapter_path.write_text(json.dumps(adapter), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = loop.main(
                    [
                        "resolve",
                        "--alias",
                        "NLMYTGen",
                        "--registry",
                        str(registry_path),
                        "--hosts",
                        str(hosts_path),
                        "--adapter",
                        str(adapter_path),
                        "--dry-run",
                    ]
                )
        self.assertEqual(exit_code, 2)

    def test_T18_single_thread_requires_explicit_mode(self) -> None:
        coordinator = resolved()
        single = resolved(mode="single-thread")
        self.assertFalse(coordinator["in_place"])
        self.assertTrue(single["in_place"])
        self.assertEqual(single["mode"], "single-thread")
        self.assertIsNone(single["supervisor_thread_id"])

    def test_legacy_migration_preserves_payload_and_needs_verification(self) -> None:
        legacy = {
            "schema_version": 1,
            "bindings": [
                {
                    "project_name": "NLMYTGen",
                    "project_root": "C:\\Old\\NLMYTGen",
                    "remote": "git@github.com:Example/NLMYTGen.git",
                    "supervisor_thread_id": "old-supervisor",
                    "worker_thread_id": "old-worker",
                    "custom_old_field": "preserve-me",
                }
            ],
        }
        migrated, report = loop.migrate_legacy_registry(legacy)
        item = migrated["repositories"][0]
        supervisor = migrated["supervisor_bindings"][0]
        worker = migrated["worker_bindings"][0]
        self.assertEqual(item["repository_id"], "github.com/example/nlmytgen")
        self.assertEqual(supervisor["binding_status"], "needs_verification")
        self.assertEqual(worker["binding_status"], "needs_verification")
        self.assertEqual(
            item["migration"]["legacy_record"]["custom_old_field"], "preserve-me"
        )
        self.assertTrue(report["legacy_payloads_preserved"])

    def test_remote_normalization_equates_https_and_ssh(self) -> None:
        https = loop.normalize_remote("https://github.com/Example/NLMYTGen.git")
        ssh = loop.normalize_remote("git@github.com:Example/NLMYTGen.git")
        self.assertEqual(https, ssh)

    def test_complete_mission_with_pending_transport_is_valid(self) -> None:
        mission = loop.new_mission(
            {
                "repository_id": REPO_ID,
                "launch_set_id": "launch-1",
                "mission_id": "mission-1",
                "attempt_id": 1,
                "worker_task_id": "worker-thank",
                "host_id": THANK_ID,
                "supervisor_thread_id": "sup-video",
                "supervision_lane": "video",
                "external_effects": {
                    "transport": "pending",
                    "recipient_open": "unverified",
                    "upload": "not_required",
                    "publication": "not_required",
                    "release": "not_required",
                },
            }
        )
        mission["mission_status"] = "complete"
        loop.validate_mission(mission)

    def test_worker_report_identity_mismatch_is_rejected(self) -> None:
        mission = loop.new_mission(
            {
                "repository_id": REPO_ID,
                "launch_set_id": "launch-1",
                "mission_id": "mission-1",
                "attempt_id": 1,
                "worker_task_id": "worker-thank",
                "host_id": THANK_ID,
                "supervisor_thread_id": "sup-video",
                "supervision_lane": "video",
            }
        )
        for event in (
            "repository_resolved",
            "host_resolved",
            "bindings_validated",
            "supervisor_work_order_requested",
            "work_order_received",
        ):
            loop.advance_linear(mission, event)
        loop.dispatch_worker(mission, "a" * 64)
        for key, wrong in (
            ("mission_id", "wrong-mission"),
            ("worker_task_id", "wrong-worker"),
            ("host_id", PLANNER_ID),
        ):
            with self.subTest(key=key):
                packet = worker_report_packet()
                packet[key] = wrong
                with self.assertRaisesRegex(
                    loop.ProtocolError, "identity mismatch"
                ):
                    loop.receive_worker_result(mission, packet)

    def test_inactive_binding_fails_closed_even_when_thread_reads(self) -> None:
        registry, hosts, adapter = fixture()
        registry["supervisor_bindings"][0]["binding_status"] = "inactive"
        registry["supervisor_bindings"][0]["supervisor_thread_id"] = None
        registry["supervisor_bindings"][0][
            "allow_create_supervisor_chat"
        ] = True
        result = loop.resolve_launch(
            "NLMYTGen",
            mode="coordinator",
            registry=registry,
            hosts=hosts,
            adapter=adapter,
        )
        self.assertEqual(
            result["classification"], "BINDING_REPAIR_SUPERVISOR_STATUS"
        )
        self.assertFalse(result["bindings_validated"])

        registry, hosts, adapter = fixture()
        registry["worker_bindings"][0]["binding_status"] = "invalid"
        registry["worker_bindings"][0]["worker_task_id"] = None
        registry["worker_bindings"][0]["allow_create_worker_task"] = True
        result = loop.resolve_launch(
            "NLMYTGen",
            mode="coordinator",
            registry=registry,
            hosts=hosts,
            adapter=adapter,
        )
        self.assertEqual(
            result["classification"], "BINDING_REPAIR_WORKER_STATUS"
        )
        self.assertFalse(result["bindings_validated"])

    def test_continuation_uses_new_attempt_identity_and_dispatches(self) -> None:
        first, report_hash = mission_to_result()
        loop.request_adjudication(first, report_hash)
        loop.apply_supervisor_verdict(
            first,
            "continue",
            next_work_order={"mission_id": "mission-2", "attempt_id": 1},
        )
        second = loop.start_continuation(
            first, {"mission_id": "mission-2", "attempt_id": 1}
        )
        self.assertEqual(second["completed_worker_turns"], 1)
        for event in (
            "repository_resolved",
            "host_resolved",
            "bindings_validated",
            "supervisor_work_order_requested",
            "work_order_received",
        ):
            loop.advance_linear(second, event)
        loop.dispatch_worker(second, "b" * 64)
        self.assertEqual(second["state"], "WORKER_DISPATCHED")

    def test_safety_ceiling_becomes_terminal_after_adjudication(self) -> None:
        mission = loop.new_mission(
            {
                "repository_id": REPO_ID,
                "launch_set_id": "launch-1",
                "mission_id": "mission-1",
                "attempt_id": 1,
                "worker_task_id": "worker-thank",
                "host_id": THANK_ID,
                "supervisor_thread_id": "sup-video",
                "supervision_lane": "video",
                "safety_ceiling": 1,
            }
        )
        for event in (
            "repository_resolved",
            "host_resolved",
            "bindings_validated",
            "supervisor_work_order_requested",
            "work_order_received",
        ):
            loop.advance_linear(mission, event)
        loop.dispatch_worker(mission, "a" * 64)
        mission, report_hash = loop.receive_worker_result(
            mission, worker_report_packet()
        )
        loop.request_adjudication(mission, report_hash)
        loop.apply_supervisor_verdict(
            mission,
            "continue",
            next_work_order={"mission_id": "mission-2", "attempt_id": 1},
        )
        self.assertEqual(mission["state"], "SAFETY_CEILING")

    def test_missing_worker_cli_path_is_reachable(self) -> None:
        registry, hosts, adapter = fixture()
        registry["worker_bindings"][0]["worker_task_id"] = None
        registry["worker_bindings"][0]["binding_status"] = "needs_verification"
        adapter["threads"] = [supervisor_thread()]
        loop.validate_registry(registry, hosts)
        result = loop.resolve_launch(
            "NLMYTGen",
            mode="coordinator",
            registry=registry,
            hosts=hosts,
            adapter=adapter,
        )
        self.assertEqual(
            result["classification"],
            "USER_ACTION_CREATE_OR_BIND_WORKER_TASK",
        )
        self.assertEqual(result["terminal_route"], "USER_ACTION")

    def test_json_stdout_falls_back_to_ascii_on_cp932(self) -> None:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp932", newline="\n")
        with contextlib.redirect_stdout(stream):
            loop._json_stdout({"path": "Residual Atlas — First Playable"})
            stream.flush()
        rendered = raw.getvalue().decode("cp932")
        self.assertEqual(
            json.loads(rendered),
            {"path": "Residual Atlas — First Playable"},
        )
        self.assertIn("\\u2014", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
