"""Typed, immutable context shared by CLI command handlers."""

from __future__ import annotations

import argparse
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from cdpx import session as session_api
from cdpx.option_types import NavigationWait, StorageKind
from cdpx.policy import Authority, ExecutionContext, PolicyError
from cdpx.security import RedactionContext

CommandHandler = Callable[["CommandInvocation"], int | None]


@dataclass(frozen=True)
class CommandOptions:
    """Immutable, explicit argparse result for every supported CLI field."""

    command: str
    func: CommandHandler
    timeout: float
    pretty: bool
    full: bool
    limit: int
    max_actions: int | None
    target: str | None = None
    session: str | None = None
    run_id: str | None = None
    action: str | list[str] | None = None
    url: str | None = None
    wait: NavigationWait = "load"
    selector: str | None = None
    expression: str | None = None
    await_promise: bool = False
    secret_env: str | None = None
    clear: bool = False
    key: str | None = None
    output: str | None = None
    full_page: bool = False
    fmt: str = "png"
    duration: float = 2.0
    follow: bool = False
    max: int | None = None
    settle: float = 0.5
    show_values: bool = False
    name: str | None = None
    value_env: str | None = None
    kind: StorageKind = "local"
    panels: list[str] | str | None = None
    rule: list[str] | None = None
    preset: str | None = None
    reset: bool = False
    click: str | None = None
    path: str | None = None
    scenario_action: str | None = None
    session_action: str | None = None
    session_run_id: str | None = None
    authority: Authority | None = None
    origins: str = ""
    ttl: float = 3600.0
    export: bool = False
    startup_timeout: float = session_api.DEFAULT_STARTUP_TIMEOUT
    session_path: str | None = None
    session_target: str | None = None
    host: str = ""
    port: int = 0
    evidence_dir: str = ""

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> CommandOptions:
        values = vars(namespace)
        func = values.get("func")
        if not callable(func):
            raise RuntimeError("CLI command without handler")
        return cls(
            command=cast(str, values["command"]),
            func=cast(CommandHandler, func),
            timeout=float(values["timeout"]),
            pretty=bool(values["pretty"]),
            full=bool(values["full"]),
            limit=int(values["limit"]),
            max_actions=cast(int | None, values["max_actions"]),
            target=cast(str | None, values.get("target")),
            session=cast(str | None, values.get("session")),
            run_id=cast(str | None, values.get("run_id")),
            action=cast(str | list[str] | None, values.get("action")),
            url=cast(str | None, values.get("url")),
            wait=_navigation_wait(values.get("wait", "load")),
            selector=cast(str | None, values.get("selector")),
            expression=cast(str | None, values.get("expression")),
            await_promise=bool(values.get("await_promise", False)),
            secret_env=cast(str | None, values.get("secret_env")),
            clear=bool(values.get("clear", False)),
            key=cast(str | None, values.get("key")),
            output=cast(str | None, values.get("output")),
            full_page=bool(values.get("full_page", False)),
            fmt=cast(str, values.get("fmt", "png")),
            duration=float(values.get("duration", 2.0)),
            follow=bool(values.get("follow", False)),
            max=cast(int | None, values.get("max")),
            settle=float(values.get("settle", 0.5)),
            show_values=bool(values.get("show_values", False)),
            name=cast(str | None, values.get("name")),
            value_env=cast(str | None, values.get("value_env")),
            kind=_storage_kind(values.get("kind", "local")),
            panels=cast(list[str] | str | None, values.get("panels")),
            rule=cast(list[str] | None, values.get("rule")),
            preset=cast(str | None, values.get("preset")),
            reset=bool(values.get("reset", False)),
            click=cast(str | None, values.get("click")),
            path=cast(str | None, values.get("path")),
            scenario_action=cast(str | None, values.get("scenario_action")),
            session_action=cast(str | None, values.get("session_action")),
            session_run_id=cast(str | None, values.get("session_run_id")),
            authority=_authority(values.get("authority")),
            origins=cast(str, values.get("origins", "")),
            ttl=float(values.get("ttl", 3600.0)),
            export=bool(values.get("export", False)),
            startup_timeout=float(
                values.get("startup_timeout", session_api.DEFAULT_STARTUP_TIMEOUT)
            ),
            session_path=cast(str | None, values.get("session_path")),
            session_target=cast(str | None, values.get("session_target")),
        )

    def with_session_run_id(self, run_id: str | None) -> CommandOptions:
        return replace(self, session_run_id=run_id)

    def with_lifecycle_identity(self, *, path: str | None, target: str | None) -> CommandOptions:
        return replace(self, session_path=path, session_target=target)

    def with_browser_identity(
        self, *, session_path: str | None, run_id: str | None, target: str | None
    ) -> CommandOptions:
        return replace(self, session=session_path, run_id=run_id, target=target)

    def with_runtime_endpoint(
        self, *, host: str, port: int, evidence_dir: str = ""
    ) -> CommandOptions:
        return replace(self, host=host, port=port, evidence_dir=evidence_dir)


def _navigation_wait(value: Any) -> NavigationWait:
    if value == "load":
        return "load"
    if value == "domcontentloaded":
        return "domcontentloaded"
    if value == "none":
        return "none"
    raise RuntimeError(f"invalid CLI navigation wait: {value!r}")


def _storage_kind(value: Any) -> StorageKind:
    if value == "local":
        return "local"
    if value == "session":
        return "session"
    raise RuntimeError(f"invalid CLI storage: {value!r}")


def _authority(value: Any) -> Authority | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError(f"invalid CLI authority: {value!r}")
    try:
        return Authority(value)
    except ValueError as error:
        raise RuntimeError(f"invalid CLI authority: {value!r}") from error


@dataclass(frozen=True)
class SessionArtifactPolicy:
    """Confinement, metadata and retention policy for session artifacts."""

    manifest: session_api.SessionManifest

    def path(self, requested: str, category: str, *, must_exist: bool = False) -> str:
        name = Path(requested).name
        if (
            not name
            or len(name) > 128
            or not name[0].isascii()
            or not name[0].isalnum()
            or any(not char.isascii() or not (char.isalnum() or char in "._-") for char in name)
        ):
            raise PolicyError(f"session: invalid artifact name: {name or requested}")
        root = Path(self.manifest.artifacts_dir) / category
        if root.is_symlink():
            raise PolicyError(f"session: symbolic artifact directory forbidden: {root}")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        destination = root / name
        if destination.is_symlink():
            raise PolicyError(f"session: symbolic artifact forbidden: {destination}")
        if must_exist:
            self._assert_existing_private_file(destination)
        return str(destination)

    @staticmethod
    def _assert_existing_private_file(destination: Path) -> None:
        try:
            info = destination.lstat()
        except OSError as error:
            raise PolicyError(f"session: artifact not found: {destination}") from error
        if not stat.S_ISREG(info.st_mode):
            raise PolicyError(f"session: regular artifact required: {destination}")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PolicyError("session: artifact owned by another user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PolicyError("session: artifact permissions too open; 0600 required")

    @staticmethod
    def metadata(data: dict[str, Any], classification: str) -> dict[str, Any]:
        return {
            **data,
            "classification": classification,
            "upload_allowed": False,
            "retention": "session",
        }

    def remaining_ttl(self) -> float:
        try:
            remaining = (
                datetime.fromisoformat(self.manifest.expires_at) - datetime.now(UTC)
            ).total_seconds()
        except ValueError as error:
            raise PolicyError("session: invalid session expiration") from error
        if remaining <= 0:
            raise PolicyError(f"session expired: {self.manifest.session_id}")
        return remaining


@dataclass(frozen=True)
class CommandInvocation:
    """Prepared command state; parser output is never mutated after conversion."""

    options: CommandOptions
    redaction: RedactionContext
    execution: ExecutionContext | None = None
    manifest: session_api.SessionManifest | None = None
    artifacts: SessionArtifactPolicy | None = None

    def with_session_run_id(self, run_id: str | None) -> CommandInvocation:
        return replace(self, options=self.options.with_session_run_id(run_id))

    def with_lifecycle_identity(self, *, path: str | None, target: str | None) -> CommandInvocation:
        return replace(
            self,
            options=self.options.with_lifecycle_identity(path=path, target=target),
        )

    def with_browser_identity(
        self, *, session_path: str | None, run_id: str | None, target: str | None
    ) -> CommandInvocation:
        return replace(
            self,
            options=self.options.with_browser_identity(
                session_path=session_path,
                run_id=run_id,
                target=target,
            ),
        )

    def with_runtime_endpoint(
        self, *, host: str, port: int, evidence_dir: str = ""
    ) -> CommandInvocation:
        return replace(
            self,
            options=self.options.with_runtime_endpoint(
                host=host,
                port=port,
                evidence_dir=evidence_dir,
            ),
        )

    def with_session(self, manifest: session_api.SessionManifest) -> CommandInvocation:
        return replace(
            self,
            execution=manifest.execution_context(),
            manifest=manifest,
            artifacts=SessionArtifactPolicy(manifest),
        )

    def require_execution(self) -> ExecutionContext:
        if self.execution is None:
            raise RuntimeError("execution context not prepared")
        return self.execution

    def require_artifacts(self) -> SessionArtifactPolicy:
        if self.artifacts is None:
            raise PolicyError("session: manifest required for artifacts")
        return self.artifacts
