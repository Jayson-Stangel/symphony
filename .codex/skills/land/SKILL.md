---
name: symphony-land
description:
  Land an approved Symphony-managed pull request after verifying the target
  repository's required validation and reviews.
---

# Land

Use this skill when the operator approves merging. Follow the target repository's
AGENTS.md, ticket and approval boundaries; approval for one branch or environment
does not authorize another.

## Verify the current PR

1. Read the issue, its workpad and current repository instructions. Recover the
   pending merge obligations recorded at handoff: required checks, required
   reviewers, acceptance evidence and operator approval. Reconcile them with the
   repository's current requirements rather than treating the workpad as authority
   to waive a requirement.
2. Locate the PR and inspect its current head, actual base branch, mergeability,
   checks, review requests, submitted reviews and available feedback. Use the
   actual base branch for synchronization; do not assume main.
3. If changes or conflict resolution are necessary, preserve existing work,
   follow the repository's commit/push instructions and rerun affected required
   validation. Reassess evidence against the resulting head.
4. Address actionable review feedback or explicitly explain a justified deferral
   within the authorized scope. Unresolved correctness findings prevent merge.

Useful snapshots (these do not independently establish permission to merge):

```bash
gh pr view --json number,url,baseRefName,headRefOid,mergeable,reviewDecision,reviewRequests,reviews,statusCheckRollup
gh pr checks
gh pr checks --required
gh api repos/{owner}/{repo}/pulls/{number}/comments
```

`--required` reports GitHub branch-protection requirements only. Also verify the
commands/checks required by AGENTS.md and the ticket, even when GitHub lists no
required checks. If requirement discovery fails, record the missing fact and
return to Human Review rather than assuming there are no obligations. Inspect the
relevant run and its head revision; an empty list is neither a failure nor proof that repository-required validation passed.
Optional checks may be reported without delaying handoff or merge unless their
results reveal an unresolved correctness problem.

## Enforce pending review obligations

For each required reviewer or review process, verify that the required review
actually completed and its feedback was addressed on the applicable head. A
pending review request, empty comments, or GitHub's overall review decision does
not satisfy a separately required review. Follow the repository's policy on
whether an earlier review remains valid after a change. Do not require every
installed bot to review unless the repository or ticket requires it.

Moving the issue to Merging supplies the operator's merge approval only; it does
not waive required external reviews or validation. If an external required check
or review remains pending, record the exact obligation, source of the requirement
and current head in the existing workpad, return the issue to Human Review and
end the turn. The operator can resume Merging when the obligation is satisfied.
Use Blocked for an actual external failure requiring intervention, not ordinary
review or CI latency. Do not occupy a worker with a polling loop or use the legacy
all-check `land_watch.py` helper as a merge gate.

## Merge and verify

Immediately before merging, refresh the PR head and required evidence. If the
head changed, reassess validation and reviews; do not merge using stale evidence.
Merge only when required checks and reviews are satisfied, actionable feedback
is resolved, the PR is conflict-free and the exact action is authorized. Respect
repository rules on merge method and branch deletion. Do not enable auto-merge
as a substitute for requirements that GitHub branch protection does not enforce.

Use the repository's approved merge command or connector, verify that the PR
merged into the intended base, then record the merged revision and completed
obligations in the existing workpad and move the issue to Done. If merging fails,
inspect the reason; do not bypass checks or rewrite published history.
