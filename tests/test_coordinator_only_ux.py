from __future__ import annotations

import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import supervise_repo_loop as loop  # noqa: E402


HOST_ID = "host-33333333-3333-4333-8333-333333333333"
REPO_A = "github.com/example/context-a"
REPO_B = "github.com/example/context-b"
ROOT_A = "X:\\fixtures\\context-a"
ROOT_B = "X:\\fixtures\\context-b"


def inspector(path: str) -> dict[str, str]:
    identities = {
        ROOT_A.casefold(): REPO_A,
        ROOT_B.casefold(): REPO_B,
    }
    repository_id = identities.get(path.casefold())
    if repository_id is None:
        raise loop.ProtocolError(f"not a fixture repository: {path}")
    return {
        "root": path,
        "remote_url": f"https://{repository_id}",
        "repository_id": repository_id,
    }


def repository(repository_id: str, order: int) -> dict:
    return {
        "schema_version": 2,
        "repository_id": repository_id,
        "aliases": [f"context-{order}"],
        "default_supervision_lane": "default",
        "remote_identity": repository_id,
        "stable_order": order,
        "allow_request_next_mission": True,
    }


def supervisor(repository_id: str, lane: str = "default") -> dict:
    suffix = repository_id.rsplit("/", 1)[-1]
    return {
        "schema_version": 2,
        "repository_id": repository_id,
        "supervision_lane": lane,
        "supervisor_project_id": "project-supervision",
        "supervisor_thread_id": f"supervisor-{suffix}-{lane}",
        "expected_supervisor_title": f"Supervisor {suffix} {lane}",
        "last_verified_at": "2026-07-28T00:00:00Z",
        "binding_status": "active",
        "allow_create_supervisor_chat": False,
    }


def worker(repository_id: str, root: str) -> dict:
    suffix = repository_id.rsplit("/", 1)[-1]
    return {
        "schema_version": 2,
        "repository_id": repository_id,
        "worker_task_id": f"worker-{suffix}",
        "host_id": HOST_ID,
        "root_hint": root,
        "last_verified_at": "2026-07-28T00:00:00Z",
        "binding_status": "active",
        "allow_create_worker_task": True,
    }


def worker_thread(
    repository_id: str,
    root: str,
    task_id: str | None = None,
    *,
    title: str = "Persistent backend task",
) -> dict:
    suffix = repository_id.rsplit("/", 1)[-1]
    return {
        "id": task_id or f"worker-{suffix}",
        "kind": "codex",
        "host_id": "local",
        "cwd": root,
        "repository_id": repository_id,
        "title": title,
        "read_verified": True,
        "status": "idle",
    }


def supervisor_thread(repository_id: str, lane: str = "default") -> dict:
    binding = supervisor(repository_id, lane)
    return {
        "id": binding["supervisor_thread_id"],
        "kind": "chatgpt",
        "project_id": binding["supervisor_project_id"],
        "title": binding["expected_supervisor_title"],
        "read_verified": True,
        "status": "idle",
    }


def fixture(*, include_workers: bool = True) -> tuple[dict, dict, dict, dict]:
    registry = {
        "schema_version": 2,
        "coordinator_policy": {
            "allow_create_worker_task": True,
            "allow_create_supervisor_chat": False,
            "user_visible_codex_entry_points": 1,
        },
        "repositories": [repository(REPO_A, 0), repository(REPO_B, 1)],
        "supervisor_bindings": [supervisor(REPO_A), supervisor(REPO_B)],
        "worker_bindings": (
            [worker(REPO_A, ROOT_A), worker(REPO_B, ROOT_B)]
            if include_workers
            else []
        ),
    }
    host = {
        "host_id": HOST_ID,
        "aliases": ["local"],
        "app_host_ids": ["local"],
        "workspace_roots": ["X:\\fixtures"],
        "known_repository_roots": {REPO_A: ROOT_A, REPO_B: ROOT_B},
        "root_verifications": {
            REPO_A: {"root": ROOT_A, "repository_id": REPO_A},
            REPO_B: {"root": ROOT_B, "repository_id": REPO_B},
        },
        "available_worker_tasks": {},
        "capabilities": {"codex_thread_create": True},
        "last_seen_at": "2026-07-28T00:00:00Z",
    }
    hosts = {"schema_version": 2, "hosts": [host], "private_artifacts": []}
    adapter = {
        "schema_version": 1,
        "current_host_alias": "local",
        "capabilities": {"create_codex_thread": True},
        "projects": [],
        "threads": [
            supervisor_thread(REPO_A),
            supervisor_thread(REPO_B),
        ],
    }
    if include_workers:
        adapter["threads"].extend(
            [
                worker_thread(REPO_A, ROOT_A),
                worker_thread(REPO_B, ROOT_B),
            ]
        )
    coordinator = {
        "schema_version": 2,
        "contract": "global-coordinator-only-v2",
        "user_visible_codex_entry_points": 1,
        "normal_user_input_surface": "coordinator",
        "standard_prompts": {
            "this_repository": loop.COORDINATOR_PROMPT_THIS_REPOSITORY,
            "next_actionable_registered_repository": (
                loop.COORDINATOR_PROMPT_NEXT_ACTIONABLE
            ),
        },
        "active_repository_selector": None,
        "pending_repository_ids": [],
        "pending_user_responses": [],
        "authorized_runtime_actions": [],
        "coordinator_task": {
            "scope": "all_repositories",
            "task_id": os.environ.get(
                "CODEX_THREAD_ID", "test-primary-coordinator"
            ),
            "binding_status": "active",
        },
    }
    return registry, hosts, adapter, coordinator


def materialize_git_fixture_roots(
    base: Path, registry: dict, hosts: dict, adapter: dict
) -> None:
    base.mkdir(parents=True, exist_ok=True)
    host = hosts["hosts"][0]
    host["workspace_roots"] = [str(base)]
    for repository_id in (REPO_A, REPO_B):
        token = repository_id.rsplit("/", 1)[-1]
        root = base / token
        root.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.email", "fixture@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Frontier Fixture"],
            cwd=root,
            check=True,
        )
        (root / "README.md").write_text(
            f"# {token}\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "fixture frontier"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        host["known_repository_roots"][repository_id] = str(root)
        host["root_verifications"][repository_id] = {
            "root": str(root),
            "repository_id": repository_id,
        }
        for binding in registry.get("worker_bindings", []):
            if binding.get("repository_id") == repository_id:
                binding["root_hint"] = str(root)
        for thread in adapter.get("threads", []):
            if (
                thread.get("kind") == "codex"
                and thread.get("repository_id") == repository_id
            ):
                thread["cwd"] = str(root)


def certified_frontier_fixture(
    registry: dict, signals: list[dict]
) -> dict:
    state = loop.default_frontier_state(
        item["repository_id"] for item in registry["repositories"]
    )
    by_repository = {item["repository_id"]: item for item in signals}
    for repository in registry["repositories"]:
        repository_id = repository["repository_id"]
        signal = by_repository[repository_id]
        git = signal["git"]
        token = repository_id.rsplit("/", 1)[-1]
        loop.apply_frontier_event(
            state,
            {
                "repository_id": repository_id,
                "lane_id": repository["default_supervision_lane"],
                "frontier_epoch": 1,
                "frontier_event_id": f"frontier-{token}",
                "artifact_id": f"artifact-{token}",
                "artifact_revision": "fixture-current",
                "artifact_sha256": loop.sha256_text(f"artifact-{token}"),
                "branch": git.get("branch"),
                "head_sha": git.get("head_sha"),
                "disposition": "accepted",
                "source_actor": "supervisor",
                "source_message_id": f"message-{token}",
                "source_result_id": f"result-{token}",
                "based_on_frontier_epoch": 0,
                "supersedes_event_ids": [],
                "recorded_at": "2026-08-02T00:00:00Z",
            },
        )
    return state


def certified_project_context_fixture(
    registry: dict, signals: list[dict], frontier: dict
) -> dict:
    state = loop.default_project_context_state(
        item["repository_id"] for item in registry["repositories"]
    )
    by_repository = {item["repository_id"]: item for item in signals}
    for repository in registry["repositories"]:
        repository_id = repository["repository_id"]
        lane = repository["default_supervision_lane"]
        token = repository_id.rsplit("/", 1)[-1]
        frontier_record = frontier["records"][f"{repository_id}|{lane}"]
        signal = by_repository[repository_id]
        loop.apply_project_context_event(
            state,
            {
                "repository_id": repository_id,
                "project_context_revision": 1,
                "project_context_event_id": f"context-{token}",
                "based_on_project_context_revision": 0,
                "source_actor": "supervisor",
                "source_message_id": f"context-message-{token}",
                "authority_revision": signal["git"]["head_sha"],
                "authority_fingerprint": signal["authority_fingerprint"],
                "north_star": f"Deliver the current value for {repository_id}.",
                "current_bottleneck": "The current Mission is not complete.",
                "completion_definition": "The current gate has exact evidence.",
                "roadmap": {
                    "overall_position": "active delivery",
                    "current_block": "current Mission",
                    "next_gate": "exact Supervisor verdict",
                    "completion_definition": "The current gate has exact evidence.",
                    "completed_blocks": ["authority identified"],
                    "next_blocks": ["complete current Mission"],
                },
                "active_lanes": [lane],
                "lane_frontier_event_ids": {
                    lane: frontier_record["frontier_event_id"]
                },
                "cross_lane_conflicts": [],
                "decisions_since_prior": [],
                "evidence_manifest": [
                    {
                        "evidence_id": f"authority-{token}",
                        "kind": "authority_observation",
                        "locator": signal["root"],
                        "authority_role": "current_authority",
                        "sha256": signal["authority_fingerprint"],
                    }
                ],
                "omitted_evidence": [],
                "supersedes_context_event_ids": [],
                "recorded_at": "2026-08-02T00:00:00Z",
            },
        )
    return state


def bind_mission_to_frontier(mission_value: dict, signal: dict) -> dict:
    bound = copy.deepcopy(mission_value)
    token = bound["repository_id"].rsplit("/", 1)[-1]
    bound["value_contract"]["authority_fingerprint"] = signal[
        "authority_fingerprint"
    ]
    bound["active_artifact"] = {
        "artifact_id": f"artifact-{token}",
        "artifact_revision": "fixture-current",
        "artifact_sha256": loop.sha256_text(f"artifact-{token}"),
    }
    return bound


def mark_semantic_result_applied(
    scheduler: dict, action_id: str, result_id: str
) -> dict:
    lease = next(
        item for item in scheduler["route_leases"] if item["action_id"] == action_id
    )
    for state in (
        "result_received",
        "result_parsed",
        "result_validated",
        "result_applied",
    ):
        loop._set_external_lifecycle(  # noqa: SLF001 - scheduler unit boundary
            lease, state, details={"result_id": result_id}
        )
    return {
        "result_id": result_id,
        "source_thread_id": lease["recipient_thread_id"],
        "source_turn_id": f"turn-{result_id}",
        "source_message_id": f"message-{result_id}",
        "disposition": "accepted",
        "frontier_event_id": f"frontier-{result_id}",
        "frontier_epoch": 1,
        "authority_fingerprint": "f" * 64,
        "result_sha256": loop.sha256_text(result_id),
    }


def fixture_value_contract() -> dict:
    return {
        "contract_version": 1,
        "authority_source": "docs/CURRENT_HANDOFF.md",
        "authority_revision": "fixture-revision",
        "authority_fingerprint": "f" * 64,
        "authority_next_action": "Advance the existing fixture artifact.",
        "north_star": "Exercise deterministic Coordinator routing.",
        "current_bottleneck": "The existing fixture action has not run.",
        "current_gate": "Existing fixture action is pending.",
        "gate_delta": "Move the existing fixture action to its next state.",
        "expected_authority_state_after": "Existing fixture action is complete.",
        "expected_user_value": "The current fixture path is exercised.",
        "smallest_deliverable": "One existing fixture transition.",
        "next_consumer": "The deterministic scheduler assertion.",
        "reuse_or_integration": "Reuse the existing fixture Mission.",
        "existing_artifact_reused": True,
        "creates_new_artifact": False,
        "new_source_story_form_or_candidate": False,
        "advances_current_next_action": True,
        "adoption_test": "The expected scheduler action is selected.",
        "kill_condition": "Stop if a new artifact would be needed.",
        "objective_fit": "direct",
        "work_class": "quick_win",
        "max_worker_turns": 1,
        "genre_or_domain_shift": False,
        "out_of_scope": ["new artifacts"],
    }


def mission(
    repository_id: str,
    state: str,
    *,
    mission_id: str,
    priority: str = "ordinary",
    lane: str = "default",
    include_value_contract: bool = True,
) -> dict:
    suffix = repository_id.rsplit("/", 1)[-1]
    item = {
        "schema_version": 2,
        "repository_id": repository_id,
        "launch_set_id": "global-coordinator",
        "mission_id": mission_id,
        "attempt_id": "attempt-1",
        "worker_task_id": f"worker-{suffix}",
        "host_id": HOST_ID,
        "supervisor_thread_id": f"supervisor-{suffix}-{lane}",
        "supervision_lane": lane,
        "mode": "coordinator",
        "state": state,
        "mission_status": "running",
        "review_status": "pending",
        "external_effects": loop.default_external_effects(),
        "dispatch_keys": [],
        "returned_report_hashes": [],
        "completed_worker_turns": 0,
        "safety_ceiling": 8,
        "priority": priority,
        "events": [],
    }
    if include_value_contract:
        item["value_contract"] = fixture_value_contract()
    return item


def review_card(project_name: str = "Context A") -> dict:
    return {
        "project_name": project_name,
        "purpose": "Review one exact artifact at its stage gate.",
        "review_policy": {
            "gate": "required",
            "depth": "standard",
            "stage": "artifact-checkpoint",
        },
        "artifact": {"artifact_id": "artifact-1", "sha256": "a" * 64},
        "review_entry": "X:\\fixtures\\review\\index.html",
        "criteria": [
            {"key": "intent", "question": "Does the artifact match the intent?"},
            {"key": "clarity", "question": "Is the result clear without context?"},
        ],
        "reply_contract": {"accept": "accept", "reject": "give the reason"},
        "post_reply_behavior": (
            "The Coordinator routes the reply to the exact Supervisor and resumes "
            "only this Mission."
        ),
        "non_escalation_boundary": "No publish, release, or Git authority is granted.",
        "owner": "User",
        "state": "Waiting for artifact review.",
    }


def blocked_contract(blocker_id: str = "runtime-helper") -> dict:
    return {
        "contract_version": 2,
        "blocker_id": blocker_id,
        "introduced_by": {
            "event": "supervisor-verdict",
            "at": "2026-08-01T00:00:00Z",
            "evidence": "X:\\fixtures\\events\\supervisor-verdict.json",
        },
        "requirement": "A neutral process must exit successfully.",
        "rationale": "The Worker cannot start the required runtime before this gate.",
        "qualifies_when": ["The allowlisted neutral probe exits 0."],
        "does_not_qualify": ["A text-only continue request."],
        "diagnostics_completed": ["Read-only path inspection passed."],
        "owner": "runtime owner",
        "next_permitted_probe": "Run the registered neutral probe once after a changed signal.",
        "retry_policy": "on_changed_satisfied_signal",
        "input_route": {
            "destination": "Coordinator direction update",
            "format": "probe result and exact evidence path",
        },
        "baseline_observation_fingerprint": "b" * 64,
    }


class CoordinatorOnlyUxTests(unittest.TestCase):
    def test_T27_user_facing_codex_entry_point_is_exactly_one(self) -> None:
        _, _, _, coordinator = fixture()
        self.assertEqual(loop.USER_VISIBLE_CODEX_ENTRY_POINTS, 1)
        loop.validate_coordinator_state(coordinator)
        interface = (SKILL_ROOT / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            f'default_prompt: "{loop.COORDINATOR_PROMPT_THIS_REPOSITORY}"',
            interface,
        )
        self.assertNotIn("repository alias", interface.casefold())

    def test_T28_existing_repository_worker_auto_binds_without_user_input(self) -> None:
        registry, hosts, adapter, _ = fixture(include_workers=False)
        adapter["threads"].append(
            worker_thread(REPO_A, ROOT_A, "existing-worker")
        )
        result = loop.ensure_worker_binding(
            REPO_A,
            HOST_ID,
            ROOT_A,
            registry,
            hosts["hosts"][0],
            adapter,
            [],
            inspect=inspector,
        )
        self.assertEqual(
            result["classification"], "WORKER_AUTO_DISCOVERED_AND_BOUND"
        )
        self.assertFalse(result["user_input_required"])
        self.assertEqual(
            registry["worker_bindings"][0]["worker_task_id"],
            "existing-worker",
        )

    def test_T29_missing_worker_is_auto_created_once_when_capable(self) -> None:
        registry, hosts, adapter, _ = fixture(include_workers=False)
        created: list[tuple[str, str, str]] = []

        def create(repository_id: str, host_id: str, root: str) -> dict:
            created.append((repository_id, host_id, root))
            return worker_thread(repository_id, root, "created-worker")

        result = loop.ensure_worker_binding(
            REPO_A,
            HOST_ID,
            ROOT_A,
            registry,
            hosts["hosts"][0],
            adapter,
            [],
            inspect=inspector,
            create_worker=create,
        )
        self.assertEqual(result["classification"], "WORKER_AUTO_CREATED_AND_BOUND")
        self.assertEqual(result["worker_tasks_created"], 1)
        self.assertEqual(len(created), 1)
        self.assertEqual(len(registry["worker_bindings"]), 1)

    def test_T30_coordinator_sends_created_worker_bootstrap(self) -> None:
        registry, hosts, adapter, _ = fixture(include_workers=False)
        sent: list[tuple[str, dict]] = []

        result = loop.ensure_worker_binding(
            REPO_A,
            HOST_ID,
            ROOT_A,
            registry,
            hosts["hosts"][0],
            adapter,
            [],
            inspect=inspector,
            create_worker=lambda repository_id, _host_id, root: worker_thread(
                repository_id, root, "created-worker"
            ),
            send_message=lambda task_id, packet: sent.append((task_id, packet)),
        )
        self.assertTrue(result["bootstrap_sent_by_coordinator"])
        self.assertEqual(len(sent), 1)
        self.assertFalse(sent[0][1]["user_input_required"])
        self.assertTrue(sent[0][1]["persistent_worker"])

    def test_T31_same_generic_prompt_resolves_two_remote_contexts(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        outputs = [
            loop.resolve_coordinator_target(
                "this-repository",
                context={"invocation_git_root": root},
                registry=copy.deepcopy(registry),
                hosts=copy.deepcopy(hosts),
                adapter=copy.deepcopy(adapter),
                coordinator_state=copy.deepcopy(coordinator),
                missions=[],
                inspect=inspector,
            )
            for root in (ROOT_A, ROOT_B)
        ]
        self.assertEqual([item["repository_id"] for item in outputs], [REPO_A, REPO_B])
        self.assertEqual(
            {item["generic_prompt"] for item in outputs},
            {loop.COORDINATOR_PROMPT_THIS_REPOSITORY},
        )

    def test_T32_generic_prompts_contain_no_repository_specific_name(self) -> None:
        prompts = (
            loop.COORDINATOR_PROMPT_THIS_REPOSITORY,
            loop.COORDINATOR_PROMPT_NEXT_ACTIONABLE,
        )
        for prompt in prompts:
            self.assertNotIn("context-a", prompt.casefold())
            self.assertNotIn("context-b", prompt.casefold())
            self.assertNotIn("github.com", prompt.casefold())

    def test_T33_global_queue_uses_stable_priority(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        missions = [
            mission(REPO_A, "TRIGGERED", mission_id="ordinary"),
            mission(
                REPO_B,
                "SUPERVISOR_ADJUDICATION_REQUESTED",
                mission_id="verdict",
            ),
        ]
        result = loop.select_next_actionable_repository(
            registry, hosts, adapter, missions, coordinator
        )
        self.assertEqual(result["repository_id"], REPO_B)
        self.assertEqual(result["selection_priority"], 2)
        self.assertEqual(result["selection_reason"], "supervisor_verdict_waiting")
        resumable = mission(
            REPO_A,
            "USER_DECISION",
            mission_id="resumable",
        )
        coordinator["pending_user_responses"] = [
            {
                "repository_id": REPO_A,
                "mission_id": "resumable",
                "attempt_id": "attempt-1",
            }
        ]
        resumed = loop.select_next_actionable_repository(
            registry,
            hosts,
            adapter,
            [*missions, resumable],
            coordinator,
        )
        self.assertEqual(resumed["repository_id"], REPO_A)
        self.assertEqual(resumed["selection_priority"], 1)

    def test_T34_freeform_user_response_routes_only_to_supervisor(self) -> None:
        item = mission(REPO_A, "USER_DECISION", mission_id="review")
        result = loop.normalize_user_response(item, "Keep the bounded repair.")
        self.assertEqual(result["recipient_kind"], "web_supervisor")
        self.assertEqual(
            result["recipient_thread_id"], item["supervisor_thread_id"]
        )
        self.assertEqual(
            result["prohibited_recipient_worker_task_id"],
            item["worker_task_id"],
        )
        self.assertEqual(
            result["packet"]["raw_user_response"],
            "Keep the bounded repair.",
        )

    def test_T35_no_next_mission_before_supervisor_verdict(self) -> None:
        item = mission(REPO_A, "WORKER_RESULT_RECEIVED", mission_id="review")
        with self.assertRaisesRegex(loop.ProtocolError, "prior CONTINUE"):
            loop.start_continuation(
                item,
                {"mission_id": "next", "attempt_id": "attempt-2"},
            )

    def test_T36_user_document_has_no_direct_endpoint_write_steps(self) -> None:
        text = (
            SKILL_ROOT / "references" / "user-operation.md"
        ).read_text(encoding="utf-8")
        forbidden = (
            "paste the Worker Report",
            "paste the Supervisor Prompt",
            "write to the Supervisor",
            "write to the Worker",
            "bootstrap Prompt",
        )
        for phrase in forbidden:
            self.assertNotIn(phrase.casefold(), text.casefold())

    def test_T37_user_document_has_no_repository_replacement_example(self) -> None:
        text = (
            SKILL_ROOT / "references" / "user-operation.md"
        ).read_text(encoding="utf-8")
        forbidden = (
            "<REPOSITORY>",
            "NLMYTGen",
            "ClipPipeGen",
            "Fast Fiction Factory",
            "LOWPASS",
            "retro-character-lab",
            "CodexGameAssetWorkbench",
            "absolute path",
        )
        for phrase in forbidden:
            self.assertNotIn(phrase.casefold(), text.casefold())

    def test_T38_multiple_worker_candidates_stop_for_minimal_decision(self) -> None:
        registry, hosts, adapter, _ = fixture(include_workers=False)
        adapter["threads"].extend(
            [
                worker_thread(REPO_A, ROOT_A, "candidate-1"),
                worker_thread(REPO_A, ROOT_A, "candidate-2"),
            ]
        )
        result = loop.ensure_worker_binding(
            REPO_A,
            HOST_ID,
            ROOT_A,
            registry,
            hosts["hosts"][0],
            adapter,
            [],
            inspect=inspector,
        )
        self.assertEqual(
            result["classification"], "USER_DECISION_WORKER_CANDIDATES"
        )
        self.assertEqual(result["terminal_route"], "USER_DECISION")
        self.assertFalse(result["ask_for_task_id"])

    def test_T39_unique_worker_candidate_binds_without_question(self) -> None:
        registry, hosts, adapter, _ = fixture(include_workers=False)
        adapter["threads"].append(worker_thread(REPO_A, ROOT_A, "candidate-1"))
        result = loop.ensure_worker_binding(
            REPO_A,
            HOST_ID,
            ROOT_A,
            registry,
            hosts["hosts"][0],
            adapter,
            [],
            inspect=inspector,
        )
        self.assertEqual(result["terminal_route"], None)
        self.assertFalse(result["user_input_required"])

    def test_T40_only_missing_supervisor_emits_supervisor_user_action(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        binding = next(
            item
            for item in registry["supervisor_bindings"]
            if item["repository_id"] == REPO_A
        )
        binding["supervisor_thread_id"] = None
        binding["binding_status"] = "needs_verification"
        adapter["threads"] = [
            item
            for item in adapter["threads"]
            if item.get("id") != "supervisor-context-a-default"
        ]
        result = loop.resolve_coordinator_target(
            "this-repository",
            context={"invocation_git_root": ROOT_A},
            registry=registry,
            hosts=hosts,
            adapter=adapter,
            coordinator_state=coordinator,
            missions=[],
            inspect=inspector,
        )
        self.assertEqual(
            result["classification"],
            "USER_ACTION_CREATE_OR_BIND_SUPERVISOR_CHAT",
        )
        self.assertEqual(result["terminal_route"], "USER_ACTION")

    def test_T41_one_coordinator_keeps_two_repository_packets_separate(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        results = [
            loop.resolve_coordinator_target(
                "this-repository",
                context={"invocation_git_root": root},
                registry=copy.deepcopy(registry),
                hosts=copy.deepcopy(hosts),
                adapter=copy.deepcopy(adapter),
                coordinator_state=coordinator,
                missions=[],
                inspect=inspector,
            )
            for root in (ROOT_A, ROOT_B)
        ]
        first = mission(REPO_A, "USER_ACTION", mission_id="mission-a")
        second = mission(REPO_B, "USER_DECISION", mission_id="mission-b")
        packets = [
            loop.normalize_user_response(first, "response-a"),
            loop.normalize_user_response(second, "response-b"),
        ]
        self.assertNotEqual(results[0]["worker_task_id"], results[1]["worker_task_id"])
        self.assertEqual(packets[0]["packet"]["repository_id"], REPO_A)
        self.assertEqual(packets[1]["packet"]["repository_id"], REPO_B)
        self.assertNotEqual(
            packets[0]["recipient_thread_id"], packets[1]["recipient_thread_id"]
        )

    def test_T42_active_mission_lane_or_default_without_similar_fallback(self) -> None:
        registry, _, _, _ = fixture()
        registry["supervisor_bindings"].append(supervisor(REPO_A, "alternate"))
        lane, _ = loop.select_lane_for_context(
            registry,
            REPO_A,
            [mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="active", lane="alternate")],
        )
        self.assertEqual(lane, "alternate")
        invalid_lane, candidates = loop.select_lane_for_context(
            registry,
            REPO_A,
            [mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="active", lane="similar")],
        )
        self.assertIsNone(invalid_lane)
        self.assertEqual(candidates, ["alternate", "default"])

    def test_T43_terminal_card_routes_to_exact_individual_resume_e2e(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        gated = mission(
            REPO_A,
            "SUPERVISOR_ADJUDICATION_REQUESTED",
            mission_id="artifact-review",
        )
        gated["review_policy"] = {
            "gate": "required",
            "depth": "standard",
            "stage": "artifact-checkpoint",
        }
        other = mission(REPO_B, "WORK_ORDER_RECEIVED", mission_id="other-running")

        loop.apply_supervisor_verdict(
            gated, "user_decision", user_packet=review_card()
        )
        with tempfile.TemporaryDirectory() as temp:
            emitted = loop.emit_terminal_packet(
                gated,
                terminal_dir=Path(temp) / "packets",
                ledger_path=Path(temp) / "ledger.json",
                notifier=None,
            )
            self.assertTrue(Path(emitted["packet_path"]).is_file())

        snapshot = loop.build_coordinator_snapshot([gated, other], coordinator)
        project_states = {
            item["repository_id"]: item["run_state"]
            for item in snapshot["project_states"]
        }
        self.assertEqual(snapshot["global_state"], "RUNNING")
        self.assertFalse(snapshot["global_completion_barrier"])
        self.assertEqual(project_states[REPO_A], "parked_for_user")
        self.assertEqual(project_states[REPO_B], "running")
        self.assertEqual(snapshot["next_user_card"]["repository_id"], REPO_A)

        while_parked = loop.select_next_actionable_repository(
            registry, hosts, adapter, [gated, other], coordinator
        )
        self.assertEqual(while_parked["repository_id"], REPO_B)

        queued = loop.queue_user_response(
            gated, coordinator, "Accept this exact artifact."
        )
        self.assertEqual(queued["recipient_thread_id"], gated["supervisor_thread_id"])
        self.assertEqual(len(coordinator["pending_user_responses"]), 1)
        resumed = loop.select_next_actionable_repository(
            registry, hosts, adapter, [gated, other], coordinator
        )
        self.assertEqual(resumed["repository_id"], REPO_A)
        self.assertEqual(resumed["selection_priority"], 1)

        routed = loop.acknowledge_user_response_routed(
            gated, coordinator, queued["response_id"]
        )
        self.assertEqual(
            routed["recipient_thread_id"], gated["supervisor_thread_id"]
        )
        self.assertEqual(gated["state"], "USER_DECISION")
        self.assertFalse(routed["semantic_result_applied"])
        awaiting_result = loop.select_next_actionable_repository(
            registry, hosts, adapter, [gated, other], coordinator
        )
        self.assertEqual(awaiting_result["repository_id"], REPO_A)
        self.assertEqual(awaiting_result["selection_priority"], 1)

        gated["last_routed_user_response_id"] = queued["response_id"]
        loop._record_state(  # noqa: SLF001 - synthetic semantic-result boundary
            gated,
            loop.USER_RESPONSE_ADJUDICATION_STATE,
            {"response_id": queued["response_id"], "result_received": True},
        )
        loop.apply_supervisor_verdict(gated, "accept")
        self.assertEqual(gated["state"], "COMPLETE")
        self.assertEqual(gated["review_status"], "accepted")
        self.assertEqual(other["state"], "WORK_ORDER_RECEIVED")

    def test_T44_review_depth_does_not_create_a_global_gate(self) -> None:
        base = {
            "repository_id": REPO_A,
            "launch_set_id": "global-coordinator",
            "mission_id": "policy-check",
            "attempt_id": "attempt-1",
            "worker_task_id": "worker-context-a",
            "host_id": HOST_ID,
            "supervisor_thread_id": "supervisor-context-a-default",
            "supervision_lane": "default",
        }
        deep_supervisor_review = loop.new_mission(
            {
                **base,
                "review_policy": {
                    "gate": "none",
                    "depth": "deep",
                    "stage": "technical-proof",
                },
            }
        )
        light_user_gate = loop.new_mission(
            {
                **base,
                "mission_id": "light-checkpoint",
                "review_policy": {
                    "gate": "required",
                    "depth": "light",
                    "stage": "direction-choice",
                },
            }
        )
        self.assertEqual(deep_supervisor_review["review_status"], "not_required")
        self.assertEqual(deep_supervisor_review["review_policy"]["depth"], "deep")
        self.assertEqual(light_user_gate["review_status"], "pending")
        self.assertEqual(light_user_gate["review_policy"]["depth"], "light")

    def test_T45_multiple_reviews_are_presented_one_at_a_time(self) -> None:
        _, _, _, coordinator = fixture()
        first = mission(REPO_A, "USER_DECISION", mission_id="review-a")
        first["user_packet"] = review_card("Context A")
        first["review_policy"] = first["user_packet"]["review_policy"]
        second = mission(REPO_B, "USER_DECISION", mission_id="review-b")
        second["user_packet"] = review_card("Context B")
        second["review_policy"] = second["user_packet"]["review_policy"]
        snapshot = loop.build_coordinator_snapshot([first, second], coordinator)
        self.assertEqual(snapshot["queued_user_card_count"], 2)
        self.assertEqual(snapshot["remaining_user_card_count"], 1)
        self.assertEqual(snapshot["presentation"], "one_card_at_a_time")
        self.assertEqual(snapshot["next_user_card"]["repository_id"], REPO_A)

    def test_T46_cli_persists_queue_then_exact_route_acknowledgement(self) -> None:
        _, _, _, coordinator = fixture()
        gated = mission(REPO_A, "USER_DECISION", mission_id="cli-review")
        gated["user_packet"] = review_card()
        gated["review_policy"] = gated["user_packet"]["review_policy"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mission_path = root / "mission.json"
            coordinator_path = root / "coordinator.json"
            loop.atomic_write_json(mission_path, gated)
            loop.atomic_write_json(coordinator_path, coordinator)
            with redirect_stdout(io.StringIO()):
                route_code = loop.main(
                    [
                        "route-user-response",
                        "--mission",
                        str(mission_path),
                        "--coordinator-state",
                        str(coordinator_path),
                        "--response",
                        "Accept the exact artifact.",
                    ]
                )
            self.assertEqual(route_code, 0)
            queued_state = loop.load_json(coordinator_path)
            self.assertEqual(len(queued_state["pending_user_responses"]), 1)
            response_id = queued_state["pending_user_responses"][0]["response_id"]
            with redirect_stdout(io.StringIO()):
                ack_code = loop.main(
                    [
                        "ack-user-response",
                        "--mission",
                        str(mission_path),
                        "--coordinator-state",
                        str(coordinator_path),
                        "--response-id",
                        response_id,
                    ]
                )
            self.assertEqual(ack_code, 0)
            routed_mission = loop.load_json(mission_path)
            routed_state = loop.load_json(coordinator_path)
            self.assertEqual(
                routed_mission["state"], "USER_DECISION"
            )
            self.assertEqual(
                routed_state["pending_user_responses"][0]["recipient_thread_id"],
                gated["supervisor_thread_id"],
            )
            self.assertEqual(
                routed_state["pending_user_responses"][0]["response_state"],
                "delivery_acknowledged",
            )
            self.assertEqual(routed_state.get("routed_user_responses", []), [])

    def test_T47_consumed_continuation_is_not_reselected(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        prior = mission(REPO_A, "CONTINUE", mission_id="repair")
        prior["next_work_order"] = {
            "mission_id": "repair",
            "attempt_id": "attempt-2",
        }
        successor = mission(REPO_A, "COMPLETE", mission_id="repair")
        successor["attempt_id"] = "attempt-2"
        current = mission(REPO_B, "WORK_ORDER_RECEIVED", mission_id="current")
        selected = loop.select_next_actionable_repository(
            registry,
            hosts,
            adapter,
            [prior, successor, current],
            coordinator,
        )
        self.assertEqual(selected["repository_id"], REPO_B)
        snapshot = loop.build_coordinator_snapshot(
            [prior, successor, current], coordinator
        )
        states = {
            item["repository_id"]: item for item in snapshot["project_states"]
        }
        self.assertEqual(states[REPO_A]["running_mission_count"], 0)

    def test_T48_all_current_terminals_use_the_same_actionable_plan(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        completed_a = mission(REPO_A, "COMPLETE", mission_id="complete-a")
        completed_b = mission(REPO_B, "COMPLETE", mission_id="complete-b")
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [completed_a, completed_b],
            coordinator,
            loop.default_scheduler_state(),
        )
        self.assertEqual(plan["global_state"], "READY")
        self.assertEqual(plan["execution_state"], "READY")
        self.assertTrue(plan["all_current_missions_terminal"])
        self.assertTrue(plan["cycle_should_continue_now"])
        self.assertFalse(plan["cycle_checkpoint_allowed"])
        self.assertFalse(plan["has_inflight_work"])
        self.assertEqual(plan["next_action"]["kind"], "request_next_mission")
        self.assertFalse(plan["watchdog_should_be_armed"])
        self.assertFalse(plan["cycle_should_rearm"])

    def test_T49_dispatch_ready_is_not_inflight_until_exact_send(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="dispatch")
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )

        self.assertEqual(plan["next_action"]["kind"], "dispatch_work_order")
        self.assertEqual(plan["global_state"], "READY")
        self.assertEqual(plan["execution_state"], "READY")
        self.assertFalse(plan["has_inflight_work"])
        self.assertTrue(plan["cycle_should_continue_now"])
        self.assertFalse(plan["cycle_checkpoint_allowed"])

        action_id = plan["next_action"]["action_id"]
        claimed = loop.claim_coordinator_action(scheduler, plan, action_id)
        self.assertEqual(claimed["classification"], "COORDINATOR_ACTION_CLAIMED")
        claimed_plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        self.assertEqual(
            claimed_plan["scheduler_claim"]["action_id"], action_id
        )
        self.assertEqual(claimed_plan["active_routes"], [])
        self.assertEqual(claimed_plan["global_state"], "RUNNING")
        self.assertEqual(claimed_plan["execution_state"], "DRAINING")
        self.assertFalse(claimed_plan["has_inflight_work"])
        self.assertTrue(claimed_plan["watchdog_should_be_armed"])

        recipient = claimed["action"]["payload"]["route"][
            "recipient_thread_id"
        ]
        loop.prepare_coordinator_action_delivery(
            scheduler, action_id, recipient, "a" * 64
        )
        loop.mark_coordinator_action_sent(
            scheduler, action_id, recipient, packet_sha256="a" * 64
        )
        waiting_plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        self.assertTrue(waiting_plan["has_inflight_work"])
        self.assertFalse(waiting_plan["cycle_checkpoint_allowed"])

    def test_T50_successor_requests_are_once_per_frontier_and_fair(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        missions = [
            mission(REPO_A, "COMPLETE", mission_id="complete-a"),
            mission(REPO_B, "COMPLETE", mission_id="complete-b"),
        ]
        scheduler = loop.default_scheduler_state()

        first = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        self.assertEqual(
            first["next_action"]["payload"]["selection"]["repository_id"],
            REPO_A,
        )
        first_id = first["next_action"]["action_id"]
        first_claim = loop.claim_coordinator_action(scheduler, first, first_id)
        first_recipient = first_claim["action"]["payload"]["route"][
            "recipient_thread_id"
        ]
        loop.prepare_coordinator_action_delivery(
            scheduler, first_id, first_recipient, "a" * 64
        )
        loop.mark_coordinator_action_sent(
            scheduler, first_id, first_recipient, packet_sha256="a" * 64
        )
        evidence_a = mark_semantic_result_applied(
            scheduler, first_id, "supervisor-result-a"
        )
        loop.complete_coordinator_action(
            scheduler, first_id, "no_work", evidence=evidence_a
        )

        second = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        self.assertEqual(
            second["next_action"]["payload"]["selection"]["repository_id"],
            REPO_B,
        )
        second_id = second["next_action"]["action_id"]
        second_claim = loop.claim_coordinator_action(scheduler, second, second_id)
        second_recipient = second_claim["action"]["payload"]["route"][
            "recipient_thread_id"
        ]
        loop.prepare_coordinator_action_delivery(
            scheduler, second_id, second_recipient, "b" * 64
        )
        loop.mark_coordinator_action_sent(
            scheduler, second_id, second_recipient, packet_sha256="b" * 64
        )
        evidence_b = mark_semantic_result_applied(
            scheduler, second_id, "supervisor-result-b"
        )
        loop.complete_coordinator_action(
            scheduler, second_id, "no_work", evidence=evidence_b
        )

        idle = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        self.assertFalse(idle["wake_required"])
        self.assertIsNone(idle["next_action"])
        self.assertTrue(idle["cycle_checkpoint_allowed"])
        self.assertFalse(idle["cycle_should_rearm"])

    def test_T51_blocked_recovery_retries_only_after_semantic_change(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        registry["repositories"][1]["allow_request_next_mission"] = False
        blocked = mission(REPO_A, "BLOCKED", mission_id="blocked")
        blocked["blocked_contract"] = blocked_contract()
        blocked["blocked_contract"]["observation"] = "failed"
        scheduler = loop.default_scheduler_state()
        first = loop.build_coordinator_plan(
            registry, hosts, adapter, [blocked], coordinator, scheduler
        )
        self.assertEqual(first["next_action"]["kind"], "inspect_blocked_recovery")
        action_id = first["next_action"]["action_id"]
        loop.claim_coordinator_action(scheduler, first, action_id)
        loop.complete_coordinator_action(scheduler, action_id, "unchanged")

        blocked["updated_at"] = "2099-01-01T00:00:00Z"
        unchanged = loop.build_coordinator_plan(
            registry, hosts, adapter, [blocked], coordinator, scheduler
        )
        self.assertFalse(unchanged["wake_required"])

        blocked["blocked_contract"]["observation"] = "ready"
        changed = loop.build_coordinator_plan(
            registry, hosts, adapter, [blocked], coordinator, scheduler
        )
        self.assertTrue(changed["wake_required"])
        self.assertEqual(changed["next_action"]["kind"], "inspect_blocked_recovery")
        self.assertNotEqual(changed["next_action"]["action_id"], action_id)

    def test_T52_same_user_card_is_presented_once(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        registry["repositories"][1]["allow_request_next_mission"] = False
        gated = mission(REPO_A, "USER_DECISION", mission_id="review")
        gated["user_packet"] = review_card()
        gated["review_policy"] = gated["user_packet"]["review_policy"]
        scheduler = loop.default_scheduler_state()

        first = loop.build_coordinator_plan(
            registry, hosts, adapter, [gated], coordinator, scheduler
        )
        self.assertEqual(first["next_action"]["kind"], "present_user_card")
        action_id = first["next_action"]["action_id"]
        loop.claim_coordinator_action(scheduler, first, action_id)
        loop.complete_coordinator_action(scheduler, action_id, "presented")
        waiting = loop.build_coordinator_plan(
            registry, hosts, adapter, [gated], coordinator, scheduler
        )
        self.assertFalse(waiting["wake_required"])
        self.assertEqual(waiting["global_state"], "AWAITING_USER_ONLY")

    def test_T53_authority_content_change_wakes_once(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        registry["repositories"][0]["authority_watch"] = ["AUTHORITY.md"]
        registry["repositories"][1]["allow_request_next_mission"] = False
        completed = mission(REPO_A, "COMPLETE", mission_id="complete")
        scheduler = loop.default_scheduler_state()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            authority = root / "AUTHORITY.md"
            authority.write_text("revision one\n", encoding="utf-8")
            hosts["hosts"][0]["known_repository_roots"][REPO_A] = str(root)

            signals = loop.collect_authority_signals(registry, hosts, adapter)
            first = loop.build_coordinator_plan(
                registry,
                hosts,
                adapter,
                [completed],
                coordinator,
                scheduler,
                authority_signals=signals,
            )
            self.assertEqual(
                first["next_action"]["kind"], "reconcile_repository_authority"
            )
            action_id = first["next_action"]["action_id"]
            loop.claim_coordinator_action(scheduler, first, action_id)
            loop.complete_coordinator_action(scheduler, action_id, "reconciled")

            same = loop.build_coordinator_plan(
                registry,
                hosts,
                adapter,
                [completed],
                coordinator,
                scheduler,
                authority_signals=loop.collect_authority_signals(
                    registry, hosts, adapter
                ),
            )
            self.assertNotEqual(
                same["next_action"]["kind"], "reconcile_repository_authority"
            )

            authority.write_text("revision two\n", encoding="utf-8")
            changed = loop.build_coordinator_plan(
                registry,
                hosts,
                adapter,
                [completed],
                coordinator,
                scheduler,
                authority_signals=loop.collect_authority_signals(
                    registry, hosts, adapter
                ),
            )
            self.assertEqual(
                changed["next_action"]["kind"], "reconcile_repository_authority"
            )
            self.assertNotEqual(changed["next_action"]["action_id"], action_id)

    def test_T54_status_and_plan_cli_share_the_same_decision(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        scheduler = loop.default_scheduler_state()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materialize_git_fixture_roots(
                root / "repositories", registry, hosts, adapter
            )
            paths = {
                "registry": root / "registry.json",
                "hosts": root / "hosts.json",
                "adapter": root / "adapter.json",
                "coordinator": root / "coordinator.json",
                "scheduler": root / "scheduler.json",
                "missions": root / "missions",
            }
            paths["missions"].mkdir()
            for name, value in (
                ("registry", registry),
                ("hosts", hosts),
                ("adapter", adapter),
                ("coordinator", coordinator),
                ("scheduler", scheduler),
            ):
                loop.atomic_write_json(paths[name], value)
            loop.atomic_write_json(
                paths["missions"] / "complete.json",
                mission(REPO_A, "COMPLETE", mission_id="complete"),
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
            ]
            status_out = io.StringIO()
            with redirect_stdout(status_out):
                self.assertEqual(loop.main(["coordinator-status", *common]), 0)
            plan_out = io.StringIO()
            with redirect_stdout(plan_out):
                self.assertEqual(loop.main(["coordinator-plan", *common]), 0)
            status = json.loads(status_out.getvalue())
            plan = json.loads(plan_out.getvalue())
            self.assertEqual(status["state_fingerprint"], plan["state_fingerprint"])
            self.assertEqual(
                status["next_action"]["action_id"],
                plan["next_action"]["action_id"],
            )

    def test_T55_no_action_is_idle_checkpoint_not_complete(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        for item in registry["repositories"]:
            item["allow_request_next_mission"] = False
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [mission(REPO_A, "COMPLETE", mission_id="complete")],
            coordinator,
            loop.default_scheduler_state(),
        )
        self.assertFalse(plan["wake_required"])
        self.assertEqual(plan["execution_state"], "IDLE")
        self.assertTrue(plan["cycle_checkpoint_allowed"])
        selected = loop.select_next_actionable_repository(
            registry, hosts, adapter, [], coordinator
        )
        self.assertEqual(selected["coordinator_outcome"], "IDLE_CHECKPOINT")
        self.assertIsNone(selected["terminal_route"])

    def test_T56_timestamp_only_changes_do_not_change_action_identity(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        completed = mission(REPO_A, "COMPLETE", mission_id="complete")
        first = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [completed],
            coordinator,
            loop.default_scheduler_state(),
        )
        completed["updated_at"] = "2099-01-01T00:00:00Z"
        second = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [completed],
            coordinator,
            loop.default_scheduler_state(),
        )
        self.assertEqual(first["state_fingerprint"], second["state_fingerprint"])
        self.assertEqual(
            first["next_action"]["action_id"], second["next_action"]["action_id"]
        )

    def test_T57_released_claim_is_retryable_without_duplicate_send(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        completed = mission(REPO_A, "COMPLETE", mission_id="complete")
        scheduler = loop.default_scheduler_state()
        first = loop.build_coordinator_plan(
            registry, hosts, adapter, [completed], coordinator, scheduler
        )
        action_id = first["next_action"]["action_id"]
        loop.claim_coordinator_action(scheduler, first, action_id)
        loop.release_coordinator_action(scheduler, action_id, "send failed")
        retry = loop.build_coordinator_plan(
            registry, hosts, adapter, [completed], coordinator, scheduler
        )
        self.assertEqual(retry["next_action"]["action_id"], action_id)

    def test_T58_blocked_verdict_persists_recovery_contract(self) -> None:
        blocked = mission(
            REPO_A,
            "SUPERVISOR_ADJUDICATION_REQUESTED",
            mission_id="blocked-contract",
        )
        contract = blocked_contract()
        contract["recovery_probe_id"] = "neutral-process-and-runtime-doctor"
        loop.apply_supervisor_verdict(
            blocked, "blocked", user_packet=contract
        )
        self.assertEqual(blocked["state"], "BLOCKED")
        self.assertEqual(blocked["blocked_contract"], contract)

    def test_T59_direction_event_routes_exactly_without_parking_other_work(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        other = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="other")
        queued = loop.queue_coordinator_event(
            coordinator,
            kind="direction_update",
            repository_id=REPO_B,
            raw_text="Prioritize the new visual direction.",
        )
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [other], coordinator, scheduler
        )
        self.assertEqual(plan["next_action"]["kind"], "route_direction_update")
        action_id = plan["next_action"]["action_id"]
        recipient = plan["next_action"]["payload"]["route"][
            "recipient_thread_id"
        ]
        self.assertEqual(recipient, supervisor(REPO_B)["supervisor_thread_id"])
        loop.claim_coordinator_action(scheduler, plan, action_id)
        loop.prepare_coordinator_action_delivery(
            scheduler, action_id, recipient, "c" * 64
        )
        loop.mark_coordinator_action_sent(
            scheduler, action_id, recipient, packet_sha256="c" * 64
        )
        loop.acknowledge_coordinator_event_routed(
            coordinator, queued["event_id"], recipient
        )
        loop.acknowledge_coordinator_action_delivery(
            scheduler, action_id, "supervisor-ack"
        )
        with self.assertRaisesRegex(loop.ProtocolError, "result_applied"):
            loop.complete_coordinator_action(
                scheduler, action_id, "routed", evidence="supervisor-ack"
            )
        resumed = loop.build_coordinator_plan(
            registry, hosts, adapter, [other], coordinator, scheduler
        )
        self.assertEqual(other["state"], "WORK_ORDER_RECEIVED")
        self.assertEqual(
            scheduler["route_leases"][0]["external_lifecycle_state"],
            "delivery_acknowledged",
        )
        self.assertTrue(resumed["has_inflight_work"])
        self.assertEqual(coordinator["pending_user_events"][0]["state"], "delivery_acknowledged")

    def test_T60_historical_blocked_does_not_poison_newer_complete_frontier(self) -> None:
        _, _, _, coordinator = fixture()
        old = mission(REPO_A, "BLOCKED", mission_id="old-blocker")
        old["updated_at"] = "2026-01-01T00:00:00Z"
        current = mission(REPO_A, "COMPLETE", mission_id="current")
        current["updated_at"] = "2026-02-01T00:00:00Z"
        snapshot = loop.build_coordinator_snapshot([old, current], coordinator)
        self.assertEqual(snapshot["project_states"][0]["run_state"], "complete")

    def test_T61_cli_persists_delivery_without_semantic_completion(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        scheduler = loop.default_scheduler_state()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materialize_git_fixture_roots(
                root / "repositories", registry, hosts, adapter
            )
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
            for name, value in (
                ("registry", registry),
                ("hosts", hosts),
                ("adapter", adapter),
                ("coordinator", coordinator),
                ("scheduler", scheduler),
            ):
                loop.atomic_write_json(paths[name], value)
            signals = loop.collect_authority_signals(registry, hosts, adapter)
            signal_by_repository = {
                item["repository_id"]: item for item in signals
            }
            frontier = certified_frontier_fixture(registry, signals)
            loop.atomic_write_json(paths["frontier"], frontier)
            loop.atomic_write_json(
                paths["project_context"],
                certified_project_context_fixture(registry, signals, frontier),
            )
            loop.atomic_write_json(
                paths["missions"] / "dispatch.json",
                bind_mission_to_frontier(
                    mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="dispatch"),
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

            plan_out = io.StringIO()
            with redirect_stdout(plan_out):
                self.assertEqual(loop.main(["coordinator-plan", *common]), 0)
            plan = json.loads(plan_out.getvalue())
            action_id = plan["next_action"]["action_id"]
            recipient = plan["next_action"]["payload"]["route"][
                "recipient_thread_id"
            ]

            with redirect_stdout(io.StringIO()):
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
                    0,
                )
                self.assertEqual(
                    loop.main(
                        [
                            "coordinator-action-sent",
                            "--action-id",
                            action_id,
                            "--recipient-thread-id",
                            recipient,
                            "--packet-sha256",
                            "a" * 64,
                            "--after-cursor",
                            "cursor-1",
                            "--scheduler-state",
                            str(paths["scheduler"]),
                            "--coordinator-state",
                            str(paths["coordinator"]),
                        ]
                    ),
                    0,
                )

            waiting = loop.load_scheduler_state(paths["scheduler"])
            self.assertIsNone(waiting["scheduler_claim"])
            self.assertEqual(len(waiting["route_leases"]), 1)
            waiting_lease = waiting["route_leases"][0]
            self.assertEqual(waiting_lease["status"], "waiting")
            self.assertEqual(
                waiting_lease["recipient_thread_id"], recipient
            )
            self.assertEqual(waiting_lease["after_cursor"], "cursor-1")

            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    loop.main(
                        [
                            "coordinator-action-delivery-ack",
                            "--action-id",
                            action_id,
                            "--delivery-ack-id",
                            "receipt-1",
                            "--scheduler-state",
                            str(paths["scheduler"]),
                            "--coordinator-state",
                            str(paths["coordinator"]),
                        ]
                    ),
                    0,
                )
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                self.assertEqual(
                    loop.main(
                        [
                            "coordinator-action-complete",
                            "--action-id",
                            action_id,
                            "--outcome",
                            "accepted",
                            "--evidence",
                            "receipt-1",
                            "--scheduler-state",
                            str(paths["scheduler"]),
                            "--coordinator-state",
                            str(paths["coordinator"]),
                        ]
                    ),
                    2,
                )
            self.assertIn("result_applied", stderr.getvalue())
            delivered = loop.load_scheduler_state(paths["scheduler"])
            self.assertIsNone(delivered["scheduler_claim"])
            self.assertEqual(len(delivered["route_leases"]), 1)
            self.assertEqual(delivered["completed_actions"], [])
            self.assertEqual(
                delivered["route_leases"][0]["external_lifecycle_state"],
                "delivery_acknowledged",
            )

    def test_T62_snapshot_availability_never_rearms_recovery(self) -> None:
        _, _, _, coordinator = fixture()
        snapshot = loop.build_coordinator_snapshot(
            [mission(REPO_A, "COMPLETE", mission_id="complete")], coordinator
        )
        self.assertEqual(snapshot["coordinator_lifecycle"], "AVAILABLE")
        self.assertEqual(snapshot["coordinator_availability"], "AVAILABLE")
        self.assertFalse(snapshot["cycle_should_rearm"])

    def test_T63_local_claim_never_arms_recovery_automation(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        registry["repositories"][1]["allow_request_next_mission"] = False
        blocked = mission(REPO_A, "BLOCKED", mission_id="blocked")
        blocked["blocked_contract"] = blocked_contract()
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [blocked], coordinator, scheduler
        )
        self.assertEqual(plan["next_action"]["kind"], "inspect_blocked_recovery")
        self.assertFalse(plan["next_action"]["requires_external_result"])
        loop.claim_coordinator_action(
            scheduler, plan, plan["next_action"]["action_id"]
        )
        claimed = loop.build_coordinator_plan(
            registry, hosts, adapter, [blocked], coordinator, scheduler
        )
        self.assertIsNotNone(claimed["scheduler_claim"])
        self.assertEqual(claimed["active_routes"], [])
        self.assertEqual(claimed["execution_state"], "DRAINING")
        self.assertFalse(claimed["has_inflight_work"])
        self.assertFalse(claimed["watchdog_should_be_armed"])
        self.assertTrue(claimed["watchdog_should_be_paused"])

    def test_T64_external_action_requires_send_and_result_evidence(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="dispatch")
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        action_id = plan["next_action"]["action_id"]
        claimed = loop.claim_coordinator_action(scheduler, plan, action_id)
        with self.assertRaisesRegex(loop.ProtocolError, "before exact send"):
            loop.complete_coordinator_action(
                scheduler, action_id, "accepted", evidence="result"
            )
        recipient = claimed["action"]["payload"]["route"][
            "recipient_thread_id"
        ]
        with self.assertRaisesRegex(loop.ProtocolError, "prepared before send"):
            loop.mark_coordinator_action_sent(
                scheduler, action_id, recipient, packet_sha256="d" * 64
            )
        with self.assertRaisesRegex(loop.ProtocolError, "packet_sha256"):
            loop.prepare_coordinator_action_delivery(
                scheduler, action_id, recipient, "not-a-digest"
            )
        prepared = loop.prepare_coordinator_action_delivery(
            scheduler, action_id, recipient, "d" * 64
        )
        self.assertEqual(prepared["envelope"]["action_id"], action_id)
        repeated = loop.prepare_coordinator_action_delivery(
            scheduler, action_id, recipient, "d" * 64
        )
        self.assertTrue(repeated["deduplicated"])
        loop.mark_coordinator_action_sent(
            scheduler, action_id, recipient, packet_sha256="d" * 64
        )
        with self.assertRaisesRegex(loop.ProtocolError, "result_applied"):
            loop.complete_coordinator_action(scheduler, action_id, "accepted")
        with self.assertRaisesRegex(loop.ProtocolError, "result_applied"):
            loop.complete_coordinator_action(
                scheduler,
                action_id,
                "accepted",
                evidence="worker-result-receipt",
            )
        result_evidence = mark_semantic_result_applied(
            scheduler, action_id, "worker-result-receipt"
        )
        loop.complete_coordinator_action(
            scheduler, action_id, "accepted", evidence=result_evidence
        )

    def test_T65_sent_action_cannot_be_released_for_duplicate_retry(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="dispatch")
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        action_id = plan["next_action"]["action_id"]
        claimed = loop.claim_coordinator_action(scheduler, plan, action_id)
        recipient = claimed["action"]["payload"]["route"][
            "recipient_thread_id"
        ]
        loop.prepare_coordinator_action_delivery(
            scheduler, action_id, recipient, "e" * 64
        )
        loop.mark_coordinator_action_sent(
            scheduler, action_id, recipient, packet_sha256="e" * 64
        )
        with self.assertRaisesRegex(loop.ProtocolError, "must be reconciled"):
            loop.release_coordinator_action(scheduler, action_id, "wait failed")

    def test_T66_local_action_cannot_create_a_false_outbound_wait(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        registry["repositories"][1]["allow_request_next_mission"] = False
        blocked = mission(REPO_A, "BLOCKED", mission_id="blocked")
        blocked["blocked_contract"] = blocked_contract()
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [blocked], coordinator, scheduler
        )
        action_id = plan["next_action"]["action_id"]
        loop.claim_coordinator_action(scheduler, plan, action_id)
        with self.assertRaisesRegex(loop.ProtocolError, "local Coordinator action"):
            loop.mark_coordinator_action_sent(
                scheduler,
                action_id,
                "not-a-real-recipient",
                packet_sha256="f" * 64,
            )

    def test_T67_user_response_precedes_an_unrelated_review_card(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        response_mission = mission(REPO_A, "USER_DECISION", mission_id="reply")
        response_mission["user_packet"] = review_card()
        response_mission["review_policy"] = response_mission["user_packet"][
            "review_policy"
        ]
        loop.queue_user_response(
            response_mission, coordinator, "Accept the exact artifact."
        )
        unrelated_card = mission(REPO_B, "USER_DECISION", mission_id="card")
        unrelated_card["user_packet"] = review_card()
        unrelated_card["review_policy"] = unrelated_card["user_packet"][
            "review_policy"
        ]
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [response_mission, unrelated_card],
            coordinator,
            loop.default_scheduler_state(),
        )
        self.assertEqual(plan["next_action"]["kind"], "route_user_response")
        self.assertEqual(
            plan["next_action"]["payload"]["selection"]["repository_id"],
            REPO_A,
        )

    def test_T68_identical_question_recurs_only_after_semantic_result(self) -> None:
        _, _, _, coordinator = fixture()
        first = loop.queue_coordinator_event(
            coordinator,
            kind="project_question",
            repository_id=REPO_A,
            raw_text="Is this evidence still current?",
        )
        duplicate_pending = loop.queue_coordinator_event(
            coordinator,
            kind="project_question",
            repository_id=REPO_A,
            raw_text="Is this evidence still current?",
        )
        self.assertTrue(duplicate_pending["deduplicated"])
        self.assertEqual(duplicate_pending["event_id"], first["event_id"])
        loop.acknowledge_coordinator_event_routed(
            coordinator, first["event_id"], "supervisor-context-a-default"
        )
        second = loop.queue_coordinator_event(
            coordinator,
            kind="project_question",
            repository_id=REPO_A,
            raw_text="Is this evidence still current?",
        )
        self.assertTrue(second["deduplicated"])
        self.assertEqual(second["event_id"], first["event_id"])
        self.assertEqual(first["event"]["occurrence"], 1)
        self.assertEqual(
            coordinator["pending_user_events"][0]["state"],
            "delivery_acknowledged",
        )

    def test_T69_user_event_changes_the_semantic_plan_fingerprint(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        scheduler = loop.default_scheduler_state()
        before = loop.build_coordinator_plan(
            registry, hosts, adapter, [], coordinator, scheduler
        )
        loop.queue_coordinator_event(
            coordinator,
            kind="direction_update",
            repository_id=REPO_A,
            raw_text="Use the revised visual direction.",
        )
        after = loop.build_coordinator_plan(
            registry, hosts, adapter, [], coordinator, scheduler
        )
        self.assertNotEqual(before["state_fingerprint"], after["state_fingerprint"])
        self.assertEqual(after["next_action"]["kind"], "route_direction_update")

    def test_T70_prepared_delivery_is_recoverable_but_not_yet_inflight(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="dispatch")
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        action_id = plan["next_action"]["action_id"]
        claimed = loop.claim_coordinator_action(scheduler, plan, action_id)
        recipient = claimed["action"]["payload"]["route"][
            "recipient_thread_id"
        ]
        prepared = loop.prepare_coordinator_action_delivery(
            scheduler, action_id, recipient, "9" * 64
        )
        recovered = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        self.assertEqual(scheduler["scheduler_claim"]["status"], "prepared")
        self.assertEqual(
            scheduler["scheduler_claim"]["delivery_token"],
            prepared["delivery_token"],
        )
        self.assertTrue(recovered["watchdog_should_be_armed"])
        self.assertFalse(recovered["has_inflight_work"])
        self.assertEqual(recovered["execution_state"], "DRAINING")
        with self.assertRaisesRegex(loop.ProtocolError, "must be reconciled"):
            loop.release_coordinator_action(
                scheduler, action_id, "uncertain transport"
            )

    def test_T71_duplicate_send_receipt_preserves_the_original_cursor(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="dispatch")
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        action_id = plan["next_action"]["action_id"]
        claimed = loop.claim_coordinator_action(scheduler, plan, action_id)
        recipient = claimed["action"]["payload"]["route"][
            "recipient_thread_id"
        ]
        loop.prepare_coordinator_action_delivery(
            scheduler, action_id, recipient, "8" * 64
        )
        loop.mark_coordinator_action_sent(
            scheduler,
            action_id,
            recipient,
            packet_sha256="8" * 64,
            after_cursor="cursor-original",
        )
        revision = scheduler["revision"]
        self.assertIsNone(scheduler["scheduler_claim"])
        self.assertEqual(len(scheduler["route_leases"]), 1)
        sent_at = scheduler["route_leases"][0]["sent_at"]
        duplicate = loop.mark_coordinator_action_sent(
            scheduler,
            action_id,
            recipient,
            packet_sha256="8" * 64,
            after_cursor="cursor-original",
        )
        self.assertTrue(duplicate["deduplicated"])
        self.assertEqual(scheduler["revision"], revision)
        self.assertEqual(scheduler["route_leases"][0]["sent_at"], sent_at)
        with self.assertRaisesRegex(loop.ProtocolError, "conflicting wait cursor"):
            loop.mark_coordinator_action_sent(
                scheduler,
                action_id,
                recipient,
                packet_sha256="8" * 64,
                after_cursor="cursor-newer",
            )
        self.assertEqual(
            scheduler["route_leases"][0]["after_cursor"], "cursor-original"
        )

    def test_T72_primary_coordinator_prompt_has_one_machine_readable_contract(
        self,
    ) -> None:
        prompt = (
            SKILL_ROOT / "references" / "coordinator-task-prompt.md"
        ).read_text(encoding="utf-8")
        encoded_contracts = re.findall(
            r"```json\s*(\{.*?\})\s*```",
            prompt,
            flags=re.DOTALL,
        )
        self.assertEqual(
            len(encoded_contracts),
            1,
            "the Coordinator prompt must have one unambiguous JSON contract",
        )
        contract = json.loads(encoded_contracts[0])
        self.assertEqual(contract["contract_version"], 9)

        self.assertEqual(
            contract["scheduler"],
            {
                "claim_model": "scheduler_claim_plus_route_leases",
                "configured_external_route_capacity": 3,
                "supported_external_route_capacity_range": [1, 8],
                "max_execution_routes_per_repository": 1,
                "max_new_work_starts_per_repository_per_pass": 1,
                "protocol_handoff_chain": "drain_to_next_external_wait_or_terminal",
                "ready_policy": "claim_while_capacity",
                "fairness": "durable_round_robin_within_priority",
                "enforcement": {
                    "route_capacity": "scheduler_v2",
                    "route_isolation": "scheduler_v2",
                    "equal_priority_fairness": "scheduler_v2",
                    "new_work_start_budget": "primary_coordinator_pass",
                    "protocol_handoff_chain": "primary_coordinator_pass",
                },
            },
        )
        self.assertEqual(
            contract["wait"],
            {
                "mode": "transport_aware_observation",
                "codex_worker_observer": "wait_threads",
                "chatgpt_supervisor_observer": "read_thread_once_per_pass",
                "foreground_wait_budget_seconds": 60,
                "unchanged_timeout": "silent_checkpoint",
                "commentary_is_event": False,
            },
        )
        self.assertEqual(
            contract["status"]["json"],
            "state/coordinator-current-status.v1.json",
        )
        self.assertEqual(
            contract["status"]["markdown"],
            "state/coordinator-current-status.md",
        )
        self.assertEqual(
            contract["status"]["scope"], "all_registered_repositories"
        )
        self.assertEqual(
            contract["status"]["update_policy"], "semantic_change_only"
        )
        self.assertEqual(contract["status"]["schema_version"], 4)
        self.assertEqual(
            contract["frontier"]["external_result_application"],
            "coordinator-action-apply-result",
        )
        self.assertFalse(contract["frontier"]["delivery_ack_is_result"])
        self.assertIn(
            "frontier_gate",
            contract["capability_gate"]["required_plan_fields"],
        )
        self.assertEqual(contract["status"]["renderer"], "portfolio-render")
        self.assertEqual(
            contract["status"]["graph"], "mermaid_inline_and_markdown_index"
        )
        self.assertEqual(
            contract["status"]["next_user_action"], "one_complete_card_or_null"
        )
        self.assertTrue(contract["status"]["roadmap_position_per_repository"])
        self.assertEqual(
            contract["status"]["status_query"],
            "consume_observed_results_and_drain_required_handoffs_before_answer",
        )
        self.assertEqual(
            contract["status"]["checkpoint_consistency"],
            {
                "source": "same_scheduler_frontier_and_project_context_revisions_and_active_route_set",
                "projection_order": "json_then_render_then_verify",
                "on_mismatch": "CHECKPOINT_FORBIDDEN",
            },
        )
        self.assertEqual(
            set(contract["input_lineage"]["states"]),
            {
                "RECEIVED",
                "DELIVERY_ACKNOWLEDGED",
                "ROUTED",
                "ADOPTED",
                "DEFERRED",
                "REJECTED",
                "NEEDS_CLARIFICATION",
                "SUPERSEDED",
            },
        )
        self.assertTrue(contract["input_lineage"]["receipt_before_routing"])
        self.assertFalse(contract["input_lineage"]["worker_direct_route"])
        self.assertEqual(
            contract["wake"],
            {
                "idle": "user_input_only",
                "ready": "same_turn_or_owned_continuation",
                "external_routes": "foreground_then_recovery_lease",
                "periodic_idle_model_wake": False,
            },
        )
        self.assertEqual(
            contract["state_vocabulary"]["coordinator_availability"],
            ["AVAILABLE"],
        )
        self.assertEqual(
            contract["state_vocabulary"]["execution"],
            [
                "READY",
                "DRAINING",
                "WAITING_USER",
                "WAITING_EXTERNAL",
                "IDLE",
                "SAFETY_CEILING",
            ],
        )
        self.assertEqual(
            contract["state_vocabulary"]["project"],
            [
                "RUNNING",
                "READY",
                "WAITING_USER",
                "WAITING_EXTERNAL",
                "SYSTEM_BLOCKED",
                "MISSION_COMPLETE_NEXT_UNSELECTED",
                "PARKED_BY_POLICY",
                "PROJECT_COMPLETE",
            ],
        )
        self.assertEqual(
            contract["capability_gate"],
            {
                "required_scheduler_schema": 2,
                "required_plan_fields": [
                    "scheduler_claim",
                    "primary_writer_task_id",
                    "frontier_revision",
                    "frontier_safety_mode",
                    "frontier_gate",
                    "project_context_revision",
                    "project_context_safety_mode",
                    "project_context_gate",
                    "active_routes",
                    "ready_actions",
                    "required_handoff_actions",
                    "protocol_handoff_required",
                    "wait_targets",
                    "poll_targets",
                    "capacity_remaining",
                    "round_robin_cursor_repository_id",
                    "checkpoint_after_wait_allowed",
                    "route_cursor_complete",
                    "checkpoint_blockers",
                ],
                "on_missing": "MIGRATION_REQUIRED",
                "forbid_capability_overclaim": True,
            },
        )
        self.assertEqual(
            contract["mission_admission"]["legacy_uncontracted_action"],
            "resolve_mission_value_gate",
        )
        self.assertEqual(
            contract["mission_admission"]["legacy_contract_admission_event"],
            "value_contract_admitted",
        )

        # These phrases encode the old single-route monopoly and must not
        # survive a contract that advertises independent route leases.
        self.assertNotIn("wait only for that exact task", prompt)
        self.assertNotIn("Only one action is claimed at a time", prompt)
        self.assertIn(
            "coordinator-task-prompt.md",
            (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
