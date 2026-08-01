import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HostPortabilityContractTests(unittest.TestCase):
    def test_T146_manifest_separates_portable_and_host_local_automation_fields(
        self,
    ) -> None:
        manifest = json.loads(
            (ROOT / "references" / "automation-portability.v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["schema_version"], 1)
        profiles = {profile["role"]: profile for profile in manifest["profiles"]}
        self.assertEqual(
            set(profiles),
            {"coordinator_recovery_lease", "post_work_reflection"},
        )
        recovery = profiles["coordinator_recovery_lease"]
        self.assertEqual(recovery["initial_status"], "PAUSED")
        self.assertIn("target_thread_id", recovery["host_local_fields"])
        self.assertIn("primary_writer_task_id", recovery["forbidden_transfer"])
        reflection = profiles["post_work_reflection"]
        self.assertIn("one_time_gate_path", reflection["host_local_fields"])
        self.assertIn("create_or_replace_remote", reflection["forbidden_actions"])

    def test_T147_host_runbook_rejects_live_state_and_task_id_transfer(self) -> None:
        runbook = (ROOT / "docs" / "host-portability.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(runbook.split())
        for required in (
            "not an in-flight Mission",
            "Do not run two hosts as writers",
            "start it paused",
            "never paste or infer the sending host's task ID",
            "-VerifyRemoteTip",
        ):
            self.assertIn(required, normalized)

    def test_T148_local_tool_state_is_ignored_and_not_installable(self) -> None:
        ignore_path = ROOT / ".gitignore"
        if ignore_path.exists():
            ignores = set(ignore_path.read_text(encoding="utf-8").splitlines())
            self.assertTrue(
                {"/state/", "/.serena/", "/.playwright-mcp/"} <= ignores
            )
        else:
            self.assertFalse(
                (ROOT / ".git").exists(),
                "A Git checkout must include the host-local ignore contract.",
            )
        installer = (ROOT / "scripts" / "sync-installed-skill.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("(state|\\.serena)", installer)
        verifier = (ROOT / "scripts" / "verify-portable-checkout.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("(state|\\.serena|\\.playwright-mcp)", verifier)

    def test_T149_static_scheduler_contract_has_no_host_identity(self) -> None:
        paths = (
            ROOT / "references" / "automation-portability.v1.json",
            ROOT / "references" / "recovery-lease-prompt.md",
            ROOT / "scripts" / "verify-portable-checkout.ps1",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertNotIn(r"C:\Users\thank", combined)
        self.assertIsNone(
            re.search(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                combined,
                flags=re.IGNORECASE,
            )
        )


if __name__ == "__main__":
    unittest.main()
