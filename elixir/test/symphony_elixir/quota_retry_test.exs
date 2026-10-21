defmodule SymphonyElixir.QuotaRetryTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixir.AgentPause

  setup do
    root = Path.dirname(Workflow.workflow_file_path())
    peer = Path.join(root, "waiting-peer.mjs")
    turns = Path.join(root, "turns")

    File.write!(peer, """
    import { createInterface } from 'node:readline';
    import { appendFileSync } from 'node:fs';
    const reply = (id, result) => console.log(JSON.stringify({ id, result }));
    for await (const line of createInterface({ input: process.stdin })) {
      const message = JSON.parse(line);
      if (message.method === 'initialize') reply(message.id, {});
      if (message.method === 'thread/start') reply(message.id, { thread: { id: 'quota-recovery' } });
      if (message.method === 'turn/start') {
        appendFileSync(#{Jason.encode!(turns)}, JSON.stringify(message.params) + '\\n');
        reply(message.id, { turn: { id: 'waiting' } });
        // Stay in the turn until the real scheduler cancels the ineligible issue.
      }
    }
    """)

    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "memory",
      tracker_active_states: ["In Progress", "Merging"],
      poll_interval_ms: 60_000,
      max_retry_backoff_ms: 100,
      workspace_root: Path.join(root, "workspaces"),
      hook_after_create: "echo preserved > preserved",
      codex_command: "#{System.find_executable("node")} #{peer}",
      prompt: "Current state: {{ issue.state }}"
    )

    Application.put_env(:symphony_elixir, :memory_tracker_issues, [])
    server = start_supervised!({Orchestrator, name: __MODULE__})
    await_initial_poll(server)

    issue = %Issue{id: "quota-recovery", identifier: "JAY-625", title: "Quota recovery", state: "In Progress", dispatchable: true}
    %{server: server, issue: issue, root: root, turns: turns}
  end

  test "quota retry -> Human Review cancellation -> Merging picks up automatically", ctx do
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [ctx.issue])
    quota_exit(ctx.server, ctx.issue, %{})
    state = :sys.get_state(ctx.server)
    assert %{attempt: 1} = state.retry_attempts[ctx.issue.id]
    assert :ready = AgentPause.current()
    await(fn -> turn_count(ctx.turns) == 1 end)
    first = :sys.get_state(ctx.server).running[ctx.issue.id]
    assert is_pid(first.pid)

    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Human Review"}])
    poll(ctx.server)
    refute Process.alive?(first.pid)
    state = :sys.get_state(ctx.server)
    refute Map.has_key?(state.running, ctx.issue.id)
    refute MapSet.member?(state.claimed, ctx.issue.id)
    assert :ready = AgentPause.current()
    assert turn_count(ctx.turns) == 1

    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Merging"}])
    poll(ctx.server)
    await(fn -> turn_count(ctx.turns) == 2 end)
    state = :sys.get_state(ctx.server)
    assert map_size(state.running) == 1
    assert state.running[ctx.issue.id].issue.state == "Merging"
    assert File.read!(Path.join([ctx.root, "workspaces", "JAY-625", "preserved"])) == "preserved\n"
    assert File.read!(ctx.turns) =~ "Current state: Merging"
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Human Review"}])
    poll(ctx.server)
  end

  test "known Claude reset delays the existing retry timer, then releases it without recheck", ctx do
    ctx = claude_scheduler(ctx)
    reset = System.system_time(:second) + 2
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [ctx.issue])
    quota_exit(ctx.server, ctx.issue, %{resets_at: reset})
    retry = :sys.get_state(ctx.server).retry_attempts[ctx.issue.id]
    assert retry.due_at_ms - System.monotonic_time(:millisecond) > 500
    poll(ctx.server)
    refute File.exists?(ctx.turns)
    assert {:paused, %{"kind" => "quota_exhausted"}} = AgentPause.current()
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Human Review"}])
    await(fn -> not Map.has_key?(:sys.get_state(ctx.server).retry_attempts, ctx.issue.id) end, 3_000)
    assert :ready = AgentPause.current()
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Human Review"}])
    poll(ctx.server)
  end

  test "a shared Claude cooldown extension retains queued retries until reset", ctx do
    ctx = claude_scheduler(ctx)
    reset = System.system_time(:second) + 1
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [ctx.issue])
    quota_exit(ctx.server, ctx.issue, %{resets_at: reset})
    initial = :sys.get_state(ctx.server).retry_attempts[ctx.issue.id]
    extended = reset + 1
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: AgentPause.codex_profile(), resets_at: extended})
    await(fn -> :sys.get_state(ctx.server).retry_attempts[ctx.issue.id].due_at_ms > initial.due_at_ms end, 2_000)
    assert :sys.get_state(ctx.server).retry_attempts[ctx.issue.id].attempt == 1
    refute File.exists?(ctx.turns)
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Human Review"}])
    await(fn -> not Map.has_key?(:sys.get_state(ctx.server).retry_attempts, ctx.issue.id) end, 2_000)
    assert :ready = AgentPause.current()
    refute File.exists?(ctx.turns)
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Human Review"}])
    poll(ctx.server)
  end

  test "legacy consumed quota probe allows fresh eligible work after startup", ctx do
    path = Path.join(Config.agent_state_root(), "claude-pause.json")
    File.mkdir_p!(Path.dirname(path))
    File.write!(path, Jason.encode!(%{version: 1, kind: "probe_inflight", phase: "waiting", profile: AgentPause.codex_profile(), resets_at: nil}))
    stop_supervised!(Orchestrator)
    server = start_supervised!({Orchestrator, name: __MODULE__})
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Human Review"}])
    poll(server)
    refute File.exists?(ctx.turns)
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Merging"}])
    poll(server)
    await(fn -> turn_count(ctx.turns) == 1 end)
    assert :ready = AgentPause.current()
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Done"}])
    poll(server)
  end

  test "retained Claude cooldown does not delay a Codex failure retry", ctx do
    reset = System.system_time(:second) + 600
    path = Path.join(Config.agent_state_root(), "claude-pause.json")
    evidence = Jason.encode!(%{version: 1, kind: "quota_exhausted", profile: String.duplicate("a", 64), phase: "waiting", resets_at: reset})
    File.mkdir_p!(Path.dirname(path))
    File.write!(path, evidence)
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [ctx.issue])
    quota_exit(ctx.server, ctx.issue, %{}, :fixture_transient_failure)
    retry = :sys.get_state(ctx.server).retry_attempts[ctx.issue.id]
    assert retry.due_at_ms - System.monotonic_time(:millisecond) <= 100
    await(fn -> turn_count(ctx.turns) == 1 end)
    assert File.read!(path) == evidence
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Human Review"}])
    poll(ctx.server)
  end

  for kind <- [:auth_required, :policy_blocked] do
    @kind kind
    test "#{kind} uses attempt backoff with failed storage, repeats, and recovers after restart", ctx do
      ctx = claude_scheduler(ctx)
      slow_retries!()
      state_root = Config.agent_state_root()
      File.mkdir_p!(state_root)
      File.chmod!(state_root, 0o500)
      on_exit(fn -> File.chmod(state_root, 0o700) end)
      reason = {:agent_pause, @kind, %{profile: AgentPause.codex_profile()}}
      quota_exit(ctx.server, ctx.issue, %{}, reason)
      retry = :sys.get_state(ctx.server).retry_attempts[ctx.issue.id]
      assert retry.attempt == 1
      assert retry.error =~ Atom.to_string(@kind)
      refute retry.error =~ "state_write_error"
      assert retry.due_at_ms - System.monotonic_time(:millisecond) > 9_000
      assert :ready = AgentPause.current()
      refute File.exists?(Path.join(state_root, "claude-pause.json"))
      assert GenServer.call(ctx.server, :snapshot).agent.admission.ready
      assert MapSet.member?(:sys.get_state(ctx.server).claimed, ctx.issue.id)

      # A not-ready native observation must not release the retry claim or lose
      # the attempt history. No worker is executed by this timer event.
      :sys.replace_state(ctx.server, &%{&1 | readiness: %{ready: false, provider: "claude", code: "auth_required"}})
      send(ctx.server, {:retry_issue, ctx.issue.id, retry.retry_token})
      state = :sys.get_state(ctx.server)
      assert state.retry_attempts[ctx.issue.id].attempt == 1
      assert MapSet.member?(state.claimed, ctx.issue.id)
      assert state.running == %{}
      assert state.retry_attempts[ctx.issue.id].error == retry.error

      # The next actual worker failure retains the running attempt number.
      Process.cancel_timer(state.retry_attempts[ctx.issue.id].timer_ref)
      :sys.replace_state(ctx.server, &%{&1 | retry_attempts: %{}})
      quota_exit(ctx.server, ctx.issue, %{}, reason, 1)
      retry = :sys.get_state(ctx.server).retry_attempts[ctx.issue.id]
      assert retry.attempt == 2
      assert retry.due_at_ms - System.monotonic_time(:millisecond) > 19_000
      assert retry.error =~ Atom.to_string(@kind)
      File.chmod!(state_root, 0o700)
      stop_supervised!(Orchestrator)
      server = restart_claude(ctx)
      assert GenServer.call(server, :snapshot).agent.admission.ready
      assert :sys.get_state(server).retry_attempts == %{}
      refute File.exists?(Path.join(state_root, "claude-pause.json"))
      assert :ready = AgentPause.current()
    end
  end

  test "failed quota persistence keeps the reset across concurrent failures, early timers, drain and tracker changes", ctx do
    import ExUnit.CaptureLog
    ctx = claude_scheduler(ctx)
    state_root = Config.agent_state_root()
    File.mkdir_p!(state_root)
    File.chmod!(state_root, 0o500)
    on_exit(fn -> File.chmod(state_root, 0o700) end)
    reset = System.system_time(:second) + 5
    log = capture_log(fn -> quota_exit(ctx.server, ctx.issue, %{resets_at: reset}) end)
    assert log =~ "Could not persist native quota cooldown"
    assert log =~ "repair state storage"
    retry = :sys.get_state(ctx.server).retry_attempts[ctx.issue.id]
    assert retry.error =~ "quota_exhausted"
    assert retry.error =~ "state_write_error: :eacces"
    assert retry.quota_reset_at == reset
    assert retry.due_at_ms - System.monotonic_time(:millisecond) > 1_000
    refute File.exists?(Path.join(state_root, "claude-pause.json"))
    assert GenServer.call(ctx.server, :snapshot).agent.admission == %{ready: false, provider: "claude", profile: AgentPause.codex_profile(), reason: "quota_exhausted", retry_at: reset}

    other = %{ctx.issue | id: "other", identifier: "QUOTA-2"}
    quota_exit(ctx.server, other, %{})
    assert GenServer.call(ctx.server, :snapshot).agent.admission.retry_at == reset
    earlier = %{ctx.issue | id: "earlier", identifier: "QUOTA-3"}
    later = %{ctx.issue | id: "later", identifier: "QUOTA-4"}
    quota_exit(ctx.server, earlier, %{resets_at: reset - 1})
    quota_exit(ctx.server, later, %{resets_at: reset + 1})
    reset = reset + 1
    assert GenServer.call(ctx.server, :snapshot).agent.admission.retry_at == reset
    # An early timer cannot drop the only observed cooldown before admission.
    send(ctx.server, {:retry_issue, ctx.issue.id, retry.retry_token})
    state = :sys.get_state(ctx.server)
    assert state.running == %{}
    assert state.retry_attempts[ctx.issue.id].quota_reset_at == reset - 1
    assert GenServer.call(ctx.server, :snapshot).agent.admission.retry_at == reset

    assert {:error, :reset_not_due} = GenServer.call(ctx.server, {:agent_control, :recheck, false})
    assert GenServer.call(ctx.server, :snapshot).agent.admission.retry_at == reset

    # Real tracker movement and drain cannot cancel the bounded account deadline.
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Done"}, %{other | state: "Human Review"}, %{earlier | state: "Done"}, %{later | state: "Done"}])
    File.chmod!(state_root, 0o700)
    assert {:ok, _} = GenServer.call(ctx.server, {:agent_control, :drain, false})
    retry = :sys.get_state(ctx.server).retry_attempts[ctx.issue.id]
    send(ctx.server, {:retry_issue, ctx.issue.id, retry.retry_token})
    assert :sys.get_state(ctx.server).retry_attempts[ctx.issue.id].quota_reset_at == reset - 1
    assert {:ok, _} = GenServer.call(ctx.server, {:agent_control, :resume, false})
    assert GenServer.call(ctx.server, :snapshot).agent.admission.retry_at == reset
    await(fn -> :sys.get_state(ctx.server).retry_attempts == %{} end, 8_000)
    refute MapSet.member?(:sys.get_state(ctx.server).claimed, ctx.issue.id)
    assert :sys.get_state(ctx.server).running == %{}
    assert GenServer.call(ctx.server, :snapshot).agent.admission.ready
  end

  test "repaired quota storage persists the next reset and actual scheduler restart honors it", ctx do
    ctx = claude_scheduler(ctx)
    state_root = Config.agent_state_root()
    File.mkdir_p!(state_root)
    File.chmod!(state_root, 0o500)
    on_exit(fn -> File.chmod(state_root, 0o700) end)
    reset = System.system_time(:second) + 600
    quota_exit(ctx.server, ctx.issue, %{resets_at: reset})
    stop_supervised!(Orchestrator)
    # Unwritten state cannot survive restart. A healthy model-free check does
    # not attest available quota; the next native outcome establishes it again.
    server = restart_claude(ctx)
    assert GenServer.call(server, :snapshot).agent.admission.ready
    assert :sys.get_state(server).retry_attempts == %{}
    File.chmod!(state_root, 0o700)
    later = reset + 60
    quota_exit(server, ctx.issue, %{resets_at: later})
    refute :sys.get_state(server).retry_attempts[ctx.issue.id].error =~ "state_write_error"
    assert {:paused, %{"resets_at" => ^later}} = AgentPause.current()
    stop_supervised!(Orchestrator)
    server = restart_claude(ctx)
    assert GenServer.call(server, :snapshot).agent.admission.retry_at == later
    assert :sys.get_state(server).running == %{}
    assert :sys.get_state(server).retry_attempts == %{}
  end

  defp slow_retries! do
    path = Workflow.workflow_file_path()
    File.write!(path, String.replace(File.read!(path), "max_retry_backoff_ms: 100", "max_retry_backoff_ms: 60000"))
    SymphonyElixir.WorkflowStore.force_reload()
  end

  defp restart_claude(ctx) do
    node = Path.join(ctx.root, "native-check")
    helper = Path.join(ctx.root, "readiness.json")
    server = start_supervised!({Orchestrator, name: __MODULE__, readiness_opts: [node_path: node, helper_path: helper]})
    await_initial_poll(server)
    server
  end

  # Exercise Claude's scheduler cooldown using its existing readiness seam.
  # The issue becomes ineligible before reset; no native Claude process starts.
  defp claude_scheduler(ctx) do
    SymphonyElixir.TestSupport.isolate_inference_environment()
    previous = Application.fetch_env(:symphony_elixir, :coding_agent)
    Application.put_env(:symphony_elixir, :coding_agent, :claude)

    on_exit(fn ->
      case previous do
        {:ok, value} -> Application.put_env(:symphony_elixir, :coding_agent, value)
        :error -> Application.delete_env(:symphony_elixir, :coding_agent)
      end
    end)

    node = Path.join(ctx.root, "native-check")
    helper = Path.join(ctx.root, "readiness.json")
    File.write!(node, "#!/bin/sh\ncat \"$1\"\n")
    File.chmod!(node, 0o755)

    File.write!(
      helper,
      Jason.encode!(%{
        ready: true,
        profile: AgentPause.codex_profile(),
        protocol: 1,
        sdk: "fixture-sdk",
        cli: "fixture-cli",
        policy: 1
      })
    )

    stop_supervised!(Orchestrator)
    server = start_supervised!({Orchestrator, name: __MODULE__, readiness_opts: [node_path: node, helper_path: helper]})
    await_initial_poll(server)
    %{ctx | server: server}
  end

  defp quota_exit(server, issue, details, reason \\ :quota, attempt \\ nil) do
    {:ok, worker} =
      Task.Supervisor.start_child(SymphonyElixir.TaskSupervisor, fn ->
        receive do
          :quota ->
            details = Map.put(details, :profile, AgentPause.codex_profile())
            exit(if reason == :quota, do: {:agent_pause, :quota_exhausted, details}, else: reason)
        end
      end)

    :sys.replace_state(server, fn state ->
      # Never overwrite a live native worker when injecting the quota outcome.
      assert state.running == %{}
      # Observe an actual worker DOWN, just as the provider adapter reports quota.
      entry = %{
        pid: worker,
        ref: Process.monitor(worker),
        identifier: issue.identifier,
        issue: issue,
        retry_attempt: attempt,
        started_at: DateTime.utc_now()
      }

      %{state | running: Map.put(state.running, issue.id, entry), claimed: MapSet.put(state.claimed, issue.id)}
    end)

    send(worker, :quota)
    await(fn -> Map.has_key?(:sys.get_state(server).retry_attempts, issue.id) end)
  end

  defp await_initial_poll(server) do
    # Wait for readiness and the actual first cycle; a manual cycle can leave
    # init's delayed poll queued and overwrite the fixture's injected worker.
    await(fn ->
      state = :sys.get_state(server)

      state.readiness.ready and not state.poll_check_in_progress and
        state.next_poll_due_at_ms > System.monotonic_time(:millisecond) + 1_000
    end)
  end

  defp poll(server) do
    send(server, :run_poll_cycle)
    :sys.get_state(server)
  end

  defp turn_count(path) do
    case File.read(path) do
      {:ok, data} -> length(String.split(data, "\n", trim: true))
      _ -> 0
    end
  end

  defp await(check, timeout \\ 1_500) do
    until = System.monotonic_time(:millisecond) + timeout
    await_until(check, until)
  end

  defp await_until(check, until) do
    if check.() do
      :ok
    else
      assert System.monotonic_time(:millisecond) < until, "quota recovery did not advance"
      Process.sleep(5)
      await_until(check, until)
    end
  end
end
