"""Rendu CommonMark sûr pour la documentation du cockpit.

Le HTML source reste désactivé. Les seules balises injectées par cdpx sont
produites par des règles de rendu contrôlées, notamment pour Mermaid. Les liens
internes sont résolus contre le catalogue documentaire afin qu'un rapport
ouvert hors ligne ne pointe jamais vers un faux chemin sous ``.proof/``.
"""

from __future__ import annotations

import html
import posixpath
import re
import unicodedata
import urllib.parse
from collections.abc import Collection, MutableMapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.token import Token


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "section"


def _document_route(path: str, fragment: str = "") -> str:
    route = f"#/docs/view/{urllib.parse.quote(path, safe='/-._~')}"
    if fragment:
        route += f"?section={urllib.parse.quote(_slug(fragment), safe='-._~')}"
    return route


def _resolve_repo_path(source_path: str, target: str) -> str | None:
    if not source_path or not target:
        return None
    parent = str(PurePosixPath(source_path).parent)
    candidate = posixpath.normpath(posixpath.join(parent, urllib.parse.unquote(target)))
    if candidate == ".." or candidate.startswith("../") or candidate.startswith("/"):
        return None
    return candidate.removeprefix("./")


def _render_fence(
    default_rule: Any,
    tokens: Sequence[Token],
    idx: int,
    options: MutableMapping[str, Any],
    env: MutableMapping[str, Any],
) -> str:
    token = tokens[idx]
    language = token.info.strip().split(maxsplit=1)[0].lower() if token.info.strip() else ""
    if language == "mermaid":
        return f'<pre class="mermaid">{html.escape(token.content)}</pre>\n'
    return default_rule(tokens, idx, options, env)


def _render_link_open(
    renderer: Any,
    tokens: Sequence[Token],
    idx: int,
    options: MutableMapping[str, Any],
    env: MutableMapping[str, Any],
) -> str:
    token = tokens[idx]
    href = str(token.attrGet("href") or "")
    parsed = urllib.parse.urlsplit(href)
    catalog_paths = {str(item) for item in env.get("catalog_paths", ())}
    source_path = str(env.get("source_path", ""))

    if parsed.scheme in {"http", "https"}:
        token.attrSet("target", "_blank")
        token.attrSet("rel", "noopener noreferrer")
    elif parsed.scheme == "mailto":
        token.attrSet("rel", "noopener noreferrer")
    elif parsed.scheme:
        token.attrs.pop("href", None)
        token.attrSet("class", "doc-link-unavailable")
        token.attrSet("aria-disabled", "true")
    elif href.startswith("#") and source_path in catalog_paths:
        token.attrSet("href", _document_route(source_path, parsed.fragment))
    elif catalog_paths:
        resolved = _resolve_repo_path(source_path, parsed.path)
        if resolved in catalog_paths:
            token.attrSet("href", _document_route(resolved, parsed.fragment))
        else:
            token.attrs.pop("href", None)
            token.attrSet("class", "doc-link-unavailable")
            token.attrSet("aria-disabled", "true")
            token.attrSet("title", "Document non publié dans le cockpit")
    return renderer.renderToken(tokens, idx, options, env)


def _add_heading_anchors(state: Any) -> None:
    counts: dict[str, int] = {}
    tokens = state.tokens
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or tokens[index + 1].type != "inline":
            continue
        base = _slug(tokens[index + 1].content)
        counts[base] = counts.get(base, 0) + 1
        suffix = f"-{counts[base]}" if counts[base] > 1 else ""
        token.attrSet("id", f"{base}{suffix}")


def _renderer() -> MarkdownIt:
    md = MarkdownIt(
        "commonmark",
        {
            "html": False,
            "linkify": False,
            "typographer": False,
            "langPrefix": "lang-",
        },
    ).enable("table")
    renderer: Any = md.renderer
    default_fence = renderer.rules["fence"]

    def fence_rule(
        _renderer: Any,
        tokens: Sequence[Token],
        idx: int,
        options: MutableMapping[str, Any],
        env: MutableMapping[str, Any],
    ) -> str:
        return _render_fence(default_fence, tokens, idx, options, env)

    md.add_render_rule("fence", fence_rule)
    md.add_render_rule("link_open", _render_link_open)
    md.core.ruler.after("inline", "cdpx_heading_anchors", _add_heading_anchors)
    return md


_MARKDOWN = _renderer()


def render_markdown(
    text: str,
    *,
    source_path: str = "",
    catalog_paths: Collection[str] = (),
) -> str:
    """Rend un document Markdown avec un contexte de navigation optionnel."""

    env = {
        "source_path": source_path,
        "catalog_paths": tuple(catalog_paths),
    }
    return _MARKDOWN.render(text, env)


def markdown_title(text: str) -> str | None:
    """Retourne le texte du premier H1 sans accepter de HTML source."""

    tokens = _MARKDOWN.parse(text)
    for index, token in enumerate(tokens[:-1]):
        if token.type == "heading_open" and token.tag == "h1":
            inline = tokens[index + 1]
            if inline.type == "inline":
                return inline.content.strip() or None
    return None


def escape_mermaid_source(value: str) -> str:
    """Helper public testé pour rappeler que Mermaid reçoit du texte échappé."""

    return escapeHtml(value)
