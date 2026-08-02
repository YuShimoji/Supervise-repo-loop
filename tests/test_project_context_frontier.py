from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import supervise_repo_loop as loop  # noqa: E402
from test_coordinator_only_ux import (  # noqa: E402
    HOST_ID,
    fixture,
    mission as coordinator_mission,
    review_card,
)
from test_frontier_reconciliation import (  # noqa: E402
    LANE,
    OWNER,
    REPO_A,
    authority_signal,
    external_result,
    frontier_event,
    mission_record,
    portfolio_v2,
    scheduler_with_waiting_route,
)


CANARY_REPOSITORIES = (
    "canary.local/existing/clip-pipeline",
    "canary.local/existing/narrated-media",
    "canary.local/existing/fiction-factory",
    "canary.local/existing/residual-game",
    "canary.local/new/web-product",
    "canary.local/new/game-runtime",
    "canary.local/new/media-pipeline",
)


def signal_for(repository_id: str) -> dict:
    payload = {
        "repository_id": repository_id,
        "root": f"C:/synthetic/{repository_id.rsplit('/', 1)[-1]}",
        "sources": [],
        "authority_watch_configured": False,
        "git": {
            "status": "present",
            "branch": "main",
            "head_sha": loop.sha256_text(repository_id)[:40],
            "dirty": False,
            "dirty_paths": [],
        },
        "high_water_marks": [],
    }
    payload["authority_fingerprint"] = loop.canonical_json_hash(payload)
    return payload


def frontier_for(repository_ids: tuple[str, ...]) -> tuple[dict, list[dict]]:
    state = loop.default_frontier_state(repository_ids)
    signals = [signal_for(repository_id) for repository_id in repository_ids]
    by_repository = {item["repository_id"]: item for item in signals}
    for repository_id in repository_ids:
        token = repository_id.replace("/", "-")
        signal = by_repository[repository_id]
        loop.apply_frontier_event(
            state,
            {
                "repository_id": repository_id,
                "lane_id": LANE,
                "frontier_epoch": 1,
                "frontier_event_id": f"frontier-{token}",
                "artifact_id": f"artifact-{token}",
                "artifact_revision": "canary-current",
                "artifact_sha256": loop.sha256_text(f"artifact-{token}"),
                "branch": signal["git"]["branch"],
                "head_sha": signal["git"]["head_sha"],
                "disposition": "accepted",
                "source_actor": "supervisor",
                "source_message_id": f"message-{token}",
                "source_result_id": f"result-{token}",
                "based_on_frontier_epoch": 0,
                "supersedes_event_ids": [],
                "recorded_at": "2026-08-02T00:00:00Z",
            },
        )
    return state, signals


def context_event(
    repository_id: str,
    frontier: dict,
    signal: dict,
    *,
    revision: int = 1,
    actor: str = "supervisor",
    supersedes: list[str] | None = None,
    active_lanes: list[str] | None = None,
    event_id: str | None = None,
) -> dict:
    lanes = active_lanes or [LANE]
    token = repository_id.replace("/", "-")
    return {
        "repository_id": repository_id,
        "project_context_revision": revision,
        "project_context_event_id": event_id or f"context-{token}-{revision}",
        "based_on_project_context_revision": revision - 1,
        "source_actor": actor,
        "source_message_id": f"context-message-{token}-{revision}",
        "authority_revision": signal["git"]["head_sha"],
        "authority_fingerprint": signal["authority_fingerprint"],
        "north_star": f"Deliver current user value for {repository_id}.",
        "current_bottleneck": "The current evidence-bearing gate is unfinished.",
        "completion_definition": "Every current gate has exact evidence.",
        "roadmap": {
            "overall_position": "current delivery block",
            "current_block": "evidence-bearing implementation",
            "next_gate": "exact current review",
            "completion_definition": "Every current gate has exact evidence.",
            "completed_blocks": ["authority located"],
            "next_blocks": ["complete current gate"],
        },
        "active_lanes": lanes,
        "lane_frontier_event_ids": {
            lane: frontier["records"][f"{repository_id}|{lane}"][
                "frontier_event_id"
            ]
            for lane in lanes
        },
        "cross_lane_conflicts": [],
        "decisions_since_prior": [],
        "evidence_manifest": [
            {
                "evidence_id": f"authority-{token}-{revision}",
                "kind": "authority_observation",
                "locator": signal["root"],
                "authority_role": "current_authority",
                "sha256": signal["authority_fingerprint"],
            }
        ],
        "omitted_evidence": [],
        "supersedes_context_event_ids": list(supersedes or []),
        "recorded_at": f"2026-08-02T00:{revision:02d}:00Z",
    }


def verified_context(
    repository_ids: tuple[str, ...], frontier: dict, signals: list[dict]
) -> dict:
    state = loop.default_project_context_state(repository_ids)
    by_repository = {item["repository_id"]: item for item in signals}
    for repository_id in repository_ids:
        outcome = loop.apply_project_context_event(
            state,
            context_event(
                repository_id,
                frontier,
                by_repository[repository_id],
            ),
        )
        if outcome["classification"] != "PROJECT_CONTEXT_EVENT_APPLIED":
            raise AssertionError(outcome)
    return state


def seven_repository_topology() -> tuple[dict, dict, dict, dict]:
    registry, hosts, adapter, coordinator = fixture()
    registry["repositories"] = []
    registry["supervisor_bindings"] = []
    registry["worker_bindings"] = []
    host = hosts["hosts"][0]
    host["known_repository_roots"] = {}
    host["root_verifications"] = {}
    adapter["threads"] = []
    for order, repository_id in enumerate(CANARY_REPOSITORIES):
        token = repository_id.rsplit("/", 1)[-1] + f"-{order}"
        root = f"C:/synthetic/{token}"
        registry["repositories"].append(
            {
                "schema_version": 2,
                "repository_id": repository_id,
                "aliases": [token],
                "default_supervision_lane": LANE,
                "remote_identity": repository_id,
                "stable_order": order,
                "allow_request_next_mission": True,
            }
        )
        registry["supervisor_bindings"].append(
            {
                "schema_version": 2,
                "repository_id": repository_id,
                "supervision_lane": LANE,
                "supervisor_project_id": "project-canary",
                "supervisor_thread_id": f"supervisor-{token}",
                "expected_supervisor_title": f"Supervisor {token}",
                "last_verified_at": "2026-08-02T00:00:00Z",
                "binding_status": "active",
                "allow_create_supervisor_chat": False,
            }
        )
        registry["worker_bindings"].append(
            {
                "schema_version": 2,
                "repository_id": repository_id,
                "worker_task_id": f"worker-{token}",
                "host_id": HOST_ID,
                "root_hint": root,
                "last_verified_at": "2026-08-02T00:00:00Z",
                "binding_status": "active",
                "allow_create_worker_task": True,
            }
        )
        host["known_repository_roots"][repository_id] = root
        host["root_verifications"][repository_id] = {
            "root": root,
            "repository_id": repository_id,
        }
        adapter["threads"].extend(
            [
                {
                    "id": f"supervisor-{token}",
                    "kind": "chatgpt",
                    "project_id": "project-canary",
                    "title": f"Supervisor {token}",
                    "read_verified": True,
                    "status": "idle",
                },
                {
                    "id": f"worker-{token}",
                    "kind": "codex",
                    "host_id": "local",
                    "cwd": root,
                    "repository_id": repository_id,
                    "title": f"Worker {token}",
                    "read_verified": True,
                    "status": "idle",
                },
            ]
        )
    return registry, hosts, adapter, coordinator


class ProjectContextFrontierTests(unittest.TestCase):
    def test_PC_LOOP_01_legacy_abstention_cannot_suppress_supervisor_reconciliation(
        self,
    ) -> None:
        registry, hosts, adapter, coordinator = seven_repository_topology()
        frontier = loop.default_frontier_state(CANARY_REPOSITORIES)
        signals = [signal_for(repository_id) for repository_id in CANARY_REPOSITORIES]
        context = loop.default_project_context_state(CANARY_REPOSITORIES)
        waiting = coordinator_mission(
            CANARY_REPOSITORIES[1],
            "SUPERVISOR_ADJUDICATION_REQUESTED",
            mission_id="waiting-review",
            lane=LANE,
        )
        waiting["review_policy"] = {
            "gate": "required",
            "depth": "standard",
            "stage": "artifact-checkpoint",
        }
        loop.apply_supervisor_verdict(
            waiting,
            "user_decision",
            user_packet=review_card("Waiting project"),
        )

        first_plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [waiting],
            coordinator,
            loop.default_scheduler_state(),
            authority_signals=signals,
            frontier_state=frontier,
            project_context_state=context,
        )
        current = next(
            item
            for item in first_plan["ready_actions"]
            if item["kind"] == "reconcile_repository_frontier"
        )
        legacy_payload = copy.deepcopy(current["payload"])
        legacy_payload.pop("route")
        legacy_action = loop._scheduler_action(
            "reconcile_repository_frontier",
            legacy_payload,
            priority=current["priority"],
            stable_order=current["stable_order"],
        )
        scheduler = loop.default_scheduler_state()
        scheduler["completed_actions"].append(
            {
                "action_id": legacy_action["action_id"],
                "kind": legacy_action["kind"],
                "requires_external_result": False,
                "outcome": "authority_conflict",
                "evidence": "legacy-observation.json",
            }
        )

        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [waiting],
            coordinator,
            scheduler,
            authority_signals=signals,
            frontier_state=frontier,
            project_context_state=context,
        )
        action = plan["next_action"]
        self.assertEqual(action["kind"], "reconcile_repository_frontier")
        self.assertNotEqual(action["action_id"], legacy_action["action_id"])
        self.assertTrue(action["requires_external_result"])
        self.assertEqual(
            action["payload"]["route"]["recipient_kind"], "supervisor"
        )
        self.assertEqual(
            action["payload"]["route"]["observer_kind"], "chatgpt_poll"
        )
        self.assertIsNotNone(action["payload"]["route"]["recipient_thread_id"])
        self.assertEqual(plan["execution_state"], "READY")
        self.assertEqual(
            plan["next_user_card"]["repository_id"], CANARY_REPOSITORIES[1]
        )

    def test_PC_CANARY_01_existing_four_and_new_three_use_one_contract(self) -> None:
        registry, hosts, adapter, coordinator = seven_repository_topology()
        frontier, signals = frontier_for(CANARY_REPOSITORIES)
        context = verified_context(CANARY_REPOSITORIES, frontier, signals)
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [],
            coordinator,
            loop.default_scheduler_state(),
            authority_signals=signals,
            frontier_state=frontier,
            project_context_state=context,
        )
        requests = [
            item
            for item in plan["ready_actions"]
            if item["kind"] == "request_next_mission"
        ]
        self.assertEqual(len(requests), 7)
        self.assertEqual(plan["project_context_safety_mode"], "PROJECT_CONTEXT_VERIFIED")
        seen = set()
        for action in requests:
            envelope = action["payload"]["supervisor_context_envelope"]
            route = action["payload"]["route"]
            self.assertEqual(envelope["repository_id"], route["repository_id"])
            self.assertEqual(envelope["lane_id"], route["supervision_lane"])
            self.assertEqual(envelope["action_kind"], action["kind"])
            self.assertNotIn("canary.local/", envelope["action_kind"])
            seen.add(envelope["repository_id"])
        self.assertEqual(seen, set(CANARY_REPOSITORIES))

    def test_PC_IDENTITY_01_completed_bound_action_does_not_reappear(self) -> None:
        registry, hosts, adapter, coordinator = seven_repository_topology()
        frontier, signals = frontier_for(CANARY_REPOSITORIES)
        context = verified_context(CANARY_REPOSITORIES, frontier, signals)
        scheduler = loop.default_scheduler_state()
        first_plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [],
            coordinator,
            scheduler,
            authority_signals=signals,
            frontier_state=frontier,
            project_context_state=context,
        )
        completed = next(
            item
            for item in first_plan["ready_actions"]
            if item["kind"] == "request_next_mission"
        )
        scheduler["completed_actions"].append(
            {
                "action_id": completed["action_id"],
                "kind": completed["kind"],
                "requires_external_result": False,
            }
        )
        second_plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [],
            coordinator,
            scheduler,
            authority_signals=signals,
            frontier_state=frontier,
            project_context_state=context,
        )
        self.assertNotIn(
            completed["action_id"],
            {item["action_id"] for item in second_plan["ready_actions"]},
        )

    def test_PC_MIGRATION_01_new_registration_is_legacy_unverified(self) -> None:
        frontier, signals = frontier_for(CANARY_REPOSITORIES)
        context = verified_context(CANARY_REPOSITORIES, frontier, signals)
        new_repository = "canary.local/future/unknown-project-type"
        migrated = loop.migrate_project_context_state(
            context, [*CANARY_REPOSITORIES, new_repository]
        )
        self.assertEqual(
            migrated["repository_status"][new_repository], "legacy_unverified"
        )
        self.assertEqual(
            migrated["safety_mode"], loop.PROJECT_CONTEXT_SAFETY_MODE
        )

    def test_PC_CROSS_LANE_01_other_lane_change_invalidates_envelope(self) -> None:
        repository_id = CANARY_REPOSITORIES[0]
        frontier, signals = frontier_for((repository_id,))
        signal = signals[0]
        base = frontier["records"][f"{repository_id}|{LANE}"]
        editorial = copy.deepcopy(base)
        editorial.update(
            {
                "lane_id": "editorial",
                "frontier_event_id": "frontier-editorial-1",
                "artifact_id": "artifact-editorial-1",
                "artifact_revision": "editorial-1",
                "artifact_sha256": loop.sha256_text("editorial-1"),
                "source_message_id": "editorial-message-1",
                "source_result_id": "editorial-result-1",
            }
        )
        loop.apply_frontier_event(frontier, editorial)
        context = loop.default_project_context_state([repository_id])
        first = context_event(
            repository_id,
            frontier,
            signal,
            active_lanes=[LANE, "editorial"],
        )
        loop.apply_project_context_event(context, first)
        envelope = loop.build_supervisor_context_envelope(
            context, frontier, repository_id, LANE, "advance_mission", signal
        )
        next_editorial = copy.deepcopy(editorial)
        next_editorial.update(
            {
                "frontier_epoch": 2,
                "frontier_event_id": "frontier-editorial-2",
                "artifact_revision": "editorial-2",
                "artifact_sha256": loop.sha256_text("editorial-2"),
                "source_message_id": "editorial-message-2",
                "source_result_id": "editorial-result-2",
                "based_on_frontier_epoch": 1,
                "supersedes_event_ids": ["frontier-editorial-1"],
            }
        )
        loop.apply_frontier_event(frontier, next_editorial)
        with self.assertRaisesRegex(loop.ProtocolError, "stale|incomplete"):
            loop.validate_supervisor_context_envelope(
                envelope,
                context,
                frontier,
                signal,
                expected_action_kind="advance_mission",
            )

    def test_PC_AUTHORITY_01_lower_precedence_cannot_replace_human_context(self) -> None:
        repository_id = CANARY_REPOSITORIES[0]
        frontier, signals = frontier_for((repository_id,))
        state = loop.default_project_context_state([repository_id])
        human = context_event(
            repository_id, frontier, signals[0], actor="human", event_id="human-context"
        )
        loop.apply_project_context_event(state, human)
        supervisor = context_event(
            repository_id,
            frontier,
            signals[0],
            revision=2,
            actor="supervisor",
            supersedes=["human-context"],
        )
        outcome = loop.apply_project_context_event(state, supervisor)
        self.assertEqual(outcome["classification"], "PROJECT_CONTEXT_PRECEDENCE_REJECTED")
        self.assertEqual(state["contexts"][repository_id]["source_actor"], "human")

    def test_PC_LEDGER_01_current_context_requires_append_only_event(self) -> None:
        repository_id = CANARY_REPOSITORIES[0]
        frontier, signals = frontier_for((repository_id,))
        state = verified_context((repository_id,), frontier, signals)
        state["events"] = []
        with self.assertRaisesRegex(loop.ProtocolError, "append-only"):
            loop.validate_project_context_state(state)

    def test_PC_RESULT_01_stale_context_result_is_quarantined(self) -> None:
        first_frontier = frontier_event(
            epoch=1,
            based_on=0,
            artifact_id="artifact-current",
            actor="supervisor",
            token="context-current",
        )
        frontier = loop.default_frontier_state([REPO_A])
        loop.apply_frontier_event(frontier, first_frontier)
        signal = authority_signal(first_frontier)
        context = loop.default_project_context_state([REPO_A])
        first_context = context_event(REPO_A, frontier, signal)
        loop.apply_project_context_event(context, first_context)
        envelope = loop.build_supervisor_context_envelope(
            context,
            frontier,
            REPO_A,
            LANE,
            "await_supervisor_verdict",
            signal,
        )
        scheduler = scheduler_with_waiting_route()
        scheduler["route_leases"][0]["action"]["payload"][
            "supervisor_context_envelope"
        ] = copy.deepcopy(envelope)
        scheduler["active_claim"] = copy.deepcopy(scheduler["route_leases"][0])
        next_context = context_event(
            REPO_A,
            frontier,
            signal,
            revision=2,
            supersedes=[first_context["project_context_event_id"]],
        )
        loop.apply_project_context_event(context, next_context)
        result_event = frontier_event(
            epoch=2,
            based_on=1,
            artifact_id="artifact-result",
            actor="supervisor",
            token="context-result",
            supersedes=[first_frontier["frontier_event_id"]],
        )
        payload = external_result(result_event, result_id="stale-context-result")
        payload["supervisor_context_envelope_id"] = envelope["envelope_id"]
        payload["based_on_project_context_revision"] = envelope[
            "project_context_revision"
        ]
        portfolio = portfolio_v2()
        outcome = loop.apply_external_result_transaction(
            scheduler,
            frontier,
            [mission_record()],
            portfolio,
            "action-frontier",
            payload,
            observed_authority_signal=payload["authority_signal"],
            project_context_state=context,
            actor_task_id=OWNER,
        )
        self.assertEqual(
            outcome["classification"],
            "STALE_PROJECT_CONTEXT_RESULT_QUARANTINED",
        )
        self.assertEqual(
            frontier["records"][f"{REPO_A}|{LANE}"]["artifact_id"],
            "artifact-current",
        )
        self.assertEqual(portfolio["schema_version"], 4)
        self.assertEqual(
            portfolio["repositories"][0]["project_context_status"],
            "verified",
        )

    def test_PC_RESULT_02_authorized_effect_is_applied_then_context_reconciles(self) -> None:
        first_frontier = frontier_event(
            epoch=1,
            based_on=0,
            artifact_id="artifact-before-work",
            actor="supervisor",
            token="effect-before",
        )
        frontier = loop.default_frontier_state([REPO_A])
        loop.apply_frontier_event(frontier, first_frontier)
        old_signal = authority_signal(first_frontier)
        context = loop.default_project_context_state([REPO_A])
        first_context = context_event(REPO_A, frontier, old_signal)
        loop.apply_project_context_event(context, first_context)
        envelope = loop.build_supervisor_context_envelope(
            context,
            frontier,
            REPO_A,
            LANE,
            "await_supervisor_verdict",
            old_signal,
        )
        scheduler = scheduler_with_waiting_route()
        scheduler["route_leases"][0]["action"]["payload"][
            "supervisor_context_envelope"
        ] = copy.deepcopy(envelope)
        scheduler["active_claim"] = copy.deepcopy(scheduler["route_leases"][0])
        result_event = frontier_event(
            epoch=2,
            based_on=1,
            artifact_id="artifact-after-work",
            actor="supervisor",
            token="effect-after",
            supersedes=[first_frontier["frontier_event_id"]],
            head_sha="b" * 40,
        )
        payload = external_result(result_event, result_id="authorized-effect")
        payload["supervisor_context_envelope_id"] = envelope["envelope_id"]
        payload["based_on_project_context_revision"] = envelope[
            "project_context_revision"
        ]
        portfolio = portfolio_v2()
        outcome = loop.apply_external_result_transaction(
            scheduler,
            frontier,
            [mission_record()],
            portfolio,
            "action-frontier",
            payload,
            observed_authority_signal=payload["authority_signal"],
            project_context_state=context,
            actor_task_id=OWNER,
        )
        self.assertEqual(outcome["classification"], "EXTERNAL_RESULT_APPLIED")
        self.assertEqual(
            frontier["records"][f"{REPO_A}|{LANE}"]["artifact_id"],
            "artifact-after-work",
        )
        self.assertEqual(
            portfolio["repositories"][0]["project_context_status"],
            "reconciliation_required",
        )
        self.assertEqual(
            portfolio["project_context_safety_mode"],
            loop.PROJECT_CONTEXT_SAFETY_MODE,
        )
        self.assertIsNone(portfolio["repositories"][0]["project_context"])

    def test_PC_AUDIT_01_read_only_audit_reports_all_unverified_projects(self) -> None:
        registry, _, _, _ = seven_repository_topology()
        frontier, signals = frontier_for(CANARY_REPOSITORIES)
        context = loop.default_project_context_state(CANARY_REPOSITORIES)
        before = copy.deepcopy(context)
        result = loop.audit_project_context_state(
            registry, context, frontier, signals
        )
        self.assertEqual(
            result["classification"], "PROJECT_CONTEXT_RECONCILIATION_REQUIRED"
        )
        self.assertEqual(len(result["findings"]), 7)
        self.assertFalse(result["mutated"])
        self.assertEqual(context, before)

    def test_PC_INPUT_01_direction_transport_can_repair_missing_context(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        frontier, signals = frontier_for((REPO_A, "github.com/example/context-b"))
        receipt = loop.queue_coordinator_event(
            coordinator,
            kind="direction_update",
            repository_id=REPO_A,
            raw_text="Restore the full project north star and all active lanes.",
        )
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [],
            coordinator,
            loop.default_scheduler_state(),
            authority_signals=signals,
            frontier_state=frontier,
            project_context_state=loop.default_project_context_state(
                [REPO_A, "github.com/example/context-b"]
            ),
        )
        routed = next(
            item
            for item in plan["ready_actions"]
            if item["kind"] == "route_direction_update"
        )
        self.assertEqual(
            routed["payload"]["event"]["event_id"], receipt["event_id"]
        )
        self.assertNotIn(
            "supervisor_context_envelope", routed["payload"]
        )
        reconciliation = next(
            item
            for item in plan["ready_actions"]
            if item["kind"] == "reconcile_project_context"
        )
        self.assertEqual(
            reconciliation["payload"]["reconciliation_contract"]["schema"],
            "project-context-result.v1",
        )
        self.assertEqual(
            reconciliation["payload"]["reconciliation_contract"]["apply_command"],
            "coordinator-action-apply-project-context-result",
        )
        self.assertTrue(reconciliation["requires_external_result"])
        self.assertEqual(
            reconciliation["payload"]["route"]["recipient_kind"],
            "supervisor",
        )
        self.assertEqual(
            reconciliation["payload"]["route"]["observer_kind"],
            "chatgpt_poll",
        )

    def test_PC_RESULT_03_exact_supervisor_result_advances_context_and_route(
        self,
    ) -> None:
        registry, hosts, adapter, coordinator = fixture()
        repositories = tuple(
            item["repository_id"] for item in registry["repositories"]
        )
        frontier, signals = frontier_for(repositories)
        context = loop.default_project_context_state(repositories)
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [],
            coordinator,
            scheduler,
            authority_signals=signals,
            frontier_state=frontier,
            project_context_state=context,
        )
        action = plan["next_action"]
        self.assertEqual(action["kind"], "reconcile_project_context")
        owner = plan["primary_writer_task_id"]
        loop.claim_coordinator_action(
            scheduler, plan, action["action_id"], owner_task_id=owner
        )
        packet_sha256 = loop.sha256_text("project-context-request")
        route = action["payload"]["route"]
        loop.prepare_coordinator_action_delivery(
            scheduler,
            action["action_id"],
            route["recipient_thread_id"],
            packet_sha256,
            actor_task_id=owner,
        )
        loop.mark_coordinator_action_sent(
            scheduler,
            action["action_id"],
            route["recipient_thread_id"],
            packet_sha256=packet_sha256,
            after_cursor="context-cursor-1",
            actor_task_id=owner,
        )
        portfolio = loop._refresh_portfolio_after_external_result(
            portfolio_v2(),
            scheduler,
            frontier,
            [],
            signals,
            project_context_state=context,
        )
        signal = next(
            item
            for item in signals
            if item["repository_id"] == route["repository_id"]
        )
        event = context_event(route["repository_id"], frontier, signal)
        event["source_message_id"] = "context-result-message-1"
        result = {
            "schema_version": 1,
            "result_id": "context-result-1",
            "action_id": action["action_id"],
            "repository_id": route["repository_id"],
            "lane_id": route["supervision_lane"],
            "source_actor": "supervisor",
            "source_thread_id": route["recipient_thread_id"],
            "source_turn_id": "context-turn-1",
            "source_message_id": event["source_message_id"],
            "based_on_project_context_revision": 0,
            "project_context_event": event,
        }

        applied = loop.apply_project_context_result_transaction(
            scheduler,
            context,
            frontier,
            [],
            portfolio,
            action["action_id"],
            result,
            authority_signals=signals,
            actor_task_id=owner,
        )

        self.assertEqual(
            applied["classification"], "PROJECT_CONTEXT_RESULT_APPLIED"
        )
        self.assertEqual(
            context["repository_status"][route["repository_id"]], "verified"
        )
        self.assertEqual(scheduler["route_leases"], [])
        self.assertEqual(
            scheduler["completed_actions"][-1]["external_lifecycle_state"],
            "result_applied",
        )
        self.assertEqual(
            portfolio["repositories"][0]["project_context_status"], "verified"
        )

    def test_PC_PORTFOLIO_01_v4_rejects_stale_project_position(self) -> None:
        first_frontier = frontier_event(
            epoch=1,
            based_on=0,
            artifact_id="artifact-current",
            actor="supervisor",
            token="portfolio-context",
        )
        frontier = loop.default_frontier_state([REPO_A])
        loop.apply_frontier_event(frontier, first_frontier)
        signal = authority_signal(first_frontier)
        context = loop.default_project_context_state([REPO_A])
        first_context = context_event(REPO_A, frontier, signal)
        loop.apply_project_context_event(context, first_context)
        portfolio = loop.migrate_portfolio_to_project_context_v4(
            portfolio_v2(), context, frontier, [signal]
        )
        self.assertEqual(portfolio["schema_version"], 4)
        row = portfolio["repositories"][0]
        self.assertEqual(row["project_context_status"], "verified")
        self.assertEqual(
            row["roadmap"], first_context["roadmap"]
        )
        rendered = loop.render_portfolio_markdown(portfolio)
        self.assertIn("## Certified project context", rendered)
        self.assertIn(first_context["north_star"], rendered)
        second_context = context_event(
            REPO_A,
            frontier,
            signal,
            revision=2,
            supersedes=[first_context["project_context_event_id"]],
        )
        second_context["roadmap"]["current_block"] = "new current block"
        loop.apply_project_context_event(context, second_context)
        with self.assertRaisesRegex(loop.ProtocolError, "stale"):
            loop.validate_portfolio_project_context_consistency(
                portfolio, context, frontier, [signal]
            )

    def test_PC_PORTFOLIO_02_unverified_context_preserves_waiting_user(self) -> None:
        first_frontier = frontier_event(
            epoch=1,
            based_on=0,
            artifact_id="artifact-current",
            actor="supervisor",
            token="portfolio-waiting-user",
        )
        frontier = loop.default_frontier_state([REPO_A])
        loop.apply_frontier_event(frontier, first_frontier)
        signal = authority_signal(first_frontier)
        context = loop.default_project_context_state([REPO_A])
        portfolio = portfolio_v2()
        portfolio["execution_state"] = "WAITING_USER"
        portfolio["active_route_count"] = 0
        portfolio["active_routes"] = []
        portfolio["next_user_action"] = {
            "repository_id": REPO_A,
            "kind": "USER_ACTION",
            "purpose": "Supply the exact current source bytes.",
            "why_now": "The exact Mission is parked for user input.",
            "entrypoint": "This Coordinator task",
            "requirements": ["Attach one exact source file or paste its text."],
            "reply_format": "Attach the file or paste the text.",
            "owner": "User",
            "post_reply_behavior": "Resume only the exact parked Mission.",
            "non_escalation_boundary": "Do not infer missing source content.",
        }
        row = portfolio["repositories"][0]
        row["state"] = "WAITING_USER"
        row["why"] = "The exact Mission is parked for user input."
        row["owner"] = "User"
        row["next_move"] = "Supply the exact current source bytes."

        migrated = loop.migrate_portfolio_to_project_context_v4(
            portfolio, context, frontier, [signal]
        )

        self.assertEqual(migrated["schema_version"], 4)
        self.assertEqual(migrated["execution_state"], "WAITING_USER")
        self.assertEqual(migrated["repositories"][0]["state"], "WAITING_USER")
        self.assertEqual(
            migrated["repositories"][0]["project_context_status"],
            "legacy_unverified",
        )
        self.assertEqual(
            migrated["repositories"][0]["next_move"],
            "Supply the exact current source bytes.",
        )
        self.assertIn(
            "Supply the exact current source bytes.",
            loop.render_portfolio_markdown(migrated),
        )


if __name__ == "__main__":
    unittest.main()
