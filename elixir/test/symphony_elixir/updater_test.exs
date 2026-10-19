defmodule SymphonyElixir.UpdaterTest do
  use ExUnit.Case, async: true

  @launcher Path.expand("../../../scripts/symphony", __DIR__)
  @git_env [{"GIT_CONFIG_GLOBAL", "/dev/null"}, {"GIT_CONFIG_NOSYSTEM", "1"}, {"GIT_CONFIG_COUNT", nil}]

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-update-#{System.unique_integer([:positive])}")
    seed = Path.join(root, "seed")
    remote = Path.join(root, "upstream.git")
    installation = Path.join(root, "installation")
    File.mkdir_p!(root)
    git!(root, ["init", "--bare", remote])
    git!(root, ["init", "--initial-branch=main", seed])
    File.mkdir_p!(Path.join(seed, "scripts"))
    stub = Path.join(seed, "scripts/symphony")
    File.write!(stub, "#!/bin/sh\nprintf '%s\\n' \"$1\" >> \"$FIXTURE_LOG\"\n")
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

  test "dirty installation is refused before fetching or running install", ctx do
    File.write!(Path.join(ctx.installation, "untracked.txt"), "preserved")
    advance!(ctx)
    assert {output, 1} = update(ctx)
    assert output =~ "uncommitted changes"
    assert git!(ctx.installation, ["rev-parse", "origin/main"]) == ctx.initial
    assert File.read!(Path.join(ctx.installation, "untracked.txt")) == "preserved"
    refute File.exists?(ctx.log)
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

  defp update(ctx) do
    env = @git_env ++ [{"SYMPHONY_HOME", ctx.installation}, {"FIXTURE_LOG", ctx.log}]
    System.cmd("bash", [@launcher, "update"], cd: ctx.installation, env: env, stderr_to_stdout: true)
  end
end
