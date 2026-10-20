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
    # The initial poll sees no issues; all later transitions use the memory tracker.
    poll(server)
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

  test "known reset delays the existing retry timer, then resumes without recheck", ctx do
    reset = System.system_time(:second) + 2
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [ctx.issue])
    quota_exit(ctx.server, ctx.issue, %{resets_at: reset})
    retry = :sys.get_state(ctx.server).retry_attempts[ctx.issue.id]
    assert retry.due_at_ms - System.monotonic_time(:millisecond) > 500
    poll(ctx.server)
    refute File.exists?(ctx.turns)
    assert {:paused, %{"kind" => "quota_exhausted"}} = AgentPause.current()
    await(fn -> turn_count(ctx.turns) == 1 end, 3_000)
    assert :ready = AgentPause.current()
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: "Human Review"}])
    poll(ctx.server)
  end

  test "a shared cooldown extension retains queued retries and does not duplicate dispatch", ctx do
    reset = System.system_time(:second) + 1
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [ctx.issue])
    quota_exit(ctx.server, ctx.issue, %{resets_at: reset})
    initial = :sys.get_state(ctx.server).retry_attempts[ctx.issue.id]
    extended = reset + 1
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: AgentPause.codex_profile(), resets_at: extended})
    await(fn -> :sys.get_state(ctx.server).retry_attempts[ctx.issue.id].due_at_ms > initial.due_at_ms end, 2_000)
    assert :sys.get_state(ctx.server).retry_attempts[ctx.issue.id].attempt == 1
    refute File.exists?(ctx.turns)
    await(fn -> turn_count(ctx.turns) == 1 end, 2_000)
    poll(ctx.server)
    assert map_size(:sys.get_state(ctx.server).running) == 1
    assert turn_count(ctx.turns) == 1
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

  defp quota_exit(server, issue, details) do
    {:ok, worker} =
      Task.Supervisor.start_child(SymphonyElixir.TaskSupervisor, fn ->
        receive do
          :quota ->
            details = Map.put(details, :profile, AgentPause.codex_profile())
            exit({:agent_pause, :quota_exhausted, details})
        end
      end)

    :sys.replace_state(server, fn state ->
      # Observe an actual worker DOWN, just as the provider adapter reports quota.
      entry = %{
        pid: worker,
        ref: Process.monitor(worker),
        identifier: issue.identifier,
        issue: issue,
        started_at: DateTime.utc_now()
      }

      %{state | running: Map.put(state.running, issue.id, entry), claimed: MapSet.put(state.claimed, issue.id)}
    end)

    send(worker, :quota)
    await(fn -> Map.has_key?(:sys.get_state(server).retry_attempts, issue.id) end)
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
