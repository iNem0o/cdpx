# GitHub governance

This document records the required GitHub settings and operating procedure.
`HARNESS.md` defines quality and security, [VALIDATION.md](VALIDATION.md)
defines the executed gates, and [RELEASING.md](RELEASING.md) defines
publication.

## Contribution cycle

1. Create a focused branch from `main`.
2. Open a draft pull request and let `CI` produce the proof.
3. Mark it ready to trigger the advisory CodeRabbit review.
4. Inspect the **Full release gate** summary and proof artifact.
5. Fix the branch until **`PR Gate / Required`** is green.
6. Resolve actionable review conversations and merge with squash.
7. Delete the merged branch.

`./dev check` is the normative quality gate. `./dev release` adds the
internal package validation but publishes nothing.

## Required repository settings

| Setting | Required value |
| --- | --- |
| Default branch | `main` |
| Required check | `PR Gate / Required`, including Dependency Review |
| Conversations | resolution required |
| Force-push and deletion | forbidden on `main` |
| Merge methods | squash only |
| Merged branches | automatically deleted |
| Workflow permissions | `contents: read` by default; no PR approval |
| Third-party actions | explicitly authorized and pinned by SHA |
| Pull-request proof | `.proof/shareable/` only, retained 14 days |
| Release environment | `release`, with required approval |
| Dependency updates | Dependabot groups patch/minor updates; majors remain isolated; no automerge |
| Automated review | CodeRabbit reviews ready PRs in advisory mode |
| Security | CodeQL default setup, Dependabot alerts, secret scanning with push protection, and private vulnerability reporting |

If the GitHub plan does not provide rulesets, branch protection, secret
scanning or private vulnerability reporting, record the unavailable setting
as an active risk. Never make the repository public to bypass a plan limit.

## Verification

Run these commands with an administrator account:

```bash
gh repo view inem0o/cdpx \
  --json visibility,defaultBranchRef,deleteBranchOnMerge,squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed
gh api repos/inem0o/cdpx/actions/permissions
gh api repos/inem0o/cdpx/actions/permissions/workflow
gh api repos/inem0o/cdpx/rulesets
gh ruleset check --default --repo inem0o/cdpx
gh api repos/inem0o/cdpx/private-vulnerability-reporting
gh api repos/inem0o/cdpx/code-scanning/default-setup
gh api repos/inem0o/cdpx/vulnerability-alerts
gh pr checks <PR_NUMBER> --repo inem0o/cdpx
```

An unavailable protection is not equivalent to an active rule. Re-read the
setting after every administrative change.

## Diagnosing a blocked merge

1. Read the exact state of `PR Gate / Required`.
2. Open the failed, cancelled or skipped job.
3. Inspect the job summary, then `validation-summary.json` and
   `proof-report.html`.
4. Confirm that the branch is current and conversations are resolved.
5. Reproduce the reported `./dev` command locally, fix it and rerun the gate.

A workflow modified by a pull request executes that branch's code. Read-only
permissions and the absence of `pull_request_target` reduce the risk; an
administratively controlled required workflow or ruleset provides the final
enforcement.

## Exceptional incident

Disable a protection only for a verified blocking incident with explicit
owner approval, never to merge red CI. Record the pull request, workflow run
and cause, export the current rule, perform the minimum intervention, restore
the rule immediately and verify the resulting JSON.
