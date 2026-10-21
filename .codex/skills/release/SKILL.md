---
name: release
description: Cut a Symphony release by bumping the committed version, landing it, tagging the merged commit, and verifying the Burrito release workflow. Use when asked to release, tag, or retag Symphony.
---

# Release

1. Inspect status, remotes and repository instructions. Verify this fork's
   integration `dev` and production `main` refs. Start a focused `codex/` feature
   branch from the latest verified `dev` in a clean development checkout or
   worktree. Never disturb unrelated changes or modify the stable installation.
2. Pick the requested version. If none is given, use the next patch after the
   latest `vX.Y.Z` tag.
3. Update `elixir/mix.exs` so `version: "X.Y.Z"` matches the intended tag.
   Search the old version and change other files only when they are true
   release-version sources, not examples.
4. Run `make -C elixir all`, then commit, push, and create a feature PR against
   `dev`. Land it only after the required validation, reviews and merge approval.
5. Prepare a promotion PR from `dev` to `main`. Before merging, present the
   complete included PR inventory with links/purposes, the promotion PR and exact
   head revision, and obtain explicit operator approval for that exact scope.
   Check the inventory against live Git history. A release request or approval
   to land in `dev` does not approve unrelated integrated changes; renew approval
   if the included PRs or revision change. Follow the repository's `AGENTS.md`.
6. After the approved promotion merges, fetch and verify the exact merged `main`
   revision and its `mix.exs` version, then create an annotated tag on that commit:

   ```sh
   git tag -a vX.Y.Z <merged-commit> -m "Symphony vX.Y.Z"
   git push origin vX.Y.Z
   ```

7. Watch `burrito-release` until it finishes. Verify the build, smoke, and
   release jobs pass and that the GitHub release has all expected assets.

Do not tag an uncommitted or unmerged revision. Do not move a published tag
without explicit user approval.

Stable rollout is a separate approval boundary. Present the proposed stable
revision, complete change inventory and runtime/project targets, and obtain
explicit approval before using the installed `symphony update`. Tagging or
approving a promotion alone does not authorize rollout.
