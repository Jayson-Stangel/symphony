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
3. Set `scripts/release/manager-version` to the fresh manager version. The normal
   release uses committed `scripts/release/release-schema=2` for independent
   components; preserve the separate engine version in `elixir/mix.exs` unless
   the engine itself requires a version change. Engine/image labels describe
   their actual build source and version, including when verified components
   are reused. Never republish or overwrite a completed release (including v0.0.4).
   Validate the selected identity with `manager_version.py`; protected tag
   preflight refuses mismatched tags and completed releases before fanout.
4. Run `make -C elixir all`, then commit, push, and create a feature PR against
   `dev`. Land it only after the required validation, reviews and merge approval.
5. Prepare a promotion PR from `dev` to `main`. Before merging, present the
   complete included PR inventory with links/purposes, the promotion PR and exact
   head revision, and obtain explicit operator approval for that exact scope.
   Check the inventory against live Git history. A release request or approval
   to land in `dev` does not approve unrelated integrated changes; renew approval
   if the included PRs or revision change. Follow the repository's `AGENTS.md`.
6. After the approved promotion merges, fetch and verify the exact merged `main`
   revision and its `scripts/release/manager-version`, then create an annotated tag on that commit:

   ```sh
   git tag -a vX.Y.Z <merged-commit> -m "Symphony vX.Y.Z"
   git push origin vX.Y.Z
   ```

7. For nonpublishing proof before tagging, dispatch `burrito-release.yml` on the
   exact reviewed feature or integration ref with `retain_diagnostic=true` and
   `engine_only=false`, `controller_only=false`. The normal caller forwards the
   committed schema; a direct runner dispatch's explicit schema override is
   diagnostic policy, not normal-release acceptance. Require both native manager
   archives, all four selected components and actual qualification/cleanup.
   Record omitted prior-package or migration lifecycles explicitly.
8. Watch `burrito-release` until it finishes. Verify the build, smoke, and
   release jobs pass and that the GitHub release has all expected assets.

Do not tag an uncommitted or unmerged revision. Do not move a published tag
without explicit user approval.

Stable rollout is a separate approval boundary. Present the proposed stable
revision, complete change inventory and runtime/project targets, and obtain
explicit approval before using the installed `symphony update`. Tagging or
approving a promotion alone does not authorize rollout.
