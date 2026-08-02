from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import supervise_repo_loop as loop  # noqa: E402
from test_coordinator_only_ux import (  # noqa: E402
    REPO_A,
    bind_mission_to_frontier,
    certified_frontier_fixture,
    certified_project_context_fixture,
    fixture,
    materialize_git_fixture_roots,
    mission,
)


PRIMARY = "019f-primary-coordinator"
SECONDARY = "019f-repair-or-audit-task"


def quick_win_value_contract() -> dict:
    return {
        "contract_version": 1,
        "authority_source": "docs/CURRENT_HANDOFF.md",
        "authority_revision": "rev-7",
        "authority_fingerprint": "f" * 64,
        "authority_next_action": "Open the existing exact artifact for review.",
        "north_star": "Make the existing review route usable end to end.",
        "current_bottleneck": "The exact review entry cannot be opened in one step.",
        "current_gate": "Exact existing artifact has not been reviewed.",
        "gate_delta": "Move the existing artifact from unreachable to reviewable.",
        "expected_authority_state_after": "Existing artifact awaits one verdict.",
        "expected_user_value": "The user can reach the current artifact immediately.",
        "smallest_deliverable": "One existing-artifact launcher and readback.",
        "next_consumer": "The current artifact review gate.",
        "reuse_or_integration": "The launcher opens the already selected artifact.",
        "existing_artifact_reused": True,
        "creates_new_artifact": False,
        "new_source_story_form_or_candidate": False,
        "advances_current_next_action": True,
        "adoption_test": "The exact artifact opens from one command.",
        "kill_condition": "Stop if the launcher would require a new artifact.",
        "objective_fit": "direct",
        "work_class": "quick_win",
        "max_worker_turns": 1,
        "genre_or_domain_shift": False,
        "out_of_scope": ["new genre", "new asset generation"],
    }


class PrimaryCoordinatorWriterGuardTests(unittest.TestCase):
    def _files(self, root: Path) -> tuple[dict[str, Path], list[str], dict]:
        registry, hosts, adapter, coordinator = fixture()
        materialize_git_fixture_roots(
            root / "repositories", registry, hosts, adapter
        )
        coordinator = copy.deepcopy(coordinator)
        coordinator["coordinator_task"] = {
            "scope": "all_repositories",
            "task_id": PRIMARY,
            "binding_status": "active",
        }
        paths = {
            "registry": root / "registry.json",
            "hosts": root / "hosts.json",
            "adapter": root / "adapter.json",
            "coordinator": root / "coordinator.json",
            "scheduler": root / "scheduler.json",
            "frontier": root / "frontier.json",
            "project_context": root / "project-context.json",
            "missions": root / "missions",
        }
        paths["missions"].mkdir()
        documents = {
            "registry": registry,
            "hosts": hosts,
            "adapter": adapter,
            "coordinator": coordinator,
            "scheduler": loop.default_scheduler_state(),
        }
        for name, document in documents.items():
            loop.atomic_write_json(paths[name], document)
        signals = loop.collect_authority_signals(registry, hosts, adapter)
        signal_by_repository = {item["repository_id"]: item for item in signals}
        frontier = certified_frontier_fixture(registry, signals)
        loop.atomic_write_json(paths["frontier"], frontier)
        loop.atomic_write_json(
            paths["project_context"],
            certified_project_context_fixture(registry, signals, frontier),
        )
        loop.atomic_write_json(
            paths["missions"] / "ready.json",
            bind_mission_to_frontier(
                mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="writer-guard"),
                signal_by_repository[REPO_A],
            ),
        )
        common = [
            "--registry",
            str(paths["registry"]),
            "--hosts",
            str(paths["hosts"]),
            "--adapter",
            str(paths["adapter"]),
            "--coordinator-state",
            str(paths["coordinator"]),
            "--scheduler-state",
            str(paths["scheduler"]),
            "--missions-dir",
            str(paths["missions"]),
            "--frontier-state",
            str(paths["frontier"]),
            "--project-context-state",
            str(paths["project_context"]),
        ]
        return paths, common, coordinator

    def test_T128_secondary_task_can_plan_but_cannot_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths, common, _ = self._files(Path(temp))
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": SECONDARY}):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(loop.main(["coordinator-plan", *common]), 0)
                plan = json.loads(stdout.getvalue())
                before = paths["scheduler"].read_bytes()
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(
                        loop.main(
                            [
                                "coordinator-action-claim",
                                "--action-id",
                                plan["next_action"]["action_id"],
                                *common,
                            ]
                        ),
                        2,
                    )
                self.assertIn("READ_ONLY_NON_COORDINATOR_TASK", stderr.getvalue())
                self.assertEqual(paths["scheduler"].read_bytes(), before)

    def test_T129_primary_owns_claim_and_secondary_cannot_advance_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths, common, _ = self._files(Path(temp))
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": PRIMARY}):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(loop.main(["coordinator-plan", *common]), 0)
                plan = json.loads(stdout.getvalue())
                action = plan["next_action"]
                action_id = action["action_id"]
                recipient = action["payload"]["route"]["recipient_thread_id"]
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        loop.main(
                            [
                                "coordinator-action-claim",
                                "--action-id",
                                action_id,
                                *common,
                            ]
                        ),
                        0,
                    )
            claimed = loop.load_scheduler_state(paths["scheduler"])
            self.assertEqual(claimed["scheduler_claim"]["owner_task_id"], PRIMARY)
            before = paths["scheduler"].read_bytes()
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": SECONDARY}):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(
                        loop.main(
                            [
                                "coordinator-action-prepare",
                                "--action-id",
                                action_id,
                                "--recipient-thread-id",
                                recipient,
                                "--packet-sha256",
                                "a" * 64,
                                "--scheduler-state",
                                str(paths["scheduler"]),
                                "--coordinator-state",
                                str(paths["coordinator"]),
                            ]
                        ),
                        2,
                    )
            self.assertIn("READ_ONLY_NON_COORDINATOR_TASK", stderr.getvalue())
            self.assertEqual(paths["scheduler"].read_bytes(), before)

    def test_T130_unbound_writer_fails_closed(self) -> None:
        _, _, _, coordinator = fixture()
        coordinator["coordinator_task"]["task_id"] = None
        with self.assertRaisesRegex(
            loop.ProtocolError, "PRIMARY_COORDINATOR_WRITER_UNBOUND"
        ):
            loop.require_primary_coordinator_writer(
                coordinator, actor_task_id=PRIMARY
            )

    def test_T131_new_live_mission_requires_quick_value_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths, _, _ = self._files(root)
            payload = {
                "repository_id": REPO_A,
                "launch_set_id": "writer-guard",
                "mission_id": "mission-without-value",
                "attempt_id": "attempt-1",
                "worker_task_id": "worker-context-a",
                "host_id": "host-33333333-3333-4333-8333-333333333333",
                "supervisor_thread_id": "supervisor-context-a-default",
                "supervision_lane": "default",
            }
            payload_path = root / "payload.json"
            loop.atomic_write_json(payload_path, payload)
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": PRIMARY}):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(
                        loop.main(
                            [
                                "mission-init",
                                "--payload",
                                str(payload_path),
                                "--missions-dir",
                                str(paths["missions"]),
                                "--coordinator-state",
                                str(paths["coordinator"]),
                            ]
                        ),
                        2,
                    )
            self.assertIn("MISSION_VALUE_GATE", stderr.getvalue())
            self.assertEqual(len(list(paths["missions"].glob("*.json"))), 1)

    def test_T132_valid_quick_win_mission_is_persisted_with_value_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths, _, _ = self._files(root)
            payload = {
                "repository_id": REPO_A,
                "launch_set_id": "writer-guard",
                "mission_id": "mission-with-value",
                "attempt_id": "attempt-1",
                "worker_task_id": "worker-context-a",
                "host_id": "host-33333333-3333-4333-8333-333333333333",
                "supervisor_thread_id": "supervisor-context-a-default",
                "supervision_lane": "default",
                "value_contract": quick_win_value_contract(),
            }
            payload_path = root / "payload.json"
            loop.atomic_write_json(payload_path, payload)
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": PRIMARY}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        loop.main(
                            [
                                "mission-init",
                                "--payload",
                                str(payload_path),
                                "--missions-dir",
                                str(paths["missions"]),
                                "--coordinator-state",
                                str(paths["coordinator"]),
                            ]
                        ),
                        0,
                    )
            created = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in paths["missions"].glob("*.json")
                if path.name != "ready.json"
            ]
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0]["value_contract"]["work_class"], "quick_win")

    def test_T133_domain_shift_requires_explicit_user_authority(self) -> None:
        contract = quick_win_value_contract()
        contract["genre_or_domain_shift"] = True
        with self.assertRaisesRegex(loop.ProtocolError, "explicit user authorization"):
            loop.validate_mission_value_contract(contract)

    def test_T134_cloned_authority_cannot_claim_the_canonical_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            sandbox = Path(temp)
            live_root = sandbox / "state"
            live_root.mkdir()
            paths, common, _ = self._files(live_root)
            clone = sandbox / "secondary-coordinator.json"
            cloned_state = loop.load_json(paths["coordinator"])
            cloned_state["coordinator_task"]["task_id"] = SECONDARY
            loop.atomic_write_json(clone, cloned_state)
            cloned_common = list(common)
            cloned_common[
                cloned_common.index(str(paths["coordinator"]))
            ] = str(clone)
            patches = (
                mock.patch.object(loop, "SKILL_ROOT", sandbox),
                mock.patch.object(loop, "DEFAULT_BINDINGS", paths["registry"]),
                mock.patch.object(loop, "DEFAULT_HOSTS", paths["hosts"]),
                mock.patch.object(loop, "DEFAULT_ADAPTER", paths["adapter"]),
                mock.patch.object(
                    loop, "DEFAULT_COORDINATOR_STATE", paths["coordinator"]
                ),
                mock.patch.object(
                    loop, "DEFAULT_SCHEDULER_STATE", paths["scheduler"]
                ),
                mock.patch.object(loop, "DEFAULT_MISSIONS", paths["missions"]),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], mock.patch.dict(
                os.environ, {"CODEX_THREAD_ID": SECONDARY}
            ):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(
                        loop.main(["coordinator-plan", *cloned_common]), 0
                    )
                plan = json.loads(stdout.getvalue())
                before = paths["scheduler"].read_bytes()
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(
                        loop.main(
                            [
                                "coordinator-action-claim",
                                "--action-id",
                                plan["next_action"]["action_id"],
                                *cloned_common,
                            ]
                        ),
                        2,
                    )
            self.assertIn("CANONICAL_LIVE_STATE_REQUIRED", stderr.getvalue())
            self.assertEqual(paths["scheduler"].read_bytes(), before)

    def test_T135_direct_claim_requires_actor_identity(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="direct-claim")],
            coordinator,
            scheduler,
        )
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
            loop.ProtocolError, "exact actor task ID"
        ):
            loop.claim_coordinator_action(
                scheduler, plan, plan["next_action"]["action_id"]
            )

    def test_T136_new_artifact_requires_explicit_user_authority(self) -> None:
        contract = quick_win_value_contract()
        contract.update(
            {
                "existing_artifact_reused": False,
                "creates_new_artifact": True,
                "new_artifact_justification": "A new candidate was proposed.",
            }
        )
        with self.assertRaisesRegex(loop.ProtocolError, "explicit user authorization"):
            loop.validate_mission_value_contract(contract)

    def test_T137_non_quick_work_requires_no_quick_win_evidence(self) -> None:
        contract = quick_win_value_contract()
        contract.update(
            {
                "work_class": "bounded_slice",
                "max_worker_turns": 2,
                "why_not_smaller": "The gate spans two inseparable transitions.",
                "why_not_one_turn": "One transition needs the prior receipt.",
            }
        )
        with self.assertRaisesRegex(
            loop.ProtocolError, "no smaller current-gate move"
        ):
            loop.validate_mission_value_contract(contract)

    def test_T138_writer_rebind_is_explicit_and_idle_only(self) -> None:
        _, _, _, coordinator = fixture()
        coordinator["coordinator_task"]["task_id"] = PRIMARY
        scheduler = loop.default_scheduler_state()
        scheduler["scheduler_claim"] = {
            "action_id": "active",
            "action": {"kind": "test"},
        }
        with self.assertRaisesRegex(loop.ProtocolError, "NOT_IDLE"):
            loop.rebind_primary_coordinator_writer(
                coordinator,
                scheduler,
                expected_current_task_id=PRIMARY,
                new_task_id=SECONDARY,
                reason="User selected a replacement task.",
                confirmation=loop.PRIMARY_WRITER_REBIND_CONFIRMATION,
                actor_task_id=SECONDARY,
            )
        scheduler["scheduler_claim"] = None
        result = loop.rebind_primary_coordinator_writer(
            coordinator,
            scheduler,
            expected_current_task_id=PRIMARY,
            new_task_id=SECONDARY,
            reason="User selected a replacement task.",
            confirmation=loop.PRIMARY_WRITER_REBIND_CONFIRMATION,
            actor_task_id=SECONDARY,
        )
        self.assertEqual(result["classification"], "PRIMARY_COORDINATOR_WRITER_REBOUND")
        self.assertEqual(coordinator["coordinator_task"]["task_id"], SECONDARY)
        loop.validate_coordinator_state(coordinator)

    def test_T140_legacy_mission_is_routed_to_value_gate_not_worker(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        legacy = mission(
            REPO_A,
            "WORK_ORDER_RECEIVED",
            mission_id="legacy-without-value-contract",
            include_value_contract=False,
        )
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [legacy],
            coordinator,
            loop.default_scheduler_state(),
        )
        action = plan["next_action"]
        self.assertEqual(action["kind"], "resolve_mission_value_gate")
        self.assertEqual(action["payload"]["route"]["recipient_kind"], "supervisor")
        self.assertEqual(
            action["payload"]["mission_value_gate"]["blocked_action_kind"],
            "dispatch_work_order",
        )
        self.assertNotIn(
            "dispatch_work_order", [item["kind"] for item in plan["ready_actions"]]
        )

    def test_T141_valid_value_contract_preserves_worker_dispatch(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        ready = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="value-admitted")
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [ready],
            coordinator,
            loop.default_scheduler_state(),
        )
        action = plan["next_action"]
        self.assertEqual(action["kind"], "dispatch_work_order")
        self.assertEqual(action["payload"]["route"]["recipient_kind"], "worker")
        self.assertNotIn("mission_value_gate", action["payload"])

    def test_T142_value_contract_admission_is_replay_safe_and_immutable(self) -> None:
        legacy = mission(
            REPO_A,
            "WORK_ORDER_RECEIVED",
            mission_id="legacy-admission",
            include_value_contract=False,
        )
        contract = quick_win_value_contract()
        admitted = loop.admit_mission_value_contract(legacy, contract)
        self.assertEqual(admitted["classification"], "MISSION_VALUE_CONTRACT_ADMITTED")
        self.assertEqual(legacy["state"], "WORK_ORDER_RECEIVED")
        self.assertEqual(
            legacy["events"][-1]["details"]["event_kind"],
            "MISSION_VALUE_CONTRACT_ADMITTED",
        )
        event_count = len(legacy["events"])
        replay = loop.admit_mission_value_contract(legacy, copy.deepcopy(contract))
        self.assertEqual(
            replay["classification"], "MISSION_VALUE_CONTRACT_ALREADY_ADMITTED"
        )
        self.assertEqual(len(legacy["events"]), event_count)
        replacement = copy.deepcopy(contract)
        replacement["current_bottleneck"] = "A different bottleneck."
        with self.assertRaisesRegex(loop.ProtocolError, "cannot be replaced"):
            loop.admit_mission_value_contract(legacy, replacement)

    def test_T144_secondary_cannot_compile_migrate_or_render(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths, _, _ = self._files(root)
            command_cases = (
                (
                    [
                        "compile-work-order",
                        "--mission",
                        str(root / "missing-mission.json"),
                        "--output",
                        str(root / "compiled.json"),
                        "--coordinator-state",
                        str(paths["coordinator"]),
                    ],
                    (root / "compiled.json",),
                ),
                (
                    [
                        "migrate",
                        "--legacy",
                        str(root / "missing-legacy.json"),
                        "--output",
                        str(root / "migrated.json"),
                        "--report",
                        str(root / "migration-report.json"),
                        "--coordinator-state",
                        str(paths["coordinator"]),
                    ],
                    (root / "migrated.json", root / "migration-report.json"),
                ),
                (
                    [
                        "portfolio-render",
                        "--input",
                        str(root / "missing-portfolio.json"),
                        "--output",
                        str(root / "portfolio.md"),
                        "--scheduler-state",
                        str(paths["scheduler"]),
                        "--coordinator-state",
                        str(paths["coordinator"]),
                    ],
                    (root / "portfolio.md",),
                ),
            )
            for arguments, outputs in command_cases:
                with self.subTest(command=arguments[0]), mock.patch.dict(
                    os.environ, {"CODEX_THREAD_ID": SECONDARY}
                ):
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr):
                        self.assertEqual(loop.main(arguments), 2)
                    self.assertIn(
                        "READ_ONLY_NON_COORDINATOR_TASK", stderr.getvalue()
                    )
                    self.assertTrue(all(not path.exists() for path in outputs))

    def test_T145_secondary_resolve_cannot_persist_registry_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths, common, _ = self._files(root)
            registry_before = paths["registry"].read_bytes()
            hosts_before = paths["hosts"].read_bytes()
            with mock.patch.object(
                loop,
                "resolve_coordinator_target",
                return_value={
                    "classification": "RESOLVED",
                    "registry_changed": True,
                },
            ), mock.patch.dict(os.environ, {"CODEX_THREAD_ID": SECONDARY}):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(loop.main(["resolve", *common]), 2)
            self.assertIn("READ_ONLY_NON_COORDINATOR_TASK", stderr.getvalue())
            self.assertEqual(paths["registry"].read_bytes(), registry_before)
            self.assertEqual(paths["hosts"].read_bytes(), hosts_before)

    def test_T150_secondary_cannot_apply_project_context_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths, _, _ = self._files(root)
            context_path = root / "project-context-write.json"
            with mock.patch.dict(
                os.environ, {"CODEX_THREAD_ID": SECONDARY}
            ):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(
                        loop.main(
                            [
                                "project-context-apply-event",
                                "--event",
                                str(root / "missing-event.json"),
                                "--coordinator-state",
                                str(paths["coordinator"]),
                                "--project-context-state",
                                str(context_path),
                            ]
                        ),
                        2,
                    )
            self.assertIn("READ_ONLY_NON_COORDINATOR_TASK", stderr.getvalue())
            self.assertFalse(context_path.exists())

    def test_T151_secondary_cannot_apply_project_context_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths, _, _ = self._files(root)
            context_path = root / "project-context-write.json"
            portfolio_path = root / "portfolio-write.json"
            with mock.patch.dict(
                os.environ, {"CODEX_THREAD_ID": SECONDARY}
            ):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(
                        loop.main(
                            [
                                "coordinator-action-apply-project-context-result",
                                "--action-id",
                                "context-action",
                                "--result",
                                str(root / "missing-result.json"),
                                "--coordinator-state",
                                str(paths["coordinator"]),
                                "--scheduler-state",
                                str(paths["scheduler"]),
                                "--project-context-state",
                                str(context_path),
                                "--portfolio",
                                str(portfolio_path),
                                "--journal-dir",
                                str(root / "journal"),
                            ]
                        ),
                        2,
                    )
            self.assertIn("READ_ONLY_NON_COORDINATOR_TASK", stderr.getvalue())
            self.assertFalse(context_path.exists())
            self.assertFalse(portfolio_path.exists())


if __name__ == "__main__":
    unittest.main()
