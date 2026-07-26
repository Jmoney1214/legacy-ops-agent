from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_ops.agent_cli import main
from legacy_ops.agent_workspace import (
    AgentWorkspaceError,
    AgentWorkspaceValidator,
    scaffold_agent,
)
from legacy_ops.domain import Severity


class AgentWorkspaceTests(unittest.TestCase):
    def test_scaffold_validates_and_digest_changes_with_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = scaffold_agent(
                root,
                agent_id="inventory_agent",
                display_name="Inventory Agent",
                purpose="Review inventory records and surface replenishment exceptions.",
                owner="inventory_operations",
                risk_level=Severity.MEDIUM,
            )
            self.assertEqual(workspace.eval_case_count, 3)
            self.assertEqual(len(workspace.artifact_digest), 64)
            previous = workspace.artifact_digest
            instructions = workspace.instructions_path
            instructions.write_text(
                instructions.read_text(encoding="utf-8")
                + "\nAdditional verified operating rule.\n",
                encoding="utf-8",
            )
            updated = AgentWorkspaceValidator(root).validate(workspace.directory)
            self.assertNotEqual(previous, updated.artifact_digest)

    def test_secret_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = scaffold_agent(
                root,
                agent_id="finance_agent",
                display_name="Finance Agent",
                purpose="Analyze verified finance records and surface reconciliation exceptions.",
                owner="finance_operations",
            )
            (workspace.directory / ".env").write_text(
                "OPENAI_API_KEY=secret", encoding="utf-8"
            )
            with self.assertRaises(AgentWorkspaceError):
                AgentWorkspaceValidator(root).validate(workspace.directory)

    def test_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = scaffold_agent(
                root,
                agent_id="events_agent",
                display_name="Events Agent",
                purpose="Coordinate verified event tasks and identify missing execution details.",
                owner="events_operations",
            )
            outside = root / "outside.md"
            outside.write_text("outside", encoding="utf-8")
            link = workspace.directory / "linked.md"
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaises(AgentWorkspaceError):
                AgentWorkspaceValidator(root).validate(workspace.directory)

    def test_less_than_three_evals_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = scaffold_agent(
                root,
                agent_id="marketing_agent",
                display_name="Marketing Agent",
                purpose="Prepare grounded marketing work from approved business information.",
                owner="marketing_operations",
            )
            case = {
                "id": "only_case",
                "input": {"task": "test"},
                "expected": {"status": "completed"},
                "tags": ["test"],
            }
            workspace.eval_cases_path.write_text(
                json.dumps(case) + "\n", encoding="utf-8"
            )
            with self.assertRaises(AgentWorkspaceError):
                AgentWorkspaceValidator(root).validate(workspace.directory)

    def test_cli_scaffold_and_validate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = main(
                [
                    "scaffold",
                    "vendor_agent",
                    "--display-name",
                    "Vendor Agent",
                    "--purpose",
                    "Review vendor records and prepare grounded follow-up work.",
                    "--owner",
                    "vendor_operations",
                    "--workspace",
                    directory,
                ]
            )
            self.assertEqual(result, 0)
            result = main(
                [
                    "validate",
                    "agents/vendor_agent",
                    "--workspace",
                    directory,
                ]
            )
            self.assertEqual(result, 0)


    def test_secret_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = scaffold_agent(
                root,
                agent_id="security_agent",
                display_name="Security Agent",
                purpose="Review security evidence and report verified policy exceptions.",
                owner="security_operations",
            )
            (workspace.directory / "notes.md").write_text(
                "api_key=sk-proj-" + "A" * 32,
                encoding="utf-8",
            )
            with self.assertRaises(AgentWorkspaceError):
                AgentWorkspaceValidator(root).validate(workspace.directory)

    def test_invalid_scaffold_id_is_reported_as_workspace_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AgentWorkspaceError):
                scaffold_agent(
                    directory,
                    agent_id="../invalid",
                    display_name="Invalid Agent",
                    purpose="Demonstrate that invalid scaffold paths fail before creation.",
                    owner="agent_platform",
                )


if __name__ == "__main__":
    unittest.main()
