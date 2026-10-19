defmodule SymphonyElixir.RunnerLifecycleTest do
  use ExUnit.Case, async: true

  @launcher Path.expand("../../../scripts/symphony", __DIR__)

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-runner-#{System.unique_integer([:positive])}")
    repo = Path.join(root, "repo")
    config = Path.join(root, "config")
    runtime = Path.join(root, "container-runtime")
    File.mkdir_p!(repo)
    File.mkdir_p!(Path.join(config, "projects"))
    File.mkdir_p!(Path.join(config, "runners"))
    {_, 0} = System.cmd("git", ["init", "-q", repo])
    {_, 0} = System.cmd("git", ["-C", repo, "remote", "add", "origin", "https://github.com/example/service.git"])
    File.write!(Path.join(repo, "WORKFLOW.md"), "fixture workflow")
    File.write!(Path.join(config, "projects/service.env"), "GH_TOKEN=fixture\n")

    File.write!(runtime, """
    #!/bin/bash
    set -eu
    printf '%s\\t' "$@" >> "$FIXTURE_LOG"
    printf '\\n' >> "$FIXTURE_LOG"
    state=$FIXTURE_STATE
    case "${!#}" in *-before-update) state=$FIXTURE_STATE.backup ;; esac
    case "$1" in
      inspect)
        [ -f "$state" ] || { printf '\\n'; exit 1; }
        case "$*" in
          *State.Running*) cat "$state" ;;
          *com.symphony.mode*) echo listener ;;
          *com.symphony.agent*) cat "$state.agent" ;;
          *com.symphony.port*) cat "$state.port" ;;
          *com.symphony.profile*) cat "$state.profile" ;;
          *"{{.Image}}"*) cat "$state.image" ;;
          *com.symphony.storage*) cat "$state.storage" ;;
          *Mounts*) storage=$(cat "$state.storage"); printf '%s-workspaces /workspaces\\n%s-state /var/lib/symphony\\n' "$storage" "$storage" ;;
        esac ;;
      image)
        case "$*" in
          *com.symphony.agent*)
            case "$*" in *runner-claude*|*fixture-claude-image*) echo claude ;; *) echo codex ;; esac ;;
          *Config.User*) echo 11001:11001 ;;
          *)
            case "$*" in
              *runner-claude*|*fixture-claude-image*) echo fixture-claude-image ;;
              *) echo "${FIXTURE_IMAGE_ID:-fixture-image}" ;;
            esac ;;
        esac ;;
      volume)
        case "$*" in
          *--format*)
            [ "${FIXTURE_FOREIGN_VOLUME:-false}" != true ] || { echo foreign; exit 0; }
            name=${!#}; printf '%s' "${name%-claude-auth}" ;;
        esac ;;
      ps) printf '%s' "${FIXTURE_PROFILE_IN_USE:-}" ;;
      run)
        case "$*" in
          *--detach*)
            echo true > "$FIXTURE_STATE"
            printf '%s' "${!#}" > "$FIXTURE_STATE.image"
            for arg in "$@"; do
              case "$arg" in
                com.symphony.storage=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.storage" ;;
                com.symphony.agent=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.agent" ;;
                com.symphony.port=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.port" ;;
                com.symphony.profile=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.profile" ;;
              esac
            done ;;
          *"auth.mjs attest"*) printf '%s\\n' '{"version":1,"policy":1,"profile":"#{String.duplicate("a", 64)}","overages_disabled":true,"attested_at":"#{DateTime.utc_now() |> DateTime.to_iso8601()}"}' ;;
          *"auth.mjs check"*) printf '%s\\n' '{"ready":true,"model_requests":0}' ;;
        esac ;;
      stop) echo false > "$FIXTURE_STATE" ;;
      rm) rm -f "$state" ;;
      rename)
        from=$FIXTURE_STATE; to=$FIXTURE_STATE.backup
        for suffix in '' .agent .port .profile .image .storage; do
          if [ -f "$from$suffix" ]; then mv "$from$suffix" "$to$suffix"; fi
        done ;;
    esac
    """)

    File.chmod!(runtime, 0o755)
    on_exit(fn -> File.rm_rf!(root) end)

    env = [
      {"SYMPHONY_CONFIG_HOME", config},
      {"SYMPHONY_RUNNER_HOME", Path.join(config, "runners")},
      {"SYMPHONY_CONTAINER_RUNTIME", runtime},
      {"FIXTURE_LOG", Path.join(root, "calls")},
      {"FIXTURE_STATE", Path.join(root, "running")}
    ]

    %{repo: repo, config: config, root: root, env: env, log: Path.join(root, "calls"), state: Path.join(root, "running")}
  end

  test "missing container inspection discards stdout before reporting or creating", ctx do
    assert {"Isolated worker: not running\n", 0} = launch(ctx, "status")
    assert {_, 0} = launch(ctx, "start")

    calls = File.read!(ctx.log) |> String.split("\n", trim: true)
    assert Enum.any?(calls, &String.starts_with?(&1, "run\t"))
    refute Enum.any?(calls, &String.starts_with?(&1, "rm\t"))
    refute Enum.any?(calls, &(String.starts_with?(&1, "inspect\t") and String.contains?(&1, "com.symphony.storage")))
  end

  test "runner recreation reuses repository volumes and stops gracefully", ctx do
    assert {_, 0} = launch(ctx, "start")
    assert {status, 0} = launch(ctx, "status")
    assert status =~ "Workspaces: /workspaces (volume symphony-service-"
    assert {_, 0} = launch(ctx, "stop")
    assert {_, 0} = launch(ctx, "start")

    calls = File.read!(ctx.log) |> String.split("\n", trim: true)
    runs = Enum.filter(calls, &String.starts_with?(&1, "run\t"))
    assert [first, second] = runs
    assert first == second
    assert first =~ "--restart\tunless-stopped\t"
    refute first =~ "--rm\t"
    assert first =~ "-workspaces:/workspaces\t"
    assert first =~ "-state:/var/lib/symphony\t"
    assert Enum.any?(calls, &String.starts_with?(&1, "stop\t--time\t30\t"))
    stop_index = Enum.find_index(calls, &String.starts_with?(&1, "stop\t"))
    remove_index = Enum.find_index(calls, &String.starts_with?(&1, "rm\t"))
    assert stop_index < remove_index
    refute Enum.any?(calls, &String.starts_with?(&1, "volume\trm\t"))
  end

  test "official stop retains the qualified transition source and metadata", ctx do
    assert {_, 0} = launch(ctx, "start")
    metadata = File.read!(Path.join(ctx.config, "runners/service.env"))
    File.write!(ctx.log, "")
    assert {_, 0} = launch(ctx, "stop")
    assert {"Isolated worker: stopped (false)\n", 0} = launch(ctx, "status")
    assert File.read!(Path.join(ctx.config, "runners/service.env")) == metadata
    calls = File.read!(ctx.log)
    assert calls =~ "stop\t--time\t30\tsymphony-service"
    refute calls =~ "rm\t"
    assert calls =~ "com.symphony.storage"
  end

  test "development updater transitions an officially stopped runner without changing source or global links", ctx do
    dev_env = [{"SYMPHONY_RUNNER_NAMESPACE", "review"}]
    assert {_, 0} = launch_args(ctx, ["run"], dev_env)
    assert {_, 0} = launch_args(ctx, ["runner", "stop"], dev_env)
    assert {_, 0} = launch_args(ctx, ["auth", "claude", "attest", "--overages-disabled"], dev_env)
    curl = Path.join(ctx.root, "curl")
    File.write!(curl, "#!/bin/sh\nprintf '%s' '{\"agent\":{\"draining\":false,\"readiness\":{\"ready\":true}}}'\n")
    File.chmod!(curl, 0o755)
    env = dev_env ++ [{"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
    File.write!(ctx.log, "")
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "Runner transition complete: claude"
    calls = File.read!(ctx.log)
    assert calls =~ "rename\tsymphony-review-service\tsymphony-review-service-before-update"
    assert calls =~ "/state/agent-draining"
    assert calls =~ "com.symphony.agent=claude"
    assert calls =~ "tag\tfixture-claude-image\tsymphony-review-runner-claude:local"
    assert calls =~ "rm\tsymphony-review-service-before-update"
    refute calls =~ "stop\t"
    assert File.read!(Path.join(ctx.repo, "WORKFLOW.md")) == "fixture workflow"
    refute File.exists?(Path.join(ctx.config, "runners/service.transition"))
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude"], env)
  end

  test "development namespace separates container, storage, native login and image identities", ctx do
    dev_env = [{"SYMPHONY_RUNNER_NAMESPACE", "claude-dev"}]
    assert {_, 0} = launch_args(ctx, ["auth", "claude", "attest", "--overages-disabled"], dev_env)
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude", "--port", "14030"], dev_env)
    calls = File.read!(ctx.log)
    assert calls =~ "symphony-claude-dev-service-"
    assert calls =~ "--name\tsymphony-claude-dev-service\t"
    assert calls =~ "symphony-claude-dev-runner-claude:local"
    assert calls =~ "-claude-auth:/home/claude"
    refute calls =~ "symphony-service"
    refute calls =~ "symphony-runner-claude:local"

    File.write!(ctx.log, "")
    assert {_, 0} = launch_args(ctx, ["auth", "codex", "check"], dev_env)
    assert File.read!(ctx.log) =~ "symphony-claude-dev-codex-auth:/root/.codex"
  end

  test "invalid namespaces and unscoped development updates fail before runtime operations", ctx do
    for namespace <- ["../stable", "UPPER", "-dev", String.duplicate("a", 33)] do
      assert {_, 1} = launch_args(ctx, ["run"], [{"SYMPHONY_RUNNER_NAMESPACE", namespace}])
    end

    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"])
    assert message =~ "requires SYMPHONY_RUNNER_NAMESPACE"
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner"], [{"SYMPHONY_RUNNER_NAMESPACE", "test"}])
    assert message =~ "requires --agent"
    refute File.exists?(ctx.log)
  end

  test "storage follows transport changes and refuses a retained container owned by another repository", ctx do
    assert {_, 0} = launch(ctx, "start")
    assert {_, 0} = launch(ctx, "stop")
    {_, 0} = System.cmd("git", ["remote", "set-url", "origin", "git@github.com:example/service.git"], cd: ctx.repo)
    assert {_, 0} = launch(ctx, "start")
    {_, 0} = System.cmd("git", ["remote", "set-url", "origin", "https://github.com/another/service.git"], cd: ctx.repo)
    assert {_, 1} = launch(ctx, "start")
    assert {_, 1} = launch(ctx, "status")
    assert {_, 1} = launch(ctx, "stop")
    {_, 0} = System.cmd("git", ["remote", "set-url", "origin", "ssh://git@github.com/example/service.git"], cd: ctx.repo)
    assert {_, 0} = launch(ctx, "stop")
    assert {_, 0} = launch(ctx, "start")
    assert {_, 0} = launch(ctx, "stop")
    {_, 0} = System.cmd("git", ["remote", "set-url", "origin", "https://github.com/another/service.git"], cd: ctx.repo)
    assert {_, 1} = launch(ctx, "start")

    [first, second, third] =
      ctx.log
      |> File.read!()
      |> String.split("\n")
      |> Enum.filter(&(String.starts_with?(&1, "volume\tcreate\t") and String.contains?(&1, "-workspaces")))

    assert first == second
    assert first == third
  end

  test "invalid provider flags fail before any container or credential operation", ctx do
    for args <- [["run", "--agent", "unknown"], ["run", "--agent"], ["run", "--agent", "claude", "--agent", "codex"], ["run", "--agent", "claude", "--host"]] do
      assert {_, 1} = launch_args(ctx, args)
    end

    refute File.exists?(ctx.log)
  end

  test "Claude selects only its project login and keeps selected Git secrets out of arguments", ctx do
    File.write!(Path.join(ctx.config, "projects/service.env"), "GH_TOKEN=git-secret-canary\n")
    assert {_, 0} = launch_args(ctx, ["auth", "claude", "attest", "--overages-disabled"])
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude", "--port", "4111"])
    calls = File.read!(ctx.log)
    assert calls =~ "com.symphony.agent=claude"
    assert calls =~ "--read-only"
    assert calls =~ "--cap-drop\tALL"
    assert calls =~ "--security-opt\tno-new-privileges"
    assert calls =~ "127.0.0.1:4111:4010"
    assert calls =~ "-claude-auth:/home/claude"
    assert calls =~ "--env\tSYMPHONY_GIT_TOKEN\t"
    refute calls =~ "git-secret-canary"
    refute calls =~ "symphony-codex-auth"
    refute File.exists?(Path.join(ctx.repo, "CLAUDE.md"))
    assert File.read!(Path.join(ctx.repo, "WORKFLOW.md")) == "fixture workflow"
  end

  test "agent and image mismatches refuse reuse without stopping or replacing work", ctx do
    assert {_, 0} = launch(ctx, "start")
    assert {_, 0} = launch_args(ctx, ["auth", "claude", "attest", "--overages-disabled"])
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["run", "--agent", "claude"])
    assert message =~ "listener agent differs"
    assert {message, 1} = launch_args(ctx, ["run"], [{"FIXTURE_IMAGE_ID", "changed-image"}])
    assert message =~ "listener image differs"
    calls = File.read!(ctx.log)
    refute calls =~ "stop\t"
    refute calls =~ "rm\t"
    refute calls =~ "run\t"
  end

  test "doctor runs auth and disposable Git checks without a listener, build or model", ctx do
    assert {_, 0} = launch(ctx, "doctor")
    calls = File.read!(ctx.log)
    assert calls =~ "login\tstatus"
    assert calls =~ "--network\tnone"
    refute calls =~ "--detach"
    refute calls =~ "build\t"
    refute File.exists?(ctx.state)
    assert {_, 0} = launch_args(ctx, ["auth", "claude", "attest", "--overages-disabled"])
    File.write!(ctx.log, "")
    assert {_, 0} = launch_args(ctx, ["runner", "doctor", "--agent", "claude"])
    calls = File.read!(ctx.log)
    assert calls =~ "auth.mjs\tcheck"
    refute calls =~ "--detach"
    refute calls =~ "build\t"
    refute File.exists?(ctx.state)
  end

  test "foreign native volumes and active profile removal fail before native mutation", ctx do
    assert {_, 1} = launch_args(ctx, ["auth", "claude", "login"], [{"FIXTURE_FOREIGN_VOLUME", "true"}])
    refute File.read!(ctx.log) =~ "auth.mjs\tlogin"
    assert {_, 0} = launch_args(ctx, ["auth", "claude", "attest", "--overages-disabled"])
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["auth", "claude", "remove"], [{"FIXTURE_PROFILE_IN_USE", "active-container"}])
    assert message =~ "profile is in use"
    calls = File.read!(ctx.log)
    refute calls =~ "auth.mjs\tlogout"
    refute calls =~ "volume\trm"
  end

  defp launch(ctx, command), do: launch_args(ctx, ["runner", command])

  defp launch_args(ctx, args, extra_env \\ []) do
    env = Map.merge(Map.new(ctx.env), Map.new(extra_env)) |> Enum.to_list()
    System.cmd("bash", [@launcher | args], cd: ctx.repo, env: env, stderr_to_stdout: true)
  end
end
