---
name: symphony-push
description:
  Publish a Symphony-managed task safely by pushing its branch and creating or
  updating its pull request. Do not use for unrelated repositories or work.
---

# Push

## Prerequisites

- `gh` CLI is installed and available in `PATH`.

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

1. Inspect status, the current branch and remotes. Derive `integration_host` from
   the verified remote or PR URL. Establish the repository and integration base
   from its `AGENTS.md`/integration policy and any existing PR's actual base.
   Inspect an existing PR by its verified URL:
   `gh pr view "$pr_url" --json number,url,baseRefName,headRefName`.
   Verify the URL's host and base repository against the remotes, as in
   `symphony-pull`. Retain that host for every subsequent CLI/API operation.
   Resolve policy/base mismatches before publishing; do not infer `main` or the
   remote default branch when no base is established. Symphony feature PRs target
   `dev`; its `main` promotion and stable rollout retain separate exact approvals.
2. Run the target repository's required validation before pushing.
3. Push branch to `origin` with upstream tracking if needed, using whatever
   remote URL is already configured.
4. If push is not clean/rejected:
   - If the failure is a non-fast-forward or sync problem, run the `symphony-pull`
     skill to synchronize the verified PR/policy integration base, resolve
     conflicts, and rerun required validation and review.
   - Retry a normal push. Rewriting published history and using
     `--force-with-lease` require explicit authorization.
   - If the failure is due to auth, permissions, or workflow restrictions on
     the configured remote, stop and surface the exact error instead of
     rewriting remotes or switching protocols as a workaround.

5. Ensure a PR exists for the branch:
   - If no PR exists, create one with the verified integration repository and base
     explicitly selected. A failed lookup is not proof that no PR exists; surface
     auth/network/permission failures instead of falling back to a default base.
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
8. Reply with the retained verified PR URL from the explicit-number,
   host-qualified readback.

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

# Only when rewriting published history and this force push are explicitly authorized:
git push --force-with-lease origin HEAD

# Ensure a PR exists (create only if missing)
# integration_host, integration_repository (owner/repo) and integration_base come
# from verified remote/policy/PR URL metadata, including the host on github.com.
# feature_repository is the verified source owner/repo on that same GitHub host.
# Use native source/base filters and all pages; retain the newest exact-source match.
# A failed lookup stops before any PR creation or metadata mutation.
feature_owner=${feature_repository%%/*}
pr_rows=$(gh api --hostname "$integration_host" --method GET --paginate \
  "repos/$integration_repository/pulls" \
  -f head="$feature_owner:$branch" -f base="$integration_base" \
  -f state=all -f sort=created -f direction=desc \
  --jq ".[] | select(.head.repo.full_name == \"$feature_repository\") | [.number, (if .merged_at then \"MERGED\" else (.state | ascii_upcase) end)] | @tsv") || exit 1
# Native created-descending ordering makes the first matching row the newest PR.
# No matching PR across all pages yields an empty number/state.
read -r pr_number pr_state <<< "$pr_rows"
if [ "$pr_state" = "MERGED" ] || [ "$pr_state" = "CLOSED" ]; then
  echo "Current branch is tied to a closed PR; create a new branch + PR." >&2
  exit 1
fi

# Write a clear, human-friendly title that summarizes the shipped change.
pr_title="<clear PR title written for this change>"
if [ -z "$pr_state" ]; then
  pr_url=$(gh pr create --repo "$integration_host/$integration_repository" --base "$integration_base" --title "$pr_title") || exit 1
  pr_number=$(gh pr view "$pr_url" --repo "$integration_host/$integration_repository" --json number -q .number) || exit 1
else
  # REST avoids deprecated Projects (classic) queries in older gh pr edit.
  gh api --hostname "$integration_host" --method PATCH "repos/$integration_repository/pulls/$pr_number" \
    -f title="$pr_title" --jq .html_url
fi

# Write the body to a file, following the target repo's template if present.
# Example workflow:
# 1) draft the body and run the repo's documented body validator, if any
# 2) publish the validated file via REST (preserves actual newlines)
# 3) re-read the published title/body and compare with the current diff
gh api --hostname "$integration_host" --method PATCH "repos/$integration_repository/pulls/$pr_number" \
  -F body=@/tmp/pr_body.md --jq .html_url

gh pr view "$pr_number" --repo "$integration_host/$integration_repository" --json title,body

# Show PR URL for the reply
gh pr view "$pr_number" --repo "$integration_host/$integration_repository" --json url -q .url
```

## Notes

- If a GitHub CLI command fails on deprecated `projectCards` / Projects
  (classic), use explicit PR-number reads with the verified host-qualified
  `--repo`, or the equivalent `gh api --hostname` REST endpoint, then verify
  the result. This is a CLI/API
  compatibility failure, not a requirement to configure GitHub Projects or a
  repository dependency. Check `gh --version` inside the worker; the host's
  version does not establish the container's version. Do not silently ignore
  unrelated auth, permission, or network failures.
- Do not use `--force`. Use `--force-with-lease` only with explicit authorization
  to rewrite the published history; normal synchronization uses merges.
- Distinguish sync problems from remote auth/permission problems:
  - Use the `symphony-pull` skill for non-fast-forward or stale-branch issues.
  - Surface auth, permissions, or workflow restrictions directly instead of
    changing remotes or protocols.
