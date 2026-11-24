defmodule SymphonyElixir.ProjectIdentityTest do
  use ExUnit.Case, async: true

  @launcher Path.expand("../../../scripts/symphony", __DIR__)
  @runtime Path.expand("../fixtures/project_identity_runtime.py", __DIR__)
  @commit String.duplicate("c", 40)

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-identity-#{System.pid()}-#{System.unique_integer([:positive])}")
    File.mkdir_p!(root)
    runtime = Path.join(root, "runtime")
    File.mkdir_p!(runtime)
    peer = Path.join(root, "container-runtime")
    File.cp!(@runtime, peer)
    File.chmod!(peer, 0o755)
    curl = Path.join(root, "curl")

    File.write!(
      curl,
      "#!/bin/sh\ncase \"$*\" in */resume*) draining=false; ready=true ;; *) draining=true; ready=false ;; esac\nprintf '{\"agent\":{\"draining\":%s,\"admission\":{\"ready\":%s}},\"counts\":{\"running\":0}}\\n' \"$draining\" \"$ready\"\n"
    )

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
          origin = "https://github.com/#{Enum.at(["one", "two"], index)}/service.git"
          assert {_, 0} = launch(ctx, ctx.root, ["init", "--repository", origin, "--linear-project", "project-#{index}", "--workflow-branch", "main"])
          id = identity(ctx, repo)
          credentials = Path.join(ctx.root, "config/projects/#{id}.env")
          File.write!(credentials, "GH_TOKEN=sentinel-#{index}\nLINEAR_API_KEY=linear-fixture\n")
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

  test "registered runs pin the verified default and confirm the detached listener", ctx do
    [one, two] = ctx.repos
    id_one = identity(ctx, one)
    id_two = identity(ctx, two)
    default = Path.join(ctx.runtime, "remote-default")
    File.write!(default, "original-default")

    for {repository, project} <- [{"one", "project-one"}, {"two", "project-two"}] do
      opts = if repository == "two", do: ["--workflow-branch", "explicit"], else: []
      args = ["init", "--repository", "https://github.com/#{repository}/service.git", "--linear-project", project]
      assert {_, 0} = launch(ctx, ctx.root, args ++ opts)
    end

    for id <- [id_one, id_two] do
      path = Path.join(ctx.root, "config/projects/#{id}.env")
      File.write!(path, "GH_TOKEN=fixture\nLINEAR_API_KEY=fixture\n")
    end

    one = ["--project", id_one]
    two = ["--project", id_two]
    assert {_, 0} = launch(ctx, ctx.root, one ++ ["run", "--port", "4010"])
    name_one = "#{ctx.prefix}-#{id_one}"
    name_two = "#{ctx.prefix}-#{id_two}"
    assert container(ctx, name_one)["workflow-branch"] == "original-default"
    assert File.read!(Path.join(ctx.root, "config/projects/#{id_one}.remote")) =~ "\noriginal-default\n"

    failure = Path.join(ctx.runtime, "remote-start-fail")
    File.write!(failure, "")
    assert {message, 1} = launch(ctx, ctx.root, two ++ ["run", "--port", "4011"])
    assert message =~ "did not initialize its remote workflow"
    refute message =~ "Symphony container:"
    refute container(ctx, name_two)["running"]
    assert container(ctx, name_one)["running"]
    File.rm!(failure)

    assert {_, 0} = launch(ctx, ctx.root, two ++ ["run"])
    assert %{"running" => true, "port" => "4011", "workflow-branch" => "explicit"} = container(ctx, name_two)
    File.write!(default, "changed-default")
    assert {_, 0} = launch(ctx, ctx.root, one ++ ["runner", "stop"])
    assert {_, 0} = launch(ctx, ctx.root, one ++ ["run"])
    assert %{"running" => true, "port" => "4010", "workflow-branch" => "original-default"} = container(ctx, name_one)
    assert container(ctx, name_two)["running"]
  end

  test "repository identity preserves established storage and normalized remote spellings", ctx do
    [one, two] = ctx.repos
    assert identity(ctx, one) == "service-a655f1ffd4d3"
    assert identity(ctx, two) == "service-df56327e6745"
    {_, 0} = System.cmd("git", ["-C", one, "remote", "set-url", "origin", "git@github.com:one/service.git"])
    assert identity(ctx, one) == "service-a655f1ffd4d3"
  end

  test "checkout commands direct operators to the migration entry point", ctx do
    repo = hd(ctx.repos)

    for args <- [["init", "--linear-project", "fixture"], ["run"], ["runner", "status"], ["update", "--agent", "codex"], ["update", "--rollback-runner"]] do
      assert {message, 1} = launch(ctx, repo, args)
      assert message =~ "project migrate"
    end

    refute File.exists?(Path.join(ctx.runtime, "calls.jsonl"))
  end

  test "the sole registration resolves even from an unrelated Git checkout", ctx do
    [registered, unrelated] = ctx.repos
    id = identity(ctx, registered)
    assert {_, 0} = launch(ctx, ctx.root, ["init", "--repository", "https://github.com/one/service.git", "--linear-project", "fixture", "--workflow-branch", "main"])

    credentials = Path.join(ctx.root, "config/projects/#{id}.env")
    File.write!(credentials, "GH_TOKEN=fixture\nLINEAR_API_KEY=fixture\n")
    assert {_, 0} = launch(ctx, ctx.root, ["run", "--agent", "claude"])

    for directory <- [ctx.root, unrelated] do
      assert {status, 0} = launch(ctx, directory, ["runner", "status"])
      assert status =~ "Isolated Symphony: running"
    end

    legacy = Path.join(ctx.root, "config/runners/#{identity(ctx, unrelated)}.env")
    File.write!(legacy, "SYMPHONY_RUNNER_AGENT=claude\n")
    assert {message, 1} = launch(ctx, unrelated, ["runner", "status"])
    assert message =~ "project migrate"
    assert {status, 0} = launch(ctx, unrelated, ["--project", id, "runner", "status"])
    assert status =~ "Isolated Symphony: running"
  end

  test "unlabeled retained volumes cannot be claimed by a fresh registration", ctx do
    repo = hd(ctx.repos)
    id = identity(ctx, repo)
    volume = Path.join(ctx.runtime, "#{ctx.prefix}-#{id}-workspaces.volume")
    File.write!(volume, "")
    assert {message, 1} = launch(ctx, ctx.root, ["init", "--repository", "https://github.com/one/service.git", "--linear-project", "fixture"])
    assert message =~ "foreign ownership or needs verified checkout migration"
    assert File.read!(volume) == ""
    refute File.exists?(Path.join(ctx.root, "config/projects/#{id}.remote"))
  end

  for removal_failure <- ["fail-before-backup-removal-once", "fail-backup-removal-once"] do
    @removal_failure removal_failure
    test "pending Claude migration recovers with unavailable workflow after #{@removal_failure}", ctx do
      assert_migration_recovers(ctx, @removal_failure)
    end
  end

  defp assert_migration_recovers(ctx, removal_failure) do
    repo = hd(ctx.repos)
    id = identity(ctx, repo)
    name = "#{ctx.prefix}-#{id}"
    File.mkdir_p!(Path.join(ctx.root, "config/projects"))
    File.write!(Path.join(repo, "WORKFLOW.md"), "---\ntracker:\n  kind: linear\n  provider:\n    project_slug: fixture\n    api_key: $LINEAR_API_KEY\n---\nfixture\n")
    File.write!(Path.join(ctx.root, "config/projects/#{id}.env"), "GH_TOKEN=fixture\nLINEAR_API_KEY=fixture\n")
    File.write!(Path.join(ctx.runtime, "migration-commit"), @commit)

    script = """
    source "$1" --help >/dev/null
    repo_root() { checkout_root; }
    runner_start "$PWD" 4010 claude
    """

    assert {_, 0} = System.cmd("bash", ["-euc", script, "checkout-fixture", @launcher], cd: repo, env: ctx.env, stderr_to_stdout: true)
    File.write!(Path.join(ctx.runtime, "fail-rename-once"), "")

    command = ["project", "migrate", "--linear-project", "fixture", "--workflow-branch", "main", "--apply", @commit]
    assert {_, 97} = launch(ctx, repo, command)
    assert %{"running" => false} = container(ctx, name <> "-before-update")
    log = Path.join(ctx.runtime, "calls.jsonl")
    File.write!(log, "")

    File.write!(Path.join(ctx.runtime, removal_failure), "")
    assert {message, 97} = launch(ctx, repo, command)
    assert message =~ @commit
    assert %{"running" => true, "linear-project" => "fixture", "workflow-branch" => "main"} = container(ctx, name)
    calls = File.read!(log)
    assert calls =~ "--remote-workflow-probe"
    refute calls =~ "target=/etc/symphony"
    backup = Path.join(ctx.runtime, name <> "-before-update.json")
    assert File.exists?(backup) == (removal_failure == "fail-before-backup-removal-once")

    # A completed cutover can settle even if the watched branch is unavailable.
    File.write!(log, "")
    File.write!(Path.join(ctx.runtime, "remote-probe-fail"), "")
    assert {_, 0} = launch(ctx, repo, command)
    refute File.read!(log) =~ "--remote-workflow-probe"
    refute File.exists?(backup)
    refute File.exists?(Path.join(ctx.root, "config/runners/#{id}.transition"))
  end

  defp identity(ctx, repo) do
    {output, 0} = System.cmd("bash", ["-c", "source \"$1\" --help >/dev/null; project_id \"$2\"", "identity", @launcher, repo], env: ctx.env)
    String.trim(output)
  end

  defp launch(ctx, repo, args), do: System.cmd("bash", [@launcher | args], cd: repo, env: ctx.env, stderr_to_stdout: true)
  defp container(ctx, name), do: Jason.decode!(File.read!(Path.join(ctx.runtime, name <> ".json")))
end
