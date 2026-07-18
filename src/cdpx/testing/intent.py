"""Static extraction of test intent (docstring + `#:` comments).

A test's docstring describes the method's intent; `#: <text>` comments
annotate the assertions or steps they precede. Extraction is purely
static (``ast`` + ``tokenize``): zero impact on test execution, and any
analysis failure is silent (fail-open) — a missing intent must never
fail proof collection.
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

# assert = the statement proves something; step = annotated preparatory
# step; note = orphan comment (no following statement), kept so no
# written intent is lost.
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
    """Groups of consecutive `#:` comments: (first line, last line, text)."""

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
    """Extract docstring and `#:` comments from ``func``'s source, or None."""

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
        # Trailing comment on a statement's line: annotate the closest
        # enclosing statement (highest lineno among those that cover it).
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
    """Failure line in the test file, or 0 if it is elsewhere.

    A failure raised in a helper or a fixture returns 0: a silent
    correlation is better than a falsely incriminated assertion.
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
    """Mark status="failed" on the annotation covering the failure line."""

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
