"""Convertisseur Markdown -> HTML minimal pour la doc utilisateur des features.

Subset volontairement strict (ce que les fiches docs/features/*.md utilisent):
titres h2-h4, paragraphes, listes à puces, tableaux, blocs de code, code
inline, gras, liens. Tout le HTML source est échappé AVANT transformation:
une fiche ne peut pas injecter de balise dans le rapport de preuve. Zéro
dépendance externe (la seule dépendance runtime du projet reste websockets).
"""

from __future__ import annotations

import html
import re


def render_markdown(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    para: list[str] = []
    in_list = False

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            flush_para()
            close_list()
            lang = stripped[3:].strip()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # fence fermante
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(code))}</code></pre>")
            continue
        if not stripped:
            flush_para()
            close_list()
            i += 1
            continue
        heading = re.match(r"^(#{2,4})\s+(.*)$", stripped)
        if heading:
            flush_para()
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        if stripped.startswith("- "):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            i += 1
            continue
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|[\s\-|:]+\|$", lines[i + 1].strip())
        ):
            flush_para()
            close_list()
            head = "".join(f"<th>{_inline(cell)}</th>" for cell in _cells(stripped))
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = "".join(f"<td>{_inline(cell)}</td>" for cell in _cells(lines[i].strip()))
                rows.append(f"<tr>{cells}</tr>")
                i += 1
            out.append(
                f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            )
            continue
        para.append(stripped)
        i += 1
    flush_para()
    close_list()
    return "\n".join(out)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip("|").split("|")]


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped
