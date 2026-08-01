from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import supervise_repo_loop as loop  # noqa: E402
from test_coordinator_only_ux import (  # noqa: E402
    HOST_ID,
    REPO_A,
    REPO_B,
    ROOT_A,
    fixture,
    mission,
    repository,
    supervisor,
    supervisor_thread,
    worker,
    worker_thread,
)


def action_repository_id(action: dict) -> str:
    payload = action.get("payload", {})
    route = payload.get("route", {})
    selection = payload.get("selection", {})
    return str(
        route.get("repository_id")
        or selection.get("repository_id")
        or payload.get("repository_id")
        or ""
    )


def lease_repository_id(lease: dict) -> str:
    return str(
        lease.get("repository_id")
        or action_repository_id(lease.get("action", {}))
    )


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


def register_repository(
    registry: dict,
    hosts: dict,
    adapter: dict,
    repository_id: str,
    order: int,
) -> str:
    root = f"X:\\fixtures\\context-{order}"
    registry["repositories"].append(repository(repository_id, order))
    registry["supervisor_bindings"].append(supervisor(repository_id))
    registry["worker_bindings"].append(worker(repository_id, root))
    host = hosts["hosts"][0]
    host["known_repository_roots"][repository_id] = root
    host["root_verifications"][repository_id] = {
        "root": root,
        "repository_id": repository_id,
    }
    adapter["threads"].extend(
        [
            supervisor_thread(repository_id),
            worker_thread(repository_id, root),
        ]
    )
    return root


def route_for(action: dict) -> dict:
    return action["payload"]["route"]


def claim_prepare_send(
    scheduler: dict,
    plan: dict,
    *,
    digest_character: str,
    cursor: str,
) -> dict:
    action = copy.deepcopy(plan["next_action"])
    if not isinstance(action, dict):
        raise AssertionError("the plan has no claimable next action")
    action_id = action["action_id"]
    claimed = loop.claim_coordinator_action(scheduler, plan, action_id)
    recipient = route_for(claimed["action"])["recipient_thread_id"]
    digest = digest_character * 64
    loop.prepare_coordinator_action_delivery(
        scheduler,
        action_id,
        recipient,
        digest,
    )
    sent = loop.mark_coordinator_action_sent(
        scheduler,
        action_id,
        recipient,
        packet_sha256=digest,
        after_cursor=cursor,
    )
    return {
        "action": action,
        "action_id": action_id,
        "recipient": recipient,
        "digest": digest,
        "sent": sent,
    }


class SchedulerRouteLeaseV2Tests(unittest.TestCase):
    def test_T117_early_v2_observer_is_backfilled_without_route_identity_change(
        self,
    ) -> None:
        registry, hosts, adapter, coordinator = fixture()
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="observer-backfill")
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        sent = claim_prepare_send(
            scheduler, plan, digest_character="e", cursor="observer-cursor"
        )
        before = copy.deepcopy(scheduler["route_leases"][0])
        del scheduler["route_leases"][0]["observer_kind"]
        migrated = loop.migrate_scheduler_state(scheduler)
        lease = migrated["route_leases"][0]
        self.assertEqual(lease["observer_kind"], "codex_wait")
        for field in (
            "action_id",
            "delivery_token",
            "packet_sha256",
            "after_cursor",
            "status",
        ):
            self.assertEqual(lease[field], before[field])
        waiting = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, migrated
        )
        self.assertEqual(waiting["wait_targets"][0]["action_id"], sent["action_id"])
        self.assertEqual(waiting["wait_targets"][0]["host_id"], "local")
        self.assertEqual(waiting["poll_targets"], [])

    def test_T118_observer_mismatch_and_unknown_legacy_transport_fail_closed(
        self,
    ) -> None:
        registry, hosts, adapter, coordinator = fixture()
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="observer-mismatch")
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        claim_prepare_send(
            scheduler, plan, digest_character="f", cursor="observer-cursor"
        )
        scheduler["route_leases"][0]["observer_kind"] = "chatgpt_poll"
        with self.assertRaisesRegex(loop.ProtocolError, "exact adapter recipient"):
            loop.build_coordinator_plan(
                registry, hosts, adapter, [running], coordinator, scheduler
            )

        unknown = copy.deepcopy(scheduler)
        unknown["route_leases"][0].pop("observer_kind", None)
        unknown["route_leases"][0]["action"]["kind"] = "unknown_external"
        unknown["route_leases"][0]["action"]["payload"]["route"].pop(
            "recipient_kind", None
        )
        unknown["route_leases"][0]["action"]["payload"]["route"].pop(
            "observer_kind", None
        )
        with self.assertRaisesRegex(loop.ProtocolError, "observer_kind"):
            loop.migrate_scheduler_state(unknown)

        legacy_mission = mission(
            REPO_A, "WORKER_DISPATCHED", mission_id="legacy-transport-mismatch"
        )
        mismatched_adapter = copy.deepcopy(adapter)
        worker_adapter = next(
            item
            for item in mismatched_adapter["threads"]
            if item.get("id") == legacy_mission["worker_task_id"]
        )
        worker_adapter["kind"] = "chatgpt"
        worker_adapter.pop("host_id", None)
        with self.assertRaisesRegex(loop.ProtocolError, "Mission recipient role"):
            loop.build_coordinator_plan(
                registry,
                hosts,
                mismatched_adapter,
                [legacy_mission],
                coordinator,
                loop.default_scheduler_state(),
            )

    def test_T73_v1_waiting_claim_migrates_to_one_lease_without_resend(
        self,
    ) -> None:
        registry, hosts, adapter, coordinator = fixture()
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="legacy")
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [running],
            coordinator,
            loop.default_scheduler_state(),
        )
        action = plan["next_action"]
        action_id = action["action_id"]
        recipient = route_for(action)["recipient_thread_id"]
        digest = "a" * 64
        delivery_token = loop.canonical_json_hash(
            {
                "action_id": action_id,
                "recipient_thread_id": recipient,
                "packet_sha256": digest,
            }
        )
        legacy = {
            "schema_version": 1,
            "revision": 7,
            "active_claim": {
                "action_id": action_id,
                "action": action,
                "status": "waiting",
                "state_fingerprint": plan["state_fingerprint"],
                "owner_task_id": "primary-coordinator",
                "recipient_thread_id": recipient,
                "packet_sha256": digest,
                "delivery_token": delivery_token,
                "after_cursor": "legacy-cursor",
                "sent_at": "2026-08-01T00:00:00Z",
            },
            "completed_actions": [],
        }

        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler.json"
            state_path.write_text(json.dumps(legacy), encoding="utf-8")
            migrated = loop.load_scheduler_state(state_path)

        self.assertEqual(migrated["schema_version"], 2)
        self.assertIsNone(migrated["scheduler_claim"])
        self.assertEqual(len(migrated["route_leases"]), 1)
        lease = migrated["route_leases"][0]
        self.assertEqual(lease["action_id"], action_id)
        self.assertEqual(lease["status"], "waiting")
        self.assertEqual(lease["recipient_thread_id"], recipient)
        self.assertEqual(lease["packet_sha256"], digest)
        self.assertEqual(lease["delivery_token"], delivery_token)
        self.assertEqual(lease["after_cursor"], "legacy-cursor")

        revision = migrated["revision"]
        duplicate = loop.mark_coordinator_action_sent(
            migrated,
            action_id,
            recipient,
            packet_sha256=digest,
            after_cursor="legacy-cursor",
            actor_task_id="primary-coordinator",
        )
        self.assertTrue(duplicate["deduplicated"])
        self.assertEqual(migrated["revision"], revision)
        self.assertEqual(len(migrated["route_leases"]), 1)
        self.assertEqual(migrated["completed_actions"], [])

    def test_T74_plan_exposes_all_ready_actions_not_only_the_first(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        missions = [
            mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="ready-a"),
            mission(REPO_B, "WORK_ORDER_RECEIVED", mission_id="ready-b"),
        ]
        plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            missions,
            coordinator,
            loop.default_scheduler_state(),
        )

        self.assertEqual(
            [action_repository_id(item) for item in plan["ready_actions"]],
            [REPO_A, REPO_B],
        )
        self.assertEqual(action_repository_id(plan["next_action"]), REPO_A)
        self.assertEqual(plan["capacity_remaining"], 3)
        self.assertEqual(plan["active_routes"], [])
        self.assertEqual(plan["wait_targets"], [])

    def test_T75_waiting_A_keeps_B_ready_and_claimable(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        missions = [
            mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="waiting-a"),
            mission(REPO_B, "WORK_ORDER_RECEIVED", mission_id="ready-b"),
        ]
        scheduler = loop.default_scheduler_state()
        first = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        sent_a = claim_prepare_send(
            scheduler,
            first,
            digest_character="a",
            cursor="cursor-a",
        )

        after_a = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        self.assertIsNone(after_a["scheduler_claim"])
        self.assertEqual(
            [item["action_id"] for item in after_a["active_routes"]],
            [sent_a["action_id"]],
        )
        self.assertEqual(
            [action_repository_id(item) for item in after_a["ready_actions"]],
            [REPO_B],
        )
        self.assertEqual(action_repository_id(after_a["next_action"]), REPO_B)
        self.assertEqual(after_a["capacity_remaining"], 2)
        self.assertTrue(after_a["has_inflight_work"])
        self.assertTrue(after_a["has_ready_action"])
        self.assertFalse(after_a["cycle_checkpoint_allowed"])

        claimed_b = loop.claim_coordinator_action(
            scheduler,
            after_a,
            after_a["next_action"]["action_id"],
        )
        self.assertEqual(
            action_repository_id(claimed_b["action"]),
            REPO_B,
        )

    def test_T76_two_repository_route_leases_coexist(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        missions = [
            mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="route-a"),
            mission(REPO_B, "WORK_ORDER_RECEIVED", mission_id="route-b"),
        ]
        scheduler = loop.default_scheduler_state()
        first = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        sent_a = claim_prepare_send(
            scheduler, first, digest_character="a", cursor="cursor-a"
        )
        second = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        sent_b = claim_prepare_send(
            scheduler, second, digest_character="b", cursor="cursor-b"
        )

        self.assertIsNone(scheduler["scheduler_claim"])
        self.assertEqual(len(scheduler["route_leases"]), 2)
        self.assertEqual(
            {item["action_id"] for item in scheduler["route_leases"]},
            {sent_a["action_id"], sent_b["action_id"]},
        )
        self.assertEqual(
            {lease_repository_id(item) for item in scheduler["route_leases"]},
            {REPO_A, REPO_B},
        )

    def test_T77_completing_B_preserves_A_route_lease(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        missions = [
            mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="route-a"),
            mission(REPO_B, "WORK_ORDER_RECEIVED", mission_id="route-b"),
        ]
        scheduler = loop.default_scheduler_state()
        first = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        sent_a = claim_prepare_send(
            scheduler, first, digest_character="a", cursor="cursor-a"
        )
        second = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        sent_b = claim_prepare_send(
            scheduler, second, digest_character="b", cursor="cursor-b"
        )

        result_evidence = mark_semantic_result_applied(
            scheduler, sent_b["action_id"], "worker-b-result"
        )
        loop.complete_coordinator_action(
            scheduler,
            sent_b["action_id"],
            "accepted",
            evidence=result_evidence,
        )

        self.assertEqual(
            [item["action_id"] for item in scheduler["route_leases"]],
            [sent_a["action_id"]],
        )
        self.assertEqual(
            [item["action_id"] for item in scheduler["completed_actions"]],
            [sent_b["action_id"]],
        )

    def test_T78_second_external_route_for_same_repository_is_rejected(
        self,
    ) -> None:
        registry, hosts, adapter, coordinator = fixture()
        registry["repositories"][1]["allow_request_next_mission"] = False
        scheduler = loop.default_scheduler_state()
        first_mission = mission(
            REPO_A, "WORK_ORDER_RECEIVED", mission_id="same-repository-a"
        )
        first = loop.build_coordinator_plan(
            registry, hosts, adapter, [first_mission], coordinator, scheduler
        )
        claim_prepare_send(
            scheduler, first, digest_character="a", cursor="cursor-a"
        )

        second_mission = mission(
            REPO_A, "WORK_ORDER_RECEIVED", mission_id="same-repository-b"
        )
        blocked_plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [second_mission], coordinator, scheduler
        )
        self.assertEqual(
            [action_repository_id(item) for item in blocked_plan["ready_actions"]],
            [REPO_A],
        )
        self.assertIsNone(blocked_plan["next_action"])

        independent_plan = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [second_mission],
            coordinator,
            loop.default_scheduler_state(),
        )
        forged = copy.deepcopy(independent_plan)
        forged["scheduler_revision"] = scheduler["revision"]
        with self.assertRaises(loop.ProtocolError):
            loop.claim_coordinator_action(
                scheduler,
                forged,
                forged["next_action"]["action_id"],
            )

    def test_T79_concurrency_cap_three_keeps_fourth_ready(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        repository_ids = [
            REPO_A,
            REPO_B,
            "github.com/example/context-c",
            "github.com/example/context-d",
        ]
        for order, repository_id in enumerate(repository_ids[2:], start=2):
            register_repository(
                registry, hosts, adapter, repository_id, order
            )
        missions = [
            mission(
                repository_id,
                "WORK_ORDER_RECEIVED",
                mission_id=f"route-{index}",
            )
            for index, repository_id in enumerate(repository_ids)
        ]
        scheduler = loop.default_scheduler_state()

        initial = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        self.assertEqual(len(initial["ready_actions"]), 4)
        self.assertEqual(initial["concurrency_limit"], 3)

        for index in range(3):
            plan = loop.build_coordinator_plan(
                registry, hosts, adapter, missions, coordinator, scheduler
            )
            self.assertEqual(plan["capacity_remaining"], 3 - index)
            claim_prepare_send(
                scheduler,
                plan,
                digest_character=chr(ord("a") + index),
                cursor=f"cursor-{index}",
            )

        capped = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        self.assertEqual(capped["capacity_remaining"], 0)
        self.assertEqual(len(capped["active_routes"]), 3)
        self.assertEqual(
            [action_repository_id(item) for item in capped["ready_actions"]],
            [repository_ids[3]],
        )
        self.assertTrue(capped["has_ready_action"])
        self.assertIsNone(capped["next_action"])

    def test_T80_wait_targets_are_unique_and_cursor_bound(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        missions = [
            mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="wait-a"),
            mission(REPO_B, "WORK_ORDER_RECEIVED", mission_id="wait-b"),
        ]
        scheduler = loop.default_scheduler_state()
        first = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        sent_a = claim_prepare_send(
            scheduler, first, digest_character="a", cursor="cursor-a"
        )
        second = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        sent_b = claim_prepare_send(
            scheduler, second, digest_character="b", cursor="cursor-b"
        )
        waiting = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )

        targets = waiting["wait_targets"]
        self.assertEqual(len(targets), 2)
        identities = {
            (item["recipient_thread_id"], item["after_cursor"])
            for item in targets
        }
        self.assertEqual(len(identities), 2)
        self.assertEqual(
            {item["action_id"] for item in targets},
            {sent_a["action_id"], sent_b["action_id"]},
        )
        self.assertEqual(
            identities,
            {
                (sent_a["recipient"], "cursor-a"),
                (sent_b["recipient"], "cursor-b"),
            },
        )

    def test_T81_completed_and_released_commands_are_idempotent(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        missions = [
            mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="complete-a"),
            mission(REPO_B, "WORK_ORDER_RECEIVED", mission_id="release-b"),
        ]
        scheduler = loop.default_scheduler_state()

        first = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        sent = claim_prepare_send(
            scheduler, first, digest_character="a", cursor="cursor-a"
        )
        result_evidence = mark_semantic_result_applied(
            scheduler, sent["action_id"], "worker-a-result"
        )
        loop.complete_coordinator_action(
            scheduler,
            sent["action_id"],
            "accepted",
            evidence=result_evidence,
        )
        completed_revision = scheduler["revision"]
        duplicate_complete = loop.complete_coordinator_action(
            scheduler,
            sent["action_id"],
            "accepted",
            evidence=result_evidence,
        )
        self.assertTrue(duplicate_complete["deduplicated"])
        self.assertEqual(scheduler["revision"], completed_revision)
        self.assertEqual(
            sum(
                item["action_id"] == sent["action_id"]
                for item in scheduler["completed_actions"]
            ),
            1,
        )

        release_plan = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        release_id = release_plan["next_action"]["action_id"]
        loop.claim_coordinator_action(scheduler, release_plan, release_id)
        loop.release_coordinator_action(scheduler, release_id, "send failed")
        released_revision = scheduler["revision"]
        duplicate_release = loop.release_coordinator_action(
            scheduler, release_id, "send failed"
        )
        self.assertTrue(duplicate_release["deduplicated"])
        self.assertEqual(scheduler["revision"], released_revision)
        self.assertEqual(
            sum(
                item["action_id"] == release_id
                for item in scheduler["released_claims"]
            ),
            1,
        )

    def test_T82_waiting_A_with_claimable_B_forbids_wait_handoff(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        missions = [
            mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="wait-a"),
            mission(REPO_B, "WORK_ORDER_RECEIVED", mission_id="ready-b"),
        ]
        scheduler = loop.default_scheduler_state()
        first = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        claim_prepare_send(
            scheduler, first, digest_character="a", cursor="cursor-a"
        )

        active_and_ready = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        self.assertTrue(active_and_ready["has_inflight_work"])
        self.assertTrue(active_and_ready["has_claimable_action"])
        self.assertIsNotNone(active_and_ready["next_action"])
        self.assertFalse(active_and_ready["checkpoint_after_wait_allowed"])
        self.assertFalse(active_and_ready["cycle_checkpoint_allowed"])

    def test_T83_waiting_lease_without_ready_work_allows_wait_handoff(
        self,
    ) -> None:
        registry, hosts, adapter, coordinator = fixture()
        registry["repositories"][1]["allow_request_next_mission"] = False
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="wait-only")
        scheduler = loop.default_scheduler_state()
        first = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        claim_prepare_send(
            scheduler, first, digest_character="a", cursor="cursor-a"
        )

        wait_only = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        self.assertTrue(wait_only["has_inflight_work"])
        self.assertFalse(wait_only["has_ready_action"])
        self.assertFalse(wait_only["has_claimable_action"])
        self.assertTrue(wait_only["checkpoint_after_wait_allowed"])
        self.assertFalse(wait_only["cycle_checkpoint_allowed"])

    def test_T84_full_capacity_allows_wait_handoff_with_ready_queue(
        self,
    ) -> None:
        registry, hosts, adapter, coordinator = fixture()
        repository_ids = [
            REPO_A,
            REPO_B,
            "github.com/example/context-c",
            "github.com/example/context-d",
        ]
        for order, repository_id in enumerate(repository_ids[2:], start=2):
            register_repository(
                registry, hosts, adapter, repository_id, order
            )
        missions = [
            mission(
                repository_id,
                "WORK_ORDER_RECEIVED",
                mission_id=f"cap-{index}",
            )
            for index, repository_id in enumerate(repository_ids)
        ]
        scheduler = loop.default_scheduler_state()
        for index in range(3):
            plan = loop.build_coordinator_plan(
                registry, hosts, adapter, missions, coordinator, scheduler
            )
            claim_prepare_send(
                scheduler,
                plan,
                digest_character=chr(ord("a") + index),
                cursor=f"cursor-{index}",
            )

        capped = loop.build_coordinator_plan(
            registry, hosts, adapter, missions, coordinator, scheduler
        )
        self.assertEqual(capped["capacity_remaining"], 0)
        self.assertTrue(capped["has_inflight_work"])
        self.assertTrue(capped["has_ready_action"])
        self.assertFalse(capped["has_claimable_action"])
        self.assertIsNone(capped["next_action"])
        self.assertTrue(capped["checkpoint_after_wait_allowed"])
        self.assertFalse(capped["cycle_checkpoint_allowed"])

    def test_T85_scheduler_claim_forbids_wait_handoff(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        running = mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="claimed")
        scheduler = loop.default_scheduler_state()
        plan = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        loop.claim_coordinator_action(
            scheduler, plan, plan["next_action"]["action_id"]
        )

        claimed = loop.build_coordinator_plan(
            registry, hosts, adapter, [running], coordinator, scheduler
        )
        self.assertIsNotNone(claimed["scheduler_claim"])
        self.assertFalse(claimed["checkpoint_after_wait_allowed"])
        self.assertFalse(claimed["cycle_checkpoint_allowed"])

    def test_T86_idle_checkpoint_is_not_a_wait_handoff(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        for item in registry["repositories"]:
            item["allow_request_next_mission"] = False
        idle = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            [],
            coordinator,
            loop.default_scheduler_state(),
        )

        self.assertFalse(idle["has_inflight_work"])
        self.assertFalse(idle["has_ready_action"])
        self.assertFalse(idle["checkpoint_after_wait_allowed"])
        self.assertTrue(idle["cycle_checkpoint_allowed"])
        self.assertEqual(idle["execution_state"], "IDLE")

    def test_T87_released_slot_goes_to_waiting_fourth_repository(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        repository_ids = [
            REPO_A,
            REPO_B,
            "github.com/example/context-c",
            "github.com/example/context-d",
        ]
        for order, repository_id in enumerate(repository_ids[2:], start=2):
            register_repository(registry, hosts, adapter, repository_id, order)
        missions = [
            mission(
                repository_id,
                "WORK_ORDER_RECEIVED",
                mission_id=f"round-robin-{index}",
            )
            for index, repository_id in enumerate(repository_ids)
        ]
        scheduler = loop.default_scheduler_state()
        sent: list[dict] = []

        for index in range(3):
            plan = loop.build_coordinator_plan(
                registry, hosts, adapter, missions, coordinator, scheduler
            )
            sent.append(
                claim_prepare_send(
                    scheduler,
                    plan,
                    digest_character=chr(ord("a") + index),
                    cursor=f"cursor-{index}",
                )
            )

        self.assertEqual(
            scheduler["round_robin_cursor_repository_id"],
            repository_ids[2],
        )
        result_evidence = mark_semantic_result_applied(
            scheduler, sent[0]["action_id"], "repository-a-result"
        )
        loop.complete_coordinator_action(
            scheduler,
            sent[0]["action_id"],
            "accepted",
            evidence=result_evidence,
        )

        # A has immediately produced a successor at the same priority. D was
        # already queued while capacity was full and must receive the slot.
        successor_missions = [
            mission(
                REPO_A,
                "WORK_ORDER_RECEIVED",
                mission_id="round-robin-a-successor",
            ),
            *missions[1:],
        ]
        refill = loop.build_coordinator_plan(
            registry,
            hosts,
            adapter,
            successor_missions,
            coordinator,
            scheduler,
        )

        self.assertEqual(refill["round_robin_cursor_repository_id"], repository_ids[2])
        self.assertEqual(
            [action_repository_id(item) for item in refill["ready_actions"]],
            [repository_ids[3], REPO_A],
        )
        self.assertEqual(
            action_repository_id(refill["next_action"]),
            repository_ids[3],
        )

    def test_T88_early_v2_state_derives_cursor_without_changing_routes(self) -> None:
        registry, hosts, adapter, coordinator = fixture()
        missions = [
            mission(REPO_A, "WORK_ORDER_RECEIVED", mission_id="legacy-v2-a"),
            mission(REPO_B, "WORK_ORDER_RECEIVED", mission_id="legacy-v2-b"),
        ]
        scheduler = loop.default_scheduler_state()
        for index in range(2):
            plan = loop.build_coordinator_plan(
                registry, hosts, adapter, missions, coordinator, scheduler
            )
            claim_prepare_send(
                scheduler,
                plan,
                digest_character=chr(ord("a") + index),
                cursor=f"legacy-v2-cursor-{index}",
            )

        scheduler.pop("round_robin_cursor_repository_id")
        original_revision = scheduler["revision"]
        original_identities = [
            (
                item["action_id"],
                item["recipient_thread_id"],
                item["packet_sha256"],
                item["delivery_token"],
                item["after_cursor"],
            )
            for item in scheduler["route_leases"]
        ]

        migrated = loop.migrate_scheduler_state(scheduler)

        self.assertEqual(migrated["revision"], original_revision)
        self.assertEqual(migrated["round_robin_cursor_repository_id"], REPO_B)
        self.assertEqual(
            [
                (
                    item["action_id"],
                    item["recipient_thread_id"],
                    item["packet_sha256"],
                    item["delivery_token"],
                    item["after_cursor"],
                )
                for item in migrated["route_leases"]
            ],
            original_identities,
        )


if __name__ == "__main__":
    unittest.main()
