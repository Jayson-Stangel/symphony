---
name: symphony-push
description:
  Publish a Symphony-managed task safely by pushing its branch and creating or
  updating its pull request. Do not use for unrelated repositories or work.
---

# Push

## Prerequisites

- `gh` CLI is installed and available in `PATH`.
- `gh auth status` succeeds for GitHub operations in this repo.

## Repository policy

Symphony manages repositories with different languages and build systems. Read
the target repository's `AGENTS.md`, `WORKFLOW.md`, development documentation,
and ticket validation requirements before choosing checks or PR formatting.
Use that repository's commands and tools. Symphony's own `make -C elixir all`
and `mix pr_body.check` apply only when the target repository requires them;
they are not prerequisites for using this skill. Do not install or report
missing Elixir tooling for a project that does not use it.

## Goals

- Push current branch changes to `origin` safely.
- Create a PR if none exists for the branch, otherwise update the existing PR.
- Keep branch history clean when remote has moved.

## Related Skills

- `symphony-pull`: use this when push is rejected or sync is not clean (non-fast-forward,
  merge conflict risk, or stale branch).

## Steps

1. Identify current branch and confirm remote state.
2. Run the target repository's required validation before pushing.
3. Push branch to `origin` with upstream tracking if needed, using whatever
   remote URL is already configured.
4. If push is not clean/rejected:
   - If the failure is a non-fast-forward or sync problem, run the `symphony-pull`
     skill to merge `origin/main`, resolve conflicts, and rerun validation.
   - Push again; use `--force-with-lease` only when history was rewritten.
   - If the failure is due to auth, permissions, or workflow restrictions on
     the configured remote, stop and surface the exact error instead of
     rewriting remotes or switching protocols as a workaround.

5. Ensure a PR exists for the branch:
   - If no PR exists, create one.
   - If a PR exists and is open, update it.
   - If branch is tied to a closed/merged PR, create a new branch + PR.
   - Write a proper PR title that clearly describes the change outcome
   - For branch updates, explicitly reconsider whether current PR title still
     matches the latest scope; update it if it no longer does.
6. Write/update PR body using the target repository's PR template when present:
   - Fill every section with concrete content for this change.
   - Replace all placeholder comments (`<!-- ... -->`).
   - Keep bullets/checkboxes where template expects them.
   - If PR already exists, refresh body content so it reflects the total PR
     scope (all intended work on the branch), not just the newest commits,
     including newly added work, removed work, or changed approach.
   - Do not reuse stale description text from earlier iterations.
   - If no template exists, describe the outcome, rationale, validation results,
     and material limitations without creating a template or borrowing one from
     Symphony.
7. Run the target repository's PR-body validator when one is documented. Otherwise
   review the body against its requirements and actual validation evidence.
8. Reply with the PR URL from `gh pr view`.

## Commands

```sh
# Identify branch
branch=$(git branch --show-current)

# Before pushing, run the validation commands discovered in the target repo.
# For example, use npm run validate only if that repository requires it.

# Initial push: respect the current origin remote.
git push -u origin HEAD

# If that failed because the remote moved, use the symphony-pull skill. After
# pull-skill resolution and re-validation, retry the normal push:
git push -u origin HEAD

# If the configured remote rejects the push for auth, permissions, or workflow
# restrictions, stop and surface the exact error.

# Only if history was rewritten locally:
git push --force-with-lease origin HEAD

# Ensure a PR exists (create only if missing)
pr_state=$(gh pr view --json state -q .state 2>/dev/null || true)
if [ "$pr_state" = "MERGED" ] || [ "$pr_state" = "CLOSED" ]; then
  echo "Current branch is tied to a closed PR; create a new branch + PR." >&2
  exit 1
fi

# Write a clear, human-friendly title that summarizes the shipped change.
pr_title="<clear PR title written for this change>"
if [ -z "$pr_state" ]; then
  gh pr create --title "$pr_title"
else
  # REST avoids deprecated Projects (classic) queries in older gh pr edit.
  pr_number=$(gh pr view --json number -q .number)
  gh api --method PATCH "repos/{owner}/{repo}/pulls/$pr_number" \
    -f title="$pr_title" --jq .html_url
fi

# Write the body to a file, following the target repo's template if present.
# Example workflow:
# 1) draft the body and run the repo's documented body validator, if any
# 2) publish the validated file via REST (preserves actual newlines)
# 3) re-read the published title/body and compare with the current diff
pr_number=$(gh pr view --json number -q .number)
gh api --method PATCH "repos/{owner}/{repo}/pulls/$pr_number" \
  -F body=@/tmp/pr_body.md --jq .html_url

gh pr view --json title,body

# Show PR URL for the reply
gh pr view --json url -q .url
```

## Notes

- If a GitHub CLI command fails on deprecated `projectCards` / Projects
  (classic), use explicit `gh pr view --json <needed-fields>` reads or the
  equivalent `gh api` REST endpoint, then verify the result. This is a CLI/API
  compatibility failure, not a requirement to configure GitHub Projects or a
  repository dependency. Check `gh --version` inside the worker; the host's
  version does not establish the container's version. Do not silently ignore
  unrelated auth, permission, or network failures.
- Do not use `--force`; only use `--force-with-lease` as the last resort.
- Distinguish sync problems from remote auth/permission problems:
  - Use the `symphony-pull` skill for non-fast-forward or stale-branch issues.
  - Surface auth, permissions, or workflow restrictions directly instead of
    changing remotes or protocols.
