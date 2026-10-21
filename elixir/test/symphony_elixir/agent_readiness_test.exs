defmodule SymphonyElixir.AgentReadinessTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixir.{AgentPause, AgentReadiness}
  alias SymphonyElixir.Tracker.Memory

  setup do
    SymphonyElixir.TestSupport.isolate_inference_environment()
    root = Path.join(System.tmp_dir!(), "symphony-readiness-#{System.unique_integer([:positive])}")
    File.mkdir_p!(root)
    keys = [:coding_agent, :agent_state_root]
    previous = Map.new(keys, &{&1, Application.fetch_env(:symphony_elixir, &1)})
    Application.put_env(:symphony_elixir, :coding_agent, :claude)
    Application.put_env(:symphony_elixir, :agent_state_root, Path.join(root, "state"))
    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", poll_interval_ms: 60_000)
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [])
    node = Path.join(root, "native-check")
    helper = Path.join(root, "output.json")
    File.write!(node, "#!/bin/sh\ncat \"$1\"\n")
    File.chmod!(node, 0o755)

    on_exit(fn ->
      Enum.each(previous, fn
        {key, {:ok, value}} -> Application.put_env(:symphony_elixir, key, value)
        {key, :error} -> Application.delete_env(:symphony_elixir, key)
      end)

      File.rm_rf!(root)
    end)

    %{root: root, helper: helper, node: node, opts: [node_path: node, helper_path: helper]}
  end

  test "model-free readiness validates protocol, bounded diagnostics, profile and attestation metadata", ctx do
    good = ready()

    for value <- [
          good,
          Map.put(good, "profile", "invalid"),
          Map.put(good, "attested_at", "invalid"),
          Map.put(good, "protocol", 99),
          Map.put(good, "sdk", String.duplicate("a", 129)),
          Map.put(good, "cli", nil),
          %{"code" => "auth_required"},
          %{"code" => "secret-canary"}
        ] do
      File.write!(ctx.helper, Jason.encode!(value))
      result = AgentReadiness.check(ctx.opts)
      assert result.ready == (value == good)
      refute inspect(result) =~ "secret-canary"
    end

    for data <- ["invalid", "[]", String.duplicate("secret-canary", 1000)] do
      File.write!(ctx.helper, data)
      assert %{ready: false, code: "runtime_unavailable"} = AgentReadiness.check(ctx.opts)
    end

    assert %{ready: false} = AgentReadiness.check(Keyword.put(ctx.opts, :helper_path, "/missing"))
    File.write!(ctx.helper, Jason.encode!(good))

    for timeout <- [0, -1, "unbounded"] do
      assert %{ready: false, code: "runtime_unavailable"} = AgentReadiness.check(Keyword.put(ctx.opts, :timeout_ms, timeout))
    end

    assert %{ready: false, code: "runtime_unavailable"} = AgentReadiness.check(Keyword.put(ctx.opts, :node_path, 123))
    File.chmod!(ctx.node, 0o644)
    assert %{ready: false} = AgentReadiness.check(ctx.opts)
    File.chmod!(ctx.node, 0o755)
    previous = System.get_env("ANTHROPIC_API_KEY")

    try do
      System.put_env("ANTHROPIC_API_KEY", "secret-canary")
      assert %{ready: true} = AgentReadiness.check(ctx.opts)
    after
      restore_env("ANTHROPIC_API_KEY", previous)
    end
  end

  test "tool environment validation is a bounded readiness observation without secret values", ctx do
    File.write!(ctx.helper, Jason.encode!(%{"code" => "invalid_tool_environment", "detail" => "secret-canary"}))
    assert %{ready: false, code: "invalid_tool_environment"} = result = AgentReadiness.check(ctx.opts)
    refute inspect(result) =~ "secret-canary"
    assert :ready = AgentPause.current()
  end

  test "compatible dependency upgrades remain ready under the existing protocol contract", ctx do
    upgraded = ready() |> Map.put("sdk", "0.999.0") |> Map.put("cli", "9.999.0")
    File.write!(ctx.helper, Jason.encode!(upgraded))
    assert %{ready: true, sdk: "0.999.0", cli: "9.999.0", protocol: 1} = AgentReadiness.check(ctx.opts)

    for bad <- [Map.delete(upgraded, "protocol"), Map.put(upgraded, "policy", 99), Map.put(upgraded, "sdk", ""), Map.put(upgraded, "cli", String.duplicate("a", 129))] do
      File.write!(ctx.helper, Jason.encode!(bad))
      assert %{ready: false, code: "runtime_unavailable"} = AgentReadiness.check(ctx.opts)
    end
  end

  test "Codex admission and resume ignore retained Claude pauses", ctx do
    Application.put_env(:symphony_elixir, :coding_agent, :codex)

    for kind <- [:auth_required, :quota_exhausted] do
      File.rm_rf!(Path.join(ctx.root, "state"))
      assert :ok = AgentPause.record(kind, %{profile: String.duplicate("a", 64), resets_at: System.system_time(:second) + 600})
      path = Path.join(ctx.root, "state/claude-pause.json")
      evidence = File.read!(path)
      {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe)
      assert %{ready: true, provider: "codex"} = Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.admission
      assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.pause == nil
      assert {:ok, _} = GenServer.call(pid, {:agent_control, :drain, false})
      assert {:ok, %{admission: %{ready: true}}} = GenServer.call(pid, {:agent_control, :recheck, false})
      assert File.read!(path) == evidence
      GenServer.stop(pid)
    end
  end

  test "safe persisted pause diagnostics remain visible after scheduler restart", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    assert :ok = AgentPause.record(:auth_required, %{profile: String.duplicate("a", 64), startup_code: "unqualified_auth", stage: "startup"})

    for _restart <- 1..2 do
      {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
      await_check(pid)

      assert %{"kind" => "auth_required", "code" => "unqualified_auth", "stage" => "startup"} =
               Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.pause

      assert {:error, "auth_required"} = GenServer.call(pid, {:agent_control, :resume, false})
      GenServer.stop(pid)
    end
  end

  test "resume refuses auth and policy pauses while quota cooldown is reported explicitly", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    await_check(pid)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)

    for kind <- [:auth_required, :policy_blocked] do
      File.rm_rf!(Path.join(ctx.root, "state"))
      assert :ok = AgentPause.record(kind, %{profile: String.duplicate("a", 64)})
      assert {:ok, _} = GenServer.call(pid, {:agent_control, :drain, false})
      assert {:error, reason} = GenServer.call(pid, {:agent_control, :resume, false})
      assert reason == Atom.to_string(kind)
      assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
      assert {:paused, _} = AgentPause.current()
    end

    File.rm_rf!(Path.join(ctx.root, "state"))
    reset = System.system_time(:second) + 600
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: String.duplicate("a", 64), resets_at: reset})
    assert {:ok, %{draining: false, admission: %{ready: false, reason: "quota_exhausted", retry_at: ^reset}}} = GenServer.call(pid, {:agent_control, :resume, false})
    assert %{ready: false, reason: "quota_exhausted"} = Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.admission
    assert {:paused, _} = AgentPause.current()
    assert {:error, :reset_not_due} = GenServer.call(pid, {:agent_control, :recheck, false})
  end

  test "recheck preserves the specific existing legacy-evidence refusal", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    tools = Path.join([ctx.root, "state", "claude-tools"])
    File.mkdir_p!(tools)
    File.write!(Path.join(tools, "invalid name"), "secret-canary")
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    await_check(pid)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    assert {:ok, _} = GenServer.call(pid, {:agent_control, :drain, false})
    assert {:error, :unknown_state_file} = GenServer.call(pid, {:agent_control, :recheck, true})
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    assert File.read!(Path.join(tools, "invalid name")) == "secret-canary"
  end

  test "drain survives orchestrator restart and account changes block explicit recovery", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    await_check(pid)
    assert {:ok, %{draining: true, running: 0}} = GenServer.call(pid, {:agent_control, :drain, false})
    guard = Path.join([ctx.root, "state", "agent-upgrade-unresumed"])
    File.write!(guard, "unresumed\n")
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    GenServer.stop(pid)
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    await_check(pid)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    File.write!(ctx.helper, Jason.encode!(ready(String.duplicate("b", 64))))
    assert {:error, "native_identity_mismatch"} = GenServer.call(pid, {:agent_control, :resume, false})
    assert File.exists?(guard)
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    File.write!(ctx.helper, Jason.encode!(ready()))
    assert :ok = AgentPause.record(:auth_required, %{profile: String.duplicate("a", 64)})
    assert {:ok, %{draining: false}} = GenServer.call(pid, {:agent_control, :recheck, false})
    refute File.exists?(guard)
    assert {:ok, %{draining: true}} = GenServer.call(pid, {:agent_control, :drain, false})
    refute File.exists?(guard)
    assert {:probe, _} = AgentPause.current()
    assert {:error, :invalid_action} = GenServer.call(pid, {:agent_control, :unknown, false})
    GenServer.stop(pid)
  end

  test "resume stays drained if the durable upgrade marker cannot be invalidated", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    guard = Path.join([ctx.root, "state", "agent-upgrade-unresumed"])
    File.mkdir_p!(guard)
    File.write!(Path.join([ctx.root, "state", "agent-draining"]), "draining\n")
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    await_check(pid)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    assert {:error, :state_write_failed} = GenServer.call(pid, {:agent_control, :resume, false})
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    File.rmdir!(guard)
    assert {:ok, %{draining: false}} = GenServer.call(pid, {:agent_control, :resume, false})
  end

  test "unready startup does not clean terminal workspaces or begin work", ctx do
    workspace_root = Path.join(ctx.root, "workspaces")
    marker = Path.join(workspace_root, "PROBE-1/retained")
    File.mkdir_p!(Path.dirname(marker))
    File.write!(marker, "untracked work")
    issue = %Issue{id: "one", identifier: "PROBE-1", title: "Retain", state: "Done", dispatchable: false}
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])
    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", workspace_root: workspace_root)
    File.write!(ctx.helper, Jason.encode!(%{code: "auth_required"}))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    await_check(pid)
    snapshot = Orchestrator.snapshot(__MODULE__.Probe, 5_000)
    assert snapshot.agent.readiness.ready == false
    assert snapshot.running == []
    assert File.read!(marker) == "untracked work"
    GenServer.stop(pid)
  end

  test "Claude defers its one startup cleanup until admission and does not repeat it on recovery", ctx do
    workspace_root = Path.join(ctx.root, "workspaces")
    issue = %Issue{id: "startup-cleanup", identifier: "CLEAN-1", title: "Finished", state: "Done", dispatchable: false}

    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "memory",
      workspace_root: workspace_root,
      poll_interval_ms: 60_000
    )

    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])
    assert {:ok, workspace} = Workspace.create_for_issue(issue)
    File.write!(Path.join(workspace, "retained"), "work")
    File.write!(ctx.helper, Jason.encode!(%{code: "auth_required"}))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    await_check(pid)
    assert :sys.get_state(pid).startup_cleanup_pending
    assert File.exists?(workspace)

    assert {:ok, _} = GenServer.call(pid, {:agent_control, :drain, false})
    File.write!(ctx.helper, Jason.encode!(ready()))
    {_timer, token} = :sys.get_state(pid).readiness_timer
    send(pid, {:refresh_agent_readiness, token})
    await_check(pid)
    assert :sys.get_state(pid).startup_cleanup_pending
    assert File.exists?(workspace)
    assert {:ok, _} = GenServer.call(pid, {:agent_control, :resume, false})
    await(fn -> not :sys.get_state(pid).startup_cleanup_pending end)
    refute File.exists?(workspace)

    assert {:ok, ^workspace} = Workspace.create_for_issue(issue)
    File.write!(Path.join(workspace, "retained"), "completed while unclaimed")

    for observation <- [%{code: "auth_required"}, ready()] do
      File.write!(ctx.helper, Jason.encode!(observation))
      {_timer, token} = :sys.get_state(pid).readiness_timer
      send(pid, {:refresh_agent_readiness, token})
      await_check(pid)
    end

    send(pid, :run_poll_cycle)
    assert is_map(GenServer.call(pid, :snapshot))
    refute :sys.get_state(pid).startup_cleanup_pending
    assert File.read!(Path.join(workspace, "retained")) == "completed while unclaimed"
  end

  test "Codex startup and restart keep legacy tracker evidence local to its issue", ctx do
    Application.put_env(:symphony_elixir, :coding_agent, :codex)
    profile = AgentPause.codex_profile()
    assert %{ready: true, provider: "codex", profile: ^profile} = AgentReadiness.check()
    tools = Path.join(ctx.root, "state/claude-tools")
    File.mkdir_p!(tools)
    marker = Path.join(tools, "codex-write.json")
    File.write!(marker, Jason.encode!(%{issue: "one", tool: "linear_graphql"}))
    File.write!(Path.join(ctx.root, "state/claude-pause.json"), Jason.encode!(%{version: 1, kind: "tool_outcome_uncertain", phase: "waiting", profile: profile, resets_at: nil}))
    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", workspace_root: Path.join(ctx.root, "workspaces"))
    issue = %Issue{id: "one", identifier: "PROBE-1", title: "Recover", state: "In Progress"}
    other = %{issue | id: "two", identifier: "PROBE-2"}

    for _restart <- 1..2 do
      {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe)

      try do
        snapshot = Orchestrator.snapshot(__MODULE__.Probe, 5_000)
        assert snapshot.agent.pause == nil
        assert :ready = AgentPause.current()
        assert PromptBuilder.build_prompt(issue) =~ "Legacy recovery evidence: 1"
        refute PromptBuilder.build_prompt(other) =~ "Legacy recovery evidence:"
        assert PromptBuilder.build_prompt(other) =~ "## Tracker recovery"
        assert File.regular?(marker)
      after
        GenServer.stop(pid)
      end
    end
  end

  test "repaired observations recover on the scheduled refresh without durable pause or manual recheck", ctx do
    File.write!(ctx.helper, Jason.encode!(%{code: "auth_required"}))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    await_check(pid)
    assert %{ready: false, code: "auth_required"} = :sys.get_state(pid).readiness
    assert :ready = AgentPause.current()
    refute File.exists?(Path.join(ctx.root, "state/claude-pause.json"))
    assert {:ok, _} = GenServer.call(pid, {:agent_control, :drain, false})
    File.write!(ctx.helper, Jason.encode!(ready()))
    {timer, token} = :sys.get_state(pid).readiness_timer
    assert Process.read_timer(timer) in 1..30_000
    send(pid, {:refresh_agent_readiness, token})
    await_check(pid)
    assert :sys.get_state(pid).readiness.ready
    assert :sys.get_state(pid).expected_profile == String.duplicate("a", 64)
    assert :sys.get_state(pid).draining
    assert %{ready: false, reason: "draining"} = Orchestrator.snapshot(__MODULE__.Probe, 200).agent.admission
    assert :ready = AgentPause.current()
  end

  test "healthy repeated checks preserve ambiguous legacy policy pauses", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    assert :ok = AgentPause.record(:policy_blocked)
    path = Path.join(ctx.root, "state/claude-pause.json")
    evidence = File.read!(path)
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    await_check(pid)
    assert :sys.get_state(pid).readiness.ready
    assert %{ready: false, reason: "policy_blocked"} = Orchestrator.snapshot(__MODULE__.Probe, 200).agent.admission
    assert %{queued: true} = GenServer.call(pid, :request_refresh)
    await_check(pid)
    assert File.read!(path) == evidence
  end

  test "slow checks leave startup, snapshots, drain and worker exits responsive", ctx do
    pid_file = Path.join(ctx.root, "native-pid")
    File.write!(ctx.helper, "{}")
    File.write!(ctx.node, "#!/bin/sh\necho $$ > '#{pid_file}'\nexec sleep 10\n")
    started = System.monotonic_time(:millisecond)
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: Keyword.put(ctx.opts, :timeout_ms, 500))
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    assert System.monotonic_time(:millisecond) - started < 250
    await(fn -> File.exists?(pid_file) end)
    assert %{ready: false, code: "checking"} = Orchestrator.snapshot(__MODULE__.Probe, 200).agent.readiness
    task = :sys.get_state(pid).readiness_task
    assert %{queued: true} = GenServer.call(pid, :request_refresh, 200)
    assert :sys.get_state(pid).readiness_task == task
    assert {:ok, _} = GenServer.call(pid, {:agent_control, :drain, false}, 200)

    issue = %Issue{id: "exit-during-readiness", identifier: "PROBE-EXIT", title: "Exit", state: "In Progress"}

    {:ok, worker} =
      Task.Supervisor.start_child(SymphonyElixir.TaskSupervisor, fn ->
        receive do
          :finish -> :ok
        end
      end)

    on_exit(fn -> send(worker, :finish) end)

    :sys.replace_state(pid, fn state ->
      entry = %{
        pid: worker,
        ref: Process.monitor(worker),
        identifier: issue.identifier,
        issue: issue,
        started_at: DateTime.utc_now(),
        session_id: nil,
        codex_app_server_pid: nil,
        codex_input_tokens: 0,
        codex_output_tokens: 0,
        codex_total_tokens: 0,
        last_codex_timestamp: nil,
        last_codex_message: nil,
        last_codex_event: nil
      }

      %{state | running: %{issue.id => entry}, claimed: MapSet.new([issue.id])}
    end)

    send(worker, :finish)
    await(fn -> map_size(:sys.get_state(pid).running) == 0 end)
    assert Map.has_key?(:sys.get_state(pid).retry_attempts, issue.id)
    assert :sys.get_state(pid).readiness_task == task
    await_check(pid)
    refute :sys.get_state(pid).readiness.ready
    assert :ready = AgentPause.current()
    assert_native_gone(pid_file)
  end

  test "failed observations block new admission while an established worker remains owned", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    await_check(pid)
    issue = %Issue{id: "existing-session", identifier: "PROBE-ACTIVE", title: "Existing", state: "In Progress", dispatchable: true}

    {:ok, worker} =
      Task.Supervisor.start_child(SymphonyElixir.TaskSupervisor, fn ->
        receive do
          :finish -> :ok
        end
      end)

    on_exit(fn -> send(worker, :finish) end)

    :sys.replace_state(pid, fn state ->
      entry = %{
        pid: worker,
        ref: Process.monitor(worker),
        identifier: issue.identifier,
        issue: issue,
        started_at: DateTime.utc_now(),
        session_id: nil,
        codex_app_server_pid: nil,
        codex_input_tokens: 0,
        codex_output_tokens: 0,
        codex_total_tokens: 0,
        last_codex_timestamp: nil,
        last_codex_message: nil,
        last_codex_event: nil
      }

      %{state | running: %{issue.id => entry}, claimed: MapSet.new([issue.id])}
    end)

    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])
    File.write!(ctx.helper, Jason.encode!(%{code: "overage_attestation_expired"}))
    assert %{queued: true} = GenServer.call(pid, :request_refresh)
    await_check(pid)
    assert %{ready: false, reason: "overage_attestation_expired"} = Orchestrator.snapshot(__MODULE__.Probe, 200).agent.admission
    assert Process.alive?(worker)
    assert :sys.get_state(pid).running[issue.id].pid == worker
    assert :ready = AgentPause.current()
    send(worker, :finish)
    await(fn -> map_size(:sys.get_state(pid).running) == 0 end)
  end

  test "drain cancels a pending resume and late results cannot undrain or change the pinned profile", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    await_check(pid)
    release = Path.join(ctx.root, "release")
    started = Path.join(ctx.root, "started")
    File.write!(ctx.helper, Jason.encode!(ready(String.duplicate("b", 64))))
    File.write!(ctx.node, "#!/bin/sh\ntouch '#{started}'\nwhile [ ! -f '#{release}' ]; do sleep 0.01; done\ncat \"$1\"\n")
    caller = Task.async(fn -> GenServer.call(pid, {:agent_control, :resume, false}, 2_000) end)
    await(fn -> File.exists?(started) end)
    assert {:error, :readiness_check_in_progress} = GenServer.call(pid, {:agent_control, :resume, false}, 200)
    assert {:ok, _} = GenServer.call(pid, {:agent_control, :drain, false}, 200)
    assert {:error, :agent_drained} = Task.await(caller)
    File.write!(release, "ready")
    await_check(pid)
    assert :sys.get_state(pid).draining
    assert :sys.get_state(pid).expected_profile == String.duplicate("a", 64)
    assert %{ready: false, code: "native_identity_mismatch"} = :sys.get_state(pid).readiness
  end

  test "task crashes and scheduler restart leave only deadline-bounded native checks", ctx do
    pid_file = Path.join(ctx.root, "native-pids")
    File.write!(ctx.helper, "{}")
    File.write!(ctx.node, "#!/bin/sh\necho $$ >> '#{pid_file}'\nexec sleep 10\n")
    opts = [name: __MODULE__.Probe, readiness_opts: Keyword.put(ctx.opts, :timeout_ms, 200)]
    {:ok, pid} = Orchestrator.start_link(opts)
    await(fn -> File.exists?(pid_file) end)
    task = :sys.get_state(pid).readiness_task
    Process.exit(task.pid, :kill)
    await_check(pid)
    assert %{ready: false, code: "runtime_unavailable"} = :sys.get_state(pid).readiness
    assert :ready = AgentPause.current()
    GenServer.stop(pid)
    {:ok, pid} = Orchestrator.start_link(opts)
    await(fn -> length(File.read!(pid_file) |> String.split("\n", trim: true)) == 2 end)
    task = :sys.get_state(pid).readiness_task
    GenServer.stop(pid)
    refute Process.alive?(task.pid)
    assert_native_gone(pid_file)
  end

  test "unchanged healthy observations preserve the configured long poll timer without tracker calls", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    await_check(pid)
    await_poll_idle(pid)
    trace_polls(pid)
    previous = :sys.get_state(pid)
    assert previous.poll_interval_ms == 60_000
    assert Process.read_timer(previous.tick_timer_ref) > 30_000

    for _ <- 1..3 do
      # Metadata renewal does not change effective admission.
      File.write!(ctx.helper, Jason.encode!(ready()))
      {_timer, token} = :sys.get_state(pid).readiness_timer
      send(pid, {:refresh_agent_readiness, token})
      await_check(pid)
      state = :sys.get_state(pid)
      assert state.tick_timer_ref == previous.tick_timer_ref
      assert state.tick_token == previous.tick_token
      assert state.next_poll_due_at_ms == previous.next_poll_due_at_ms
    end

    assert_no_polls(pid)
  end

  test "repaired observation promptly polls and dispatches before the long tracker interval", ctx do
    dispatched = Path.join(ctx.root, "dispatched")

    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "memory",
      poll_interval_ms: 60_000,
      workspace_root: Path.join(ctx.root, "workspaces"),
      hook_before_run: "touch '#{dispatched}'; exit 1"
    )

    issue = %Issue{id: "recovered-dispatch", identifier: "PROBE-RECOVER", title: "Recover", state: "In Progress", dispatchable: true}
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])
    File.write!(ctx.helper, Jason.encode!(%{code: "auth_required"}))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    await_check(pid)
    await_poll_idle(pid)
    trace_polls(pid)
    refute File.exists?(dispatched)
    File.write!(ctx.helper, Jason.encode!(ready()))
    {_timer, token} = :sys.get_state(pid).readiness_timer
    send(pid, {:refresh_agent_readiness, token})
    await_check(pid)
    assert_receive {:trace, ^pid, :call, {SymphonyElixir.Tracker.Memory, :fetch_issues_by_states, [_states]}}, 1_000
    await(fn -> File.exists?(dispatched) end)
    assert :sys.get_state(pid).readiness.ready
    assert :ready = AgentPause.current()
  end

  test "readiness recovery coalesces with a poll cycle already in progress", ctx do
    File.write!(ctx.helper, Jason.encode!(%{code: "auth_required"}))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    await_check(pid)
    await_poll_idle(pid)
    trace_polls(pid)

    :sys.replace_state(pid, fn state ->
      Process.cancel_timer(state.tick_timer_ref)
      %{state | poll_check_in_progress: true, tick_timer_ref: nil, tick_token: nil, next_poll_due_at_ms: nil}
    end)

    File.write!(ctx.helper, Jason.encode!(ready()))
    {_timer, token} = :sys.get_state(pid).readiness_timer
    send(pid, {:refresh_agent_readiness, token})
    await_check(pid)
    state = :sys.get_state(pid)
    assert state.poll_check_in_progress
    assert state.tick_timer_ref == nil
    assert_no_polls(pid)
    # Complete the existing cycle with the repaired observation; no second cycle is queued.
    send(pid, :run_poll_cycle)
    # First admitted cycle performs its one startup sweep, then the ordinary read.
    assert_receive {:trace, ^pid, :call, {Memory, :fetch_issues_by_states, [terminal_states]}}, 1_000
    assert terminal_states == Config.settings!().tracker.terminal_states
    assert_receive {:trace, ^pid, :call, {Memory, :fetch_issues_by_states, [observed_states]}}, 1_000
    assert observed_states == Config.settings!().tracker.active_states
    await_poll_idle(pid)
    assert_no_polls(pid)
    assert Process.read_timer(:sys.get_state(pid).tick_timer_ref) > 30_000
  end

  defp await_poll_idle(pid) do
    await(fn ->
      state = :sys.get_state(pid)

      not state.poll_check_in_progress and is_integer(state.next_poll_due_at_ms) and
        state.next_poll_due_at_ms > System.monotonic_time(:millisecond) + 30_000
    end)
  end

  defp trace_polls(pid) do
    Code.ensure_loaded!(SymphonyElixir.Tracker.Memory)
    :erlang.trace_pattern({SymphonyElixir.Tracker.Memory, :fetch_issues_by_states, 1}, true, [:local])
    :erlang.trace(pid, true, [:call, {:tracer, self()}])

    on_exit(fn ->
      :erlang.trace_pattern({SymphonyElixir.Tracker.Memory, :fetch_issues_by_states, 1}, false, [:local])
    end)
  end

  defp assert_no_polls(pid) do
    ref = :erlang.trace_delivered(pid)
    assert_receive {:trace_delivered, ^pid, ^ref}, 1_000
    refute_receive {:trace, ^pid, :call, {SymphonyElixir.Tracker.Memory, :fetch_issues_by_states, _states}}, 0
  end

  defp await_check(pid) do
    await(fn ->
      state = :sys.get_state(pid)
      is_nil(state.readiness_task) and state.readiness[:code] != "checking"
    end)
  end

  defp assert_native_gone(path) do
    if File.dir?("/proc/self") do
      pids = File.read!(path) |> String.split("\n", trim: true)
      await(fn -> Enum.all?(pids, &(not native_running?(&1))) end)
    end
  end

  defp native_running?(pid) do
    case File.read("/proc/#{pid}/stat") do
      {:ok, data} -> (data |> String.split(") ") |> List.last() |> String.first()) not in ["Z", "X"]
      _ -> false
    end
  end

  defp await(check, attempts \\ 300)
  defp await(_check, 0), do: flunk("readiness state did not converge")

  defp await(check, attempts) do
    if check.() do
      :ok
    else
      Process.sleep(5)
      await(check, attempts - 1)
    end
  end

  defp ready(profile \\ String.duplicate("a", 64)) do
    %{
      "ready" => true,
      "protocol" => 1,
      "profile" => profile,
      "sdk" => "0.3.303",
      "cli" => "2.1.303",
      "policy" => 1,
      "overages_disabled_attested" => true,
      "attested_at" => DateTime.to_iso8601(DateTime.utc_now())
    }
  end
end
