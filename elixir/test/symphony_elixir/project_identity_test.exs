defmodule SymphonyElixir.ProjectIdentityTest do
  use ExUnit.Case, async: true

  @launcher Path.expand("../../../scripts/symphony", __DIR__)
  @runtime Path.expand("../fixtures/project_identity_runtime.py", __DIR__)

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-identity-#{System.pid()}-#{System.unique_integer([:positive])}")
    File.mkdir_p!(root)
    runtime = Path.join(root, "runtime")
    File.mkdir_p!(runtime)
    peer = Path.join(root, "container-runtime")
    File.cp!(@runtime, peer)
    File.chmod!(peer, 0o755)
    curl = Path.join(root, "curl")
    File.write!(curl, "#!/bin/sh\nprintf '%s\\n' '{\"agent\":{\"draining\":false,\"admission\":{\"ready\":true}},\"counts\":{\"running\":0}}'\n")
    File.chmod!(curl, 0o755)

    repos =
      for org <- ["one", "two"] do
        repo = Path.join([root, org, "service"])
        File.mkdir_p!(repo)
        {_, 0} = System.cmd("git", ["init", "-q", repo])
        {_, 0} = System.cmd("git", ["-C", repo, "remote", "add", "origin", "https://github.com/#{org}/service.git"])
        repo
      end

    namespace = "identity-#{System.pid()}-#{System.unique_integer([:positive])}"

    env = [
      {"SYMPHONY_HOME", Path.expand("../../..", __DIR__)},
      {"SYMPHONY_RUNNER_NAMESPACE", namespace},
      {"SYMPHONY_CONFIG_HOME", Path.join(root, "config")},
      {"SYMPHONY_SKILLS_DIR", Path.join(root, "skills")},
      {"SYMPHONY_CONTAINER_RUNTIME", peer},
      {"IDENTITY_RUNTIME", runtime},
      {"PATH", root <> ":" <> System.fetch_env!("PATH")}
    ]

    on_exit(fn -> File.rm_rf!(root) end)
    %{root: root, repos: repos, env: env, runtime: runtime, prefix: "symphony-#{namespace}"}
  end

  test "same-basename projects initialize and operate independently for both providers", ctx do
    for agent <- ["codex", "claude"] do
      ids =
        for {repo, index} <- Enum.with_index(ctx.repos) do
          assert {_, 0} = launch(ctx, repo, ["init", "--linear-project", "project-#{index}"])
          id = identity(ctx, repo)
          credentials = Path.join(ctx.root, "config/projects/#{id}.env")
          File.write!(credentials, "GH_TOKEN=sentinel-#{index}\n")
          assert {_, 0} = launch(ctx, repo, ["run", "--agent", agent, "--port", "#{4010 + index}"])
          data = container(ctx, "#{ctx.prefix}-#{id}")
          assert data["credentials"] == credentials
          assert data["storage"] == "#{ctx.prefix}-#{id}"
          assert {_, 0} = launch(ctx, repo, ["runner", "stop"])
          # Ordinary run cannot change provider; let the next pass start fresh.
          File.rm!(Path.join(ctx.runtime, "#{ctx.prefix}-#{id}.json"))
          File.rm!(Path.join(ctx.root, "config/runners/#{id}.env"))
          id
        end

      assert length(Enum.uniq(ids)) == 2
    end

    refute File.exists?(Path.join(ctx.root, "config/projects/service.env"))
  end

  test "repository identity preserves established storage and normalized remote spellings", ctx do
    [one, two] = ctx.repos
    assert identity(ctx, one) == "service-a655f1ffd4d3"
    assert identity(ctx, two) == "service-df56327e6745"
    {_, 0} = System.cmd("git", ["-C", one, "remote", "set-url", "origin", "git@github.com:one/service.git"])
    assert identity(ctx, one) == "service-a655f1ffd4d3"
  end

  test "completed journal cleanup evidence is moved without rewriting", ctx do
    repo = hd(ctx.repos)
    {id, _, _} = legacy!(ctx, repo)
    old = Path.join(ctx.root, "config/runners/service.transition.complete")
    File.mkdir_p!(old)
    File.write!(Path.join(old, "image.next"), "retained image evidence")
    assert {_, 0} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    assert File.read!(Path.join(ctx.root, "config/runners/#{id}.transition.complete/image.next")) == "retained image evidence"
    refute File.exists?(old)
  end

  test "migration retains credential bytes, stopped old runner and exact storage", ctx do
    repo = hd(ctx.repos)
    {id, old, name} = legacy!(ctx, repo)
    credentials = File.read!(Path.join(ctx.root, "config/projects/service.env"))
    assert {message, 0} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    assert message =~ "identity migrated"
    assert File.read!(Path.join(ctx.root, "config/projects/#{id}.env")) == credentials
    refute File.exists?(Path.join(ctx.runtime, "#{old}.json"))
    assert container(ctx, name)["running"] == false
    assert container(ctx, name)["storage"] == name
    assert {_, 0} = launch(ctx, repo, ["run"])
    assert container(ctx, name)["running"] == true
    # Idempotent explicit migration never rewrites credentials/workflow.
    assert {_, 0} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    assert File.read!(Path.join(ctx.root, "config/projects/#{id}.env")) == credentials
  end

  test "interrupted rename retains pending rollback and blocks ordinary operation until retry", ctx do
    repo = hd(ctx.repos)
    {id, old, name} = legacy!(ctx, repo)
    data = container(ctx, old)
    write_container(ctx, old <> "-before-update", data)
    write_container(ctx, old, Map.merge(data, %{"agent" => "claude", "image" => "fixture-claude-image"}))
    journal = Path.join(ctx.root, "config/runners/service.transition")
    File.mkdir_p!(journal)
    File.write!(Path.join(journal, "target"), "claude\n")
    File.write!(Path.join(journal, "port"), "4010\n")
    File.write!(Path.join(journal, "image"), "fixture-claude-image\n")
    previous = "SYMPHONY_RUNNER_PORT=4010\nSYMPHONY_RUNNER_AGENT=codex\nSYMPHONY_RUNNER_IMAGE_ID=fixture-codex-image\nSYMPHONY_RUNNER_PROFILE=none\n"
    File.write!(Path.join(journal, "previous.env"), previous)
    File.write!(Path.join(ctx.runtime, "fail-rename-once"), "")
    assert {_, 97} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    assert {message, 1} = launch(ctx, repo, ["run"])
    assert message =~ "migrate-project-identity"
    assert {_, 0} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    migrated = Path.join(ctx.root, "config/runners/#{id}.transition")
    assert File.read!(Path.join(migrated, "previous.env")) == previous
    assert container(ctx, name <> "-before-update")["image"] == "fixture-codex-image"
    assert {_, 0} = launch(ctx, repo, ["update", "--development-runner", "--rollback-runner"])
    assert container(ctx, name)["image"] == "fixture-codex-image"
    refute File.exists?(migrated)
  end

  test "unknown, foreign and mixed resources refuse migration before any move", ctx do
    repo = hd(ctx.repos)
    {id, old, name} = legacy!(ctx, repo)
    legacy = Path.join(ctx.root, "config/projects/service.env")
    target = Path.join(ctx.root, "config/projects/#{id}.env")
    File.write!(target, "GH_TOKEN=foreign\n")
    assert {message, 1} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    assert message =~ "mixed legacy"
    assert File.read!(target) == "GH_TOKEN=foreign\n"
    assert File.exists?(legacy)
    File.rm!(target)
    data = container(ctx, old)
    write_container(ctx, old, Map.put(data, "storage", "foreign"))
    assert {message, 1} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    assert message =~ "foreign runner storage"
    refute File.exists?(target)
    write_container(ctx, old, data)
    write_container(ctx, name, data)
    assert {message, 1} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    assert message =~ "both legacy"
    assert File.exists?(legacy)
    File.rm!(Path.join(ctx.runtime, "#{name}.json"))
    File.write!(Path.join(ctx.root, "config/runners/service.transition"), "unknown")
    assert {message, 1} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    assert message =~ "unsafe or foreign"
    assert File.exists?(legacy)
  end

  test "a foreign basename-era runner does not prevent independent new project operation", ctx do
    [one, two] = ctx.repos
    {_, old, _} = legacy!(ctx, one)
    original = container(ctx, old)
    File.write!(Path.join(two, "WORKFLOW.md"), "preexisting workflow")
    assert {_, 0} = launch(ctx, two, ["init", "--linear-project", "independent"])
    id = identity(ctx, two)
    File.write!(Path.join(ctx.root, "config/projects/#{id}.env"), "GH_TOKEN=second-project\n")
    assert {_, 0} = launch(ctx, two, ["run", "--port", "4011"])
    assert container(ctx, old) == original
    assert container(ctx, "#{ctx.prefix}-#{id}")["storage"] != original["storage"]
    assert File.read!(Path.join(ctx.root, "config/projects/service.env")) == "GH_TOKEN=legacy-fixture\n"
  end

  test "backup-only interrupted transition migrates and rolls back", ctx do
    repo = hd(ctx.repos)
    {id, old, name} = legacy!(ctx, repo)
    File.rename!(Path.join(ctx.runtime, "#{old}.json"), Path.join(ctx.runtime, "#{old}-before-update.json"))
    journal = Path.join(ctx.root, "config/runners/service.transition")
    File.mkdir_p!(journal)

    for {file, value} <- [
          {"target", "claude\n"},
          {"port", "4010\n"},
          {"image", "fixture-claude-image\n"},
          {"previous.env", "SYMPHONY_RUNNER_PORT=4010\nSYMPHONY_RUNNER_AGENT=codex\nSYMPHONY_RUNNER_IMAGE_ID=fixture-codex-image\nSYMPHONY_RUNNER_PROFILE=none\n"}
        ] do
      File.write!(Path.join(journal, file), value)
    end

    assert {_, 0} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    assert {_, 0} = launch(ctx, repo, ["update", "--development-runner", "--rollback-runner"])
    assert container(ctx, name)["running"] == true
    assert container(ctx, name)["image"] == "fixture-codex-image"
    refute File.exists?(Path.join(ctx.root, "config/runners/#{id}.transition"))
  end

  test "unproven basename credentials are never automatically adopted", ctx do
    repo = hd(ctx.repos)
    File.mkdir_p!(Path.join(ctx.root, "config/projects"))
    File.write!(Path.join(ctx.root, "config/projects/service.env"), "GH_TOKEN=unproven\n")
    assert {message, 1} = launch(ctx, repo, ["update", "--development-runner", "--migrate-project-identity"])
    assert message =~ "no owned retained runner"
    # A new same-basename repository is independent, even with legacy config present.
    assert {_, 0} = launch(ctx, repo, ["init", "--linear-project", "fresh"])
    refute File.read!(Path.join(ctx.root, "config/projects/#{identity(ctx, repo)}.env")) =~ "unproven"
  end

  defp legacy!(ctx, repo) do
    assert {_, 0} = launch(ctx, repo, ["init", "--linear-project", "legacy"])
    id = identity(ctx, repo)
    name = "#{ctx.prefix}-#{id}"
    File.write!(Path.join(ctx.root, "config/projects/#{id}.env"), "GH_TOKEN=legacy-fixture\n")
    assert {_, 0} = launch(ctx, repo, ["run"])
    assert {_, 0} = launch(ctx, repo, ["runner", "stop"])
    old = "#{ctx.prefix}-service"
    File.rename!(Path.join(ctx.runtime, "#{name}.json"), Path.join(ctx.runtime, "#{old}.json"))

    for base <- ["projects", "runners"] do
      File.rename!(Path.join(ctx.root, "config/#{base}/#{id}.env"), Path.join(ctx.root, "config/#{base}/service.env"))
    end

    {id, old, name}
  end

  defp identity(ctx, repo) do
    {output, 0} = System.cmd("bash", ["-c", "source \"$1\" --help >/dev/null; project_id \"$2\"", "identity", @launcher, repo], env: ctx.env)
    String.trim(output)
  end

  defp launch(ctx, repo, args), do: System.cmd("bash", [@launcher | args], cd: repo, env: ctx.env, stderr_to_stdout: true)
  defp container(ctx, name), do: Jason.decode!(File.read!(Path.join(ctx.runtime, name <> ".json")))
  defp write_container(ctx, name, data), do: File.write!(Path.join(ctx.runtime, name <> ".json"), Jason.encode!(data))
end
