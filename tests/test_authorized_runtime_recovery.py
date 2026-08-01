from __future__ import annotations

import copy
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import supervise_repo_loop as loop  # noqa: E402
from test_coordinator_only_ux import (  # noqa: E402
    HOST_ID,
    REPO_A,
    REPO_B,
    fixture,
)


RUNTIME_ACTION_ID = "TEST-CODEX-SANDBOX-DENY-READ-RECOVERY-01"
AUTHORIZATION_ID = (
    "CODEX-SANDBOX-DENY-READ-STATE-REVERSIBLE-REINITIALIZATION-V1"
)


class AuthorizedRuntimeRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / ".codex" / ".sandbox" / "deny_read_acl_state.json"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"\0" * loop.AUTHORIZED_RUNTIME_TARGET_PRE_SIZE)
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        self.registry, self.hosts, self.adapter, self.coordinator = fixture()
        self.coordinator["authorized_runtime_actions"] = []
        host = self.hosts["hosts"][0]
        host["known_repository_roots"][REPO_A] = str(self.repo_root)
        host["root_verifications"][REPO_A] = {
            "root": str(self.repo_root),
            "repository_id": REPO_A,
        }
        worker_binding = next(
            item
            for item in self.registry["worker_bindings"]
            if item["repository_id"] == REPO_A
        )
        worker_binding["root_hint"] = str(self.repo_root)
        self.supervisor_thread_id = next(
            item["supervisor_thread_id"]
            for item in self.registry["supervisor_bindings"]
            if item["repository_id"] == REPO_A
        )
        self.worker_task_id = worker_binding["worker_task_id"]
        for thread in self.adapter["threads"]:
            if thread.get("id") == self.worker_task_id:
                thread["cwd"] = str(self.repo_root)
        self.supervisor_text = self.root / "supervisor.txt"
        self.supervisor_text.write_text(
            "authorization_evidence:\n"
            "  action_id:\n"
            f"    {'a' * 32}\n"
            "  payload_sha256:\n"
            f"    {'b' * 64}\n"
            "exact_target:\n"
            f"  {self.target}\n"
            "expected_pre_repair_identity:\n"
            "  size_bytes: 22\n"
            f"  sha256: {loop.AUTHORIZED_RUNTIME_TARGET_PRE_SHA256.upper()}\n"
            "repair_executor:\n"
            "  runtime-owner maintenance surface on the same Thank host\n"
            "required_probe_surface:\n"
            "  restricted workspace-write Windows sandbox\n"
            "disallowed_probe_surface:\n"
            "  danger-full-access\n",
            encoding="utf-8",
        )
        self.decision = self.root / "decision.json"
        decision = {
            "schema_version": 2,
            "event_kind": "SUPERVISOR_DIRECTION_UPDATE_VERDICT",
            "event_id": "event-runtime-01",
            "repository_id": REPO_A,
            "mission_id": "runtime-recovery-mission",
            "attempt_id": 1,
            "supervisor_thread_id": self.supervisor_thread_id,
            "disposition": "ADOPTED",
            "authorization_id": AUTHORIZATION_ID,
            "resulting_action_id": RUNTIME_ACTION_ID,
            "repair_authorization_gate": "SATISFIED",
            "runtime_recovery_gate": "PENDING",
            "supervisor_text_path": str(self.supervisor_text),
            "supervisor_text_sha256": loop.sha256_file(self.supervisor_text),
        }
        self.decision.write_text(
            json.dumps(decision, sort_keys=True), encoding="utf-8"
        )
        self.spec = {
            "schema_version": 1,
            "runtime_action_id": RUNTIME_ACTION_ID,
            "repository_id": REPO_A,
            "mission_id": "runtime-recovery-mission",
            "attempt_id": "1",
            "supervision_lane": "default",
            "supervisor_thread_id": self.supervisor_thread_id,
            "worker_task_id": self.worker_task_id,
            "worker_host_id": HOST_ID,
            "handler_id": loop.AUTHORIZED_RUNTIME_HANDLER_ID,
            "authorization_id": AUTHORIZATION_ID,
            "decision_evidence_path": str(self.decision),
            "decision_evidence_sha256": loop.sha256_file(self.decision),
            "target_path": str(self.target),
            "target_pre_sha256": loop.AUTHORIZED_RUNTIME_TARGET_PRE_SHA256,
            "target_pre_size": loop.AUTHORIZED_RUNTIME_TARGET_PRE_SIZE,
        }
        self.authorization_scheduler = loop.default_scheduler_state()
        self.authorization_scheduler["completed_actions"].append(
            {
                "action_id": "a" * 32,
                "kind": "route_direction_update",
                "outcome": "ADOPTED_RUNTIME_RECOVERY_ACTION",
                "packet_sha256": "b" * 64,
                "recipient_thread_id": self.supervisor_thread_id,
                "repository_id": REPO_A,
                "route_class": "control",
                "requires_external_result": True,
                "delivery_token": "c" * 64,
                "state_fingerprint": "d" * 64,
                "evidence": str(self.decision),
            }
        )
        self.target_patch = mock.patch.object(
            loop, "DEFAULT_CODEX_DENY_READ_STATE_PATH", self.target
        )
        self.target_patch.start()
        self.addCleanup(self.target_patch.stop)

    def register(self) -> dict:
        return loop.register_authorized_runtime_action(
            self.coordinator,
            copy.deepcopy(self.spec),
            registry=self.registry,
            hosts=self.hosts,
            adapter=self.adapter,
            scheduler_state=self.authorization_scheduler,
            trusted_events_dir=self.root,
        )

    def plan(self, scheduler: dict) -> dict:
        return loop.build_coordinator_plan(
            self.registry,
            self.hosts,
            self.adapter,
            [],
            self.coordinator,
            scheduler,
        )

    def claim(self, scheduler: dict, expected_kind: str) -> dict:
        plan = self.plan(scheduler)
        action = plan["next_action"]
        self.assertEqual(action["kind"], expected_kind)
        loop.claim_coordinator_action(scheduler, plan, action["action_id"])
        return action

    def send_external(self, scheduler: dict, action: dict) -> None:
        route = action["payload"]["route"]
        digest = loop.sha256_text(action["action_id"])
        loop.prepare_coordinator_action_delivery(
            scheduler,
            action["action_id"],
            route["recipient_thread_id"],
            digest,
        )
        loop.mark_coordinator_action_sent(
            scheduler,
            action["action_id"],
            route["recipient_thread_id"],
            packet_sha256=digest,
            after_cursor="cursor-1",
        )

    def reconcile_completion(self, scheduler: dict) -> dict:
        self.assertTrue(self.plan(scheduler)["watchdog_should_be_armed"])
        action = self.claim(
            scheduler, "reconcile_authorized_runtime_completion"
        )
        return loop.reconcile_authorized_runtime_completion(
            self.coordinator, scheduler, action["action_id"]
        )

    def write_probe_receipt(
        self, record: dict, action: dict, outcome: str
    ) -> Path:
        receipt_path = Path(record["recovery_receipt_path"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt.pop("execution_surface", None)
        receipt["repair_execution_surface"] = (
            loop.AUTHORIZED_RUNTIME_REPAIR_EXECUTION_SURFACE
        )
        receipt["probe_execution_surface"] = (
            loop.AUTHORIZED_RUNTIME_PROBE_EXECUTION_SURFACE
        )
        receipt["phase"] = "WORKER_PROBE_RESULT"
        receipt["probe_action_id"] = action["action_id"]
        contract = action["payload"]["probe_contract"]
        statuses = contract["receipt_completion_required"]["status_matrix"][
            outcome
        ]

        def process_result(name: str, status: str) -> dict:
            if status == "not_started":
                return {"status": status}
            result = {
                "status": status,
                "cwd": contract[name]["cwd"],
                "argv": contract[name]["argv"],
                "sandbox_mode": "restricted_workspace_write",
                "exit_code": 0 if status == "passed" else None,
                "helper_error": None if status == "passed" else "helper failure",
            }
            return result

        receipt["probe_a"] = process_result("probe_a", statuses["probe_a"])
        if statuses["postcheck"] == "passed":
            receipt["postcheck"] = {
                "status": "passed",
                "path": record["target_path"],
                "checks": {
                    "exists": True,
                    "nonempty": True,
                    "not_nul_only": True,
                    "valid_json": True,
                },
            }
        else:
            receipt["postcheck"] = {"status": statuses["postcheck"]}
        receipt["probe_b"] = process_result("probe_b", statuses["probe_b"])
        expected_doctor = action["payload"]["probe_contract"]["runtime_doctor"][
            "expected"
        ]
        receipt["runtime_doctor"] = {
            "status": statuses["runtime_doctor"],
            "expected": expected_doctor,
        }
        if statuses["runtime_doctor"] == "failed":
            receipt["runtime_doctor"]["observed"] = {}
            receipt["runtime_doctor"]["error"] = "runtime doctor failed"
        if outcome == "probe_passed":
            receipt["runtime_doctor"]["observed"] = copy.deepcopy(expected_doctor)
        if outcome == "probe_a_failed":
            target = Path(record["target_path"])
            if target.is_file():
                try:
                    json.loads(target.read_text(encoding="utf-8"))
                    parse_result = "valid"
                except (OSError, UnicodeError, json.JSONDecodeError):
                    parse_result = "invalid"
                receipt["regenerated_state"] = {
                    "path": record["target_path"],
                    "exists": True,
                    "size": target.stat().st_size,
                    "sha256": loop.sha256_file(target),
                    "json_parse_result": parse_result,
                }
            else:
                receipt["regenerated_state"] = {
                    "path": record["target_path"],
                    "exists": False,
                    "size": None,
                    "sha256": None,
                    "json_parse_result": "absent",
                }
        if outcome in {
            "probe_b_failed",
            "runtime_doctor_failed",
            "probe_passed",
        }:
            receipt["regenerated_state"] = {
                "path": record["target_path"],
                "exists": True,
                "size": self.target.stat().st_size,
                "sha256": loop.sha256_file(self.target),
                "json_parse_result": "valid",
            }
        receipt["product_resume_readiness"] = "NOT_YET_SATISFIED"
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return receipt_path

    def prepare_repair(self, scheduler: dict) -> tuple[dict, dict]:
        self.register()
        execute = self.claim(scheduler, "execute_authorized_runtime_repair")
        result = loop.execute_authorized_runtime_action(
            self.coordinator, scheduler, execute["action_id"]
        )
        self.assertEqual(result["classification"], "AUTHORIZED_RUNTIME_REPAIR_PREPARED")
        record = self.coordinator["authorized_runtime_actions"][0]
        return execute, record

    def test_T108_registration_rejects_handler_path_identity_and_forged_decision(
        self,
    ) -> None:
        for field, value in (
            ("handler_id", "arbitrary_shell_v1"),
            ("target_path", str(self.root / "other.json")),
            ("target_pre_size", 23),
            ("target_pre_sha256", "c" * 64),
        ):
            candidate = copy.deepcopy(self.spec)
            candidate[field] = value
            with self.subTest(field=field), self.assertRaises(loop.ProtocolError):
                loop.register_authorized_runtime_action(
                    copy.deepcopy(self.coordinator),
                    candidate,
                    registry=self.registry,
                    hosts=self.hosts,
                    adapter=self.adapter,
                    scheduler_state=self.authorization_scheduler,
                    trusted_events_dir=self.root,
                )

        decision = json.loads(self.decision.read_text(encoding="utf-8"))
        decision["disposition"] = "PROPOSED"
        self.decision.write_text(json.dumps(decision), encoding="utf-8")
        forged = copy.deepcopy(self.spec)
        forged["decision_evidence_sha256"] = loop.sha256_file(self.decision)
        with self.assertRaises(loop.ProtocolError):
            loop.register_authorized_runtime_action(
                copy.deepcopy(self.coordinator),
                forged,
                registry=self.registry,
                hosts=self.hosts,
                adapter=self.adapter,
                scheduler_state=self.authorization_scheduler,
                trusted_events_dir=self.root,
            )

    def test_T127_registration_requires_separate_authorized_surfaces(self) -> None:
        original_text = self.supervisor_text.read_text(encoding="utf-8")
        original_decision = json.loads(self.decision.read_text(encoding="utf-8"))
        cases = (
            (
                "repair_executor",
                "runtime-owner maintenance surface on the same Thank host",
                "restricted workspace-write Windows sandbox",
            ),
            (
                "repair_executor_wrong_host",
                "runtime-owner maintenance surface on the same Thank host",
                "runtime-owner maintenance surface on the same Other host",
            ),
            (
                "repair_executor_missing_host",
                "runtime-owner maintenance surface on the same Thank host",
                "runtime-owner maintenance surface",
            ),
            (
                "required_probe_surface",
                "restricted workspace-write Windows sandbox",
                "danger-full-access Windows sandbox",
            ),
            (
                "disallowed_probe_surface",
                "  danger-full-access\n",
                "  restricted workspace-write\n",
            ),
        )
        for case, expected, replacement in cases:
            with self.subTest(case=case):
                self.supervisor_text.write_text(
                    original_text.replace(expected, replacement),
                    encoding="utf-8",
                )
                decision = copy.deepcopy(original_decision)
                decision["supervisor_text_sha256"] = loop.sha256_file(
                    self.supervisor_text
                )
                self.decision.write_text(
                    json.dumps(decision, sort_keys=True), encoding="utf-8"
                )
                candidate = copy.deepcopy(self.spec)
                candidate["decision_evidence_sha256"] = loop.sha256_file(
                    self.decision
                )
                with self.assertRaises(loop.ProtocolError):
                    loop.register_authorized_runtime_action(
                        copy.deepcopy(self.coordinator),
                        candidate,
                        registry=self.registry,
                        hosts=self.hosts,
                        adapter=self.adapter,
                        scheduler_state=self.authorization_scheduler,
                        trusted_events_dir=self.root,
                    )

    def test_T109_dry_run_and_precondition_mismatch_do_not_mutate_target(self) -> None:
        self.register()
        scheduler = loop.default_scheduler_state()
        action = self.claim(scheduler, "execute_authorized_runtime_repair")
        self.assertFalse(self.plan(scheduler)["watchdog_should_be_armed"])
        before_state = copy.deepcopy(self.coordinator)
        before_scheduler = copy.deepcopy(scheduler)
        dry = loop.execute_authorized_runtime_action(
            self.coordinator, scheduler, action["action_id"], dry_run=True
        )
        self.assertFalse(dry["would_mutate"])
        self.assertEqual(self.coordinator, before_state)
        self.assertEqual(scheduler, before_scheduler)
        self.assertEqual(self.target.read_bytes(), b"\0" * 22)

        self.target.write_bytes(b"x" * 22)
        result = loop.execute_authorized_runtime_action(
            self.coordinator, scheduler, action["action_id"]
        )
        self.assertEqual(
            result["classification"], "AUTHORIZED_RUNTIME_PRECONDITION_MISMATCH"
        )
        record = self.coordinator["authorized_runtime_actions"][0]
        self.assertFalse(record["authority_consumed"])
        self.assertEqual(record["phase"], "RESULT_READY")
        self.assertEqual(self.target.read_bytes(), b"x" * 22)
        self.assertFalse(Path(record["backup_path"]).exists())
        self.assertFalse(Path(record["quarantine_path"]).exists())
        plan = self.plan(scheduler)
        self.assertTrue(plan["watchdog_should_be_armed"])
        self.assertEqual(
            plan["next_action"]["kind"],
            "return_authorized_runtime_recovery_result",
        )
        self.assertTrue(plan["next_action"]["requires_external_result"])
        self.assertEqual(
            plan["next_action"]["payload"]["route"]["recipient_kind"],
            "supervisor",
        )
        self.assertEqual(
            plan["next_action"]["payload"]["route"]["observer_kind"],
            "chatgpt_poll",
        )

    def test_T119_registration_requires_live_exact_bindings(self) -> None:
        cases = (
            "supervisor_inactive",
            "supervisor_unverified",
            "worker_inactive",
            "worker_unverified",
            "worker_wrong_host",
            "worker_wrong_cwd",
            "root_verification_drift",
        )
        for case in cases:
            registry = copy.deepcopy(self.registry)
            hosts = copy.deepcopy(self.hosts)
            adapter = copy.deepcopy(self.adapter)
            if case == "supervisor_inactive":
                next(
                    item
                    for item in registry["supervisor_bindings"]
                    if item["repository_id"] == REPO_A
                )["binding_status"] = "inactive"
            elif case == "supervisor_unverified":
                next(
                    item
                    for item in adapter["threads"]
                    if item["id"] == self.supervisor_thread_id
                )["read_verified"] = False
            elif case == "worker_inactive":
                next(
                    item
                    for item in registry["worker_bindings"]
                    if item["repository_id"] == REPO_A
                )["binding_status"] = "inactive"
            elif case == "worker_unverified":
                next(
                    item
                    for item in adapter["threads"]
                    if item["id"] == self.worker_task_id
                )["read_verified"] = False
            elif case == "worker_wrong_host":
                next(
                    item
                    for item in adapter["threads"]
                    if item["id"] == self.worker_task_id
                )["host_id"] = "other-host"
            elif case == "worker_wrong_cwd":
                next(
                    item
                    for item in adapter["threads"]
                    if item["id"] == self.worker_task_id
                )["cwd"] = str(self.root / "wrong")
            else:
                hosts["hosts"][0]["root_verifications"][REPO_A]["root"] = str(
                    self.root / "wrong"
                )
            with self.subTest(case=case), self.assertRaises(loop.ProtocolError):
                loop.register_authorized_runtime_action(
                    copy.deepcopy(self.coordinator),
                    copy.deepcopy(self.spec),
                    registry=registry,
                    hosts=hosts,
                    adapter=adapter,
                    scheduler_state=copy.deepcopy(self.authorization_scheduler),
                    trusted_events_dir=self.root,
                )

    def test_T120_phase_invariants_fail_closed(self) -> None:
        self.register()
        original = self.coordinator["authorized_runtime_actions"][0]
        invalid_authorized = copy.deepcopy(original)
        invalid_authorized["authority_consumed"] = True
        invalid_result = copy.deepcopy(original)
        invalid_result["phase"] = "RESULT_READY"
        invalid_complete = copy.deepcopy(original)
        invalid_complete["phase"] = "COMPLETE"
        invalid_complete["recovery_result"] = {}
        for candidate in (invalid_authorized, invalid_result, invalid_complete):
            with self.assertRaises(loop.ProtocolError):
                loop.validate_authorized_runtime_action(candidate)

    def test_T121_stale_preserved_pair_with_live_target_is_not_resumed(self) -> None:
        self.register()
        scheduler = loop.default_scheduler_state()
        action = self.claim(scheduler, "execute_authorized_runtime_repair")
        record = self.coordinator["authorized_runtime_actions"][0]
        Path(record["backup_path"]).write_bytes(b"\0" * 22)
        Path(record["quarantine_path"]).write_bytes(b"\0" * 22)
        result = loop.execute_authorized_runtime_action(
            self.coordinator, scheduler, action["action_id"]
        )
        self.assertEqual(
            result["classification"], "AUTHORIZED_RUNTIME_PRECONDITION_MISMATCH"
        )
        self.assertFalse(record["authority_consumed"])
        self.assertEqual(
            record["recovery_result"]["reason"],
            "stale_preserved_pair_with_active_target",
        )
        self.assertEqual(self.target.read_bytes(), b"\0" * 22)

    def test_T125_wrong_preserved_pair_receipt_records_actual_identity(self) -> None:
        self.register()
        scheduler = loop.default_scheduler_state()
        action = self.claim(scheduler, "execute_authorized_runtime_repair")
        record = self.coordinator["authorized_runtime_actions"][0]
        observed = {
            "backup": b"wrong-backup",
            "quarantine": b"wrong-quarantine-content",
        }
        for field, content in observed.items():
            Path(record[f"{field}_path"]).write_bytes(content)
        result = loop.execute_authorized_runtime_action(
            self.coordinator, scheduler, action["action_id"]
        )
        self.assertEqual(
            result["classification"], "AUTHORIZED_RUNTIME_PRECONDITION_MISMATCH"
        )
        receipt = json.loads(
            Path(record["recovery_receipt_path"]).read_text(encoding="utf-8")
        )
        for field, content in observed.items():
            proof = receipt[field]
            self.assertTrue(proof["exists"])
            self.assertTrue(proof["is_file"])
            self.assertEqual(proof["size"], len(content))
            self.assertEqual(proof["sha256"], loop.sha256_bytes(content))
            self.assertEqual(proof["content_class"], "non_nul_bytes")
            self.assertFalse(proof["expected_identity_match"])
            self.assertNotEqual(
                proof["sha256"], loop.AUTHORIZED_RUNTIME_TARGET_PRE_SHA256
            )
        self.assertEqual(self.target.read_bytes(), b"\0" * 22)

    def test_T124_registration_cli_persists_exact_typed_ledger(self) -> None:
        paths = {
            "coordinator": self.root / "coordinator.json",
            "registry": self.root / "registry.json",
            "hosts": self.root / "hosts.json",
            "adapter": self.root / "adapter.json",
            "scheduler": self.root / "scheduler.json",
            "spec": self.root / "spec.json",
        }
        documents = {
            "coordinator": self.coordinator,
            "registry": self.registry,
            "hosts": self.hosts,
            "adapter": self.adapter,
            "scheduler": self.authorization_scheduler,
            "spec": self.spec,
        }
        for key, document in documents.items():
            paths[key].write_text(
                json.dumps(document, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        with (
            mock.patch.object(loop, "DEFAULT_COORDINATOR_EVENTS", self.root),
            mock.patch.object(
                loop, "DEFAULT_COORDINATOR_STATE", paths["coordinator"]
            ),
            mock.patch.object(loop, "DEFAULT_BINDINGS", paths["registry"]),
            mock.patch.object(loop, "DEFAULT_HOSTS", paths["hosts"]),
            mock.patch.object(loop, "DEFAULT_ADAPTER", paths["adapter"]),
            mock.patch.object(
                loop, "DEFAULT_SCHEDULER_STATE", paths["scheduler"]
            ),
        ):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = loop.main(
                    [
                        "authorized-runtime-action-register",
                        "--spec",
                        str(paths["spec"]),
                        "--coordinator-state",
                        str(paths["coordinator"]),
                        "--registry",
                        str(paths["registry"]),
                        "--hosts",
                        str(paths["hosts"]),
                        "--adapter",
                        str(paths["adapter"]),
                        "--scheduler-state",
                        str(paths["scheduler"]),
                    ]
                )
        self.assertEqual(exit_code, 0, stdout.getvalue())
        persisted = json.loads(paths["coordinator"].read_text(encoding="utf-8"))
        self.assertEqual(len(persisted["authorized_runtime_actions"]), 1)
        record = persisted["authorized_runtime_actions"][0]
        self.assertEqual(record["phase"], "AUTHORIZED")
        self.assertEqual(record["worker_adapter_host_id"], "local")
        loop.validate_coordinator_state(persisted)

    def test_T139_secondary_runtime_actor_cannot_use_primary_claim(self) -> None:
        self.register()
        scheduler = loop.default_scheduler_state()
        action = self.claim(scheduler, "execute_authorized_runtime_repair")
        owner = scheduler["scheduler_claim"]["owner_task_id"]
        secondary = copy.deepcopy(self.coordinator)
        secondary["coordinator_task"]["task_id"] = "secondary-repair-task"
        before = self.target.read_bytes()
        with self.assertRaisesRegex(
            loop.ProtocolError, "COORDINATOR_WRITER_OWNERSHIP_MISMATCH"
        ):
            loop.execute_authorized_runtime_action(
                secondary,
                scheduler,
                action["action_id"],
                actor_task_id="secondary-repair-task",
            )
        self.assertEqual(scheduler["scheduler_claim"]["owner_task_id"], owner)
        self.assertEqual(self.target.read_bytes(), before)
        record = self.coordinator["authorized_runtime_actions"][0]
        self.assertEqual(record["phase"], "AUTHORIZED")
        self.assertFalse(Path(record["backup_path"]).exists())

    def test_T110_prepare_is_byte_exact_receipted_and_idempotent(self) -> None:
        scheduler = loop.default_scheduler_state()
        action, record = self.prepare_repair(scheduler)
        runtime_payload = action["payload"]["runtime_action"]
        self.assertEqual(
            runtime_payload["repair_execution_surface"],
            "runtime_owner_maintenance",
        )
        self.assertEqual(
            runtime_payload["probe_execution_surface"],
            "restricted_workspace_write",
        )
        self.assertNotIn("execution_surface", runtime_payload)
        self.assertFalse(self.target.exists())
        self.assertEqual(Path(record["backup_path"]).read_bytes(), b"\0" * 22)
        self.assertEqual(Path(record["quarantine_path"]).read_bytes(), b"\0" * 22)
        receipt = json.loads(
            Path(record["recovery_receipt_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["original_target"]["content_class"], "all_nul_bytes")
        self.assertEqual(receipt["mutation_counts"]["acl"], 0)
        self.assertIn("authorization_payload_sha256", receipt)
        self.assertEqual(
            receipt["repair_execution_surface"], "runtime_owner_maintenance"
        )
        self.assertEqual(
            receipt["probe_execution_surface"], "restricted_workspace_write"
        )
        self.assertNotIn("execution_surface", receipt)
        probe = self.plan(scheduler)["next_action"]
        self.assertEqual(probe["kind"], "probe_authorized_runtime_repair")
        self.assertEqual(
            probe["payload"]["probe_contract"]["probe_execution_surface"],
            "restricted_workspace_write",
        )
        self.assertNotIn("execution_surface", probe["payload"]["probe_contract"])
        before = copy.deepcopy((self.coordinator, scheduler))
        repeated = loop.execute_authorized_runtime_action(
            self.coordinator, scheduler, action["action_id"]
        )
        self.assertTrue(repeated["deduplicated"])
        self.assertEqual((self.coordinator, scheduler), before)

    def test_T111_local_claim_release_boundary_and_receipt_required_completion(
        self,
    ) -> None:
        self.register()
        scheduler = loop.default_scheduler_state()
        action = self.claim(scheduler, "execute_authorized_runtime_repair")
        with self.assertRaises(loop.ProtocolError):
            loop.complete_coordinator_action(
                scheduler,
                action["action_id"],
                "repair_prepared",
                evidence="missing.json",
                coordinator_state=self.coordinator,
            )
        released = loop.release_coordinator_action(
            scheduler, action["action_id"], "pre-effect retry"
        )
        self.assertEqual(released["classification"], "COORDINATOR_ACTION_RELEASED_FOR_RETRY")

        action = self.claim(scheduler, "execute_authorized_runtime_repair")
        record = self.coordinator["authorized_runtime_actions"][0]
        Path(record["backup_path"]).write_bytes(b"\0" * 22)
        record["authority_consumed"] = True
        record["phase"] = "EFFECT_PREPARED"
        receipt = loop._write_authorized_runtime_receipt(record, "EFFECT_PREPARED")
        loop._prepare_authorized_runtime_effect_claim(
            scheduler, action["action_id"], receipt
        )
        self.assertTrue(self.plan(scheduler)["watchdog_should_be_armed"])
        with self.assertRaises(loop.ProtocolError):
            loop.release_coordinator_action(
                scheduler, action["action_id"], "post-effect retry"
            )

    def test_T112_probe_a_failure_rolls_back_and_preserves_both_copies(self) -> None:
        scheduler = loop.default_scheduler_state()
        _, record = self.prepare_repair(scheduler)
        probe = self.claim(scheduler, "probe_authorized_runtime_repair")
        self.assertEqual(probe["payload"]["route"]["observer_kind"], "codex_wait")
        doctor = probe["payload"]["probe_contract"]["runtime_doctor"]["expected"]
        self.assertEqual(
            doctor["ymm4_version"],
            "4.54.0.1+76b177dd451f9d162816dabc4ac658180e869582",
        )
        status_matrix = probe["payload"]["probe_contract"][
            "receipt_completion_required"
        ]["status_matrix"]
        self.assertEqual(
            status_matrix["probe_a_failed"],
            {
                "probe_a": "failed",
                "postcheck": "not_started",
                "probe_b": "not_started",
                "runtime_doctor": "not_started",
            },
        )
        self.assertEqual(status_matrix, loop.AUTHORIZED_RUNTIME_PROBE_STATUS_MATRIX)
        self.send_external(scheduler, probe)
        evidence = self.write_probe_receipt(record, probe, "probe_a_failed")
        worker_receipt = json.loads(evidence.read_text(encoding="utf-8"))
        loop.complete_coordinator_action(
            scheduler,
            probe["action_id"],
            "probe_a_failed",
            evidence=str(evidence),
            coordinator_state=self.coordinator,
        )
        self.assertEqual(record["phase"], "ROLLBACK_REQUIRED")
        replay_before = copy.deepcopy((self.coordinator, scheduler))
        loop.complete_coordinator_action(
            scheduler,
            probe["action_id"],
            "probe_a_failed",
            evidence=str(evidence),
            coordinator_state=self.coordinator,
        )
        self.assertEqual((self.coordinator, scheduler), replay_before)
        rollback = self.claim(scheduler, "rollback_authorized_runtime_repair")
        self.target.write_text('{"regenerated": false}', encoding="utf-8")
        rolled_back = loop.rollback_authorized_runtime_action(
            self.coordinator, scheduler, rollback["action_id"]
        )
        self.assertEqual(rolled_back["classification"], "AUTHORIZED_RUNTIME_ROLLBACK_COMPLETED")
        self.assertEqual(self.target.read_bytes(), b"\0" * 22)
        self.assertTrue(Path(record["backup_path"]).exists())
        self.assertTrue(Path(record["quarantine_path"]).exists())
        rolled_back_receipt = json.loads(evidence.read_text(encoding="utf-8"))
        for field in (
            "repair_execution_surface",
            "probe_execution_surface",
            "probe_action_id",
            "probe_a",
            "postcheck",
            "probe_b",
            "runtime_doctor",
            "regenerated_state",
        ):
            self.assertEqual(rolled_back_receipt[field], worker_receipt[field])
        self.assertTrue(rolled_back_receipt["rollback_performed"])
        returned = self.plan(scheduler)["next_action"]
        self.assertEqual(returned["kind"], "return_authorized_runtime_recovery_result")
        self.assertEqual(returned["payload"]["route"]["observer_kind"], "chatgpt_poll")

    def test_T113_late_probe_failures_keep_regenerated_state(self) -> None:
        for outcome, classification in (
            ("probe_b_failed", "CWD_OR_PATH_SCOPED_SANDBOX_FAILURE"),
            (
                "runtime_doctor_failed",
                "SANDBOX_RECOVERED_RUNTIME_READINESS_FAILED",
            ),
        ):
            with self.subTest(outcome=outcome):
                scheduler = loop.default_scheduler_state()
                _, record = self.prepare_repair(scheduler)
                self.target.write_text('{"valid": true}', encoding="utf-8")
                probe = self.claim(scheduler, "probe_authorized_runtime_repair")
                self.send_external(scheduler, probe)
                evidence = self.write_probe_receipt(record, probe, outcome)
                loop.complete_coordinator_action(
                    scheduler,
                    probe["action_id"],
                    outcome,
                    evidence=str(evidence),
                    coordinator_state=self.coordinator,
                )
                self.assertEqual(record["phase"], "RESULT_READY")
                self.assertEqual(record["recovery_result"]["classification"], classification)
                self.assertFalse(record["recovery_result"]["rolled_back"])
                self.assertEqual(self.target.read_text(encoding="utf-8"), '{"valid": true}')
                Path(record["backup_path"]).unlink()
                Path(record["quarantine_path"]).unlink()
                self.coordinator["authorized_runtime_actions"].clear()
                self.target.write_bytes(b"\0" * 22)

    def test_T114_chatgpt_routes_are_polled_not_waited_and_replay_is_noop(self) -> None:
        self.coordinator.setdefault("pending_user_events", []).append(
            {
                "event_id": "question-1",
                "kind": "project_question",
                "repository_id": REPO_B,
                "mission_id": None,
                "raw_text": "status?",
                "state": "queued",
                "priority": 1,
                "queued_at": "2026-08-01T00:00:00Z",
            }
        )
        scheduler = loop.default_scheduler_state()
        action = self.claim(scheduler, "route_project_question")
        self.send_external(scheduler, action)
        waiting = self.plan(scheduler)
        self.assertEqual(waiting["wait_targets"], [])
        self.assertEqual(len(waiting["poll_targets"]), 1)
        self.assertEqual(waiting["poll_targets"][0]["observer_kind"], "chatgpt_poll")
        self.assertNotIn("host_id", waiting["poll_targets"][0])
        self.assertEqual(len(waiting["active_routes"]), 1)
        loop.complete_coordinator_action(
            scheduler, action["action_id"], "answered", evidence="answer.json"
        )
        state_before = copy.deepcopy(scheduler)
        loop.complete_coordinator_action(
            scheduler, action["action_id"], "answered", evidence="answer.json"
        )
        self.assertEqual(scheduler, state_before)

    def test_T115_local_repair_preserves_unrelated_route_identity(self) -> None:
        self.coordinator.setdefault("pending_user_events", []).append(
            {
                "event_id": "unrelated-question",
                "kind": "project_question",
                "repository_id": REPO_B,
                "mission_id": None,
                "raw_text": "keep waiting",
                "state": "queued",
                "priority": 1,
                "queued_at": "2026-08-01T00:00:00Z",
            }
        )
        scheduler = loop.default_scheduler_state()
        unrelated = self.claim(scheduler, "route_project_question")
        self.send_external(scheduler, unrelated)
        lease_before = copy.deepcopy(scheduler["route_leases"][0])

        self.register()
        execute = self.claim(scheduler, "execute_authorized_runtime_repair")
        loop.execute_authorized_runtime_action(
            self.coordinator, scheduler, execute["action_id"]
        )
        self.assertEqual(scheduler["route_leases"], [lease_before])
        next_plan = self.plan(scheduler)
        self.assertEqual(next_plan["next_action"]["kind"], "probe_authorized_runtime_repair")
        self.assertEqual(len(next_plan["poll_targets"]), 1)
        self.assertEqual(next_plan["poll_targets"][0]["action_id"], unrelated["action_id"])

    def test_T116_fake_probe_pass_and_unbound_supervisor_result_are_rejected(
        self,
    ) -> None:
        scheduler = loop.default_scheduler_state()
        _, record = self.prepare_repair(scheduler)
        self.target.write_text('{"valid": true}', encoding="utf-8")
        probe = self.claim(scheduler, "probe_authorized_runtime_repair")
        self.send_external(scheduler, probe)
        before = copy.deepcopy((self.coordinator, scheduler))
        with self.assertRaises(loop.ProtocolError):
            loop.complete_coordinator_action(
                scheduler,
                probe["action_id"],
                "probe_passed",
                evidence=record["recovery_receipt_path"],
                coordinator_state=self.coordinator,
            )
        self.assertEqual((self.coordinator, scheduler), before)

        evidence = self.write_probe_receipt(record, probe, "probe_passed")
        for field, value in (
            ("repair_execution_surface", "restricted_workspace_write"),
            ("probe_execution_surface", "runtime_owner_maintenance"),
            ("execution_surface", "restricted_workspace_write"),
        ):
            with self.subTest(receipt_surface=field):
                forged = json.loads(evidence.read_text(encoding="utf-8"))
                forged[field] = value
                evidence.write_text(
                    json.dumps(forged, sort_keys=True), encoding="utf-8"
                )
                before = copy.deepcopy((self.coordinator, scheduler))
                with self.assertRaises(loop.ProtocolError):
                    loop.complete_coordinator_action(
                        scheduler,
                        probe["action_id"],
                        "probe_passed",
                        evidence=str(evidence),
                        coordinator_state=self.coordinator,
                    )
                self.assertEqual((self.coordinator, scheduler), before)
                evidence = self.write_probe_receipt(
                    record, probe, "probe_passed"
                )

        forged = json.loads(evidence.read_text(encoding="utf-8"))
        forged["probe_a"]["cwd"] = str(self.root)
        evidence.write_text(json.dumps(forged, sort_keys=True), encoding="utf-8")
        before = copy.deepcopy((self.coordinator, scheduler))
        with self.assertRaises(loop.ProtocolError):
            loop.complete_coordinator_action(
                scheduler,
                probe["action_id"],
                "probe_passed",
                evidence=str(evidence),
                coordinator_state=self.coordinator,
            )
        self.assertEqual((self.coordinator, scheduler), before)

        evidence = self.write_probe_receipt(record, probe, "probe_passed")
        worker_receipt_bytes = evidence.read_bytes()
        loop.complete_coordinator_action(
            scheduler,
            probe["action_id"],
            "probe_passed",
            evidence=str(evidence),
            coordinator_state=self.coordinator,
        )
        returned = self.claim(
            scheduler, "return_authorized_runtime_recovery_result"
        )
        self.send_external(scheduler, returned)
        with self.assertRaises(loop.ProtocolError):
            loop.complete_coordinator_action(
                scheduler,
                returned["action_id"],
                "accepted",
                evidence="unbound.json",
                coordinator_state=self.coordinator,
            )
        supervisor_evidence = self.root / "runtime-supervisor-result.json"
        supervisor_evidence.write_text(
            json.dumps(
                {
                    "event_kind": "SUPERVISOR_RUNTIME_RECOVERY_VERDICT",
                    "disposition": "accepted",
                    "repository_id": record["repository_id"],
                    "mission_id": record["mission_id"],
                    "attempt_id": record["attempt_id"],
                    "supervisor_thread_id": record["supervisor_thread_id"],
                    "runtime_action_id": record["runtime_action_id"],
                    "runtime_identity_sha256": record["identity_sha256"],
                    "authorization_id": record["authorization_id"],
                    "recovery_receipt_sha256": loop.sha256_file(evidence),
                    "product_resume_readiness": (
                        "ELIGIBLE_FOR_LATER_SUPERVISOR_WORK_ORDER"
                    ),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        loop.complete_coordinator_action(
            scheduler,
            returned["action_id"],
            "accepted",
            evidence=str(supervisor_evidence),
            coordinator_state=self.coordinator,
        )
        self.assertEqual(record["phase"], "COMPLETE")
        self.assertEqual(record["supervisor_result"]["disposition"], "accepted")
        self.assertEqual(evidence.read_bytes(), worker_receipt_bytes)
        replay_before = copy.deepcopy((self.coordinator, scheduler))
        loop.complete_coordinator_action(
            scheduler,
            returned["action_id"],
            "accepted",
            evidence=str(supervisor_evidence),
            coordinator_state=self.coordinator,
        )
        self.assertEqual((self.coordinator, scheduler), replay_before)

    def test_T122_split_brain_reconciles_execute_probe_and_supervisor(self) -> None:
        self.register()
        scheduler = loop.default_scheduler_state()
        execute = self.claim(scheduler, "execute_authorized_runtime_repair")
        stale_coordinator = copy.deepcopy(self.coordinator)
        claimed_scheduler = copy.deepcopy(scheduler)
        loop.execute_authorized_runtime_action(
            self.coordinator, scheduler, execute["action_id"]
        )

        coordinator_ahead = copy.deepcopy(self.coordinator)
        claimed_scheduler["scheduler_claim"]["status"] = "effect_prepared"
        claimed_scheduler["scheduler_claim"]["effect_receipt"] = copy.deepcopy(
            coordinator_ahead["authorized_runtime_actions"][0]["effect_receipt"]
        )
        ahead_result = loop.execute_authorized_runtime_action(
            coordinator_ahead, claimed_scheduler, execute["action_id"]
        )
        self.assertEqual(
            ahead_result["classification"],
            "AUTHORIZED_RUNTIME_REPAIR_COMPLETION_RECONCILED",
        )
        self.assertIsNone(claimed_scheduler["scheduler_claim"])

        self.coordinator = stale_coordinator
        reconciled = self.reconcile_completion(scheduler)
        self.assertEqual(
            reconciled["classification"],
            "AUTHORIZED_RUNTIME_COMPLETION_RECONCILED",
        )
        record = self.coordinator["authorized_runtime_actions"][0]
        self.assertEqual(record["phase"], "REPAIR_PREPARED")

        self.target.write_text('{"valid": true}', encoding="utf-8")
        probe = self.claim(scheduler, "probe_authorized_runtime_repair")
        self.send_external(scheduler, probe)
        evidence = self.write_probe_receipt(record, probe, "probe_passed")
        stale_coordinator = copy.deepcopy(self.coordinator)
        loop.complete_coordinator_action(
            scheduler,
            probe["action_id"],
            "probe_passed",
            evidence=str(evidence),
            coordinator_state=self.coordinator,
        )
        self.coordinator = stale_coordinator
        self.reconcile_completion(scheduler)
        record = self.coordinator["authorized_runtime_actions"][0]
        self.assertEqual(record["phase"], "RESULT_READY")

        returned = self.claim(
            scheduler, "return_authorized_runtime_recovery_result"
        )
        self.send_external(scheduler, returned)
        supervisor_evidence = self.root / "split-supervisor-result.json"
        supervisor_evidence.write_text(
            json.dumps(
                {
                    "event_kind": "SUPERVISOR_RUNTIME_RECOVERY_VERDICT",
                    "disposition": "accepted",
                    "repository_id": record["repository_id"],
                    "mission_id": record["mission_id"],
                    "attempt_id": record["attempt_id"],
                    "supervisor_thread_id": record["supervisor_thread_id"],
                    "runtime_action_id": record["runtime_action_id"],
                    "runtime_identity_sha256": record["identity_sha256"],
                    "authorization_id": record["authorization_id"],
                    "recovery_receipt_sha256": loop.sha256_file(evidence),
                    "product_resume_readiness": (
                        "ELIGIBLE_FOR_LATER_SUPERVISOR_WORK_ORDER"
                    ),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        stale_coordinator = copy.deepcopy(self.coordinator)
        loop.complete_coordinator_action(
            scheduler,
            returned["action_id"],
            "accepted",
            evidence=str(supervisor_evidence),
            coordinator_state=self.coordinator,
        )
        self.coordinator = stale_coordinator
        self.reconcile_completion(scheduler)
        self.assertEqual(
            self.coordinator["authorized_runtime_actions"][0]["phase"],
            "COMPLETE",
        )

    def test_T126_probe_a_failure_requires_exact_present_target_identity(
        self,
    ) -> None:
        scheduler = loop.default_scheduler_state()
        _, record = self.prepare_repair(scheduler)
        probe = self.claim(scheduler, "probe_authorized_runtime_repair")
        self.send_external(scheduler, probe)
        invalid_content = b"{invalid-json"
        self.target.write_bytes(invalid_content)
        evidence = self.write_probe_receipt(record, probe, "probe_a_failed")
        exact_receipt = json.loads(evidence.read_text(encoding="utf-8"))
        expected_regenerated = copy.deepcopy(exact_receipt["regenerated_state"])
        self.assertEqual(expected_regenerated["sha256"], loop.sha256_bytes(invalid_content))
        self.assertEqual(expected_regenerated["json_parse_result"], "invalid")

        forged = copy.deepcopy(exact_receipt)
        forged["regenerated_state"] = {
            "path": record["target_path"],
            "size": None,
            "sha256": None,
            "json_parse_result": "pending",
        }
        evidence.write_text(json.dumps(forged, sort_keys=True), encoding="utf-8")
        before = copy.deepcopy((self.coordinator, scheduler))
        with self.assertRaises(loop.ProtocolError):
            loop.complete_coordinator_action(
                scheduler,
                probe["action_id"],
                "probe_a_failed",
                evidence=str(evidence),
                coordinator_state=self.coordinator,
            )
        self.assertEqual((self.coordinator, scheduler), before)

        evidence.write_text(
            json.dumps(exact_receipt, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        loop.complete_coordinator_action(
            scheduler,
            probe["action_id"],
            "probe_a_failed",
            evidence=str(evidence),
            coordinator_state=self.coordinator,
        )
        rollback = self.claim(scheduler, "rollback_authorized_runtime_repair")
        loop.rollback_authorized_runtime_action(
            self.coordinator, scheduler, rollback["action_id"]
        )
        rolled_back_receipt = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(
            rolled_back_receipt["regenerated_state"], expected_regenerated
        )
        self.assertTrue(rolled_back_receipt["rollback_performed"])

    def test_T123_split_brain_reconciles_rollback(self) -> None:
        scheduler = loop.default_scheduler_state()
        _, record = self.prepare_repair(scheduler)
        probe = self.claim(scheduler, "probe_authorized_runtime_repair")
        self.send_external(scheduler, probe)
        evidence = self.write_probe_receipt(record, probe, "probe_a_failed")
        loop.complete_coordinator_action(
            scheduler,
            probe["action_id"],
            "probe_a_failed",
            evidence=str(evidence),
            coordinator_state=self.coordinator,
        )
        rollback = self.claim(scheduler, "rollback_authorized_runtime_repair")
        claimed_scheduler = copy.deepcopy(scheduler)
        stale_coordinator = copy.deepcopy(self.coordinator)
        self.target.write_text('{"invalid": true}', encoding="utf-8")
        loop.rollback_authorized_runtime_action(
            self.coordinator, scheduler, rollback["action_id"]
        )

        coordinator_ahead = copy.deepcopy(self.coordinator)
        claimed_scheduler["scheduler_claim"]["status"] = "effect_prepared"
        claimed_scheduler["scheduler_claim"]["effect_receipt"] = copy.deepcopy(
            coordinator_ahead["authorized_runtime_actions"][0]["effect_receipt"]
        )
        ahead_result = loop.rollback_authorized_runtime_action(
            coordinator_ahead, claimed_scheduler, rollback["action_id"]
        )
        self.assertEqual(
            ahead_result["classification"],
            "AUTHORIZED_RUNTIME_ROLLBACK_COMPLETION_RECONCILED",
        )

        self.coordinator = stale_coordinator
        self.reconcile_completion(scheduler)
        record = self.coordinator["authorized_runtime_actions"][0]
        self.assertEqual(record["phase"], "RESULT_READY")
        self.assertTrue(record["recovery_result"]["rolled_back"])
        self.assertEqual(self.target.read_bytes(), b"\0" * 22)


if __name__ == "__main__":
    unittest.main()
