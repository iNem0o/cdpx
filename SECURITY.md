# Security Policy

## Supported Versions

Before the first release, security fixes target only the `master` branch.
After release, they target the latest release and the default branch.
Older pre-1.0 versions may not receive a backported fix.

## Reporting a Vulnerability

Do not open a public issue and do not publish proof of exploitation. Use
GitHub's private form:

[Report a vulnerability privately](https://github.com/inem0o/cdpx/security/advisories/new)

This channel is visible only to the repository's authorized maintainers. If
the form is not available, do not expose the details publicly: wait for the
repository owner to enable **Private vulnerability reporting** in the
GitHub settings.

The report should contain, without personal data or real secrets:

- the affected version or commit;
- the minimal reproduction scenario;
- the estimated impact;
- a proposed mitigation if known.

Maintainers triage the report in GitHub, coordinate the fix and the
disclosure, then credit the reporter if they wish. No response time or
bounty program is guaranteed.

## Sensitive Scope

cdpx can execute JavaScript, read page state, and drive trusted actions in
the targeted Chrome. The following are notably considered sensitive:

- a bypass of `CDPX_ORIGINS`;
- a leak of cookies or headers despite redaction by default;
- an unintended connection to a non-disposable browser;
- a system command execution triggered from browser input;
- a corruption or path traversal when writing artifacts.

Usage errors with no security impact can be reported in the
[public issues](https://github.com/inem0o/cdpx/issues).
