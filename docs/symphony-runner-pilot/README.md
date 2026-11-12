# JAY-934 self-development pilot

The operator reviews this plan, the [pilot workflow](WORKFLOW.md) and
[JAY-934](https://linear.app/jayson-stangel/issue/JAY-934) before authorizing
activation. The pilot covers one Codex worker, one retry timestamp fix and one
review-ready PR against `Jayson-Stangel/symphony:dev`. The coordinator owns the
upstream PR, review and any separately authorized merge. The pilot ends with
the review handoff and, if requested, one revision on the same PR.

## Runtime and credentials

- Use a dedicated Linux x86-64 pilot installation of the verified 0.0.8 bundle,
  source revision `eb8c4e596b28767c6d8b3a8c3d5bf176d6813db9`. The
  [release manifest](https://github.com/Jayson-Stangel/symphony/releases/download/v0.0.8/release.json)
  records the Linux bundle SHA-256
  `7932158b01bde32982c7f223fa02292f8b79ef92c921333abe374681b2406823`.
  Run the verified installer with pilot-owned paths and invoke its installed
  command by absolute path. Keep its manager and image pinned for the full
  pilot, including any rework.
- Select the released Codex linux/amd64 image with the packaged
  `environment select --agent codex` command in the pilot context. The registry
  reference is
  `ghcr.io/jayson-stangel/symphony-codex@sha256:2523c49ecdc126b79c3219323646cc3bf92db36fc0239c6ba0419857aea48108`.
  The registry manifest references OCI config
  `sha256:06d26557bc4589e1f069b769a581c9b1d21c02d0efb9b04faef4309f26`.
  Record the local image ID returned by the selected Docker daemon, the
  selection and the running image. Confirm protocol 1 and the embedded source
  revision above. The manifest digest and Docker's reported local ID are
  recorded separately.
- Use namespace `selfdev-jay934`, loopback port `14034` after checking it is
  free, a pilot-owned launch checkout and separate config, runner state,
  workspace and Codex auth storage. Set `SYMPHONY_APP_HOME`,
  `SYMPHONY_CONFIG_HOME`, `SYMPHONY_RUNNER_HOME`,
  `SYMPHONY_RUNNER_NAMESPACE`, `SYMPHONY_CODEX_AUTH_VOLUME`,
  `SYMPHONY_BIN_DIR` and `SYMPHONY_SKILLS_DIR` in the pilot command context.
  Keep secrets outside the launch checkout. The launcher mounts that checkout
  read-only and workers edit their own clone. Keep the existing protected Codex
  sandbox and leave the host Docker socket and host credential stores outside
  the runner.
- Supply a dedicated Codex login and the minimum Linear access for project
  reads, workpad updates and the pilot issue's state changes. Confirm the
  actual Linear credential scope and keep it out of the workspace. Give the
  worker a GitHub credential with Contents read/write scoped only to
  [`Jayson-Stangel/symphony-jay934-pilot`](https://github.com/Jayson-Stangel/symphony-jay934-pilot),
  the private disposable repository used for branch handoff. Keep Actions
  disabled there. The coordinator publishes the tested commit to the upstream
  feature branch and opens the PR using a separate identity.

## Approval and execution

1. Review and adopt the narrowly scoped policy change through the ordinary
   `dev` PR process. Record the current upstream `dev` SHA containing that
   policy and the worker branch
   `codex/jay-934-retry-deadline` on JAY-934. Leave the issue in Backlog.
   Seed the disposable repository's `dev` with the same upstream commit so the
   worker can publish its branch with ordinary Contents write access.
2. With approval to prepare the isolated environment, materialize the reviewed
   pilot workflow as the root `WORKFLOW.md` in the separate launch checkout.
   Set its Git origin to the upstream repository so project identity resolves
   correctly. Verify the bundle and image, dedicated configuration and mounts,
   strict rendering of fresh and retry prompts, protected toolchain, and
   GitHub and Linear access. Verify the worker can use the disposable repository
   and the coordinator can fetch from it. The released image uses Elixir 1.19.6/OTP 28
   and Node 24.22.1; repository CI uses the pins in `elixir/mise.toml`. Record
   any checks deferred to CI. A failed boundary check leaves the issue held.
3. After explicit activation approval, create the temporary routing label
   `Symphony Pilot JAY-934` and verify its only intended attachment is JAY-934.
   With JAY-934 still in Backlog and zero eligible issues, start the dedicated
   listener. Verify its workflow hash, image, namespace and zero-worker
   dashboard. The label and active states narrow admission, and the
   `before_run` hook checks the exact workspace before every worker start.
   After explicit dispatch approval, apply the label to JAY-934 alone and
   move it to Todo. The coordinator monitors the dashboard and logs through
   handoff, verifies the issue, workspace and workpad, and drains on unexpected
   work, unresolved Linear access, or repeated attempts with no progress.
   The turn limit applies per attempt, so ending a turn can leave the issue
   eligible for another attempt. If a worker cannot finish after drain, use the
   isolated runner's supported stop command and preserve its workspace.
4. At Agent Review, fetch the disposable branch and verify its tip against the
   tested SHA in the workpad. Review the diff, then push that exact commit as the
   upstream feature branch and open the PR against `dev`. Verify Application
   checks, Package qualification, Browser qualification and PR description.
   Record any intentionally omitted checks. If rework is needed, record the findings,
   move only JAY-934 to Rework, and confirm that the same checkout and branch
   produce the next reviewable head. Fetch, verify and fast-forward the
   upstream feature branch with that commit. Further rework goes back to the
   coordinator. An open PR left for the operator remains in Human Review.
5. At handoff or an earlier stop, drain the dedicated runner, wait for zero
   workers, then stop it using the same pilot command context. Remove the
   opt-in and record the final issue, PR, CI and runtime evidence. Preserve
   open PR work and review evidence. Apply the repository cleanup procedure to
   verified pilot-owned resources and record any retained repository, branch,
   workspace, image reference or auth volume with its owner and removal
   condition. The coordinator retires the one-use routing label after checking
   its attachments and removes the expired policy exception through normal
   review.

The pilot passes when a cold workspace produces the red/green regression, a
reviewable PR and the expected CI on its exact head without manual workspace
repair. If review requests a revision, the same branch and PR must carry it.
Missing access, a failed gate or an incomplete handoff stays visible on JAY-934.
