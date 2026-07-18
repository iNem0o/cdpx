"""Curated documentation catalog rendered in the proof cockpit."""

from __future__ import annotations

import fnmatch
import stat
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from cdpx.proofing.features import FeatureSpec, load_feature_specs
from cdpx.proofing.markdown import markdown_title, render_markdown

SCHEMA = "cdpx.docs/v1"
CONFIG_PATH = Path("docs/cockpit.toml")


def _safe_pattern(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{CONFIG_PATH}: {label} must contain non-empty paths")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        raise ValueError(f"{CONFIG_PATH}: path outside repository forbidden: {value}")
    return value


def _load_config(path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path}: invalid documentation configuration: {exc}") from exc
    allowed = {"schema", "index", "include", "exclude", "labels"}
    if set(data) - allowed:
        raise ValueError(f"{path}: unknown keys: {', '.join(sorted(set(data) - allowed))}")
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: expected schema: {SCHEMA}")
    if not isinstance(data.get("include"), list) or not data["include"]:
        raise ValueError(f"{path}: include must be a non-empty list")
    if not isinstance(data.get("exclude", []), list):
        raise ValueError(f"{path}: exclude must be a list")
    labels = data.get("labels", {})
    if not isinstance(labels, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value for key, value in labels.items()
    ):
        raise ValueError(f"{path}: labels must be a table of strings")
    return data


def _expand_paths(root: Path, config: dict[str, Any]) -> list[str]:
    excluded = [_safe_pattern(item, "exclude") for item in config.get("exclude", [])]
    found: dict[str, None] = {}
    root_resolved = root.resolve()
    for raw_pattern in config["include"]:
        pattern = _safe_pattern(raw_pattern, "include")
        matches = sorted(root.glob(pattern))
        if not matches:
            raise ValueError(f"{CONFIG_PATH}: include with no results: {pattern}")
        for path in matches:
            relative = path.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(relative, item) for item in excluded):
                continue
            try:
                info = path.lstat()
            except OSError as exc:
                raise ValueError(f"{CONFIG_PATH}: unreadable document: {relative}") from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ValueError(f"{CONFIG_PATH}: regular document required: {relative}")
            if path.suffix.lower() != ".md":
                raise ValueError(f"{CONFIG_PATH}: Markdown document required: {relative}")
            if not path.resolve().is_relative_to(root_resolved):
                raise ValueError(f"{CONFIG_PATH}: document outside repository: {relative}")
            found[relative] = None
    if not found:
        raise ValueError(f"{CONFIG_PATH}: empty documentation catalog")
    return list(found)


def _tree(documents: list[dict[str, Any]], labels: dict[str, str]) -> dict[str, Any]:
    root: dict[str, Any] = {
        "name": ".",
        "path": ".",
        "label": labels.get(".", "Documentation"),
        "documents": [],
        "children": [],
    }
    by_path = {".": root}
    for document in documents:
        parts = PurePosixPath(document["path"]).parts
        parent = root
        current_parts: list[str] = []
        for part in parts[:-1]:
            current_parts.append(part)
            current_path = "/".join(current_parts)
            if current_path not in by_path:
                node = {
                    "name": part,
                    "path": current_path,
                    "label": labels.get(current_path, part),
                    "documents": [],
                    "children": [],
                }
                by_path[current_path] = node
                parent["children"].append(node)
            parent = by_path[current_path]
        parent["documents"].append(document["path"])
    return root


def build_documentation_catalog(
    *,
    root: Path = Path("."),
    config_path: Path = CONFIG_PATH,
    feature_specs: list[FeatureSpec] | None = None,
) -> dict[str, Any]:
    """Build the catalog or return its violations without side effects."""

    violations: list[str] = []
    try:
        config = _load_config(root / config_path)
        paths = _expand_paths(root, config)
    except ValueError as exc:
        return {
            "schema": SCHEMA,
            "index": "",
            "documents": [],
            "tree": {},
            "violations": [str(exc)],
        }

    specs = feature_specs
    if specs is None:
        specs, feature_errors = load_feature_specs(root / "docs/features")
        violations.extend(feature_errors)
    feature_by_source = {spec.source: spec for spec in specs}
    missing_features = sorted(set(feature_by_source) - set(paths))
    if missing_features:
        violations.append("feature docs missing from catalog: " + ", ".join(missing_features))

    catalog_paths = tuple(paths)
    documents = []
    for relative in paths:
        feature = feature_by_source.get(relative)
        if feature is not None:
            body = feature.body
            title = feature.title
            summary = feature.summary
            kind = "feature"
            feature_id: str | None = feature.id
        else:
            body = (root / relative).read_text(encoding="utf-8")
            title = markdown_title(body) or ""
            summary = ""
            kind = "reference"
            feature_id = None
            if not title:
                violations.append(f"document without H1 title: {relative}")
                title = Path(relative).stem
        documents.append(
            {
                "id": relative,
                "path": relative,
                "title": title,
                "summary": summary,
                "kind": kind,
                "feature_id": feature_id,
                "html": render_markdown(
                    body,
                    source_path=relative,
                    catalog_paths=catalog_paths,
                ),
            }
        )

    index = _safe_pattern(config.get("index"), "index")
    if index not in paths:
        violations.append(f"documentation index missing from catalog: {index}")
    labels = {str(key): str(value) for key, value in config.get("labels", {}).items()}
    return {
        "schema": SCHEMA,
        "index": index,
        "documents": documents,
        "tree": _tree(documents, labels),
        "violations": violations,
    }


def documentation_failures(catalog: dict[str, Any]) -> list[str]:
    return [f"documentation: {item}" for item in catalog.get("violations", [])]
