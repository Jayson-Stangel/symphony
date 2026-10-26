defmodule SymphonyElixir.RunnerLifecycleTest do
  use ExUnit.Case, async: true

  @project_id "service-" <> String.slice(Base.encode16(:crypto.hash(:sha, "blob 26\0github.com/example/service"), case: :lower), 0, 12)

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
    File.write!(Path.join(config, "projects/#{@project_id}.env"), "GH_TOKEN=fixture\n")
    # Every fixture owns curl, including failures; never fall through to a host listener.
    File.write!(Path.join(root, "curl"), "#!/bin/sh\nexit 1\n")
    File.chmod!(Path.join(root, "curl"), 0o755)

    File.write!(runtime, """
    #!/bin/bash
    set -eu
    printf '%s\\t' "$@" >> "$FIXTURE_LOG"
    printf '\\n' >> "$FIXTURE_LOG"
    state=$FIXTURE_STATE
    case "${!#}" in
      *-before-update) state=$FIXTURE_STATE.backup ;;
    esac
    case "$1:${!#}" in
      inspect:symphony*-service|inspect:symphony*-service-before-update|inspect:symphony*-other|inspect:symphony*-other-before-update) exit 1 ;;
    esac
    case "$1" in
      context) printf 'ssh://fixture-daemon\\n' ;;
      info)
        case "${!#}" in
          '{{.ID}}') printf 'fixture-daemon-id\\n'; exit 0 ;;
          '{{.Name}}') printf 'fixture-daemon-host\\n'; exit 0 ;;
        esac
        [ "${FIXTURE_INFO_FAIL:-false}" != true ] || exit 1
        [ "${FIXTURE_SECURITY_EMPTY:-false}" != true ] || exit 0
        printf '%s\\n' "${FIXTURE_SECURITY_OPTIONS-name=seccomp,profile=builtin}" ;;
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
          *com.symphony.codex.runtime*) cat "$state.codex-runtime" ;;
          *com.symphony.codex.config.sha256*) cat "$state.config-sha" ;;
          *com.symphony.codex.config.owner*) cat "$state.config-owner" ;;
          *com.symphony.codex.config.profile*) echo trust-ro-v2 ;;
          *com.symphony.claude.runtime*) cat "$state.claude-runtime" ;;
          *com.symphony.profile*) cat "$state.profile" ;;
          *"{{.Image}}"*) cat "$state.image" ;;
          *com.symphony.storage*) cat "$state.storage" ;;
          *Config.Entrypoint*) echo "${FIXTURE_ENTRYPOINT:-/usr/local/bin/symphony-runner}" ;;
          *HostConfig.PortBindings*) printf '127.0.0.1 %s\\n' "$(cat "$state.port")" ;;
          *Mounts*)
            case "$*" in
              *'{{json .Mounts}}'*) printf '[{"Type":"bind","Source":"%s/WORKFLOW.md","Destination":"/etc/symphony/WORKFLOW.md","RW":false}]\\n' "$(git -C '#{repo}' rev-parse --show-toplevel)" ;;
              *Source*) printf '%s/WORKFLOW.md /etc/symphony/WORKFLOW.md false\\n' "$(git -C '#{repo}' rev-parse --show-toplevel)" ;;
              *) storage=$(cat "$state.storage"); printf '%s-workspaces /workspaces\\n%s-state /var/lib/symphony\\n%s /root/.codex\\n' "$storage" "$storage" "symphony${SYMPHONY_RUNNER_NAMESPACE:+-$SYMPHONY_RUNNER_NAMESPACE}-codex-auth" ;;
            esac
            case "$*" in
              *'{{range .Mounts}}'*) if [ -n "${FIXTURE_MOUNT_PADDING_FILE:-}" ]; then cat "$FIXTURE_MOUNT_PADDING_FILE"; fi ;;
            esac ;;
          *) cat "$state.inspect" ;;
        esac ;;
      image)
        if [ -n "${FIXTURE_TAG_CHANGED_FILE:-}" ] && [ -f "$FIXTURE_TAG_CHANGED_FILE" ]; then
          case "$*" in *'{{.Id}}'*runner-codex:*) echo sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd; exit 0 ;; esac
        fi
        case "$*" in
          *com.symphony.agent*)
            case "$*" in *runner-claude*|*fixture-claude-image*) echo claude ;; *) echo codex ;; esac ;;
          *Config.User*) echo 11001:11001 ;;
          *)
            case "$*" in
              *runner-claude*|*fixture-claude-image*) echo "${FIXTURE_CLAUDE_IMAGE_ID:-fixture-claude-image}" ;;
              *) echo "${FIXTURE_IMAGE_ID:-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}" ;;
            esac ;;
        esac ;;
      build) [ "${FIXTURE_FAIL_BUILD:-false}" != true ] ;;
      volume)
        case "$*" in
          *--format*)
            [ "${FIXTURE_FOREIGN_VOLUME:-false}" != true ] || { echo foreign; exit 0; }
            name=${!#}; printf '%s' "${name%-claude-auth}" ;;
        esac ;;
      ps)
        case "$*" in
          *-workspaces*|*-state*) printf '%s' "${FIXTURE_ACTIVE_STORAGE_USER:-}" ;;
          *) printf '%s' "${FIXTURE_PROFILE_IN_USE:-}" ;;
        esac ;;
      run)
        case "$*" in
          *--codex-config-snapshot*)
            [ "${FIXTURE_CONFIG_FAIL:-false}" != true ] || { echo '{"error":"sandbox_unqualified_config"}'; exit 1; }
            if [ -n "${FIXTURE_CONFIG_JSON:-}" ]; then printf '%s\\n' "$FIXTURE_CONFIG_JSON"
            else printf '%s\\n' '{"schema":1,"mode":"codex-config-snapshot","profile":"trust-ro-v2","config_base64":"","sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","source":{"present":false}}'; fi ;;
          *'/usr/bin/stat'*) id -u ;;

          *--check-codex-sandbox*)
            if [ -n "${FIXTURE_TAG_CHANGED_FILE:-}" ]; then
              count=0
              [ ! -f "$FIXTURE_TAG_CHANGED_FILE.count" ] || count=$(cat "$FIXTURE_TAG_CHANGED_FILE.count")
              count=$((count + 1)); printf '%s' "$count" > "$FIXTURE_TAG_CHANGED_FILE.count"
              # The first check proves the retained rollback source, not the candidate.
              [ "$count" -lt 2 ] || touch "$FIXTURE_TAG_CHANGED_FILE"
            fi
            case "$*" in *sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd*) echo sandbox_namespace_denied; exit 1 ;; esac
            [ "${FIXTURE_SANDBOX_FAIL:-false}" != true ] || { printf '%s\\n' "${FIXTURE_SANDBOX_CODE:-sandbox_namespace_denied}"; exit 1; } ;;
          *--detach*)
            python3 - "$FIXTURE_STATE.inspect" "$@" <<'INSPECTPY'
    import json,sys
    args=sys.argv[2:]; env=[]; labels={}; mounts=[]
    for i,arg in enumerate(args[:-1]):
        value=args[i+1]
        if arg=='--env' and '=' in value: env.append(value)
        if arg=='--label':
            key,content=value.split('=',1); labels[key]=content
        if arg=='--mount':
            spec=dict(part.split('=',1) for part in value.split(',') if '=' in part)
            mounts.append({'Type':spec['type'],'Source':spec['source'],'Destination':spec['target'],'RW':'readonly' not in value.split(',')})
        if arg=='--volume' and ':' in value:
            source,target,*options=value.split(':')
            mounts.append({'Type':'bind' if source.startswith('/') else 'volume','Name':source,'Source':source,'Destination':target,'RW':'ro' not in options})
    with open(sys.argv[1],'w') as stream: json.dump([{'Config':{'Env':env,'Labels':labels},'Mounts':mounts}],stream)
    INSPECTPY
            echo true > "$FIXTURE_STATE"
            printf '%s' "${!#}" > "$FIXTURE_STATE.image"
            for arg in "$@"; do
              case "$arg" in
                com.symphony.storage=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.storage" ;;
                com.symphony.agent=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.agent" ;;
                com.symphony.port=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.port" ;;
                com.symphony.codex.config.sha256=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.config-sha" ;;
                com.symphony.codex.config.owner=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.config-owner" ;;
                com.symphony.codex.runtime=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.codex-runtime" ;;
                com.symphony.claude.runtime=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.claude-runtime" ;;
                com.symphony.profile=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.profile" ;;
              esac
            done
            if [ ! -f "$FIXTURE_STATE.volume-owner" ]; then
              if [ "$(cat "$FIXTURE_STATE.agent")" = claude ]; then echo 11001; else echo 0; fi > "$FIXTURE_STATE.volume-owner"
            fi
            if [ -f "${FIXTURE_FAIL_CREATE_FILE:-/nonexistent}" ]; then
              rm "$FIXTURE_FAIL_CREATE_FILE"; exit 1
            fi ;;
          *"auth.mjs login"*|*"auth.mjs logout"*)
            if [ -n "${FIXTURE_AUTH_BARRIER:-}" ]; then
              touch "$FIXTURE_AUTH_BARRIER.entered"
              while [ ! -f "$FIXTURE_AUTH_BARRIER.release" ]; do sleep 0.01; done
            fi ;;
          *"auth.mjs check"*)
            [ "${FIXTURE_AUTH_FAIL:-false}" != true ] || exit 1
            if [ -n "${FIXTURE_AUTH_JSON:-}" ]; then
              printf '%s\\n' "$FIXTURE_AUTH_JSON"
            else
              printf '%s\\n' '{"ready":true,"profile":"#{String.duplicate("a", 64)}","model_requests":0}'
            fi ;;
          *"chown -hR"*)
            if [ -f "${FIXTURE_FAIL_PREP_BEFORE_FILE:-/nonexistent}" ]; then rm "$FIXTURE_FAIL_PREP_BEFORE_FILE"; exit 1; fi
            uid=${!#}; initialize=${@: -2:1}; provider=${@: -3:1}
            # The old launcher only changed ownership for Claude.
            case "$uid" in
              claude) uid=11001; initialize=true; provider=claude ;;
              codex) uid=$(cat "$FIXTURE_STATE.volume-owner"); initialize=true; provider=codex ;;
            esac
            if [ "$initialize" = true ]; then touch "$FIXTURE_STATE.unresumed" "$FIXTURE_STATE.draining"; fi
            printf '%s' "$uid" > "$FIXTURE_STATE.volume-owner"
            printf '%s %s %s\\n' "$provider" "$initialize" "$uid" >> "$FIXTURE_STATE.preparations"
            if [ -f "${FIXTURE_FAIL_PREP_AFTER_FILE:-/nonexistent}" ]; then rm "$FIXTURE_FAIL_PREP_AFTER_FILE"; exit 1; fi ;;
          *"/state/agent-upgrade-unresumed"*) touch "$FIXTURE_STATE.unresumed" "$FIXTURE_STATE.draining" ;;
        esac ;;
      exec)
        case "$*" in
          *agent-upgrade-unresumed*) test -f "$FIXTURE_STATE.unresumed" && test -f "$FIXTURE_STATE.draining" ;;
        esac ;;
      stop)
        echo false > "$FIXTURE_STATE"
        if [ -n "${FIXTURE_STOP_BARRIER:-}" ]; then
          touch "$FIXTURE_STOP_BARRIER.entered"
          while [ ! -f "$FIXTURE_STOP_BARRIER.release" ]; do sleep 0.01; done
        fi ;;
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
        for suffix in '' .agent .port .profile .image .storage .codex-runtime .claude-runtime .config-sha .config-owner .inspect; do
          if [ -f "$from$suffix" ]; then mv "$from$suffix" "$to$suffix"; fi
        done ;;
    esac
    """)

    File.chmod!(runtime, 0o755)
    on_exit(fn -> File.rm_rf!(root) end)

    namespace = "test-#{System.pid()}-#{System.unique_integer([:positive])}"

    env = [
      {"SYMPHONY_RUNNER_NAMESPACE", namespace},
      {"PATH", root <> ":" <> System.fetch_env!("PATH")},
      {"SYMPHONY_CONFIG_HOME", config},
      {"SYMPHONY_RUNNER_HOME", Path.join(config, "runners")},
      {"SYMPHONY_CONTAINER_RUNTIME", runtime},
      {"FIXTURE_LOG", Path.join(root, "calls")},
      {"FIXTURE_STATE", Path.join(root, "running")}
    ]

    %{repo: repo, config: config, root: root, namespace: namespace, env: env, log: Path.join(root, "calls"), state: Path.join(root, "running")}
  end

  for override <- [false, true] do
    @override override
    test "update --config-dir selects runner metadata with explicit override #{@override}", ctx do
      selected = Path.join(ctx.root, "selected")
      File.mkdir_p!(Path.join(selected, "projects"))
      File.cp!(Path.join(ctx.config, "projects/#{@project_id}.env"), Path.join(selected, "projects/#{@project_id}.env"))
      runner_home = if @override, do: Path.join(ctx.root, "explicit-runners"), else: nil
      env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}, {"SYMPHONY_RUNNER_HOME", runner_home}]
      ready_curl!(ctx)
      assert {_, 0} = launch_args(ctx, ["run"], env ++ [{"SYMPHONY_CONFIG_HOME", selected}])
      assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--config-dir", selected, "--agent", "claude"], env)
      metadata = Path.join(runner_home || Path.join(selected, "runners"), "#{@project_id}.env")
      assert File.read!(metadata) =~ "SYMPHONY_RUNNER_AGENT=claude"
      refute File.exists?(Path.join(ctx.config, "runners/#{@project_id}.env"))
    end
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

  test "cross-provider completion restores target ownership in both directions", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}]
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"], env)
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert File.read!(ctx.state <> ".volume-owner") == "11001"
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"], env)
    assert File.read!(ctx.state <> ".volume-owner") == "0"
    assert File.read!(ctx.state <> ".preparations") == "claude true 11001\ncodex true 0\n"
  end

  for {source, target, owner} <- [{"codex", "claude", "0"}, {"claude", "codex", "11001"}], position <- ["BEFORE", "AFTER"] do
    @source source
    @target target
    @owner owner
    @position position
    test "#{source} rollback retries after failure #{position} ownership and before source start", ctx do
      env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}]
      ready_curl!(ctx)
      assert {_, 0} = launch_args(ctx, ["run", "--agent", @source], env)
      fail = Path.join(ctx.root, "fail-prepare-once")
      File.write!(fail, "")
      assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", @target], env ++ [{"FIXTURE_FAIL_PREP_#{@position}_FILE", fail}])
      assert String.trim(File.read!(ctx.state <> ".backup")) == "false"
      refute File.exists?(ctx.state)
      start = Path.join(ctx.root, "fail-start-once")
      File.write!(start, "")
      assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env ++ [{"FIXTURE_FAIL_START_FILE", start}])
      assert File.read!(ctx.state <> ".volume-owner") == @owner
      assert String.trim(File.read!(ctx.state)) == "false"
      assert File.dir?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
      assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
      assert File.read!(ctx.state <> ".volume-owner") == @owner
      assert File.read!(ctx.state <> ".agent") == @source
      refute File.exists?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
    end
  end

  test "ownership-only Claude rollback preserves cleanup uncertainty", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}]
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude"], env)
    fail = Path.join(ctx.root, "fail-prepare-once")
    File.write!(fail, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"], env ++ [{"FIXTURE_FAIL_PREP_AFTER_FILE", fail}])
    File.write!(ctx.state <> ".cleanup-unconfirmed", "retained evidence")
    start = Path.join(ctx.root, "fail-start-once")
    File.write!(start, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env ++ [{"FIXTURE_FAIL_START_FILE", start}])
    assert File.read!(ctx.state <> ".volume-owner") == "11001"
    assert File.read!(ctx.state <> ".cleanup-unconfirmed") == "retained evidence"
    assert File.read!(ctx.state <> ".preparations") =~ "claude false 11001"
  end

  test "active retained source refuses rollback before candidate or storage mutation", ctx do
    env = pending_claude_transition!(ctx)
    File.write!(ctx.state <> ".backup", "true")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    assert message =~ "prior runner is unexpectedly active"
    refute File.read!(ctx.log) =~ ~r/(^|\n)(stop|rm|rename|run|start)\t/
  end

  test "foreign active storage refuses rollback ownership and prior start", ctx do
    env = pending_claude_transition!(ctx)
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env ++ [{"FIXTURE_ACTIVE_STORAGE_USER", "foreign-container"}])
    assert message =~ "retained volume is in use"
    refute File.read!(ctx.log) =~ "chown -hR"
    refute File.read!(ctx.log) =~ "start\t"
    assert String.trim(File.read!(ctx.state)) == "false"
    assert File.dir?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
  end

  test "missing container inspection discards stdout before reporting or creating", ctx do
    assert {"Isolated worker: not running\n", 0} = launch(ctx, "status")
    assert {_, 0} = launch(ctx, "start")

    calls = File.read!(ctx.log) |> String.split("\n", trim: true)
    assert Enum.any?(calls, &String.starts_with?(&1, "run\t"))
    refute Enum.any?(calls, &String.starts_with?(&1, "rm\t"))
    before_creation = Enum.take_while(calls, &(not String.contains?(&1, "--detach")))
    refute Enum.any?(before_creation, &(String.starts_with?(&1, "inspect\t") and String.contains?(&1, "com.symphony.storage") and String.contains?(&1, "symphony-#{ctx.namespace}-#{@project_id}\t")))
  end

  test "runner recreation reuses repository volumes and stops gracefully", ctx do
    assert {_, 0} = launch(ctx, "start")
    assert {status, 0} = launch(ctx, "status")
    assert status =~ "Workspaces: /workspaces (volume symphony-#{ctx.namespace}-service-"
    assert {_, 0} = launch(ctx, "stop")
    assert {_, 0} = launch(ctx, "start")

    calls = File.read!(ctx.log) |> String.split("\n", trim: true)
    runs = Enum.filter(calls, &(String.starts_with?(&1, "run\t") and String.contains?(&1, "--detach\t")))
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
    metadata = File.read!(Path.join(ctx.config, "runners/#{@project_id}.env"))
    File.write!(ctx.log, "")
    assert {_, 0} = launch(ctx, "stop")
    assert {"Isolated worker: stopped (false)\n", 0} = launch(ctx, "status")
    assert File.read!(Path.join(ctx.config, "runners/#{@project_id}.env")) == metadata
    calls = File.read!(ctx.log)
    assert calls =~ "stop\t--time\t30\tsymphony-#{ctx.namespace}-service"
    refute calls =~ "rm\t"
    assert calls =~ "com.symphony.storage"
  end

  test "development updater transitions an officially stopped runner without changing source or global links", ctx do
    dev_env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}]
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
    assert calls =~ "rename\tsymphony-#{ctx.namespace}-#{@project_id}\tsymphony-#{ctx.namespace}-#{@project_id}-before-update"
    assert calls =~ "/state/agent-draining"
    assert calls =~ "com.symphony.agent=claude"
    assert calls =~ "tag\tfixture-claude-image\tsymphony-#{ctx.namespace}-runner-claude:local"
    assert calls =~ "rm\tsymphony-#{ctx.namespace}-#{@project_id}-before-update"
    refute calls =~ "stop\t"
    assert File.read!(Path.join(ctx.repo, "WORKFLOW.md")) == "fixture workflow"
    refute File.exists?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude"], env)
  end

  test "legacy Codex refuses every new provider transition before mutation while running or stopped", ctx do
    assert {_, 0} = launch_args(ctx, ["run"])
    File.write!(ctx.state <> ".agent", "<no value>")
    metadata = Path.join(ctx.config, "runners/#{@project_id}.env")
    File.write!(metadata, "SYMPHONY_RUNNER_PORT=4010\n")
    credentials = Path.join(ctx.config, "projects/#{@project_id}.env")
    original_credentials = File.read!(credentials)
    original_inspect = File.read!(ctx.state <> ".inspect")

    for state <- ["true\n", "false\n"], agent <- ["claude", "codex"] do
      File.write!(ctx.state, state)
      File.write!(ctx.log, "")
      assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", agent])
      assert message =~ "fresh packaged install"
      assert File.read!(ctx.state) == state
      assert File.read!(ctx.state <> ".agent") == "<no value>"
      assert File.read!(ctx.state <> ".inspect") == original_inspect
      assert File.read!(metadata) == "SYMPHONY_RUNNER_PORT=4010\n"
      assert File.read!(credentials) == original_credentials
      refute File.exists?(Path.join(ctx.config, "runners/#{@project_id}.transition"))

      for operation <- ["stop\t", "rename\t", "build\t", "--codex-config-snapshot", "--check-codex-sandbox", "--detach", "volume\trm"] do
        refute File.read!(ctx.log) =~ operation
      end
    end
  end

  test "legacy qualification rejects unknown entrypoints and mismatched port metadata", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}]
    assert {_, 0} = launch_args(ctx, ["run"], env)
    File.write!(ctx.state <> ".agent", "<no value>")
    File.write!(Path.join(ctx.config, "runners/#{@project_id}.env"), "SYMPHONY_RUNNER_PORT=4010\n")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--pause-runner"], env ++ [{"FIXTURE_ENTRYPOINT", "/foreign"}])
    assert message =~ "unknown legacy entrypoint"
    File.write!(Path.join(ctx.config, "runners/#{@project_id}.env"), "SYMPHONY_RUNNER_PORT=4999\n")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--pause-runner"], env)
    assert message =~ "legacy loopback port differs"
    refute File.read!(ctx.log) =~ "stop\t"
  end

  test "resuming a transition cannot implicitly stop a restarted legacy listener", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}]
    assert {_, 0} = launch_args(ctx, ["run"], env)
    File.write!(ctx.state <> ".agent", "<no value>")
    File.write!(Path.join(ctx.config, "runners/#{@project_id}.env"), "SYMPHONY_RUNNER_PORT=4010\n")
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
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

  test "ordinary journal retry cannot resume a same-image legacy listener as a completed candidate", ctx do
    assert {_, 0} = launch_args(ctx, ["run"])
    File.write!(ctx.state <> ".agent", "<no value>")
    File.write!(Path.join(ctx.config, "runners/#{@project_id}.env"), "SYMPHONY_RUNNER_PORT=4010\n")
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
    File.mkdir_p!(journal)
    File.write!(Path.join(journal, "target"), "codex\n")
    File.write!(Path.join(journal, "port"), "4010\n")
    File.write!(Path.join(journal, "image"), "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
    File.write!(ctx.log, "")
    File.write!(Path.join(ctx.root, "sleep"), "#!/bin/sh\nexit 0\n")
    File.chmod!(Path.join(ctx.root, "sleep"), 0o755)
    # Exercise the ordinary transition after source handoff, without invoking the source updater.
    script = ~s|source "$1" --help >/dev/null; runner_transition "$(repo_root)" codex false|
    assert {message, 1} = System.cmd("bash", ["-c", script, "fixture", @launcher], cd: ctx.repo, env: ctx.env, stderr_to_stdout: true)
    assert message =~ "pending transition has no qualified Codex settings"
    refute File.read!(ctx.log) =~ "stop\t"
    refute File.read!(ctx.log) =~ "rename\t"

    record =
      Enum.join(["codex-config-v2", "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", File.read!(ctx.state <> ".config-sha"), File.read!(ctx.state <> ".config-owner")], "\t") <>
        "\n"

    File.chmod!(journal, 0o700)
    File.write!(Path.join(journal, "image"), record)
    File.chmod!(Path.join(journal, "image"), 0o600)
    assert {message, 1} = System.cmd("bash", ["-c", script, "fixture", @launcher], cd: ctx.repo, env: ctx.env, stderr_to_stdout: true)
    assert message =~ "use symphony update --pause-runner"
    refute message =~ "candidate recovery refused"
    refute File.read!(ctx.log) =~ "stop\t"
    refute File.read!(ctx.log) =~ "rename\t"
    assert String.trim(File.read!(ctx.state)) == "true"
    assert File.read!(Path.join(journal, "image")) == record
  end

  test "pre-existing pending transition refuses incompatible legacy rollback and preserves both containers", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
    assert {_, 0} = launch_args(ctx, ["run"], env)
    assert {_, 0} = launch_args(ctx, ["runner", "stop"], env)
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
    # Model a transition retained by an older manager. New intents now refuse
    # incompatible sources before mutation; pending old-install recovery stays deferred.
    File.write!(ctx.state <> ".backup.agent", "<no value>")
    File.write!(Path.join(ctx.root, "curl-calls"), "")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    assert message =~ "legacy Codex runtime cannot safely reuse retained settings"
    assert File.read!(ctx.state <> ".agent") == "claude"
    assert File.read!(ctx.state <> ".backup.agent") == "<no value>"
    assert File.dir?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
    refute File.read!(ctx.log) =~ "stop\t"
    refute File.read!(ctx.log) =~ "rm\t"
    refute File.read!(ctx.log) =~ "volume\trm"
  end

  test "compatible rollback retains image identity and retries a failed prior start", ctx do
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"])
    fault = Path.join(ctx.root, "create-once")
    File.write!(fault, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], [{"FIXTURE_FAIL_CREATE_FILE", fault}])
    previous = Path.join(ctx.config, "runners/#{@project_id}.transition/previous.env")
    image_keys = File.read!(previous) |> String.split("\n") |> Enum.filter(&String.starts_with?(&1, "SYMPHONY_RUNNER_IMAGE_ID="))
    assert image_keys == ["SYMPHONY_RUNNER_IMAGE_ID=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    fail_start = Path.join(ctx.root, "fail-start-once")
    File.write!(fail_start, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], [{"FIXTURE_FAIL_START_FILE", fail_start}])
    assert File.read!(ctx.state <> ".agent") == "codex"
    assert File.read!(ctx.state) |> String.trim() == "false"
    refute File.exists?(ctx.state <> ".backup")
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"])
    assert message =~ "Prior runner restored"
    refute File.exists?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
    refute File.read!(ctx.log) =~ "volume\trm"
  end

  test "rollback restores a provider-labelled image with the pre-admission state API", ctx do
    ready_curl!(ctx)
    File.chmod!(Path.join(ctx.root, "curl"), 0o755)
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
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
    refute File.exists?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
  end

  test "recheck client allows two serialized bounded observations without extending other controls", ctx do
    ready_curl!(ctx)
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
    assert {_, 0} = launch_args(ctx, ["run"], env)
    calls = Path.join(ctx.root, "control-calls")

    File.write!(Path.join(ctx.root, "curl"), """
    #!/bin/sh
    printf '%s\\n' "$*" >> '#{calls}'
    printf '{}'
    """)

    for action <- ["recheck", "resume", "drain"] do
      assert {_, 0} = launch_args(ctx, ["runner", action], env)
    end

    [recheck, resume, drain] = calls |> File.read!() |> String.split("\n", trim: true)
    assert recheck =~ "--max-time 80"
    assert resume =~ "--max-time 45"
    assert drain =~ "--max-time 45"
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
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
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
    refute File.exists?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
    refute File.read!(ctx.log) =~ "volume\trm"
  end

  test "development retry replaces a stale crash-loop candidate without changing workflow or rollback source", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
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
    assert calls =~ "stop\t--time\t30\tsymphony-#{ctx.namespace}-service"
    assert calls =~ "tag\tfixture-claude-image-new\tsymphony-#{ctx.namespace}-runner-claude:local"
    refute calls =~ "volume\trm"
    assert previous =~ "SYMPHONY_RUNNER_IMAGE_ID=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  end

  test "retry repairs metadata after candidate creation interrupts before metadata publication", ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"], env)
    assert {_, 0} = launch_args(ctx, ["runner", "stop"], env)
    metadata = Path.join(ctx.config, "runners/#{@project_id}.env")
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

  test "Claude credential mutations refuse the stopped-source transition window while checks and unrelated projects proceed", ctx do
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"])
    barrier = Path.join(ctx.root, "auth-transition-barrier")

    update =
      Task.async(fn ->
        launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], [{"FIXTURE_STOP_BARRIER", barrier}])
      end)

    on_exit(fn -> File.touch(barrier <> ".release") end)
    await_file!(barrier <> ".entered")
    assert File.read!(ctx.state) == "false\n"
    metadata = File.read!(Path.join(ctx.config, "runners/#{@project_id}.env"))
    alternate = Path.join(ctx.root, "auth-config-alias")

    for config <- [ctx.config, alternate], action <- ["login", "remove"] do
      calls = File.read!(ctx.log)

      assert {message, 1} =
               launch_args(ctx, ["auth", "claude", action], [
                 {"SYMPHONY_CONFIG_HOME", config},
                 {"SYMPHONY_RUNNER_HOME", Path.join(config, "runners")}
               ])

      assert message =~ "another updater is active"
      # Ownership is acquired before image, volume, or native-login prerequisites.
      assert File.read!(ctx.log) == calls
      assert File.read!(ctx.state) == "false\n"
      assert File.read!(Path.join(ctx.config, "runners/#{@project_id}.env")) == metadata
    end

    assert {_, 0} = launch_args(ctx, ["auth", "claude", "check"])
    other_repo = Path.join(ctx.root, "auth-other-project")
    File.mkdir_p!(other_repo)
    assert {_, 0} = System.cmd("git", ["init", "-q", other_repo])
    assert {_, 0} = System.cmd("git", ["-C", other_repo, "remote", "add", "origin", "https://github.com/example/other.git"])
    assert {_, 0} = launch_args(%{ctx | repo: other_repo}, ["auth", "claude", "login"])
    assert {_, 0} = launch_args(%{ctx | repo: other_repo}, ["auth", "claude", "remove"])

    File.touch!(barrier <> ".release")
    # The updater invokes native readiness internally while holding the same lock.
    assert {_, 0} = Task.await(update, 10_000)
    assert {_, 0} = launch_args(ctx, ["auth", "claude", "login"])
    assert {_, 0} = launch_args(ctx, ["auth", "claude", "remove"])
    calls = File.read!(ctx.log)
    assert calls =~ "auth.mjs\tlogin"
    assert calls =~ "auth.mjs\tlogout"
  end

  for action <- ["login", "remove"] do
    @action action
    test "Claude #{@action} retains project ownership through native credential mutation", ctx do
      barrier = Path.join(ctx.root, "native-auth-barrier")

      mutation =
        Task.async(fn ->
          launch_args(ctx, ["auth", "claude", @action], [{"FIXTURE_AUTH_BARRIER", barrier}])
        end)

      on_exit(fn -> File.touch(barrier <> ".release") end)
      await_file!(barrier <> ".entered")
      calls = File.read!(ctx.log)

      for args <- [["runner", "start"], ["update", "--development-runner", "--agent", "claude"], ["auth", "claude", "login"], ["auth", "claude", "remove"]] do
        assert {message, 1} = launch_args(ctx, args)
        assert message =~ "another updater is active"
        assert File.read!(ctx.log) == calls
      end

      File.touch!(barrier <> ".release")
      assert {_, 0} = Task.await(mutation, 10_000)
      assert {_, 0} = launch_args(ctx, ["auth", "claude", "check"])
    end
  end

  test "official mutations share ownership across configuration aliases while reads and unrelated projects proceed",
       ctx do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}]
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"], env)
    barrier = Path.join(ctx.root, "stop-barrier")

    update =
      Task.async(fn ->
        launch_args(
          ctx,
          ["update", "--development-runner", "--agent", "claude"],
          env ++ [{"FIXTURE_STOP_BARRIER", barrier}]
        )
      end)

    on_exit(fn -> File.touch(barrier <> ".release") end)
    await_file!(barrier <> ".entered")
    stopped = File.read!(ctx.state)
    metadata = File.read!(Path.join(ctx.config, "runners/#{@project_id}.env"))
    alternate = Path.join(ctx.root, "alternate-config")
    File.mkdir_p!(Path.join(alternate, "projects"))

    File.cp!(
      Path.join(ctx.config, "projects/#{@project_id}.env"),
      Path.join(alternate, "projects/#{@project_id}.env")
    )

    for config <- [ctx.config, alternate],
        args <- [
          ["run"],
          ["runner", "start"],
          ["runner", "stop"],
          ["update", "--development-runner", "--pause-runner"],
          ["update", "--development-runner", "--agent", "claude"],
          ["update", "--development-runner", "--rollback-runner"],
          ["runner", "drain"],
          ["runner", "resume"],
          ["runner", "recheck"]
        ] do
      alias_env = [
        {"SYMPHONY_CONFIG_HOME", config},
        {"SYMPHONY_RUNNER_HOME", Path.join(config, "runners")}
      ]

      assert {message, 1} = launch_args(ctx, args, env ++ alias_env)
      assert message =~ "another updater is active", inspect({config, args})
      assert File.read!(ctx.state) == stopped
      assert File.read!(Path.join(ctx.config, "runners/#{@project_id}.env")) == metadata
      refute File.exists?(ctx.state <> ".backup")
    end

    assert {status, 0} = launch_args(ctx, ["runner", "status"], env)
    assert status =~ "stopped"
    other_repo = Path.join(ctx.root, "other")
    File.mkdir_p!(other_repo)
    assert {_, 0} = System.cmd("git", ["init", "-q", other_repo])

    assert {_, 0} =
             System.cmd("git", [
               "-C",
               other_repo,
               "remote",
               "add",
               "origin",
               "https://github.com/example/other.git"
             ])

    File.write!(Path.join(other_repo, "WORKFLOW.md"), "fixture workflow")

    File.cp!(
      Path.join(ctx.config, "projects/#{@project_id}.env"),
      Path.join(ctx.config, "projects/#{project_id!(other_repo)}.env")
    )

    assert {_, 0} =
             launch_args(
               %{ctx | repo: other_repo},
               ["run"],
               env ++ [{"FIXTURE_STATE", Path.join(ctx.root, "other-running")}]
             )

    File.touch!(barrier <> ".release")
    assert {_, 0} = Task.await(update, 10_000)
    assert File.read!(ctx.state <> ".agent") == "claude"
    refute File.exists?(ctx.state <> ".backup")
  end

  for {source, target} <- [{"codex", "claude"}, {"claude", "codex"}] do
    @source source
    @target target
    test "retry restarts stopped #{@target} candidate after interrupted metadata publication",
         ctx do
      env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}]
      ready_curl!(ctx)
      assert {_, 0} = launch_args(ctx, ["run", "--agent", @source], env)
      fail = Path.join(ctx.root, "fail-create-once")
      File.touch!(fail)

      assert {_, 1} =
               launch_args(
                 ctx,
                 ["update", "--development-runner", "--agent", @target],
                 env ++ [{"FIXTURE_FAIL_CREATE_FILE", fail}]
               )

      assert {_, 0} = launch_args(ctx, ["runner", "stop"], env)
      assert File.read!(ctx.state) == "false\n"
      journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
      previous = File.read!(Path.join(journal, "previous.env"))
      # Provider/profile are cache fields, not authority for the retained image.
      stale = previous |> String.replace("SYMPHONY_RUNNER_AGENT=#{@source}", "SYMPHONY_RUNNER_AGENT=#{@target}") |> String.replace(~r/SYMPHONY_RUNNER_PROFILE=.*/, "SYMPHONY_RUNNER_PROFILE=stale")
      File.write!(Path.join(journal, "previous.env"), stale)
      File.write!(ctx.log, "")

      assert {message, 0} =
               launch_args(ctx, ["update", "--development-runner", "--agent", @target], env)

      assert message =~ "Runner transition complete"
      assert File.read!(ctx.log) =~ "start\tsymphony-#{ctx.namespace}-#{@project_id}\t"
      refute File.read!(ctx.log) =~ ~r/(^|\n)rm\tsymphony-#{ctx.namespace}-#{@project_id}\t/
      assert File.read!(ctx.state <> ".agent") == @target

      assert File.read!(ctx.state <> ".volume-owner") ==
               if(@target == "claude", do: "11001", else: "0")

      assert previous =~ "SYMPHONY_RUNNER_AGENT=#{@source}"
      refute File.exists?(journal)
    end
  end

  for {suffix, value, message} <- [
        {".storage", "foreign", "foreign runner storage"},
        {".image", "foreign", "candidate image differs"},
        {".agent", "codex", "candidate provider differs"},
        {".port", "4099", "candidate port differs"},
        {".backup.image", "foreign", "prior image differs"},
        {".backup.port", "4099", "prior port differs"},
        {".backup", "true", "prior runner is unexpectedly active"}
      ] do
    @suffix suffix
    @value value
    @message message
    test "stopped candidate recovery refuses changed identity #{@suffix} before start", ctx do
      env = pending_claude_transition!(ctx)
      ready_curl!(ctx)
      assert {_, 0} = launch_args(ctx, ["runner", "stop"], env)
      File.write!(ctx.state <> @suffix, @value)
      File.write!(ctx.log, "")

      assert {message, 1} =
               launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)

      assert message =~ @message
      refute File.read!(ctx.log) =~ ~r/(^|\n)(start|stop|rm|rename)\t/
      assert File.exists?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
    end
  end

  test "postcommit retry reconstructs metadata from the selected container", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
    metadata = Path.join(ctx.config, "runners/#{@project_id}.env")
    File.cp!(Path.join(journal, "previous.env"), metadata)
    File.rm!(ctx.state <> ".backup")
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert File.read!(metadata) =~ "SYMPHONY_RUNNER_IMAGE_ID=fixture-claude-image\n"
    refute File.exists?(journal)
  end

  test "stopped candidate retains rollback evidence when native auth or current admission refuses",
       ctx do
    env = pending_claude_transition!(ctx)
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["runner", "stop"], env)
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
    previous = File.read!(Path.join(journal, "previous.env"))
    File.write!(ctx.log, "")

    assert {_, 1} =
             launch_args(
               ctx,
               ["update", "--development-runner", "--agent", "claude"],
               env ++ [{"FIXTURE_AUTH_FAIL", "true"}]
             )

    refute File.read!(ctx.log) =~ ~r/(^|\n)start\t/
    assert File.read!(ctx.state) == "false\n"

    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env ++ [{"FIXTURE_ACTIVE_STORAGE_USER", "foreign-container"}])
    assert message =~ "retained volume is in use"
    refute File.read!(ctx.log) =~ ~r/(^|\n)start\t/
    assert File.read!(ctx.state) == "false\n"

    File.write!(Path.join(ctx.root, "curl"), """
    #!/bin/sh
    [ -f "$FIXTURE_STATE" ] && [ "$(cat "$FIXTURE_STATE")" = true ] || exit 1
    printf '%s' '{"counts":{"running":0},"agent":{"draining":true,"admission":{"ready":false,"reason":"quota"}}}'
    """)

    assert {message, 1} =
             launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)

    assert message =~ "candidate is not ready"
    assert File.read!(ctx.log) =~ "start\tsymphony-#{ctx.namespace}-#{@project_id}\t"
    assert File.read!(Path.join(journal, "previous.env")) == previous
    assert File.exists?(ctx.state <> ".backup")
    assert File.exists?(ctx.state <> ".unresumed")
    assert File.exists?(ctx.state <> ".draining")
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
  end

  test "rollback restores observed prior identity rather than stale cached fields", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
    # Keep the recorded image as rollback evidence; provider/profile are derived cache fields.
    File.write!(
      Path.join(journal, "previous.env"),
      "SYMPHONY_RUNNER_PORT=4010\nSYMPHONY_RUNNER_AGENT=claude\nSYMPHONY_RUNNER_IMAGE_ID=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nSYMPHONY_RUNNER_PROFILE=stale\n"
    )

    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    metadata = File.read!(Path.join(ctx.config, "runners/#{@project_id}.env"))
    assert metadata =~ "SYMPHONY_RUNNER_AGENT=codex\n"
    assert metadata =~ "SYMPHONY_RUNNER_PROFILE=none\n"
    assert metadata =~ "SYMPHONY_RUNNER_IMAGE_ID=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
  end

  test "failed development rebuild leaves the pending image, container and rollback metadata intact", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
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
    assert File.read!(Path.join(ctx.config, "runners/#{@project_id}.transition/image")) == "fixture-claude-image\n"
  end

  test "development refresh refuses a foreign candidate before stopping or removing it", ctx do
    env = pending_claude_transition!(ctx)
    File.write!(ctx.state <> ".image", "foreign-image")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}])
    assert message =~ "candidate image differs"
    assert File.read!(Path.join(ctx.config, "runners/#{@project_id}.transition/image")) == "fixture-claude-image\n"
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
    assert File.read!(Path.join(ctx.config, "runners/#{@project_id}.transition/image")) == "fixture-claude-image\n"
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "Runner transition complete: claude"
    assert File.read!(ctx.state <> ".image") == "fixture-claude-image-new"
  end

  defp pending_claude_transition!(ctx) do
    env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace}, {"PATH", ctx.root <> ":" <> System.fetch_env!("PATH")}]
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
    [ -f "$FIXTURE_STATE" ] && [ "$(cat "$FIXTURE_STATE")" = true ] || exit 1
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
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")

    for suffix <- ["", ".agent", ".port", ".profile", ".image", ".storage"] do
      if File.exists?(ctx.state <> ".backup" <> suffix) do
        File.rename!(ctx.state <> ".backup" <> suffix, ctx.state <> suffix)
      end
    end

    File.cp!(Path.join(journal, "previous.env"), Path.join(ctx.config, "runners/#{@project_id}.env"))
    ready_curl!(ctx)
    File.write!(ctx.log, "")
    env = env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}]
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "Runner transition complete: claude"
    assert File.read!(ctx.state <> ".image") == "fixture-claude-image-new"
    refute File.read!(ctx.log) =~ ~r/(^|\n)rm\tsymphony-#{ctx.namespace}-#{@project_id}\t/
  end

  test "development refresh refuses a running rollback source without replacing either container", ctx do
    env = pending_claude_transition!(ctx)
    File.write!(ctx.state <> ".backup", "true")
    File.write!(ctx.log, "")
    env = env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}]
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "prior runner is unexpectedly active"
    refute File.read!(ctx.log) =~ ~r/(^|\n)(stop|rm)\t/
    assert File.read!(Path.join(ctx.config, "runners/#{@project_id}.transition/image")) == "fixture-claude-image\n"
  end

  test "same-image development retry resumes the journal without replacing the candidate", ctx do
    env = pending_claude_transition!(ctx)
    ready_curl!(ctx)
    File.write!(ctx.log, "")
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    refute message =~ "Refreshed pending development candidate"
    calls = File.read!(ctx.log)
    refute calls =~ "stop\t"
    refute calls =~ ~r/(^|\n)rm\tsymphony-#{ctx.namespace}-#{@project_id}\t/
    assert File.read!(ctx.state <> ".image") == "fixture-claude-image"
  end

  test "failed replacement readiness retains the original rollback image and remains recoverable", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
    previous = File.read!(Path.join(journal, "previous.env"))
    File.write!(ctx.state, "false")
    env = env ++ [{"FIXTURE_CLAUDE_IMAGE_ID", "fixture-claude-image-new"}]
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], env)
    assert message =~ "candidate is not ready"
    assert File.read!(Path.join(journal, "image")) == "fixture-claude-image-new\n"
    assert File.read!(Path.join(journal, "previous.env")) == previous
    assert File.read!(ctx.state <> ".backup.image") == "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    ready_curl!(ctx)
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"], env)
    assert message =~ "Prior runner restored"
    assert File.read!(ctx.state <> ".image") == "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  end

  test "development refresh recovers a journal after the old candidate committed and backup was removed", ctx do
    env = pending_claude_transition!(ctx)
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
    # The previous transition passed readiness and removed its backup, but died before journal cleanup.
    File.rm!(ctx.state <> ".backup")
    File.cp!(Path.join(journal, "previous.env"), Path.join(ctx.config, "runners/#{@project_id}.env"))
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
    dev_env = [{"SYMPHONY_RUNNER_NAMESPACE", ctx.namespace <> "-dev"}]
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude", "--port", "14030"], dev_env)
    calls = File.read!(ctx.log)
    assert calls =~ "symphony-#{ctx.namespace}-dev-service-"
    assert calls =~ "--name\tsymphony-#{ctx.namespace}-dev-#{@project_id}\t"
    assert calls =~ "symphony-#{ctx.namespace}-dev-runner-claude:local"
    assert calls =~ "-claude-auth:/home/claude"
    refute calls =~ "symphony-#{ctx.namespace}-service"
    refute calls =~ "symphony-runner-claude:local"

    File.write!(ctx.log, "")
    assert {_, 0} = launch_args(ctx, ["auth", "codex", "check"], dev_env)
    assert File.read!(ctx.log) =~ "symphony-#{ctx.namespace}-dev-codex-auth:/root/.codex"
  end

  test "invalid namespaces and unscoped development updates fail before runtime operations", ctx do
    for namespace <- ["../stable", "UPPER", "-dev", String.duplicate("a", 33)] do
      assert {_, 1} = launch_args(ctx, ["run"], [{"SYMPHONY_RUNNER_NAMESPACE", namespace}])
    end

    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], [{"SYMPHONY_RUNNER_NAMESPACE", nil}])
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

  test "normal transition refuses unknown staging before manager activation or backup removal", ctx do
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"])

    script = """
    source "$1" --help >/dev/null
    root=$(repo_root)
    # Inject an interruption at the completed candidate's commit boundary.
    # Startup and ownership/preflight still use the actual manager functions.
    runner_record_observed_metadata() {
      journal="$runner_home/$(project_id "$root").transition"
      printf unknown > "$journal/.image.stage-foreign-name"
      chmod 600 "$journal/.image.stage-foreign-name"
      packaged_install=true
    }
    package_helper() {
      case "$1" in codex-config-store) shift; python3 -I "$symphony_home/scripts/codex-config-store.py" "$@" ;; *) return 1 ;; esac
    }
    package_finish_transition() { printf activated > "$FIXTURE_ACTIVATION_CANARY"; }
    runner_transition "$root" codex true
    """

    canary = Path.join(ctx.root, "manager-activation")
    env = [{"FIXTURE_IMAGE_ID", "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}, {"FIXTURE_ACTIVATION_CANARY", canary} | ctx.env]
    assert {_, 1} = System.cmd("bash", ["-euc", script, "fixture", @launcher], cd: ctx.repo, env: env, stderr_to_stdout: true)
    refute File.exists?(canary)
    assert File.exists?(ctx.state <> ".backup")
    assert File.read!(ctx.state <> ".backup.image") == "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert File.read!(ctx.state <> ".image") == "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
    assert File.dir?(journal)
    assert File.read!(Path.join(journal, ".image.stage-foreign-name")) == "unknown"
    refute File.exists?(journal <> ".complete")
  end

  for unknown <- [false, true] do
    @unknown unknown
    test "journal settles interrupted refresh before completion unknown=#{unknown}", ctx do
      script = """
      source "$1" --help >/dev/null
      root=$(repo_root); runtime=$(container_runtime)
      runner_project_lock "$root"
      journal="$runner_home/$(project_id "$root").transition"
      mkdir -m 700 "$journal"
      printf codex > "$journal/target"; printf 4010 > "$journal/port"; printf metadata > "$journal/previous.env"
      codex_config_select "$root" "$runtime" sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      codex_config_journal_write "$root" sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "$journal"
      codex_config_store selection-stage "$(codex_config_selection_path "$journal")" sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc "$codex_config_sha" "$codex_config_owner" >/dev/null
      temporary="$journal/.image.stage-#{if @unknown, do: "foreign-name", else: String.duplicate("a", 32)}"
      printf 'codex-config-v2' > "$temporary"; chmod 600 "$temporary"
      runner_finish_journal "$journal"
      """

      {_, code} = System.cmd("bash", ["-euc", script, "fixture", @launcher], cd: ctx.repo, env: ctx.env, stderr_to_stdout: true)
      journal = Path.join(ctx.config, "runners/#{@project_id}.transition")

      if @unknown do
        assert code == 1
        assert File.dir?(journal)
        assert File.read!(Path.join(journal, ".image.stage-foreign-name")) == "codex-config-v2"
        assert File.exists?(Path.join(journal, "image.next"))
        assert File.exists?(Path.join(journal, "image"))
        refute File.exists?(journal <> ".complete")
      else
        assert code == 0
        refute File.exists?(journal)
        refute File.exists?(journal <> ".complete")
      end
    end
  end

  test "snapshot cleanup preserves current and staged journal references without a backup", ctx do
    assert {_, 0} = launch_args(ctx, ["run"])
    bytes = "[projects.\"/workspaces/retired-fixture\"]\ntrust_level = \"trusted\"\n"
    digest = Base.encode16(:crypto.hash(:sha256, bytes), case: :lower)

    export =
      Jason.encode!(%{
        schema: 1,
        mode: "codex-config-snapshot",
        profile: "trust-ro-v2",
        config_base64: Base.encode64(bytes),
        sha256: digest,
        source: %{
          present: true,
          uid: 0,
          gid: 0,
          mode: 0o600,
          device: 1,
          inode: 1,
          size: byte_size(bytes),
          mtime_ns: 0,
          ctime_ns: 0
        }
      })

    script = """
    source "$1" --help >/dev/null
    root=$(repo_root); runtime=$(container_runtime); name=$(runner_id "$root")
    runner_project_lock "$root"
    journal="$runner_home/$(project_id "$root").transition"
    mkdir -m 700 "$journal"; printf codex > "$journal/target"
    codex_config_observe "$root" "$runtime" "$name" sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    codex_config_journal_write "$root" sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "$journal"
    codex_config_select "$root" "$runtime" sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
    codex_config_store selection-stage "$(codex_config_selection_path "$journal")" sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc "$codex_config_sha" "$codex_config_owner" >/dev/null
    codex_config_cleanup "$root" "$runtime"
    """

    env = [{"FIXTURE_CONFIG_JSON", export} | ctx.env]
    assert {_, 0} = System.cmd("bash", ["-euc", script, "fixture", @launcher], cd: ctx.repo, env: env, stderr_to_stdout: true)
    root = Path.join(ctx.config, "runners/symphony-#{ctx.namespace}-#{@project_id}-codex-config")
    assert File.read!(Path.join(root, digest <> "/config.toml")) == bytes
    assert File.exists?(Path.join(root, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/config.toml"))
  end

  test "new same-image Codex update captures current trust once and replaces snapshot", ctx do
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"])
    previous = File.read!(ctx.state <> ".config-sha")
    bytes = "[projects.\"/workspaces/new-trust-fixture\"]\ntrust_level = \"trusted\"\n"
    digest = Base.encode16(:crypto.hash(:sha256, bytes), case: :lower)

    export =
      Jason.encode!(%{
        schema: 1,
        mode: "codex-config-snapshot",
        profile: "trust-ro-v2",
        config_base64: Base.encode64(bytes),
        sha256: digest,
        source: %{
          present: true,
          uid: 0,
          gid: 0,
          mode: 0o600,
          device: 1,
          inode: 1,
          size: byte_size(bytes),
          mtime_ns: 0,
          ctime_ns: 0
        }
      })

    File.write!(ctx.log, "")
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"], [{"FIXTURE_CONFIG_JSON", export}])
    assert File.read!(ctx.state <> ".config-sha") == digest
    refute previous == digest
    assert File.read!(ctx.state <> ".image") == "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert length(Regex.scan(~r/--codex-config-snapshot/, File.read!(ctx.log))) == 1
    assert File.read!(ctx.log) =~ "rename\t"
    refute File.exists?(ctx.state <> ".backup")
  end

  test "Codex development refresh pins one atomic tuple across interrupted replacement", ctx do
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"])
    create_fault = Path.join(ctx.root, "create-once")
    File.write!(create_fault, "")
    first = "sha256:" <> String.duplicate("c", 64)
    replacement = "sha256:" <> String.duplicate("e", 64)
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"], [{"FIXTURE_IMAGE_ID", first}, {"FIXTURE_FAIL_CREATE_FILE", create_fault}])
    journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
    before = File.read!(Path.join(journal, "image"))
    assert before =~ "codex-config-v2\t#{first}\t"
    remove_fault = Path.join(ctx.root, "remove-once")
    File.write!(remove_fault, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"], [{"FIXTURE_IMAGE_ID", replacement}, {"FIXTURE_FAIL_RM_FILE", remove_fault}])
    assert File.read!(Path.join(journal, "image")) == before
    assert File.read!(Path.join(journal, "image.next")) =~ "codex-config-v2\t#{replacement}\t"
    assert File.read!(ctx.state <> ".backup.image") == "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    File.write!(ctx.log, "")
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"], [{"FIXTURE_IMAGE_ID", replacement}])
    refute File.read!(ctx.log) =~ "--codex-config-snapshot"
    assert File.read!(ctx.state <> ".image") == replacement
    refute File.exists?(journal)
    refute File.exists?(ctx.state <> ".backup")
  end

  test "Codex sealed settings override project selectors and survive ordinary stopped restart", ctx do
    project_env = Path.join(ctx.config, "projects/#{@project_id}.env")
    File.write!(project_env, "GH_TOKEN=fixture\nHOME=/foreign\nCODEX_HOME=/foreign\nSYMPHONY_CODEX_CONFIG_PROFILE=foreign\n")
    assert {_, 0} = launch_args(ctx, ["run"])
    [container] = Jason.decode!(File.read!(ctx.state <> ".inspect"))
    assert "HOME=/root" in container["Config"]["Env"]
    assert "CODEX_HOME=/root/.codex" in container["Config"]["Env"]
    assert "SYMPHONY_CODEX_CONFIG_PROFILE=trust-ro-v2" in container["Config"]["Env"]
    digest = File.read!(ctx.state <> ".config-sha")
    assert {_, 0} = launch(ctx, "stop")
    File.write!(ctx.log, "")
    assert {_, 0} = launch(ctx, "start")
    assert File.read!(ctx.state <> ".config-sha") == digest
    refute File.read!(ctx.log) =~ "--codex-config-snapshot"
  end

  test "stopped Codex refuses altered actual snapshot mount before replacement", ctx do
    assert {_, 0} = launch_args(ctx, ["run"])
    assert {_, 0} = launch(ctx, "stop")
    [container] = Jason.decode!(File.read!(ctx.state <> ".inspect"))

    mounts =
      Enum.map(container["Mounts"], fn mount ->
        if mount["Destination"] == "/root/.codex/config.toml", do: Map.put(mount, "RW", true), else: mount
      end)

    File.write!(ctx.state <> ".inspect", Jason.encode!([Map.put(container, "Mounts", mounts)]))
    File.write!(ctx.log, "")
    assert {output, 1} = launch(ctx, "start")
    assert output =~ "actual Codex settings mounts or selectors differ"
    refute File.read!(ctx.log) =~ "rm\t"
    assert File.read!(ctx.state) == "false\n"
  end

  test "invalid native settings export refuses updater before drain or journal", ctx do
    assert {_, 0} = launch_args(ctx, ["run"])
    ready_curl!(ctx)
    File.write!(ctx.log, "")
    assert {output, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"], [{"FIXTURE_CONFIG_FAIL", "true"}])
    assert output =~ "native Codex settings snapshot refused"
    refute File.read!(ctx.log) =~ "stop\t"
    refute File.read!(ctx.log) =~ "rename\t"
    refute File.dir?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
    assert File.read!(ctx.state) == "true\n"
  end

  test "legacy Codex rollback refuses before removing the working candidate", ctx do
    assert {_, 0} = launch_args(ctx, ["run"])
    ready_curl!(ctx)
    fault = Path.join(ctx.root, "create-fault")
    File.write!(fault, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], [{"FIXTURE_FAIL_CREATE_FILE", fault}])
    assert File.exists?(ctx.state <> ".backup")
    # Already-pending old-install recovery remains refused. New transitions
    # cannot create this incompatible rollback source.
    File.write!(ctx.state <> ".backup.codex-runtime", "old-profile")
    File.write!(ctx.log, "")
    assert {output, 1} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"])
    assert output =~ "rollback refused before stopping candidate"
    refute File.read!(ctx.log) =~ "stop\t"
    refute File.read!(ctx.log) =~ "rm\t"
    assert File.read!(ctx.state) == "true\n"
  end

  test "selected daemon AppArmor profile binds fresh preflight listener and migration refusal", ctx do
    apparmor = [{"FIXTURE_SECURITY_OPTIONS", "name=apparmor,profile=default\nname=seccomp,profile=builtin"}]
    assert {_, 0} = launch_args(ctx, ["run"], apparmor)
    original = File.read!(ctx.state <> ".codex-runtime")
    assert original =~ "9a6ad788ecdd9e43e5d4d029dab0a76d467777f56428ae0e9f04cb5501a7311b:config-trust-ro-v2:apparmor:symphony-codex-candidate-b6261d4c613296dd:"
    calls = File.read!(ctx.log) |> String.split("\n")

    for call <- Enum.filter(calls, &(String.contains?(&1, "--check-codex-sandbox") or String.contains?(&1, "--detach"))) do
      assert call =~ "--security-opt\tapparmor=symphony-codex-candidate-b6261d4c613296dd"
    end

    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["run"])
    assert message =~ "runtime profile differs"
    ready_curl!(ctx)
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"])
    assert message =~ "fresh packaged install"
    assert File.read!(ctx.state <> ".codex-runtime") == original
    refute File.dir?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
    refute File.read!(ctx.log) =~ "stop\t"
    refute File.read!(ctx.log) =~ "rename\t"
    refute File.read!(ctx.log) =~ "--detach"
  end

  test "older Codex migration refuses either target before journal preflight build or shutdown", ctx do
    assert {_, 0} = launch_args(ctx, ["run"])
    File.write!(ctx.state <> ".codex-runtime", "old-runtime-profile")
    metadata = Path.join(ctx.config, "runners/#{@project_id}.env")
    original_metadata = File.read!(metadata)
    original_inspect = File.read!(ctx.state <> ".inspect")
    credentials = Path.join(ctx.config, "projects/#{@project_id}.env")
    original_credentials = File.read!(credentials)

    for agent <- ["codex", "claude"] do
      File.write!(ctx.log, "")
      assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", agent])
      assert message =~ "before journal or listener shutdown"
      assert String.trim(File.read!(ctx.state)) == "true"
      assert File.read!(ctx.state <> ".codex-runtime") == "old-runtime-profile"
      assert File.read!(ctx.state <> ".inspect") == original_inspect
      assert File.read!(metadata) == original_metadata
      assert File.read!(credentials) == original_credentials
      refute File.dir?(Path.join(ctx.config, "runners/#{@project_id}.transition"))

      for operation <- ["stop\t", "rename\t", "build\t", "--codex-config-snapshot", "--check-codex-sandbox", "--detach", "volume\trm"] do
        refute File.read!(ctx.log) =~ operation
      end
    end
  end

  test "unavailable or malformed daemon security cannot reuse an empty recipe label", ctx do
    assert {_, 0} = launch_args(ctx, ["run"])
    File.write!(ctx.state <> ".codex-runtime", "")

    for env <- [[{"FIXTURE_INFO_FAIL", "true"}], [{"FIXTURE_SECURITY_EMPTY", "true"}], [{"FIXTURE_SECURITY_OPTIONS", "invalid"}]] do
      File.write!(ctx.log, "")
      assert {message, 1} = launch_args(ctx, ["run"], env)
      assert message =~ "security options", inspect({env, message})
      refute File.read!(ctx.log) =~ "--detach"
      refute File.read!(ctx.log) =~ "stop\t"
      assert File.read!(ctx.state) |> String.trim() == "true"
    end
  end

  test "AppArmor profile integrity refuses modified missing and symlinked inputs", ctx do
    home = Path.join(ctx.root, "profile-home")
    File.mkdir_p!(Path.join(home, "scripts"))
    source = Path.join(Path.dirname(@launcher), "codex-apparmor.profile")
    profile = Path.join(home, "scripts/codex-apparmor.profile")
    File.cp!(Path.join(Path.dirname(@launcher), "codex-seccomp.json"), Path.join(home, "scripts/codex-seccomp.json"))
    File.cp!(source, profile)

    script = ~S"""
    source "$1" --help >/dev/null
    symphony_home=$2
    runner_codex_run "$SYMPHONY_CONTAINER_RUNTIME" --rm sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    """

    env = [{"FIXTURE_SECURITY_OPTIONS", "name=apparmor"} | ctx.env]
    assert {_, 0} = System.cmd("bash", ["-euc", script, "profile", @launcher, home], env: env, stderr_to_stdout: true)

    for kind <- [:modified, :symlink, :missing] do
      File.rm!(profile)

      case kind do
        :modified -> File.write!(profile, "modified")
        :symlink -> File.ln_s!(source, profile)
        :missing -> :ok
      end

      File.write!(ctx.log, "")
      assert {message, 1} = System.cmd("bash", ["-euc", script, "profile", @launcher, home], env: env, stderr_to_stdout: true)
      assert message =~ "Codex runtime profile"
      refute File.read!(ctx.log) =~ "run\t"
    end
  end

  test "invalid provider flags fail before any container or credential operation", ctx do
    for args <- [["run", "--agent", "unknown"], ["run", "--agent"], ["run", "--agent", "claude", "--agent", "codex"], ["run", "--agent", "claude", "--host"]] do
      assert {_, 1} = launch_args(ctx, args)
    end

    refute File.exists?(ctx.log)
  end

  test "Claude selects only its project login and keeps selected Git secrets out of arguments", ctx do
    File.write!(Path.join(ctx.config, "projects/#{@project_id}.env"), "GH_TOKEN=git-secret-canary\n")
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude", "--port", "4111"])
    calls = File.read!(ctx.log)
    assert calls =~ "com.symphony.agent=claude"
    assert calls =~ "--read-only"
    assert calls =~ "--cap-drop\tALL"
    assert calls =~ "--security-opt\tno-new-privileges"
    assert calls =~ "--pids-limit\t512\t--cpus\t4"
    assert calls =~ "/tmp:rw,nosuid,nodev,size=512m"
    assert calls =~ "/var/tmp/symphony-build:rw,exec,nosuid,nodev,uid=11001,gid=11001,mode=700,size=1g"
    assert calls =~ "com.symphony.claude.runtime=cpu4-pid512-build1g-v1"
    assert calls =~ "127.0.0.1:4111:4010"
    assert calls =~ "-claude-auth:/home/claude"
    assert calls =~ "--env\tSYMPHONY_GIT_TOKEN\t"
    refute calls =~ "git-secret-canary"
    refute calls =~ "symphony-#{ctx.namespace}-codex-auth"
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

    for state <- ["true", "false"] do
      File.write!(ctx.state, state)
      assert {message, 1} = launch_args(ctx, ["run", "--agent", "claude"])
      assert message =~ "listener agent differs"
      assert File.read!(ctx.state) == state
    end

    File.write!(ctx.state, "true")
    assert {message, 1} = launch_args(ctx, ["run"], [{"FIXTURE_IMAGE_ID", "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}])
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
    assert calls =~ "--check-codex-sandbox"
    assert calls =~ "--network\tnone"
    refute calls =~ "--detach"
    refute calls =~ "build\t"
    refute File.exists?(ctx.state)
    File.write!(ctx.log, "")
    assert {_, 0} = launch_args(ctx, ["runner", "doctor", "--agent", "claude"])
    calls = File.read!(ctx.log)
    assert calls =~ "auth.mjs\tcheck"
    refute calls =~ "--check-codex-sandbox"
    refute calls =~ "--detach"
    refute calls =~ "build\t"
    refute File.exists?(ctx.state)
  end

  test "sandbox refusal prevents fresh listener and doctor authentication", ctx do
    for args <- [["runner", "start"], ["runner", "doctor"]] do
      File.write!(ctx.log, "")
      assert {output, 1} = launch_args(ctx, args, [{"FIXTURE_SANDBOX_FAIL", "true"}])
      assert output =~ "sandbox_namespace_denied"
      refute File.exists?(ctx.state)
      calls = File.read!(ctx.log)
      refute calls =~ "--detach"
      refute calls =~ "login\tstatus"
      check = calls |> String.split("\n") |> Enum.find(&String.contains?(&1, "--check-codex-sandbox"))
      refute check =~ "codex-auth"
      refute check =~ "--env-file"
      assert check =~ "--volume\t/workspaces"
      assert check =~ "SYMPHONY_CONTAINERIZED=1"
      assert check =~ "seccomp="
    end
  end

  test "doctor reports launch stage remedies without authentication startup or image rebuild", ctx do
    credentials = Path.join(ctx.config, "projects/#{@project_id}.env")
    original_credentials = File.read!(credentials)
    workflow = Path.join(ctx.repo, "WORKFLOW.md")
    original_workflow = File.read!(workflow)

    for {code, stage} <- [
          {"sandbox_toolchain_unqualified", "Project toolchain refused after wrapper setup"},
          {"sandbox_wrapper_failed", "Project wrapper/bootstrap failed before protected launch"},
          {"sandbox_launch_receipt_invalid", "Project launch receipt was invalid"}
        ] do
      File.write!(ctx.log, "")
      assert {output, 1} = launch_args(ctx, ["runner", "doctor"], [{"FIXTURE_SANDBOX_FAIL", "true"}, {"FIXTURE_SANDBOX_CODE", code}])
      assert output =~ code
      assert output =~ stage
      assert output =~ "retry"
      refute String.downcase(output) =~ "rebuild"
      refute File.exists?(ctx.state)
      assert File.read!(credentials) == original_credentials
      assert File.read!(workflow) == original_workflow
      calls = File.read!(ctx.log)
      assert calls =~ "--check-codex-sandbox"

      for operation <- ["login\tstatus", "--detach", "build\t", "git-preflight", "auth.mjs", "volume\trm"] do
        refute calls =~ operation
      end
    end
  end

  test "candidate sandbox refusal preserves usable runner and user files before drain", ctx do
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"])
    metadata = Path.join(ctx.config, "runners/#{@project_id}.env")
    credentials = Path.join(ctx.config, "projects/#{@project_id}.env")
    before = {File.read!(ctx.state), File.read!(metadata), File.read!(credentials)}
    canary = Path.join(ctx.repo, "untracked-work")
    File.write!(canary, "preserve user work")
    File.write!(ctx.log, "")
    assert {output, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"], [{"FIXTURE_SANDBOX_FAIL", "true"}])
    assert output =~ "sandbox_namespace_denied"
    assert before == {File.read!(ctx.state), File.read!(metadata), File.read!(credentials)}
    assert File.read!(canary) == "preserve user work"
    refute File.exists?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
    calls = File.read!(ctx.log)
    refute calls =~ "stop\t"
    refute calls =~ "rename\t"
    refute calls =~ "--detach"
  end

  for packaged <- [false, true] do
    @packaged packaged
    test "pending journal retry preflights exact settings before draining source packaged=#{packaged}", ctx do
      assert {_, 0} = launch_args(ctx, ["run"])
      journal = Path.join(ctx.config, "runners/#{@project_id}.transition")
      File.mkdir!(journal)
      File.chmod!(journal, 0o700)

      record =
        Enum.join(
          ["codex-config-v2", "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", File.read!(ctx.state <> ".config-sha"), File.read!(ctx.state <> ".config-owner")],
          "\t"
        ) <> "\n"

      fields = %{"target" => "codex", "port" => "4010", "image" => record, "package.revision" => "fixture-revision"}
      Enum.each(fields, fn {name, value} -> File.write!(Path.join(journal, name), value) end)
      File.chmod!(Path.join(journal, "image"), 0o600)
      metadata = File.read!(Path.join(ctx.config, "runners/#{@project_id}.env"))
      File.write!(Path.join(journal, "previous.env"), metadata)
      File.write!(ctx.log, "")
      # Exercise the actual packaged retry control flow with prepared immutable
      # image metadata. Native manifest/host-helper validation has separate tests.
      script = """
      source "$1" --help >/dev/null
      runner_project_lock "$2"
      packaged_install=#{@packaged}
      project_id() { printf '%s' "$FIXTURE_PROJECT_ID"; }
      legacy_project_id() { printf service; }
      source_revision() { printf fixture-revision; }
      package_image() { printf sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; }
      package_transition_image() { printf sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; }
      package_release_image() { return 0; }
      package_transition_image_compatible() { return 0; }
      package_helper() {
        case "$1" in
          lock-directory) return 0 ;;
          sha256) shasum -a 256 "$2" | cut -d ' ' -f 1 ;;
          codex-config-store) shift; python3 -I "$symphony_home/scripts/codex-config-store.py" "$@" ;;
          *) return 1 ;;
        esac
      }
      runner_transition "$2" codex false
      """

      env = Map.merge(Map.new(ctx.env), %{"FIXTURE_PROJECT_ID" => @project_id, "FIXTURE_SANDBOX_FAIL" => "true"}) |> Enum.to_list()
      assert {output, 1} = System.cmd("bash", ["-euc", script, "retry", @launcher, ctx.repo], env: env, cd: ctx.repo, stderr_to_stdout: true)
      assert output =~ "sandbox_namespace_denied"
      assert File.read!(ctx.state) |> String.trim() == "true"
      assert File.read!(Path.join(journal, "previous.env")) == metadata
      Enum.each(fields, fn {name, value} -> assert File.read!(Path.join(journal, name)) == value end)
      calls = File.read!(ctx.log)
      refute calls =~ "stop\t"
      refute calls =~ "rename\t"
      refute calls =~ "--detach"
      refute calls =~ "--codex-config-snapshot"
    end
  end

  test "foreign native volumes and active profile removal fail before native mutation", ctx do
    assert {_, 1} = launch_args(ctx, ["auth", "claude", "login"], [{"FIXTURE_FOREIGN_VOLUME", "true"}])
    refute File.read!(ctx.log) =~ "auth.mjs\tlogin"

    for action <- ["login", "remove"] do
      File.write!(ctx.log, "")
      assert {message, 1} = launch_args(ctx, ["auth", "claude", action], [{"FIXTURE_PROFILE_IN_USE", "active-container"}])
      assert message =~ "profile is in use"
      calls = File.read!(ctx.log)
      refute calls =~ "auth.mjs\tlogin"
      refute calls =~ "auth.mjs\tlogout"
      refute calls =~ "volume\trm"
    end
  end

  defp project_id!(repo) do
    {identity, 0} = System.cmd("bash", ["-c", ~s(source "$1" --help >/dev/null; project_id "$2"), "identity", @launcher, repo])
    String.trim(identity)
  end

  defp await_file!(path, attempts \\ 500)
  defp await_file!(_path, 0), do: flunk("runner did not reach the mutation barrier")

  defp await_file!(path, attempts) do
    if File.exists?(path) do
      :ok
    else
      Process.sleep(10)
      await_file!(path, attempts - 1)
    end
  end

  defp launch(ctx, command), do: launch_args(ctx, ["runner", command])

  test "mount ownership reads the complete inspect output under pipefail", ctx do
    assert {_, 0} = launch_args(ctx, ["run"])
    File.write!(ctx.state <> ".agent", "<no value>")
    padding = Path.join(ctx.root, "additional-mounts")
    File.write!(padding, String.duplicate("unrelated-volume /unrelated-mount\n", 32_768))

    script = ~S|source "$1" --help >/dev/null; repo=$(git -C "$2" rev-parse --show-toplevel); runner_owned "$repo" "$SYMPHONY_CONTAINER_RUNTIME" "$(runner_id "$repo")"|
    env = [{"FIXTURE_MOUNT_PADDING_FILE", padding} | ctx.env]
    assert {_, 0} = System.cmd("bash", ["-euc", script, "ownership", @launcher, ctx.repo], env: env, stderr_to_stdout: true)
    assert File.read!(ctx.state) |> String.trim() == "true"
  end

  test "candidate tag changes after qualification cannot select a different image", ctx do
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"])
    changed = Path.join(ctx.root, "tag-changed")
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"], [{"FIXTURE_TAG_CHANGED_FILE", changed}])
    assert File.exists?(changed)
    assert File.read!(ctx.state <> ".image") == "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  end

  test "same-image update refuses a deferred old Codex profile before disrupting the listener", ctx do
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run"])
    File.write!(ctx.state <> ".codex-runtime", "old-profile")
    metadata = File.read!(Path.join(ctx.config, "runners/#{@project_id}.env"))
    image = File.read!(ctx.state <> ".image")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "codex"])
    assert message =~ "fresh packaged install"
    calls = File.read!(ctx.log)

    for mutation <- ["rename\t", "stop\t", "rm\t", "--detach", "agent-draining", "build\t"] do
      refute calls =~ mutation
    end

    refute File.exists?(Path.join(ctx.config, "runners/#{@project_id}.transition"))
    assert String.trim(File.read!(ctx.state)) == "true"
    assert File.read!(ctx.state <> ".codex-runtime") == "old-profile"
    assert File.read!(ctx.state <> ".image") == image
    assert File.read!(Path.join(ctx.config, "runners/#{@project_id}.env")) == metadata
  end

  test "same-image reuse refuses a changed runtime profile without touching the runner", ctx do
    assert {_, 0} = launch_args(ctx, ["run"])
    File.write!(ctx.state <> ".codex-runtime", "old-profile")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["run"])
    assert message =~ "Codex runtime profile differs"
    calls = File.read!(ctx.log)
    refute calls =~ "stop\t"
    refute calls =~ "rm\t"
    refute calls =~ "--check-codex-sandbox"
  end

  test "Claude same-image profile transition recreates the runtime", ctx do
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude"])
    File.write!(ctx.state <> ".claude-runtime", "previous-profile")
    File.write!(ctx.log, "")
    assert {message, 0} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"])
    assert message =~ "Runner transition complete"
    refute message =~ "Previously completed"
    assert File.read!(ctx.log) =~ "rename\t"
    assert File.read!(ctx.log) =~ "--detach"
    assert File.read!(ctx.state <> ".claude-runtime") == "cpu4-pid512-build1g-v1"
    assert File.read!(ctx.state <> ".backup.claude-runtime") == "previous-profile"
  end

  test "Claude same-image reuse refuses an unknown resource profile without changing retained work", ctx do
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude"])
    File.write!(ctx.state <> ".claude-runtime", "unknown-profile")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["run", "--agent", "claude"])
    assert message =~ "Claude runtime profile differs"
    calls = File.read!(ctx.log)
    refute calls =~ "stop\t"
    refute calls =~ "rm\t"
    refute calls =~ "--detach"
    assert File.read!(ctx.state <> ".claude-runtime") == "unknown-profile"
  end

  test "stopped old Claude profile requires updater and failed replacement can roll back", ctx do
    ready_curl!(ctx)
    assert {_, 0} = launch_args(ctx, ["run", "--agent", "claude"])
    File.write!(ctx.state <> ".claude-runtime", "previous-profile")
    File.write!(ctx.state, "false")
    File.write!(ctx.log, "")
    assert {message, 1} = launch_args(ctx, ["run", "--agent", "claude"])
    assert message =~ "Claude runtime profile differs"
    refute File.read!(ctx.log) =~ "rm\t"
    assert File.read!(ctx.state <> ".claude-runtime") == "previous-profile"
    fail = Path.join(ctx.root, "failed-profile-create")
    File.write!(fail, "")
    assert {_, 1} = launch_args(ctx, ["update", "--development-runner", "--agent", "claude"], [{"FIXTURE_FAIL_CREATE_FILE", fail}])
    assert File.read!(ctx.state <> ".backup.claude-runtime") == "previous-profile"
    assert {_, 0} = launch_args(ctx, ["update", "--development-runner", "--rollback-runner"])
    assert File.read!(ctx.state <> ".claude-runtime") == "previous-profile"
  end

  defp launch_args(ctx, args, extra_env \\ []) do
    env = Map.merge(Map.new(ctx.env), Map.new(extra_env)) |> Enum.to_list()
    System.cmd("bash", [@launcher | args], cd: ctx.repo, env: env, stderr_to_stdout: true)
  end
end
