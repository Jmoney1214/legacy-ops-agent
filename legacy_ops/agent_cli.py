from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent_workspace import (
    AgentWorkspaceError,
    AgentWorkspaceValidator,
    scaffold_agent,
)
from .domain import Severity


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legacy-agent",
        description="Validate and scaffold governed Legacy business agents.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser(
        "validate",
        help="Validate an agent directory and calculate its artifact digest.",
    )
    validate.add_argument("agent_directory")
    validate.add_argument("--workspace", default=".")

    scaffold = subcommands.add_parser(
        "scaffold",
        help="Create a new standard agent directory.",
    )
    scaffold.add_argument("agent_id")
    scaffold.add_argument("--display-name", required=True)
    scaffold.add_argument("--purpose", required=True)
    scaffold.add_argument("--owner", required=True)
    scaffold.add_argument(
        "--risk-level",
        choices=[item.value for item in Severity],
        default=Severity.LOW.value,
    )
    scaffold.add_argument("--workspace", default=".")

    return parser


def _workspace_payload(workspace) -> dict[str, object]:
    return {
        "agent_id": workspace.manifest.agent_id,
        "version": workspace.manifest.version,
        "manifest_fingerprint": workspace.manifest.fingerprint,
        "artifact_digest": workspace.artifact_digest,
        "artifact_files": list(workspace.artifact_files),
        "eval_case_count": workspace.eval_case_count,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            workspace = AgentWorkspaceValidator(args.workspace).validate(
                args.agent_directory
            )
        else:
            workspace = scaffold_agent(
                Path(args.workspace),
                agent_id=args.agent_id,
                display_name=args.display_name,
                purpose=args.purpose,
                owner=args.owner,
                risk_level=Severity(args.risk_level),
            )
    except AgentWorkspaceError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2

    print(
        json.dumps(
            {"status": "ok", **_workspace_payload(workspace)},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
