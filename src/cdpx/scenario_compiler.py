"""Deterministic, browser-free compiler for composed YAML scenarios."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from cdpx.scenarios import (
    FRAGMENT_SCHEMA,
    SCENARIO_SCHEMA,
    STEP_ACTIONS,
    IncludeSite,
    Scenario,
    ScenarioComposition,
    ScenarioDependency,
    ScenarioUsageError,
    StepSource,
    parse,
)

MAX_INCLUDE_DEPTH = 16
MAX_SCENARIO_FILES = 128
MAX_EXPANDED_STEPS = 1000

_ROOT_KEYS = {"schema", "name", "context", "steps", "assertions", "artifacts"}
_FRAGMENT_KEYS = {"schema", "name", "steps"}
_INCLUDE_KEYS = {"path", "as"}
_GLOB_MARKERS = frozenset("*?[]{}")
_ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")


@dataclass(frozen=True)
class _Document:
    path: Path
    logical_path: str
    raw: dict[str, Any]
    sha256: str
    kind: Literal["scenario", "fragment"]


class _Compiler:
    def __init__(
        self,
        entrypoint: Path,
        root: Path,
        *,
        max_actions: int | None,
    ) -> None:
        if max_actions is not None and max_actions < 0:
            raise ScenarioUsageError("--max-actions must be non-negative")
        self.entrypoint = entrypoint
        self.root = root
        self.max_actions = max_actions
        self._documents: dict[Path, _Document] = {}
        self._dependency_order: list[Path] = []
        self._expanded_steps: list[dict[str, Any]] = []
        self._sources: list[StepSource] = []

    def compile(self) -> Scenario:
        root = self._read_document(self.entrypoint, "scenario")
        self._validate_root(root)
        self._expand_steps(root, namespace=(), include_chain=(), stack=(root.path,), depth=0)
        step_count = len(self._expanded_steps)
        if self.max_actions is not None and step_count > self.max_actions:
            raise ScenarioUsageError(
                f"--max-actions budget exceeded: {step_count} > {self.max_actions}"
            )
        expanded = dict(root.raw)
        expanded["steps"] = self._expanded_steps
        dependencies = tuple(
            ScenarioDependency(
                path=self._documents[path].logical_path,
                sha256=self._documents[path].sha256,
                kind=self._documents[path].kind,
            )
            for path in self._dependency_order
        )
        digest_payload = json.dumps(
            [dependency.as_dict() for dependency in dependencies],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        composition = ScenarioComposition(
            entrypoint=root.logical_path,
            sha256=hashlib.sha256(digest_payload).hexdigest(),
            dependencies=dependencies,
        )
        return parse(
            expanded,
            source=Path(root.logical_path),
            step_sources=self._sources,
            composition=composition,
        )

    def _expand_steps(
        self,
        document: _Document,
        *,
        namespace: tuple[str, ...],
        include_chain: tuple[IncludeSite, ...],
        stack: tuple[Path, ...],
        depth: int,
    ) -> None:
        steps = document.raw.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ScenarioUsageError(f"{document.logical_path}: steps must be a non-empty list")
        aliases: set[str] = set()
        for local_index, item in enumerate(steps):
            where = f"{document.logical_path}:steps[{local_index}]"
            if not isinstance(item, dict):
                raise ScenarioUsageError(f"{where}: must be an object")
            if "include" in item:
                self._expand_include(
                    document,
                    local_index,
                    item,
                    namespace=namespace,
                    include_chain=include_chain,
                    stack=stack,
                    depth=depth,
                    aliases=aliases,
                )
                continue
            expanded = dict(item)
            verbs = [key for key in STEP_ACTIONS if key in item]
            if "label" not in expanded and len(verbs) == 1:
                expanded["label"] = f"{local_index:03d}-{verbs[0]}"
            label = expanded.get("label")
            if namespace and isinstance(label, str):
                expanded["label"] = ".".join((*namespace, label))
            self._append_step(
                expanded,
                StepSource(document.logical_path, local_index, include_chain),
            )

    def _expand_include(
        self,
        document: _Document,
        local_index: int,
        item: dict[str, Any],
        *,
        namespace: tuple[str, ...],
        include_chain: tuple[IncludeSite, ...],
        stack: tuple[Path, ...],
        depth: int,
        aliases: set[str],
    ) -> None:
        where = f"{document.logical_path}:steps[{local_index}]"
        self._unknown(item, {"include"}, where)
        include = item["include"]
        if not isinstance(include, dict):
            raise ScenarioUsageError(f"{where}.include: must be an object")
        self._unknown(include, _INCLUDE_KEYS, f"{where}.include")
        raw_path = include.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ScenarioUsageError(f"{where}.include.path: must be a non-empty string")
        target = self._resolve_include(document, raw_path, where)
        if target in stack:
            start = stack.index(target)
            chain = (*stack[start:], target)
            rendered = " -> ".join(self._logical_path(path) for path in chain)
            raise ScenarioUsageError(f"{where}: include cycle: {rendered}")
        next_depth = depth + 1
        if next_depth > MAX_INCLUDE_DEPTH:
            raise ScenarioUsageError(
                f"{where}: maximum include depth exceeded: {next_depth} > {MAX_INCLUDE_DEPTH}"
            )
        fragment = self._read_document(target, "fragment")
        self._validate_fragment(fragment)
        alias = include.get("as", fragment.raw["name"])
        if not isinstance(alias, str) or not _ALIAS_RE.fullmatch(alias):
            raise ScenarioUsageError(
                f"{where}.include.as: safe alias required ([A-Za-z0-9][A-Za-z0-9._-]{{0,79}})"
            )
        if alias in aliases:
            raise ScenarioUsageError(f"{where}: duplicate include alias: {alias}")
        aliases.add(alias)
        site = IncludeSite(document.logical_path, local_index)
        self._expand_steps(
            fragment,
            namespace=(*namespace, alias),
            include_chain=(*include_chain, site),
            stack=(*stack, fragment.path),
            depth=next_depth,
        )

    def _append_step(self, raw: dict[str, Any], source: StepSource) -> None:
        if len(self._expanded_steps) >= MAX_EXPANDED_STEPS:
            raise ScenarioUsageError(
                f"expanded scenario step limit exceeded: more than {MAX_EXPANDED_STEPS} steps"
            )
        self._expanded_steps.append(raw)
        self._sources.append(source)

    def _resolve_include(self, document: _Document, raw_path: str, where: str) -> Path:
        requested = Path(raw_path)
        if requested.is_absolute() or _WINDOWS_ABSOLUTE_RE.match(raw_path):
            raise ScenarioUsageError(f"{where}: absolute include forbidden: {raw_path}")
        if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", raw_path):
            raise ScenarioUsageError(f"{where}: remote include forbidden: {raw_path}")
        if any(marker in raw_path for marker in _GLOB_MARKERS):
            raise ScenarioUsageError(f"{where}: glob include forbidden: {raw_path}")
        target = (document.path.parent / requested).resolve()
        if not target.is_relative_to(self.root):
            raise ScenarioUsageError(f"{where}: include escapes scenario root: {raw_path}")
        return target

    def _read_document(
        self,
        path: Path,
        kind: Literal["scenario", "fragment"],
    ) -> _Document:
        canonical = path.resolve()
        cached = self._documents.get(canonical)
        if cached is not None:
            if cached.kind != kind:
                raise ScenarioUsageError(
                    f"{cached.logical_path}: expected {kind}, found {cached.kind}"
                )
            return cached
        if len(self._documents) >= MAX_SCENARIO_FILES:
            raise ScenarioUsageError(
                f"scenario file limit exceeded: more than {MAX_SCENARIO_FILES} files"
            )
        logical = self._logical_path(canonical)
        try:
            encoded = canonical.read_bytes()
        except OSError as error:
            raise ScenarioUsageError(f"unreadable {kind}: {logical}: {error}") from error
        try:
            text = encoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ScenarioUsageError(f"invalid UTF-8: {logical}: {error}") from error
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise ScenarioUsageError(f"invalid YAML: {logical}: {error}") from error
        if not isinstance(raw, dict):
            raise ScenarioUsageError(f"{logical}: {kind} must be a YAML object")
        document = _Document(
            path=canonical,
            logical_path=logical,
            raw=raw,
            sha256=hashlib.sha256(encoded).hexdigest(),
            kind=kind,
        )
        self._documents[canonical] = document
        self._dependency_order.append(canonical)
        return document

    def _validate_root(self, document: _Document) -> None:
        self._unknown(document.raw, _ROOT_KEYS, document.logical_path)
        schema = document.raw.get("schema")
        if schema is not None and schema != SCENARIO_SCHEMA:
            raise ScenarioUsageError(
                f"{document.logical_path}: unexpected scenario schema: {schema}"
            )

    def _validate_fragment(self, document: _Document) -> None:
        self._unknown(document.raw, _FRAGMENT_KEYS, document.logical_path)
        schema = document.raw.get("schema")
        if schema != FRAGMENT_SCHEMA:
            raise ScenarioUsageError(
                f"{document.logical_path}: unexpected fragment schema: {schema}"
            )
        name = document.raw.get("name")
        if not isinstance(name, str) or not name:
            raise ScenarioUsageError(f"{document.logical_path}: name must be a non-empty string")

    def _logical_path(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    @staticmethod
    def _unknown(data: dict[str, Any], allowed: set[str], where: str) -> None:
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ScenarioUsageError(f"{where}: unknown field(s): {', '.join(unknown)}")


def _default_root(entrypoint: Path) -> Path:
    workspace = os.environ.get("CDPX_WORKSPACE")
    if workspace:
        return Path(workspace).resolve()
    current = Path.cwd().resolve()
    if entrypoint.is_relative_to(current):
        return current
    return entrypoint.parent


def compile_scenario(
    path: str | Path,
    *,
    root: str | Path | None = None,
    max_actions: int | None = None,
) -> Scenario:
    entrypoint = Path(path).resolve()
    compilation_root = Path(root).resolve() if root is not None else _default_root(entrypoint)
    if not entrypoint.is_relative_to(compilation_root):
        raise ScenarioUsageError(f"scenario entrypoint escapes scenario root: {path}")
    return _Compiler(entrypoint, compilation_root, max_actions=max_actions).compile()
