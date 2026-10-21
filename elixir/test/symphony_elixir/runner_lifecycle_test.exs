defmodule SymphonyElixir.RunnerLifecycleTest do
  use ExUnit.Case, async: true

  @launcher Path.expand("../../../scripts/symphony", __DIR__)

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-runner-#{System.pid()}-#{System.unique_integer([:positive])}")
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
    # Every fixture owns curl, including failures; never fall through to a host listener.
    File.write!(Path.join(root, "curl"), "#!/bin/sh\nexit 1\n")
    File.chmod!(Path.join(root, "curl"), 0o755)

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
          *State.Restarting*) echo "${FIXTURE_RESTARTING:-false}" ;;
          *State.Pid*) echo "${FIXTURE_PID:-42}" ;;
          *com.symphony.mode*) echo listener ;;
          *com.symphony.agent*) cat "$state.agent" ;;
          *com.symphony.port*)
            if [ "$(cat "$state.agent")" = '<no value>' ]; then echo '<no value>'; else cat "$state.port"; fi ;;
          *com.symphony.profile*) cat "$state.profile" ;;
          *"{{.Image}}"*) cat "$state.image" ;;
          *com.symphony.storage*) cat "$state.storage" ;;
          *Config.Entrypoint*) echo "${FIXTURE_ENTRYPOINT:-/usr/local/bin/symphony-runner}" ;;
          *HostConfig.PortBindings*) printf '127.0.0.1 %s\\n' "$(cat "$state.port")" ;;
          *Mounts*)
            case "$*" in
              *Source*) printf '%s/WORKFLOW.md /etc/symphony/WORKFLOW.md false\\n' "$(git -C '#{repo}' rev-parse --show-toplevel)" ;;
              *) storage=$(cat "$state.storage"); printf '%s-workspaces /workspaces\\n%s-state /var/lib/symphony\\n%s /root/.codex\\n' "$storage" "$storage" "symphony${SYMPHONY_RUNNER_NAMESPACE:+-$SYMPHONY_RUNNER_NAMESPACE}-codex-auth" ;;
            esac ;;
        esac ;;
      image)
        case "$*" in
          *com.symphony.agent*)
            case "$*" in *runner-claude*|*fixture-claude-image*) echo claude ;; *) echo codex ;; esac ;;
          *Config.User*) echo 11001:11001 ;;
          *)
            case "$*" in
              *runner-claude*|*fixture-claude-image*) echo "${FIXTURE_CLAUDE_IMAGE_ID:-fixture-claude-image}" ;;
              *) echo "${FIXTURE_IMAGE_ID:-fixture-image}" ;;
            esac ;;
        esac ;;
      build) [ "${FIXTURE_FAIL_BUILD:-false}" != true ] ;;
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
            done
            if [ -f "${FIXTURE_FAIL_CREATE_FILE:-/nonexistent}" ]; then
              rm "$FIXTURE_FAIL_CREATE_FILE"; exit 1
            fi ;;
          *"auth.mjs check"*)
            [ "${FIXTURE_AUTH_FAIL:-false}" != true ] || exit 1
            if [ -n "${FIXTURE_AUTH_JSON:-}" ]; then
              printf '%s\\n' "$FIXTURE_AUTH_JSON"
            else
              printf '%s\\n' '{"ready":true,"profile":"#{String.duplicate("a", 64)}","model_requests":0}'
            fi ;;
          *"/state/agent-upgrade-unresumed"*) touch "$FIXTURE_STATE.unresumed" "$FIXTURE_STATE.draining" ;;
        esac ;;
      exec)
        case "$*" in
          *agent-upgrade-unresumed*) test -f "$FIXTURE_STATE.unresumed" && test -f "$FIXTURE_STATE.draining" ;;
        esac ;;
      stop) echo false > "$FIXTURE_STATE" ;;
      start)
        if [ -f "${FIXTURE_FAIL_START_FILE:-/nonexistent}" ]; then
          rm "$FIXTURE_FAIL_START_FILE"; exit 1
        fi
        echo true > "$FIXTURE_STATE" ;;
      rm)
        rm -f "$state"
        if [ -f "${FIXTURE_FAIL_RM_FILE:-/nonexistent}" ]; then rm "$FIXTURE_FAIL_RM_FILE"; exit 1; fi ;;
      rename)
        from=$FIXTURE_STATE; to=$FIXTURE_STATE.backup
        case "$2" in *-before-update) from=$FIXTURE_STATE.backup; to=$FIXTURE_STATE ;; esac
        for suffix in '' .agent .port .profile .image .storage; do
          if [ -f "$from$suffix" ]; then mv "$from$suffix" "$to$suffix"; fi
        done ;;
    esac
    """)

    File.chmod!(runtime, 0o755)
    on_exit(fn -> File.rm_rf!(root) end)

    env = [
      {"PATH", root <> ":" <> System.fetch_env!("PATH")},
      {"SYMPHONY_CONFIG_HOME", config},
      {"SYMPHONY_RUNNER_HOME", Path.join(config, "runners")},
      {"SYMPHONY_CONTAINER_RUNTIME", runtime},
      {"FIXTURE_LOG", Path.join(root, "calls")},
      {"FIXTURE_STATE", Path.join(root, "running")}
    ]

    %{repo: repo, config: config, root: root, env: env, log: Path.join(root, "calls"), state: Path.join(root, "running")}
  end

  test "Codex startup Git rewrites remain idempotent for a retained container", ctx do
    entrypoint = Path.expand("../../../runner/entrypoint.sh", __DIR__) |> File.read!()
    [_, rewrite] = Regex.run(~r/if \[ -n "\$\{GH_TOKEN:-\}" \]; then\n(.*?)\nfi/s, entrypoint)
    home = Path.join(ctx.root, "git-home")
    File.mkdir_p!(home)
    env = [{"HOME", home}, {"GIT_CONFIG_GLOBAL", Path.join(home, ".gitconfig")}, {"GIT_CONFIG_NOSYSTEM", "1"}, {"GH_TOKEN", "inert-restart-fixture"}]
    assert {_, 0} = System.cmd("bash", ["-euc", rewrite], env: env, stderr_to_stdout: true)
    assert {_, 0} = System.cmd("bash", ["-euc", rewrite], env: env, stderr_to_stdout: true)
    assert {values, 0} = System.cmd("git", ["config", "--global", "--get-all", "url.https://x-access-token:inert-restart-fixture@github.com/.insteadOf"], env: env)
    assert String.split(values, "\n", trim: true) == ["git@github.com:", "ssh://git@github.com/"]
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
    curl = Path.join(ctx.root, "curl")
    File.write!(curl, "#!/bin/sh\nprintf '%s' '{\"agent\":{\"draining\":false,\"readiness\":{\"ready\":true},\"admission\":{\"ready\":true}}}'\n")
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

  test "legacy listeners require explicit updater pause and migrate to either provider", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", "review"}]

    for agent <- ["claude", "codex"] do
      assert {_, 0} = launch_args(ctx, ["run"], env)
      File.write!(ctx.state <> ".agent", "<no value>")
      File.write!(Path.join(ctx.config, "runners/service.env"), "SYMPHONY_RUNNER_PORT=4010\n")
      curl = Path.join(ctx.root, "curl")
      File.write!(curl, "#!/bin/sh\nprintf '%s' '{\"agent\":{\"draining\":false,\"readiness\":{\"ready\":true},\"admission\":{\"ready\":true}}}'\n")
      File.chmod!(curl, 0o755)
      env = env ++ [{"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
      File.write!(ctx.log, "")
      assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", agent], env)
      assert message =~ "use symphony update --pause-runner"
      refute File.read!(ctx.log) =~ "stop\t"
      refute File.read!(ctx.log) =~ "rename\t"
      assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--pause-runner"], env)
      assert message =~ "Legacy listener has no drain API"
      assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", agent], env)
      assert message =~ "Runner transition complete: #{agent}"
      assert File.read!(ctx.state <> ".agent") == agent
      refute File.read!(ctx.log) =~ "volume\trm"
      # Reset the disposable fixture through its supported stop operation.
      assert {_, 0} = launch_args(ctx, ["runner", "stop"], env)
      File.rm!(ctx.state)
      File.rm!(Path.join(ctx.config, "runners/service.env"))
    end
  end

  test "legacy qualification rejects unknown entrypoints and mismatched port metadata", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", "review"}]
    assert {_, 0} = launch_args(ctx, ["run"], env)
    File.write!(ctx.state <> ".agent", "<no value>")
    File.write!(Path.join(ctx.config, "runners/service.env"), "SYMPHONY_RUNNER_PORT=4010\n")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--pause-runner"], env ++ [{"FIXTURE_ENTRYPOINT", "/foreign"}])
    assert message =~ "unknown legacy entrypoint"
    File.write!(Path.join(ctx.config, "runners/service.env"), "SYMPHONY_RUNNER_PORT=4999\n")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--pause-runner"], env)
    assert message =~ "legacy loopback port differs"
    refute File.read!(ctx.log) =~ "stop\t"
  end

  test "resuming a transition cannot implicitly stop a restarted legacy listener", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", "review"}]
    assert {_, 0} = launch_args(ctx, ["run"], env)
    File.write!(ctx.state <> ".agent", "<no value>")
    File.write!(Path.join(ctx.config, "runners/service.env"), "SYMPHONY_RUNNER_PORT=4010\n")
    journal = Path.join(ctx.config, "runners/service.transition")
    File.mkdir_p!(journal)
    File.write!(Path.join(journal, "target"), "claude\n")
    File.write!(Path.join(journal, "port"), "4010\n")
    File.write!(Path.join(journal, "image"), "fixture-claude-image\n")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "use symphony update --pause-runner"
    refute File.read!(ctx.log) =~ "stop\t"
    refute File.read!(ctx.log) =~ "rename\t"
    assert File.exists?(journal)
  end

  test "failed legacy migration rolls back through the old state API without resume support", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", "review"}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
    assert {_, 0} = launch_args(ctx, ["run"], env)
    File.write!(ctx.state <> ".agent", "<no value>")
    File.write!(Path.join(ctx.config, "runners/service.env"), "SYMPHONY_RUNNER_PORT=4010\n")
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--pause-runner"], env)
    curl = Path.join(ctx.root, "curl")

    File.write!(curl, """
    #!/bin/sh
    printf '%s\\n' "$*" >> "#{ctx.root}/curl-calls"
    case "$*" in */api/v1/agent/resume*) exit 1 ;; esac
    if [ "$(cat "$FIXTURE_STATE.agent")" = claude ]; then
      printf '%s' '{"counts":{"running":0},"agent":{"draining":true,"readiness":{"ready":false}}}'
    else
      printf '%s' '{"counts":{"running":0}}'
    fi
    """)

    File.chmod!(curl, 0o755)
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "candidate is not ready"
    File.write!(Path.join(ctx.root, "curl-calls"), "")
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    assert message =~ "Prior runner restored"
    assert File.read!(ctx.state <> ".agent") == "<no value>"
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--pause-runner"], env)
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "candidate is not ready"
    previous = Path.join(ctx.config, "runners/service.transition/previous.env")
    image_keys = File.read!(previous) |> String.split("\n") |> Enum.filter(&String.starts_with?(&1, "SYMPHONY_RUNNER_IMAGE_ID="))
    assert image_keys == ["SYMPHONY_RUNNER_IMAGE_ID=fixture-image"]
    File.write!(Path.join(ctx.root, "curl-calls"), "")
    fail_start = Path.join(ctx.root, "fail-start-once")
    File.write!(fail_start, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env ++ [{"FIXTURE_FAIL_START_FILE", fail_start}])
    assert File.read!(ctx.state <> ".agent") == "<no value>"
    refute File.exists?(ctx.state <> ".backup")
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    assert message =~ "Prior runner restored"
    refute File.read!(Path.join(ctx.root, "curl-calls")) =~ "/agent/resume"
    refute File.exists?(Path.join(ctx.config, "runners/service.transition"))
    refute File.read!(ctx.log) =~ "volume\trm"
  end

  test "rollback restores a provider-labelled image with the pre-admission state API", ctx do
    ready_curl!(ctx)
    File.chmod!(Path.join(ctx.root, "curl"), 0o755)
    env = [{"SYMPHONY_RUNNER_NAMESPACE", "review"}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
    assert {_, 0} = launch_args(ctx, ["run"], env)
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--pause-runner"], env)

    File.write!(Path.join(ctx.root, "curl"), """
    #!/bin/sh
    printf '%s' '{"agent":{"draining":false,"readiness":{"ready":true},"pause":null}}'
    """)

    File.chmod!(Path.join(ctx.root, "curl"), 0o755)
    # An old-shaped response must never prove the new candidate operational.
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "candidate is not ready"
    assert File.exists?(ctx.state <> ".backup")
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    assert message =~ "pre-admission API"
    assert message =~ "Prior runner restored"
    assert File.read!(ctx.state <> ".agent") == "codex"
    refute File.exists?(Path.join(ctx.config, "runners/service.transition"))
  end

  for {label, admission, succeeds} <- [
        {"quota cooldown", %{ready: false, reason: "quota_exhausted", retry_at: 4_000_000_000}, true},
        {"authentication pause", %{ready: false, reason: "auth_required"}, false},
        {"missing admission", nil, false}
      ] do
    @admission admission
    @succeeds succeeds
    test "updater validates #{label} after an HTTP-successful resume", ctx do
      env = pending_claude_transition!(ctx)
      body = Jason.encode!(%{draining: false, admission: @admission})
      File.write!(Path.join(ctx.root, "curl"), "#!/bin/sh\nprintf '%s' '#{body}'\n")
      File.chmod!(Path.join(ctx.root, "curl"), 0o755)
      {message, code} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
      assert code == 0 == @succeeds

      if @succeeds do
        assert message =~ "intentionally paused"
      else
        assert message =~ "candidate is not ready"
        assert File.exists?(ctx.state <> ".backup")
      end
    end
  end

  test "rollback recovers an API-dead unresumed candidate and refuses unproven or foreign candidates", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", "review"}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
    assert {_, 0} = launch_args(ctx, ["run"], env)
    assert {_, 0} = launch_args(ctx, ["runner", "stop"], env)
    curl = Path.join(ctx.root, "curl")

    File.write!(curl, """
    #!/bin/sh
    if [ "$(cat "$FIXTURE_STATE.agent")" = claude ]; then exit 1; fi
    printf '%s' '{"agent":{"draining":false,"readiness":{"ready":true},"admission":{"ready":true}}}'
    """)

    File.chmod!(curl, 0o755)
    File.write!(Path.join(ctx.root, "sleep"), "#!/bin/sh\nexit 0\n")
    File.chmod!(Path.join(ctx.root, "sleep"), 0o755)
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "candidate is not ready"
    assert File.exists?(ctx.state <> ".unresumed")

    File.rm!(ctx.state <> ".unresumed")
    File.write!(ctx.log, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    refute File.read!(ctx.log) =~ "stop\t"
    refute File.read!(ctx.log) =~ ~r/(^|\n)rm\t/
    assert File.exists?(ctx.state <> ".backup")

    File.write!(ctx.state <> ".unresumed", "")
    File.write!(ctx.state <> ".image", "foreign-image")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    assert message =~ "candidate image differs"
    refute File.read!(ctx.log) =~ "stop\t"

    File.write!(ctx.state <> ".image", "fixture-claude-image")
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    assert message =~ "Prior runner restored"
    assert File.read!(ctx.state <> ".agent") == "codex"
    refute File.exists?(ctx.state <> ".backup")
    refute File.exists?(Path.join(ctx.config, "runners/service.transition"))
    refute File.read!(ctx.log) =~ "volume\trm"
  end

  test "development retry replaces a stale crash-loop candidate without changing workflow or rollback source", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/service.transition")
    previous = File.read!(Path.join(journal, "previous.env"))
    ready_curl!(ctx)
    File.write!(ctx.log, "")

    env = env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}, {"FIXTURE_RESTARTING", "true"}, {"FIXTURE_PID", "0"}]
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "Refreshed pending development candidate"
    assert message =~ "Rollback source retained"
    assert File.read!(ctx.state <> ".image") == "fixture-claude-image-new"
    assert File.read!(Path.join(ctx.repo, "WORKFLOW.md")) == "fixture workflow"
    refute File.exists?(journal)
    refute File.exists?(ctx.state <> ".backup")
    calls = File.read!(ctx.log)
    assert calls =~ "stop\t--time\t30\tsymphony-review-service"
    assert calls =~ "tag\tfixture-claude-image-new\tsymphony-review-runner-claude:local"
    refute calls =~ "volume\trm"
    assert previous =~ "SYMPHONY_RUNNER_IMAGE_ID=fixture-image"
  end

  test "retry repairs metadata after candidate creation interrupts before metadata publication", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", "review"}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"], env)
    assert {_, 0} = launch_args(ctx, ["runner", "stop"], env)
    assert {_, 0} = launch_args(ctx, ["auth", "claude", "attest", "--overages-disabled"], env)
    metadata = Path.join(ctx.config, "runners/service.env")
    previous = File.read!(metadata)
    fail = Path.join(ctx.root, "fail-create-once")
    File.write!(fail, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env ++ [{"FIXTURE_FAIL_CREATE_FILE", fail}])
    assert File.read!(metadata) == previous
    assert File.read!(ctx.state <> ".image") == "fixture-claude-image"
    assert File.exists?(ctx.state <> ".backup")
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert File.read!(metadata) =~ "SYMPHONY_RUNNER_IMAGE_ID=fixture-claude-image\n"
    assert File.read!(metadata) =~ "SYMPHONY_RUNNER_AGENT=claude\n"
    assert File.read!(metadata) =~ "SYMPHONY_RUNNER_PROFILE=#{String.duplicate("a", 64)}\n"
    assert {_, 0} = launch_args(ctx, ["runner", "stop"], env)
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude"], env)
  end

  test "postcommit retry reconstructs metadata from the selected container", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/service.transition")
    metadata = Path.join(ctx.config, "runners/service.env")
    File.cp!(Path.join(journal, "previous.env"), metadata)
    File.rm!(ctx.state <> ".backup")
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert File.read!(metadata) =~ "SYMPHONY_RUNNER_IMAGE_ID=fixture-claude-image\n"
    refute File.exists?(journal)
  end

  test "rollback restores observed prior identity rather than stale cached fields", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/service.transition")
    # Keep the recorded image as rollback evidence; provider/profile are derived cache fields.
    File.write!(Path.join(journal, "previous.env"), "SYMPHONY_RUNNER_PORT=4010\nSYMPHONY_RUNNER_AGENT=claude\nSYMPHONY_RUNNER_IMAGE_ID=fixture-image\nSYMPHONY_RUNNER_PROFILE=stale\n")
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    metadata = File.read!(Path.join(ctx.config, "runners/service.env"))
    assert metadata =~ "SYMPHONY_RUNNER_AGENT=codex\n"
    assert metadata =~ "SYMPHONY_RUNNER_PROFILE=none\n"
    assert metadata =~ "SYMPHONY_RUNNER_IMAGE_ID=fixture-image\n"
  end

  test "failed development rebuild leaves the pending image, container and rollback metadata intact", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/service.transition")
    before = for name <- ~w(image previous.env target port), into: %{}, do: {name, File.read!(Path.join(journal, name))}
    File.write!(ctx.log, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env ++ [{"FIXTURE_FAIL_BUILD", "true"}])
    for {name, contents} <- before, do: assert(File.read!(Path.join(journal, name)) == contents)
    assert File.read!(ctx.state <> ".image") == "fixture-claude-image"
    assert File.exists?(ctx.state <> ".backup")
    refute File.read!(ctx.log) =~ "stop\t"
    refute File.read!(ctx.log) =~ ~r/(^|\n)rm\t/
  end

  test "development refresh retains an API-dead candidate when it cannot prove dispatch is disabled", ctx do
    env = pending_claude_transition!(ctx)
    File.rm!(ctx.state <> ".unresumed")
    File.write!(ctx.log, "")
    env = env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}]
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    refute File.read!(ctx.log) =~ ~r/(^|\n)(stop|rm)\t/
    assert File.read!(Path.join(ctx.config, "runners/service.transition/image")) == "fixture-claude-image\n"
  end

  test "development refresh refuses a foreign candidate before stopping or removing it", ctx do
    env = pending_claude_transition!(ctx)
    File.write!(ctx.state <> ".image", "foreign-image")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}])
    assert message =~ "candidate image differs"
    assert File.read!(Path.join(ctx.config, "runners/service.transition/image")) == "fixture-claude-image\n"
    refute File.read!(ctx.log) =~ "stop\t"
    refute File.read!(ctx.log) =~ ~r/(^|\n)rm\t/
  end

  test "development refresh resumes after interruption between candidate removal and journal commit", ctx do
    env = pending_claude_transition!(ctx) ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}]
    ready_curl!(ctx)
    File.write!(ctx.state, "false")
    fail = Path.join(ctx.root, "fail-rm-once")
    File.write!(fail, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env ++ [{"FIXTURE_FAIL_RM_FILE", fail}])
    refute File.exists?(ctx.state)
    assert File.exists?(ctx.state <> ".backup")
    assert File.read!(Path.join(ctx.config, "runners/service.transition/image")) == "fixture-claude-image\n"
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "Runner transition complete: claude"
    assert File.read!(ctx.state <> ".image") == "fixture-claude-image-new"
  end

  defp pending_claude_transition!(ctx) do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", "review"}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
    assert {_, 0} = launch_args(ctx, ["run"], env)
    assert {_, 0} = launch_args(ctx, ["runner", "stop"], env)
    File.write!(Path.join(ctx.root, "curl"), "#!/bin/sh\nexit 1\n")
    File.write!(Path.join(ctx.root, "sleep"), "#!/bin/sh\nexit 0\n")
    File.chmod!(Path.join(ctx.root, "curl"), 0o755)
    File.chmod!(Path.join(ctx.root, "sleep"), 0o755)
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "candidate is not ready"
    env
  end

  defp ready_curl!(ctx) do
    File.write!(Path.join(ctx.root, "curl"), """
    #!/bin/sh
    case "$*" in
      */api/v1/agent/drain*) touch "$FIXTURE_STATE.api-draining" ;;
      */api/v1/agent/resume*) rm -f "$FIXTURE_STATE.api-draining" ;;
    esac
    draining=false
    if [ -f "$FIXTURE_STATE.api-draining" ]; then draining=true; fi
    printf '{"counts":{"running":0},"agent":{"draining":%s,"readiness":{"ready":true},"admission":{"ready":true}}}' "$draining"
    """)

    File.chmod!(Path.join(ctx.root, "curl"), 0o755)
  end

  test "development refresh can retry a journal created before the original runner was renamed", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/service.transition")

    for suffix <- ["", ".agent", ".port", ".profile", ".image", ".storage"] do
      if File.exists?(ctx.state <> ".backup" <> suffix) do
        File.rename!(ctx.state <> ".backup" <> suffix, ctx.state <> suffix)
      end
    end

    File.cp!(Path.join(journal, "previous.env"), Path.join(ctx.config, "runners/service.env"))
    ready_curl!(ctx)
    File.write!(ctx.log, "")
    env = env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}]
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "Runner transition complete: claude"
    assert File.read!(ctx.state <> ".image") == "fixture-claude-image-new"
    refute File.read!(ctx.log) =~ ~r/(^|\n)rm\tsymphony-review-service\t/
  end

  test "development refresh refuses a running rollback source without replacing either container", ctx do
    env = pending_claude_transition!(ctx)
    File.write!(ctx.state <> ".backup", "true")
    File.write!(ctx.log, "")
    env = env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}]
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "prior runner is unexpectedly active"
    refute File.read!(ctx.log) =~ ~r/(^|\n)(stop|rm)\t/
    assert File.read!(Path.join(ctx.config, "runners/service.transition/image")) == "fixture-claude-image\n"
  end

  test "same-image development retry resumes the journal without replacing the candidate", ctx do
    env = pending_claude_transition!(ctx)
    ready_curl!(ctx)
    File.write!(ctx.log, "")
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    refute message =~ "Refreshed pending development candidate"
    calls = File.read!(ctx.log)
    refute calls =~ "stop\t"
    refute calls =~ ~r/(^|\n)rm\tsymphony-review-service\t/
    assert File.read!(ctx.state <> ".image") == "fixture-claude-image"
  end

  test "failed replacement readiness retains the original rollback image and remains recoverable", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/service.transition")
    previous = File.read!(Path.join(journal, "previous.env"))
    File.write!(ctx.state, "false")
    env = env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}]
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "candidate is not ready"
    assert File.read!(Path.join(journal, "image")) == "fixture-claude-image-new\n"
    assert File.read!(Path.join(journal, "previous.env")) == previous
    assert File.read!(ctx.state <> ".backup.image") == "fixture-image"
    ready_curl!(ctx)
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    assert message =~ "Prior runner restored"
    assert File.read!(ctx.state <> ".image") == "fixture-image"
  end

  test "development refresh recovers a journal after the old candidate committed and backup was removed", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/service.transition")
    # The previous transition passed readiness and removed its backup, but died before journal cleanup.
    File.rm!(ctx.state <> ".backup")
    File.cp!(Path.join(journal, "previous.env"), Path.join(ctx.config, "runners/service.env"))
    ready_curl!(ctx)
    env = env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}]
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "Runner transition complete: claude"
    assert File.read!(ctx.state <> ".image") == "fixture-claude-image-new"
    refute File.exists?(journal)
  end

  test "runner status reports a restarting container rather than claiming it is running", ctx do
    assert {_, 0} = launch(ctx, "start")
    assert {message, 0} = launch_args(ctx, ["runner", "status"], [{"FIXTURE_RESTARTING", "true"}])
    assert message =~ "Isolated worker: restarting"
    refute message =~ "Isolated Symphony: running"
  end

  test "development namespace separates container, storage, native login and image identities", ctx do
    dev_env = [{"SYMPHONY_RUNNER_NAMESPACE", "claude-dev"}]
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

  test "Claude starts from native readiness without billing policy files and preserves account identity", ctx do
    refute File.exists?(Path.join(ctx.config, "claude"))
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude"])
    refute File.exists?(Path.join(ctx.config, "claude"))
    calls = File.read!(ctx.log)
    assert calls =~ "auth.mjs\tcheck"
    refute calls =~ "/etc/symphony/claude"
    assert calls =~ "com.symphony.profile=#{String.duplicate("a", 64)}"

    File.write!(ctx.log, "")
    changed = Jason.encode!(%{ready: true, profile: String.duplicate("b", 64), model_requests: 0})
    assert {message, 1} = launch_args(ctx, ["run", "--agent", "claude"], [{"FIXTURE_AUTH_JSON", changed}])
    assert message =~ "listener native profile differs"
    calls = File.read!(ctx.log)
    refute calls =~ "--detach"
    refute calls =~ "stop\t"
    refute calls =~ "\nrm\t"
  end

  test "invalid native identity cannot create a listener and retired attestation cannot reach Docker", ctx do
    invalid = ["not-json", "[]", Jason.encode!(%{ready: false, profile: String.duplicate("a", 64)}), Jason.encode!(%{ready: true, profile: "secret-canary"}), String.duplicate("x", 4097)]

    for value <- invalid do
      assert {output, 1} = launch_args(ctx, ["run", "--agent", "claude"], [{"FIXTURE_AUTH_JSON", value}])
      refute output =~ "secret-canary"
      refute File.exists?(ctx.state)
    end

    assert {_, 1} = launch_args(ctx, ["run", "--agent", "claude"], [{"FIXTURE_AUTH_FAIL", "true"}])
    refute File.exists?(ctx.state)
    refute File.read!(ctx.log) =~ "--detach"
    File.write!(ctx.log, "")
    assert {_, 1} = launch_args(ctx, ["auth", "claude", "attest", "--overages-disabled"])
    assert File.read!(ctx.log) == ""
  end

  test "agent and image mismatches refuse reuse without stopping or replacing work", ctx do
    assert {_, 0} = launch(ctx, "start")
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
