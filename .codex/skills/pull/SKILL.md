---
name: symphony-pull
description:
  Synchronize a Symphony-managed feature branch with its verified integration
  base using a merge, resolving conflicts safely. Do not use for unrelated
  repositories or work.
---

# Pull

## Workflow

1. Inspect git status, the current branch and remotes. Read the target repository's
   `AGENTS.md` and integration policy. Preserve existing work; commit only intended
   changes or use an isolated worktree rather than stashing unrelated work.
2. Establish the integration repository, remote and branch before merging:
   - For an existing PR, inspect its actual base and head:
     `gh pr view --json number,url,baseRefName,headRefName`.
   - From the returned PR URL, set `pr_host`, `pr_repository` (`owner/repo`) and
     `pr_number`. Query that exact PR, then verify its base repository and branch
     against configured remotes; checkout-based API placeholders can name another
     repository's same-numbered PR:
     `gh api --hostname "$pr_host" "repos/$pr_repository/pulls/$pr_number" --jq '.base | {ref, repository: .repo.full_name}'`.
   - Use the actual PR base when it agrees with target-repository policy. Resolve
     a mismatch under that policy before synchronization; an existing PR does not
     override repository integration or production approval boundaries.
   - Without a PR, use the integration base established by repository policy or
     explicit operator instructions. If neither establishes a base, report the
     missing fact; do not invent `origin/main` or use the remote default branch.
   - In Symphony itself, feature integration is `dev`; `main` is production.
     Follow its `AGENTS.md` approval boundaries for promotion and stable rollout.
3. Ensure rerere is enabled locally:
   - `git config rerere.enabled true`
   - `git config rerere.autoupdate true`
4. Fetch the verified integration remote and the feature branch's tracking remote
   if different. Confirm the current feature branch is the intended merge recipient.
5. If the feature branch is published, fast-forward from its verified tracking
   branch first. If it has diverged, inspect and reconcile without rewriting
   published history unless explicitly authorized. Skip this step for an
   unpublished branch; a missing remote feature branch is not a missing base.
6. Merge the verified integration ref, for example after setting
   `integration_remote` and `integration_base` from the verified facts above:
   - `git -c merge.conflictstyle=zdiff3 merge "$integration_remote/$integration_base"`
7. If conflicts appear, resolve them (see conflict guidance below), then:
   - `git add <files>`
   - `git commit` (or `git merge --continue` if the merge is paused)
8. Verify with project checks and review requirements from repository policy and
   the ticket. Synchronization does not waive validation or independent review.
9. Summarize the merge:
   - Record the verified base repository/ref and resulting head.
   - Call out the most challenging conflicts/files and how they were resolved.
   - Note any assumptions or follow-ups.

## Conflict Resolution Guidance (Best Practices)

- Inspect context before editing:
  - Use `git status` to list conflicted files.
  - Use `git diff` or `git diff --merge` to see conflict hunks.
  - Use `git diff :1:path/to/file :2:path/to/file` and
    `git diff :1:path/to/file :3:path/to/file` to compare base vs ours/theirs
    for a file-level view of intent.
  - With `merge.conflictstyle=zdiff3`, conflict markers include:
    - `<<<<<<<` ours, `|||||||` base, `=======` split, `>>>>>>>` theirs.
    - Matching lines near the start/end are trimmed out of the conflict region,
      so focus on the differing core.
  - Summarize the intent of both changes, decide the semantically correct
    outcome, then edit:
    - State what each side is trying to achieve (bug fix, refactor, rename,
      behavior change).
    - Identify the shared goal, if any, and whether one side supersedes the
      other.
    - Decide the final behavior first; only then craft the code to match that
      decision.
    - Prefer preserving invariants, API contracts, and user-visible behavior
      unless the conflict clearly indicates a deliberate change.
  - Open files and understand intent on both sides before choosing a resolution.
- Prefer minimal, intention-preserving edits:
  - Keep behavior consistent with the branch’s purpose.
  - Avoid accidental deletions or silent behavior changes.
- Resolve one file at a time and rerun tests after each logical batch.
- Use `ours/theirs` only when you are certain one side should win entirely.
- For complex conflicts, search for related files or definitions to align with
  the rest of the codebase.
- For generated files, resolve non-generated conflicts first, then regenerate:
  - Prefer resolving source files and handwritten logic before touching
    generated artifacts.
  - Run the CLI/tooling command that produced the generated file to recreate it
    cleanly, then stage the regenerated output.
- For import conflicts where intent is unclear, accept both sides first:
  - Keep all candidate imports temporarily, finish the merge, then run lint/type
    checks to remove unused or incorrect imports safely.
- After resolving, ensure no conflict markers remain:
  - `git diff --check`
- When unsure, note assumptions and ask for confirmation before finalizing the
  merge.

## When To Ask The User (Keep To A Minimum)

Do not ask for input unless there is no safe, reversible alternative. Prefer
making a best-effort decision, documenting the rationale, and proceeding.

Ask the user only when:

- The correct resolution depends on product intent or behavior not inferable
  from code, tests, or nearby documentation.
- The conflict crosses a user-visible contract, API surface, or migration where
  choosing incorrectly could break external consumers.
- A conflict requires selecting between two mutually exclusive designs with
  equivalent technical merit and no clear local signal.
- The merge introduces data loss, schema changes, or irreversible side effects
  without an obvious safe default.
- The branch is not the intended target, or the remote/branch names do not exist
  and cannot be determined locally.

Otherwise, proceed with the merge, explain the decision briefly in notes, and
leave a clear, reviewable commit history.
