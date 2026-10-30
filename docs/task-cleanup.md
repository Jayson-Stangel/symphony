# Task resource cleanup

Read when planning task-owned resources and before deleting branches, worktrees,
Docker resources or build caches. These procedures extend
[AGENTS.md](../AGENTS.md); stable-installation and ownership protections still
apply. Record the task inventory and retained resources in the existing task
record, without a separate inventory system.

- Cleanup is part of task completion. Track the local branches, remote branches,
  worktrees, Docker containers, images, networks and build caches created for the
  task. Use task-specific names or labels so ownership is clear. Remove disposable
  artifacts when validation finishes; report what was removed and anything retained,
  with its owner and reason. Do not leave cleanup as an unrecorded future task.
  Use concise existing task records; a separate inventory service or cleanup
  framework is not required. Routine cleanup covers only verified task-owned
  artifacts. Ambiguous ownership, shared resources or broader deletion require
  owner clearance and any missing operator authority before proceeding.
- Before Git cleanup, refresh the intended remote and inspect live PR state,
  branch tips, worktree status and unpushed commits. After a feature PR is verified
  merged into `dev`, delete its task-owned remote feature branch if it still exists,
  then remove clean, inactive task worktrees and delete the corresponding local
  branches. Verify the exact branch revision was integrated, including the PR's
  recorded head for squash merges; age or a matching branch name is insufficient.
  Confirm no peer still consumes the branch or worktree before removing it.
  Prune stale remote-tracking refs after confirming remote deletion. Preserve
  `dev`, `main`, open-PR branches, active work and unmerged or unpushed commits.
- Never force-delete branches or worktrees to make cleanup pass. Inspect every
  worktree for tracked and untracked changes, and preserve required ignored files
  and validation evidence before removal. Archive Codex-managed worktrees through
  the app's archive tool when available. Keep the current PR's branch and worktree
  until merge or an explicit handoff; record any remaining cleanup at handoff.
- Inspect Docker context, container state, labels, image references and mounts
  before deleting anything. Remove task-owned disposable containers after tests
  finish, then their unused images and networks. Use exact names or IDs and avoid
  forced removal. A stopped container or an image unused by containers may still
  belong to a configured runtime or a retained release/rollback; verify ownership
  and purpose rather than treating Docker's reclaimable count as deletion authority.
- Preserve stable and other active Symphony runtimes, their configured images,
  rollback artifacts, authentication, state and workspace volumes. These protections
  apply even when containers are stopped. Remove a task-owned disposable volume only
  after verifying it contains no credentials, user work or required evidence. Never
  use blanket `docker system prune --all --volumes` or global container/image/volume
  prune commands as routine task cleanup on a shared daemon.
- Keep build caches bounded. Prefer task-specific builders or cache scopes and
  remove them when no longer needed. On a shared builder, inspect usage and active
  builds before an explicitly scoped cache cleanup with age/retention limits; do
  not remove another task's needed cache or run broad pruning during its build.
  Verify Docker usage and Git state after cleanup and report retained resources
  or cleanup blockers without bypassing the stable-installation protections.
