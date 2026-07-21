"""Documentation guards: README and PRIMITIVES.md follow the real CLI
surface, every feature sheet is routed, and every documented `cdpx ...`
example is syntactically valid against the real parser. Drifting docs break
`make check` (HARNESS §6 spirit: a rule without a mechanical guard is a wish).
"""

import re
import shlex
import urllib.parse
from pathlib import Path

import pytest
import yaml

from cdpx.cli import build_parser
from cdpx.proof import parse_help_commands
from cdpx.proofing.features import load_feature_specs

README = Path("README.md").read_text(encoding="utf-8")
PRIMITIVES = Path("docs/PRIMITIVES.md").read_text(encoding="utf-8")
SESSION_LIFECYCLE = Path("docs/SESSION-LIFECYCLE.md").read_text(encoding="utf-8")
AGENT_GUIDE = Path("docs/AGENT-GUIDE.md").read_text(encoding="utf-8")
CDPX_SKILL = Path("skills/cdpx/SKILL.md").read_text(encoding="utf-8")
SKILL_INTERFACE = Path("skills/cdpx/agents/openai.yaml").read_text(encoding="utf-8")
SITE = Path("site/index.html").read_text(encoding="utf-8")
PAGES_WORKFLOW = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")

AGENT_GUIDE_URL = "https://inem0o.github.io/cdpx/agent-guide.md"
AGENT_ONBOARDING_PROMPT = (
    "Help me understand and set up cdpx for this project. Read "
    f"{AGENT_GUIDE_URL} first, then walk me through installation, project "
    "configuration, and a safe local smoke test step by step."
)

GLOBAL_CONTRACT_TOKENS = [
    "--pretty",
    "--full",
    "--limit",
    "--max-actions",
    "--target",
    "--session",
    "--run-id",
    "--timeout",
    "CDPX_SESSION",
    "CDPX_RUN_ID",
    "CDPX_TARGET",
    "CDPX_ORIGINS",
]


def cli_command_names() -> list[str]:
    return [command["name"] for command in parse_help_commands(build_parser().format_help())]


def test_every_cli_command_appears_in_readme_and_primitives():
    """The real CLI surface, extracted from the parser, is fully covered
    by README and PRIMITIVES.md: no subcommand can ship without
    documentation."""
    for name in cli_command_names():
        #: the command list comes from the generated --help, not a
        #: hand-maintained list: any command added without docs names the
        #: offending document here
        assert f"cdpx {name}" in README, f"command missing from README: cdpx {name}"
        assert f"cdpx {name}" in PRIMITIVES, f"command missing from PRIMITIVES.md: cdpx {name}"


def test_readme_routes_to_every_feature_doc():
    """Every declared feature sheet is reachable from the README: a sheet
    written but not routed would remain invisible to the reader."""
    specs, errors = load_feature_specs()
    #: unreadable sheets would skew coverage: we require zero loading
    #: errors before judging the routing
    assert errors == []
    for spec in specs:
        link = f"docs/features/{spec.id}.md"
        #: the README is the repository's entry point: every sheet must be
        #: linked there by its exact path
        assert link in README, f"feature sheet not routed from the README: {link}"


def test_primitives_references_every_feature_doc():
    """PRIMITIVES.md, the reference catalog, links every feature sheet: the
    catalog cannot ignore a capability documented elsewhere."""
    specs, _ = load_feature_specs()
    for spec in specs:
        #: whoever reads the catalog must be able to jump to every sheet;
        #: a capability missing from here does not exist for the user
        assert f"features/{spec.id}.md" in PRIMITIVES, f"sheet not linked in PRIMITIVES: {spec.id}"


def test_session_lifecycle_reference_is_routed_and_diagrammed():
    """The session lifecycle reference is routed from both entry points and
    keeps its four accessible mermaid diagrams: adding or removing one must
    be an explicit choice."""
    #: the document is reachable from the README as well as from the catalog
    assert "docs/SESSION-LIFECYCLE.md" in README
    assert "SESSION-LIFECYCLE.md" in PRIMITIVES
    #: the exact count freezes the visual contract, and every diagram
    #: carries an accessibility title and description (accTitle/accDescr)
    assert SESSION_LIFECYCLE.count("```mermaid") == 4
    assert SESSION_LIFECYCLE.count("accTitle:") == 4
    assert SESSION_LIFECYCLE.count("accDescr:") == 4


def test_agent_onboarding_is_routed_and_published():
    """The exact handoff prompt reaches humans from both entry points, while
    Pages publishes the canonical guide at the URL embedded in that prompt."""
    assert AGENT_ONBOARDING_PROMPT in README
    assert AGENT_ONBOARDING_PROMPT in SITE
    assert "docs/AGENT-GUIDE.md" in README
    assert AGENT_GUIDE_URL in AGENT_GUIDE

    cockpit = Path("docs/cockpit.toml").read_text(encoding="utf-8")
    assert '"docs/AGENT-GUIDE.md"' in cockpit
    assert '"docs/AGENT-GUIDE.md"' in Path("docs/features/harness-proof-cockpit.md").read_text(
        encoding="utf-8"
    )
    assert '- "docs/AGENT-GUIDE.md"' in PAGES_WORKFLOW
    assert "cp docs/AGENT-GUIDE.md site/agent-guide.md" in PAGES_WORKFLOW


def test_cdpx_skill_metadata_and_safety_contract():
    """The distributable skill has valid trigger metadata, stable UI copy and
    the session/security rules needed to operate cdpx without improvisation."""
    frontmatter = re.match(r"\A---\n(.*?)\n---\n", CDPX_SKILL, re.S)
    assert frontmatter is not None
    metadata = yaml.safe_load(frontmatter.group(1))
    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "cdpx"
    assert "explicitly mentions cdpx" in metadata["description"]
    assert "Do not trigger merely" in metadata["description"]

    interface = yaml.safe_load(SKILL_INTERFACE)
    assert interface == {
        "interface": {
            "display_name": "cdpx",
            "short_description": "Operate supervised local Chrome with cdpx",
            "default_prompt": (
                "Use $cdpx to inspect and verify this local application in a "
                "supervised browser session."
            ),
        },
        "policy": {"allow_implicit_invocation": True},
    }
    for guardrail in (
        "personal Chrome profile",
        "untrusted data",
        "minimum required authority",
        "--secret-env",
        "Never stop a session supplied by the user",
        "Stop only a session created for the current task",
    ):
        assert guardrail in CDPX_SKILL


def test_readme_documents_cli_contract():
    """The README documents the entire global CLI contract: cross-cutting
    options, session environment variables, and the three exit codes of
    the stdout/exit invariant."""
    for token in GLOBAL_CONTRACT_TOKENS:
        #: every token of the global contract (options and CDPX_* variables)
        #: must appear: a partially documented contract traps the user
        assert token in README, f"incomplete CLI contract in README: {token}"
    for exit_code in ("exit 0", "exit 1", "exit 2"):
        #: the three exit codes are part of the public contract; the search
        #: tolerates spacing to avoid freezing the formatting
        assert re.search(exit_code.replace(" ", r"\s*"), README, re.I), (
            f"undocumented exit code: {exit_code}"
        )


def test_active_user_docs_only_describe_the_supervised_session_contract():
    """No active user document mentions a removed contract (raw endpoints,
    tab management or direct manifest selection)."""
    specs, errors = load_feature_specs()
    #: the corpus under review includes every sheet; they must load
    #: without error for the check to be exhaustive
    assert errors == []
    documents = {
        "README.md": README,
        "HARNESS.md": Path("HARNESS.md").read_text(encoding="utf-8"),
        "docs/PRIMITIVES.md": PRIMITIVES,
        **{spec.source: spec.body for spec in specs},
    }
    removed_contracts = (
        "--host",
        "--port",
        "CDPX_HOST",
        "CDPX_PORT",
        "--manifest",
        "--evidence-dir",
        "tabs new",
        "tabs activate",
        "tabs close",
    )
    for source, content in documents.items():
        lowered = content.lower()
        for removed in removed_contracts:
            #: the hunt is case-insensitive and names the offending
            #: document: a single mention of a removed contract breaks the gate
            assert removed.lower() not in lowered, (
                f"{source}: removed contract still documented: {removed}"
            )


def _markdown_documents() -> list[Path]:
    paths = set(Path(".").glob("*.md"))
    for root in (
        Path("docs"),
        Path(".github"),
        Path("site"),
        Path("skills"),
        Path("tests/fixtures"),
    ):
        paths.update(root.rglob("*.md"))
    return sorted(path for path in paths if path.is_file())


def _heading_ids(text: str) -> set[str]:
    counts: dict[str, int] = {}
    anchors: set[str] = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        plain = re.sub(r"[`*_~]", "", heading).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", plain, flags=re.UNICODE)
        slug = re.sub(r"\s+", "-", slug)
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def test_markdown_local_links_and_anchors_resolve():
    """Every relative Markdown link points to a current file and heading."""
    failures: list[str] = []
    for source in _markdown_documents():
        text = source.read_text(encoding="utf-8")
        for raw_target in re.findall(r"!?\[[^\]]*]\(([^)]+)\)", text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or re.match(r"^(?:https?://|mailto:)", target):
                continue
            decoded = urllib.parse.unquote(target)
            path_part, separator, anchor = decoded.partition("#")
            destination = source if not path_part else source.parent / path_part
            destination = destination.resolve()
            if not destination.exists():
                failures.append(f"{source}: missing {target}")
                continue
            if separator and anchor and destination.suffix.lower() == ".md":
                headings = _heading_ids(destination.read_text(encoding="utf-8"))
                if anchor.lower() not in headings:
                    failures.append(f"{source}: missing anchor {target}")

    assert failures == [], "broken Markdown links:\n" + "\n".join(failures)


def _fenced_cdpx_lines(text: str) -> list[str]:
    lines = []
    for fence in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        for line in fence.splitlines():
            stripped = line.strip()
            if stripped.startswith("cdpx "):
                lines.append(stripped.rstrip("\\").strip())
    return lines


def _all_documented_examples() -> list[tuple[str, str]]:
    examples = [("README.md", line) for line in _fenced_cdpx_lines(README)]
    examples += [("docs/PRIMITIVES.md", line) for line in _fenced_cdpx_lines(PRIMITIVES)]
    examples += [("docs/AGENT-GUIDE.md", line) for line in _fenced_cdpx_lines(AGENT_GUIDE)]
    examples += [("skills/cdpx/SKILL.md", line) for line in _fenced_cdpx_lines(CDPX_SKILL)]
    specs, _ = load_feature_specs()
    for spec in specs:
        examples += [(spec.source, line) for line in _fenced_cdpx_lines(spec.body)]
    return examples


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.publish-feature-proof",
    proves=["Every documented `cdpx ...` example is accepted by the real CLI parser."],
)
@pytest.mark.parametrize(
    "source,line",
    _all_documented_examples(),
    ids=[f"{src}:{line[:40]}" for src, line in _all_documented_examples()],
)
def test_documented_cdpx_examples_parse(source, line):
    """Every `cdpx ...` line extracted from the doc's code blocks is
    accepted by the real parser: a copy-pasted example can never fail with
    a usage error."""
    argv = shlex.split(line)[1:]
    if argv in (["init"], ["runtime", "plan"]):
        # These two project-management commands belong to the POSIX launcher,
        # not the in-container argparse surface. Keeping this allowlist closed
        # makes any new launcher-only example an explicit contract decision.
        return
    try:
        build_parser().parse_args(argv)
    except SystemExit as exc:
        # --version / -h exit with 0: valid examples. Exit 2 = fake example.
        #: only a voluntary exit (help, version) is tolerated; the parser
        #: exiting on error proves the doc lies about the syntax
        assert exc.code == 0, f"{source}: invalid example: {line}"
