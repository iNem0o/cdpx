"""Extraction statique de l'intention des tests (docstring + commentaires `#:`).

La docstring d'un test décrit l'intention de la méthode; les commentaires
`#: <texte>` annotent les assertions ou étapes qu'ils précèdent. L'extraction
est purement statique (``ast`` + ``tokenize``): aucun impact sur l'exécution
des tests, et tout échec d'analyse est silencieux (fail-open) — une intention
absente ne doit jamais faire échouer la collecte de preuve.
"""

from __future__ import annotations

import ast
import inspect
import io
import textwrap
import tokenize
from dataclasses import dataclass, field
from typing import Any

INTENT_COMMENT_PREFIX = "#:"
MAX_DOCSTRING_CHARS = 2000
MAX_CODE_EXCERPT_CHARS = 200

# assert = l'instruction prouve quelque chose; step = étape préparatoire
# annotée; note = commentaire orphelin (sans instruction suivante), conservé
# pour ne perdre aucune intention écrite.
ASSERTION_KINDS = ("assert", "step", "note")


@dataclass
class AssertionIntent:
    line: int
    end_line: int
    text: str
    code_excerpt: str
    kind: str
    status: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "end_line": self.end_line,
            "text": self.text,
            "code_excerpt": self.code_excerpt,
            "kind": self.kind,
            "status": self.status,
        }


@dataclass
class TestIntent:
    docstring: str
    line: int
    assertions: list[AssertionIntent] = field(default_factory=list)


def _statement_kind(node: ast.stmt) -> str:
    if isinstance(node, ast.Assert):
        return "assert"
    if isinstance(node, ast.With | ast.AsyncWith):
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call):
                func = call.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "raises":
                    return "assert"
    return "step"


def _intent_comment_groups(source: str) -> list[tuple[int, int, str]]:
    """Groupes de commentaires `#:` consécutifs: (première ligne, dernière ligne, texte)."""

    comments: dict[int, str] = {}
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            stripped = token.string.strip()
            if not stripped.startswith(INTENT_COMMENT_PREFIX):
                continue
            text = stripped[len(INTENT_COMMENT_PREFIX) :].strip()
            if text:
                comments[token.start[0]] = text
    except tokenize.TokenError:
        return []
    groups: list[tuple[int, int, str]] = []
    for line in sorted(comments):
        if groups and line == groups[-1][1] + 1:
            first, _, text = groups[-1]
            groups[-1] = (first, line, f"{text} {comments[line]}")
        else:
            groups.append((line, line, comments[line]))
    return groups


def _candidate_statements(func_node: ast.AST) -> list[ast.stmt]:
    docstring_node = None
    body = getattr(func_node, "body", [])
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        docstring_node = body[0]
    statements = [
        node
        for node in ast.walk(func_node)
        if isinstance(node, ast.stmt) and node is not func_node and node is not docstring_node
    ]
    return sorted(statements, key=lambda node: node.lineno)


def extract_intent(func: Any) -> TestIntent | None:
    """Extrait docstring et commentaires `#:` du source de ``func``, ou None."""

    try:
        target = inspect.unwrap(func)
        source_lines, start_line = inspect.getsourcelines(target)
    except (OSError, TypeError):
        return None
    source = textwrap.dedent("".join(source_lines))
    try:
        tree = ast.parse(source)
    except (SyntaxError, IndentationError, ValueError):
        return None
    func_nodes = [
        node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    if not func_nodes:
        return None
    func_node = func_nodes[0]
    docstring = inspect.cleandoc(ast.get_docstring(func_node) or "")[:MAX_DOCSTRING_CHARS]
    offset = start_line - 1
    lines = source.splitlines()
    statements = _candidate_statements(func_node)

    assertions: list[AssertionIntent] = []
    for first, last, text in _intent_comment_groups(source):
        # Commentaire en fin de ligne d'une instruction: annoter l'instruction
        # englobante la plus proche (lineno maximal parmi celles qui couvrent).
        inline = [
            node for node in statements if node.lineno <= first <= (node.end_lineno or node.lineno)
        ]
        target_node = max(inline, key=lambda node: node.lineno) if inline else None
        if target_node is None:
            following = [node for node in statements if node.lineno > last]
            target_node = following[0] if following else None
        if target_node is None:
            assertions.append(
                AssertionIntent(
                    line=first + offset,
                    end_line=last + offset,
                    text=text,
                    code_excerpt="",
                    kind="note",
                )
            )
            continue
        excerpt = lines[target_node.lineno - 1].strip()[:MAX_CODE_EXCERPT_CHARS]
        assertions.append(
            AssertionIntent(
                line=target_node.lineno + offset,
                end_line=(target_node.end_lineno or target_node.lineno) + offset,
                text=text,
                code_excerpt=excerpt,
                kind=_statement_kind(target_node),
            )
        )
    return TestIntent(
        docstring=docstring,
        line=func_node.lineno + offset,
        assertions=sorted(assertions, key=lambda item: item.line),
    )


def failure_location(report: Any, test_path: str) -> int:
    """Ligne d'échec dans le fichier de test, ou 0 si elle est ailleurs.

    Un échec levé dans un helper ou une fixture retourne 0: mieux vaut une
    corrélation muette qu'une assertion faussement incriminée.
    """

    longrepr = getattr(report, "longrepr", None)
    crash = getattr(longrepr, "reprcrash", None)
    crash_path = getattr(crash, "path", "")
    crash_line = getattr(crash, "lineno", 0)
    if crash_path and crash_line:
        normalized = str(crash_path).replace("\\", "/")
        if normalized.endswith(test_path):
            return int(crash_line)
        return 0
    text = getattr(report, "longreprtext", "") or ""
    for line in reversed(str(text).splitlines()):
        prefix, _, rest = line.partition(":")
        digits = rest.split(":", 1)[0]
        if prefix.replace("\\", "/").endswith(test_path) and digits.isdigit():
            return int(digits)
    return 0


def mark_failed_assertion(assertions: list[dict[str, Any]], failed_line: int) -> None:
    """Marque status="failed" sur l'annotation couvrant la ligne d'échec."""

    if not failed_line:
        return
    covering = [
        entry
        for entry in assertions
        if entry.get("kind") != "note"
        and int(entry.get("line", 0)) <= failed_line <= int(entry.get("end_line", 0))
    ]
    if covering:
        innermost = max(covering, key=lambda entry: int(entry.get("line", 0)))
        innermost["status"] = "failed"
