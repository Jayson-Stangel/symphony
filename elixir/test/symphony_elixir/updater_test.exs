defmodule SymphonyElixir.UpdaterTest do
  use ExUnit.Case, async: true

  @launcher Path.expand("../../../scripts/symphony", __DIR__)
  @git_env [{"GIT_CONFIG_GLOBAL", "/dev/null"}, {"GIT_CONFIG_NOSYSTEM", "1"}, {"GIT_CONFIG_COUNT", nil}]

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-update-#{System.pid()}-#{System.unique_integer([:positive])}")
    seed = Path.join(root, "seed")
    remote = Path.join(root, "upstream.git")
    installation = Path.join(root, "installation")
    File.mkdir_p!(root)
    git!(root, ["init", "--bare", remote])
    git!(root, ["init", "--initial-branch=main", seed])
    File.mkdir_p!(Path.join(seed, "scripts"))
    stub = Path.join(seed, "scripts/symphony")
    File.write!(stub, fixture_launcher())
    File.chmod!(stub, 0o755)
    git!(seed, ["add", "scripts/symphony"])
    commit!(seed, "Initial installation")
    git!(seed, ["remote", "add", "origin", remote])
    git!(seed, ["push", "origin", "main"])
    git!(remote, ["symbolic-ref", "HEAD", "refs/heads/main"])
    git!(root, ["clone", remote, installation])
    initial = git!(installation, ["rev-parse", "HEAD"])
    on_exit(fn -> File.rm_rf!(root) end)
    %{root: root, seed: seed, remote: remote, installation: installation, initial: initial, log: Path.join(root, "calls")}
  end

  test "update prunes stale tracking refs without pruning tags, local branches, or other remotes", ctx do
    git!(ctx.installation, ["update-ref", "refs/remotes/origin/dev/obsolete", ctx.initial])
    git!(ctx.installation, ["update-ref", "refs/remotes/origin/removed", ctx.initial])
    git!(ctx.installation, ["update-ref", "refs/remotes/other/retained", ctx.initial])
    git!(ctx.installation, ["branch", "imported/retained"])
    git!(ctx.installation, ["tag", "retained-tag"])
    git!(ctx.installation, ["config", "--add", "remote.origin.fetch", "+refs/heads/*:refs/heads/imported/*"])
    git!(ctx.installation, ["config", "--add", "remote.origin.fetch", "+refs/tags/*:refs/tags/*"])
    git!(ctx.installation, ["config", "fetch.pruneTags", "true"])
    advance!(ctx)
    git!(ctx.seed, ["push", "origin", "main:dev"])
    refspecs = git!(ctx.installation, ["config", "--get-all", "remote.origin.fetch"])

    assert {output, 0} = update(ctx)
    assert output =~ "Updated:"
    assert git!(ctx.installation, ["rev-parse", "HEAD"]) == git!(ctx.seed, ["rev-parse", "HEAD"])
    assert git!(ctx.installation, ["rev-parse", "origin/dev"]) == git!(ctx.seed, ["rev-parse", "HEAD"])
    assert git!(ctx.installation, ["for-each-ref", "--format=%(refname)", "refs/remotes/origin/dev/"]) == ""
    assert git!(ctx.installation, ["for-each-ref", "--format=%(refname)", "refs/remotes/origin/removed"]) == ""

    for ref <- ["refs/heads/imported/retained", "refs/tags/retained-tag", "refs/remotes/other/retained"] do
      assert git!(ctx.installation, ["rev-parse", ref]) == ctx.initial
    end

    assert git!(ctx.installation, ["config", "--get-all", "remote.origin.fetch"]) == refspecs
    assert git!(ctx.installation, ["status", "--porcelain"]) == ""
    assert File.read!(ctx.log) == "install\nsetup\n"
  end

  test "diverged main retains its unpushed commit and does not install or set up", ctx do
    File.write!(Path.join(ctx.installation, "local.txt"), "unpushed work")
    git!(ctx.installation, ["add", "local.txt"])
    commit!(ctx.installation, "Unpushed work")
    local_head = git!(ctx.installation, ["rev-parse", "HEAD"])
    advance!(ctx)
    assert {_output, code} = update(ctx)
    assert code != 0
    assert git!(ctx.installation, ["rev-parse", "HEAD"]) == local_head
    assert File.read!(Path.join(ctx.installation, "local.txt")) == "unpushed work"
    refute File.exists?(ctx.log)
  end

  test "local main ahead of remote retains its unpushed commit without installation", ctx do
    File.write!(Path.join(ctx.installation, "local.txt"), "unpushed work")
    git!(ctx.installation, ["add", "local.txt"])
    commit!(ctx.installation, "Unpushed local main")
    local_head = git!(ctx.installation, ["rev-parse", "HEAD"])
    assert {output, 1} = update(ctx)
    assert output =~ "outside fetched upstream main"
    assert git!(ctx.installation, ["rev-parse", "HEAD"]) == local_head
    assert File.read!(Path.join(ctx.installation, "local.txt")) == "unpushed work"
    refute File.exists?(ctx.log)
  end

  test "dirty installation is refused before fetching or running install", ctx do
    File.write!(Path.join(ctx.installation, "untracked.txt"), "preserved")
    advance!(ctx)
    assert {output, 1} = update(ctx)
    assert output =~ "uncommitted changes"
    assert git!(ctx.installation, ["rev-parse", "origin/main"]) == ctx.initial
    assert File.read!(Path.join(ctx.installation, "untracked.txt")) == "preserved"
    refute File.exists?(ctx.log)
  end

  test "main tracking dev is refused before fetch or installation", ctx do
    advance!(ctx)
    git!(ctx.seed, ["push", "origin", "main:dev"])
    git!(ctx.installation, ["config", "branch.main.merge", "refs/heads/dev"])
    assert {output, 1} = update(ctx)
    assert output =~ "requires the upstream main branch"
    assert git!(ctx.installation, ["rev-parse", "HEAD"]) == ctx.initial
    assert git!(ctx.installation, ["rev-parse", "origin/main"]) == ctx.initial
    refute File.exists?(ctx.log)
  end

  test "configured upstream remote names may contain a slash", ctx do
    git!(ctx.installation, ["remote", "rename", "origin", "fork/production"])
    advance!(ctx)
    assert {_, 0} = update(ctx)
    assert git!(ctx.installation, ["rev-parse", "HEAD"]) == git!(ctx.seed, ["rev-parse", "HEAD"])
  end

  test "local main upstream is refused before installation", ctx do
    git!(ctx.installation, ["config", "branch.main.remote", "."])
    assert {output, 1} = update(ctx)
    assert output =~ "main on a configured remote"
    refute File.exists?(ctx.log)
  end

  test "advisory lock survives helper exit and releases after updater death", ctx do
    definitions = launcher_definitions!(ctx)
    lock = Path.join(ctx.root, "transition.lock")
    assert {_, 137} = lock_command(ctx, definitions, lock, "runner_transition_lock \"$2\"; kill -KILL $$")
    assert {_, 0} = lock_command(ctx, definitions, lock, "printf recovered")
    assert File.regular?(lock <> ".guard")
    refute File.exists?(lock)
  end

  test "active updater and its in-flight child retain exclusion", ctx do
    definitions = launcher_definitions!(ctx)
    lock = Path.join(ctx.root, "transition.lock")
    acquired = Path.join(ctx.root, "acquired")
    # Killing the shell must not admit a rival while its synchronous effect is
    # still pending. After that child exits, ordinary retry needs no lock repair.
    task =
      Task.async(fn ->
        lock_command(ctx, definitions, lock, "sleep 1 & printf acquired > \"$3\"; kill -KILL $$", acquired)
      end)

    wait_for_file!(acquired)
    assert {output, 1} = lock_command(ctx, definitions, lock, ":")
    assert output =~ "another updater is active"
    assert {_, 137} = Task.await(task, 5_000)
    assert {_, 0} = lock_command(ctx, definitions, lock, ":")
  end

  test "legacy active or ownerless locks are preserved; proven dead owner recovers", ctx do
    definitions = launcher_definitions!(ctx)
    lock = Path.join(ctx.root, "transition.lock")
    File.mkdir!(lock)
    assert {output, 1} = lock_command(ctx, definitions, lock, ":")
    assert output =~ "no verifiable owner"
    File.write!(Path.join(lock, "owner"), System.pid())
    assert {output, 1} = lock_command(ctx, definitions, lock, ":")
    assert output =~ "another updater is active"
    # Obtain an actual terminated process ID rather than assume a large PID is free.
    assert {pid, 0} = System.cmd("bash", ["-c", "printf '%s' $$"])
    File.write!(Path.join(lock, "owner"), pid)
    assert {_, 0} = lock_command(ctx, definitions, lock, ":")
    refute File.exists?(lock)
  end

  test "symlink and unknown legacy lock records are refused without deletion", ctx do
    definitions = launcher_definitions!(ctx)
    lock = Path.join(ctx.root, "transition.lock")
    File.ln_s!(ctx.seed, lock <> ".guard")
    assert {output, 1} = lock_command(ctx, definitions, lock, ":")
    assert output =~ "unsafe updater lock"
    File.rm!(lock <> ".guard")
    File.mkdir!(lock)
    File.write!(Path.join(lock, "owner"), "not-a-pid")
    assert {output, 1} = lock_command(ctx, definitions, lock, ":")
    assert output =~ "invalid updater lock owner"
    assert File.read!(Path.join(lock, "owner")) == "not-a-pid"
  end

  test "setup failure after fast-forward is retried even when Git is current", ctx do
    advance!(ctx)
    fail = Path.join(ctx.root, "fail-setup")
    File.write!(fail, "once")
    assert {output, 47} = update(ctx, [], [{"FIXTURE_FAIL_SETUP", fail}])
    assert output =~ "simulated setup failure"
    assert git!(ctx.installation, ["rev-parse", "HEAD"]) == git!(ctx.seed, ["rev-parse", "HEAD"])
    refute output =~ "Updated:"
    assert {output, 0} = update(ctx)
    assert output =~ "Updated:"
    assert File.read!(ctx.log) == "install\nsetup\ninstall\nsetup\n"
  end

  test "installation failure after fast-forward repeats installation and setup", ctx do
    advance!(ctx)
    fail = Path.join(ctx.root, "fail-install")
    File.write!(fail, "once")
    assert {_, 46} = update(ctx, [], [{"FIXTURE_FAIL_INSTALL", fail}])
    assert git!(ctx.installation, ["rev-parse", "HEAD"]) == git!(ctx.seed, ["rev-parse", "HEAD"])
    assert {_, 0} = update(ctx)
    assert File.read!(ctx.log) == "install\ninstall\nsetup\n"
  end

  test "current source still completes idempotent installation and setup", ctx do
    assert {output, 0} = update(ctx)
    assert output =~ "Updated: #{ctx.initial} -> #{ctx.initial}"
    assert File.read!(ctx.log) == "install\nsetup\n"
  end

  test "cross-revision handoff uses only new functions and preserves resolved paths", ctx do
    script = Path.join(ctx.seed, "scripts/symphony")
    File.write!(script, fixture_launcher("NEW "))
    git!(ctx.seed, ["add", "scripts/symphony"])
    commit!(ctx.seed, "New transition implementation")
    git!(ctx.seed, ["push", "origin", "main"])
    selected = git!(ctx.seed, ["rev-parse", "HEAD"])
    bin = Path.join(ctx.root, "selected-bin")
    config = Path.join(ctx.root, "selected-config")
    skills = Path.join(ctx.root, "selected-skills")
    runner = Path.join(ctx.root, "explicit-runners")
    details = Path.join(ctx.root, "details")
    fetches = Path.join(ctx.root, "fetches")
    real_git = System.find_executable("git")
    peer_dir = Path.join(ctx.root, "git-peer")
    File.mkdir!(peer_dir)
    File.write!(Path.join(peer_dir, "git"), "#!/bin/bash\nif [ \"${3:-}\" = fetch ]; then printf 'fetch\\n' >> \"$FIXTURE_FETCHES\"; fi\nexec \"$FIXTURE_REAL_GIT\" \"$@\"\n")
    File.chmod!(Path.join(peer_dir, "git"), 0o755)
    args = ["--agent", "claude", "--bin-dir", bin, "--config-dir", config, "--skills-dir", skills]
    env = [{"SYMPHONY_RUNNER_HOME", runner}, {"FIXTURE_DETAILS", details}, {"FIXTURE_FETCHES", fetches}, {"FIXTURE_REAL_GIT", real_git}, {"PATH", peer_dir <> ":" <> System.fetch_env!("PATH")}]
    assert {output, 0} = update(ctx, args, env)
    assert output =~ "Updated: #{ctx.initial} -> #{selected}"
    assert File.read!(ctx.log) == "NEW install\nNEW setup\nNEW transition claude\n"
    assert File.read!(details) == Enum.join([bin, config, skills, runner, git!(ctx.installation, ["rev-parse", "--show-toplevel"]), selected], "\n") <> "\n"
    assert File.read!(fetches) == "fetch\n"
  end

  test "handoff refuses source changes introduced during setup", ctx do
    marker = Path.join(ctx.installation, "unexpected")
    assert {output, 1} = update(ctx, ["--agent", "claude"], [{"FIXTURE_DIRTY_SETUP", marker}])
    assert output =~ "source changed during setup"
    refute File.read!(ctx.log) =~ "transition"
    assert File.read!(marker) == "preserved"
  end

  defp fixture_launcher(prefix \\ "") do
    [definitions, main] = String.split(File.read!(@launcher), "\ncase \"${1:-}\" in", parts: 2)

    overrides = """
    install() {
      printf '#{prefix}install\\n' >> \"$FIXTURE_LOG\"
      if [ -f \"${FIXTURE_FAIL_INSTALL:-/nonexistent}\" ]; then rm \"$FIXTURE_FAIL_INSTALL\"; exit 46; fi
    }
    setup() {
      printf '#{prefix}setup\\n' >> \"$FIXTURE_LOG\"
      if [ -f \"${FIXTURE_FAIL_SETUP:-/nonexistent}\" ]; then
        rm \"$FIXTURE_FAIL_SETUP\"; printf 'simulated setup failure\\n'; exit 47
      fi
      if [ -n \"${FIXTURE_DIRTY_SETUP:-}\" ]; then printf preserved > \"$FIXTURE_DIRTY_SETUP\"; fi
    }
    runner_transition() {
      printf '#{prefix}transition %s\\n' \"$2\" >> \"$FIXTURE_LOG\"
      printf '%s\\n' \"$bin_dir\" \"$config_home\" \"$skills_dir\" \"$runner_home\" \"$1\" \"$(source_revision)\" > \"$FIXTURE_DETAILS\"
    }
    """

    definitions <> overrides <> "\ncase \"${1:-}\" in" <> main
  end

  defp launcher_definitions!(ctx) do
    [definitions, _] = String.split(File.read!(@launcher), "\ncase \"${1:-}\" in", parts: 2)
    path = Path.join(ctx.root, "definitions.sh")
    File.write!(path, definitions)
    path
  end

  defp lock_command(ctx, definitions, lock, effect, marker \\ "") do
    System.cmd("bash", ["-c", ~s(source "$1"; runner_transition_lock "$2"; ) <> effect, "fixture", definitions, lock, marker], env: [{"SYMPHONY_HOME", ctx.installation}], stderr_to_stdout: true)
  end

  defp wait_for_file!(path, attempts \\ 100)
  defp wait_for_file!(path, 0), do: flunk("lock fixture did not publish #{path}")

  defp wait_for_file!(path, attempts) do
    if File.exists?(path),
      do: :ok,
      else:
        (
          Process.sleep(10)
          wait_for_file!(path, attempts - 1)
        )
  end

  defp advance!(ctx) do
    File.write!(Path.join(ctx.seed, "update.txt"), "upstream change")
    git!(ctx.seed, ["add", "update.txt"])
    commit!(ctx.seed, "Upstream update")
    git!(ctx.seed, ["push", "origin", "main"])
  end

  defp commit!(repo, message), do: git!(repo, ["-c", "user.name=Updater Test", "-c", "user.email=updater@example.test", "commit", "-m", message])

  defp git!(repo, args) do
    {output, code} = System.cmd("git", args, cd: repo, env: @git_env, stderr_to_stdout: true)
    assert code == 0, output
    String.trim(output)
  end

  defp update(ctx, args \\ [], extra_env \\ []) do
    env =
      @git_env ++
        [
          {"SYMPHONY_HOME", ctx.installation},
          {"FIXTURE_LOG", ctx.log},
          {"SYMPHONY_BIN_DIR", Path.join(ctx.root, "bin")},
          {"SYMPHONY_CONFIG_HOME", Path.join(ctx.root, "config")},
          {"SYMPHONY_SKILLS_DIR", Path.join(ctx.root, "skills")},
          {"SYMPHONY_RUNNER_HOME", nil}
        ] ++ extra_env

    System.cmd("bash", [@launcher, "update" | args], cd: ctx.installation, env: env, stderr_to_stdout: true)
  end
end
