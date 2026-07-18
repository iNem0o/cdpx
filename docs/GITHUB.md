# GitHub Governance

This document is the source of truth for GitHub settings that cannot be
versioned. `HARNESS.md` remains normative for quality and security;
[VALIDATION.md](VALIDATION.md) describes the cockpit and its layers.

## Contribution cycle

1. create a short branch from `master` and group a targeted change into it;
2. open a draft PR, fill in the template and let `CI` produce the proof;
3. read the **Full release gate** summary and, if needed, download the cockpit;
4. fix until **`PR Gate / Required`** is green;
5. move the PR to review, resolve conversations and merge per policy;
6. let GitHub delete the merged branch.

`make check` is the normative quality gate. `make release` adds the cockpit
and distribution validation; it publishes nothing on its own. The future
release procedure stays in [RELEASE-PLAN.md](RELEASE-PLAN.md). Pushing a
`vX.Y.Z` tag is, itself, a publishing action and requires explicit
authorization.

## Expected settings

Target state of the private repository:

| Setting | Value |
| --- | --- |
| Default branch | `master` |
| Required check | `PR Gate / Required` |
| Branch up to date | required if the gate duration stays acceptable |
| Conversations | resolution mandatory |
| Approvals | 0 as long as the project must remain administrable by a single maintainer |
| Force-push / deletion | forbidden on `master` |
| Merge | squash only; branch deleted after merge |
| Default Actions | `contents: read`, no PR approval by workflow |
| Third-party Actions | GitHub and explicitly authorized actions, all pinned by SHA |
| PR artifacts | `.proof/shareable/` staging only, 14 days |
| Vulnerabilities | private reporting and Dependabot alerts enabled if the plan allows it |

The repository intentionally contains neither a `.github/settings.yml`
without a consuming application, nor a `CODEOWNERS` until a durable code
owner has been explicitly designated.

## State of the private rehearsal of July 11, 2026

The following values were read back via the API after the first real PR:

- `PRIVATE` repository, default branch `master`;
- squash only, branch update allowed and automatic deletion of merged
  branches;
- Actions enabled in `selected` mode: GitHub actions and
  `pypa/gh-action-pypi-publish@*` only; workflows read-only by default,
  with no right to approve a PR;
- Dependabot security alerts and updates active; `pypi` environment
  created, with no protection rule until a release approver has been
  decided;
- operational labels: `bug`, `enhancement`, `documentation`, `dependencies`
  and the `docker` label created by Dependabot;
- secret scanning unavailable (HTTP 422) and private vulnerability
  reporting unavailable (HTTP 404) on this private repository/plan;
- branch protection and rulesets unavailable: reading and attempting to
  write both return HTTP 403 "Upgrade to GitHub Pro or make this
  repository public".

CI does produce `PR Gate / Required`, but GitHub cannot yet make it
mandatory. The repository must stay private: the correct action is a plan
upgrade or enabling an equivalent organization ruleset, never an
opportunistic switch to public. After upgrading, apply the table above and
re-read the rule via the API before any merge or public opening.

## Checking the settings

The following commands must be run with an administrator account:

```bash
gh repo view inem0o/cdpx --json visibility,defaultBranchRef,deleteBranchOnMerge,squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed
gh api repos/inem0o/cdpx/actions/permissions
gh api repos/inem0o/cdpx/actions/permissions/workflow
gh api repos/inem0o/cdpx/rulesets
gh api repos/inem0o/cdpx/branches/master/protection
gh api repos/inem0o/cdpx/private-vulnerability-reporting
gh pr checks <PR_NUMBER> --repo inem0o/cdpx
```

An HTTP 403 mentioning a plan upgrade means GitHub does not offer rulesets
or branch protection for this private repository under the current
subscription. This is not equivalent to an active rule: the risk must stay
explicit until upgrade. Never make the repository public to work around
this limit.

## Diagnosing a blocked merge

1. check the exact name and status of `PR Gate / Required` with `gh pr checks`;
2. open the required job and identify the failed/cancelled/skipped value;
3. read the summary then the artifact as described in [VALIDATION.md](VALIDATION.md);
4. check that the branch is up to date and that all conversations are resolved;
5. reproduce the red Make target locally, fix, commit and push.

A workflow modified in a PR runs that PR's code. The aggregator check,
read-only permissions and the absence of `pull_request_target` reduce the
risk, but only a required workflow/ruleset administered outside the branch
can absolutely prevent a PR from neutralizing its own YAML. This guard must
be enabled as soon as the GitHub plan makes it available.

## Exceptional incident

A protection is only disabled for a verified blocking incident, never to
push through a red CI. Before acting, record the PR URL, the run, the cause
and the owner's approval. Export the rule with `gh api`, disable it in
*Settings → Rules → Rulesets* (or via the API), perform the minimal fix,
then immediately restore the rule and check its JSON again. Any
intervention must remain visible in the PR or a private incident log.

## Future publishing

The `Release` workflow only starts on a `v*` tag, checks the version and
that the tagged commit belongs to `master`, then uses the `pypi`
environment. Before the first tag, protect `v*` tags, require an approval
on this environment and verify Trusted Publishing. No ordinary PR must
trigger PyPI or a GitHub Release.
