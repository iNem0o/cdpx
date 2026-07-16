"""CLI handlers that create retained binary browser artifacts."""

from pathlib import Path

from cdpx.cli_context import CommandInvocation
from cdpx.client import CDPClient
from cdpx.commands.shared import (
    artifact_metadata,
    artifact_path,
    assert_session_current,
    browser_client,
    emit_json,
)
from cdpx.policy import PolicyError
from cdpx.primitives import capture


def cmd_screenshot(args: CommandInvocation) -> None:
    output = args.options.output
    if output is None:
        raise RuntimeError("sortie de capture non préparée")
    with browser_client(args) as client:
        path = artifact_path(args, output, "captures")
        result = capture.screenshot(
            client, path, full_page=args.options.full_page, fmt=args.options.fmt
        )
        _verify_artifact_origin(args, client, path)
        emit_json(args, artifact_metadata(args, result, "opaque-restricted"))


def cmd_pdf(args: CommandInvocation) -> None:
    output = args.options.output
    if output is None:
        raise RuntimeError("sortie PDF non préparée")
    with browser_client(args) as client:
        path = artifact_path(args, output, "captures")
        result = capture.pdf(client, path)
        _verify_artifact_origin(args, client, path)
        emit_json(args, artifact_metadata(args, result, "opaque-restricted"))


def _verify_artifact_origin(args: CommandInvocation, client: CDPClient, path: str) -> None:
    try:
        assert_session_current(args, client)
    except PolicyError:
        Path(path).unlink(missing_ok=True)
        raise
