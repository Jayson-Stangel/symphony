defmodule SymphonyElixir.ConcurrencyTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixirWeb.Presenter

  setup_all do
    # This suite changes process-wide provider/tracker configuration. The default
    # application scheduler must not dispatch its own workers into those fixtures.
    previous_workflow = Application.fetch_env(:symphony_elixir, :workflow_file_path)
    :ok = Supervisor.terminate_child(SymphonyElixir.Supervisor, SymphonyElixir.AgentRuntimeSupervisor)

    on_exit(fn ->
      case previous_workflow do
        {:ok, path} -> Application.put_env(:symphony_elixir, :workflow_file_path, path)
        :error -> Application.delete_env(:symphony_elixir, :workflow_file_path)
      end

      WorkflowStore.force_reload()
      assert {:ok, _pid} = Supervisor.restart_child(SymphonyElixir.Supervisor, SymphonyElixir.AgentRuntimeSupervisor)
    end)

    :ok
  end

  setup do
    # The opt-in path runs this same suite with Claude's production adapter/helper
    # in a disposable Linux image that mounts the no-inference peer at /opt.
    previous = Application.fetch_env(:symphony_elixir, :coding_agent)

    if System.get_env("SYMPHONY_TEST_CLAUDE_CONCURRENCY") == "1" do
      SymphonyElixir.TestSupport.isolate_inference_environment()
      Application.put_env(:symphony_elixir, :coding_agent, :claude)
    else
      Application.put_env(:symphony_elixir, :coding_agent, :codex)
    end

    on_exit(fn ->
      case previous do
        {:ok, value} -> Application.put_env(:symphony_elixir, :coding_agent, value)
        :error -> Application.delete_env(:symphony_elixir, :coding_agent)
      end
    end)

    root = Path.dirname(Workflow.workflow_file_path())
    peer = Path.join(root, "concurrent-peer.mjs")

    File.write!(peer, """
    import { createInterface } from 'node:readline';
    import { appendFileSync, existsSync } from 'node:fs';
    const reply = (id, result) => console.log(JSON.stringify({ id, result }));
    process.stdin.on('end', () => process.exit(0));
    for await (const line of createInterface({ input: process.stdin })) {
      const m = JSON.parse(line);
      if (m.method === 'initialize') reply(m.id, {});
      if (m.method === 'thread/start') reply(m.id, { thread: { id: `worker-${process.pid}` } });
      if (m.method === 'turn/start') {
        appendFileSync('turns', JSON.stringify({ pid: process.pid, params: m.params }) + '\\n');
        reply(m.id, { turn: { id: `turn-${process.pid}` } });
        const timer = setInterval(() => {
          if (existsSync('fail-once')) { process.exit(2); }
        }, 10);
        timer.unref();
      }
    }
    """)

    options = [
      tracker_kind: "memory",
      poll_interval_ms: 60_000,
      max_concurrent_agents: 3,
      max_retry_backoff_ms: 500,
      workspace_root: Path.join(root, "workspaces"),
      codex_command: "#{System.find_executable("node")} #{peer}"
    ]

    write_workflow_file!(Workflow.workflow_file_path(), options)
    # Existing workflows carrying the old provider field must not silently cap 3 at 1.
    contents = File.read!(Workflow.workflow_file_path())
    File.write!(Workflow.workflow_file_path(), String.replace(contents, "---\n", "---\nclaude:\n  max_concurrent_agents: 1\n", global: false))
    WorkflowStore.force_reload()
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [])
    task_supervisor = start_supervised!(Task.Supervisor)
    server = start_supervised!({Orchestrator, name: __MODULE__, task_supervisor: task_supervisor})
    issues = for n <- 1..4, do: %Issue{id: "parallel-#{n}", identifier: "PAR-#{n}", title: "Parallel #{n}", state: "In Progress", dispatchable: true}
    %{root: root, server: server, issues: issues, options: options}
  end

  test "three real worker sessions fill shared slots; fourth starts after cancellation", ctx do
    set_issues(ctx.issues)
    poll(ctx.server)
    await(fn -> Enum.count(ctx.issues, &started?(ctx, &1)) == 3 end)
    snapshot = Presenter.state_payload(__MODULE__, 5_000)
    assert snapshot.counts.running == 3
    assert snapshot.agent.max_concurrent_agents == 3
    state = :sys.get_state(ctx.server, 20_000)
    assert map_size(state.running) == 3
    assert state.running |> Map.values() |> Enum.map(& &1.session_id) |> Enum.uniq() |> length() == 3
    [stopped | retained] = Enum.filter(ctx.issues, &started?(ctx, &1))
    queued = Enum.find(ctx.issues, &(not started?(ctx, &1)))
    retained_pids = Map.new(retained, &{&1.id, state.running[&1.id].pid})
    set_issues(Enum.map(ctx.issues, fn issue -> if issue.id == stopped.id, do: %{issue | state: "Human Review"}, else: issue end))
    poll(ctx.server)
    await(fn -> started?(ctx, queued) end)
    state = :sys.get_state(ctx.server, 20_000)
    assert map_size(state.running) == 3
    refute Map.has_key?(state.running, stopped.id)
    for {id, pid} <- retained_pids, do: assert(state.running[id].pid == pid and Process.alive?(pid))
    stop_workers(ctx)
  end

  test "global/per-state reload and drain reuse the scheduler without overfilling", ctx do
    write_workflow_file!(Workflow.workflow_file_path(), Keyword.merge(ctx.options, max_concurrent_agents: 1))
    set_issues(ctx.issues)
    poll(ctx.server)
    await(fn -> Enum.count(ctx.issues, &started?(ctx, &1)) == 1 end)
    assert map_size(:sys.get_state(ctx.server, 20_000).running) == 1
    assert {:ok, %{draining: true}} = GenServer.call(ctx.server, {:agent_control, :drain, false})
    write_workflow_file!(Workflow.workflow_file_path(), Keyword.merge(ctx.options, max_concurrent_agents_by_state: %{"In Progress" => 2}))
    poll(ctx.server)
    assert map_size(:sys.get_state(ctx.server, 20_000).running) == 1
    assert {:error, :workers_active} = GenServer.call(ctx.server, {:agent_control, :resume, false})
    stop_workers(ctx)
    assert {:ok, %{draining: false}} = GenServer.call(ctx.server, {:agent_control, :resume, false})
    set_issues(ctx.issues)
    poll(ctx.server)
    await(fn -> map_size(:sys.get_state(ctx.server, 20_000).running) == 2 end)
    assert map_size(:sys.get_state(ctx.server, 20_000).running) == 2
    write_workflow_file!(Workflow.workflow_file_path(), ctx.options)
    poll(ctx.server)
    await(fn -> map_size(:sys.get_state(ctx.server, 20_000).running) == 3 end)
    write_workflow_file!(Workflow.workflow_file_path(), Keyword.merge(ctx.options, max_concurrent_agents: 1))
    poll(ctx.server)
    assert map_size(:sys.get_state(ctx.server, 20_000).running) == 3
    assert Presenter.state_payload(__MODULE__, 5_000).agent.max_concurrent_agents == 1
    stop_workers(ctx)
  end

  test "one worker failure retries its workspace while the other sessions remain live", ctx do
    issues = Enum.take(ctx.issues, 3)
    set_issues(issues)
    poll(ctx.server)
    await(fn -> Enum.all?(issues, &started?(ctx, &1)) end)
    [failed | retained] = issues
    before = :sys.get_state(ctx.server, 20_000)
    path = Path.join([ctx.root, "workspaces", failed.identifier, "fail-once"])
    File.write!(path, "fail")
    await(fn -> Map.has_key?(:sys.get_state(ctx.server, 20_000).retry_attempts, failed.id) end)
    File.rm!(path)
    await(fn -> turn_count(ctx, failed) == 2 end)
    after_retry = :sys.get_state(ctx.server, 20_000)
    assert map_size(after_retry.running) == 3
    for issue <- retained, do: assert(after_retry.running[issue.id].pid == before.running[issue.id].pid)
    assert after_retry.running[failed.id].workspace_path == before.running[failed.id].workspace_path
    stop_workers(ctx)
  end

  @tag skip: System.get_env("SYMPHONY_TEST_CLAUDE_CONCURRENCY") != "1"
  test "unconfirmed captured cleanup retries its real worker without stopping peer sessions", ctx do
    issues = Enum.take(ctx.issues, 3)
    set_issues(issues)
    poll(ctx.server)
    await(fn -> Enum.all?(issues, &started?(ctx, &1)) end)

    await(fn ->
      state = :sys.get_state(ctx.server, 20_000)
      Enum.all?(issues, &match?(%{agent_session: %{native: _}}, state.running[&1.id]))
    end)

    [failed | retained] = issues
    before = :sys.get_state(ctx.server, 20_000)
    original_session = before.running[failed.id].agent_session
    # Fault the captured cleanup executable only, preserving actual PID/generation
    # ownership; the real worker dies untrappably while the sibling Tasks stay live.
    failed_session = put_in(original_session.native.cleanup, "/missing-cleanup")
    :sys.replace_state(ctx.server, fn state -> put_in(state.running[failed.id].agent_session, failed_session) end)

    try do
      log =
        capture_log(fn ->
          Process.exit(before.running[failed.id].pid, :kill)
          await(fn -> turn_count(ctx, failed) == 2 end)
        end)

      assert log =~ "cleanup=cleanup_unconfirmed"
      after_retry = :sys.get_state(ctx.server, 20_000)
      assert map_size(after_retry.running) == 3
      assert after_retry.running[failed.id].pid != before.running[failed.id].pid
      assert after_retry.running[failed.id].workspace_path == before.running[failed.id].workspace_path
      for issue <- retained, do: assert(after_retry.running[issue.id].pid == before.running[issue.id].pid)
      assert Presenter.state_payload(__MODULE__, 5_000).agent.admission.ready
      assert :ready = SymphonyElixir.AgentPause.current()
      refute File.exists?(Path.join(Config.agent_state_root(), "claude-cleanup-unconfirmed"))
    after
      # The deliberately failed captured command is not a cleanup proof. Reuse the
      # original generation-checked command for this fixture's remaining resources.
      assert :ok = SymphonyElixir.CodingAgent.stop_session(original_session)
      stop_workers(ctx)
    end
  end

  @tag skip: System.get_env("SYMPHONY_TEST_CLAUDE_CONCURRENCY") != "1"
  test "shared Claude cooldown gates new starts while other owned sessions stay live", ctx do
    set_issues(ctx.issues)
    poll(ctx.server)
    await(fn -> Enum.count(ctx.issues, &started?(ctx, &1)) == 3 end)
    before = :sys.get_state(ctx.server, 20_000)
    [cancelled | retained] = Enum.filter(ctx.issues, &started?(ctx, &1))
    queued = Enum.find(ctx.issues, &(not started?(ctx, &1)))
    profile = String.duplicate("a", 64)
    reset = System.system_time(:second) + 600
    assert :ok = SymphonyElixir.AgentPause.record(:quota_exhausted, %{profile: profile, resets_at: reset})
    poll(ctx.server)
    assert Presenter.state_payload(__MODULE__, 5_000).agent.admission.reason == "quota_exhausted"
    set_issues(Enum.map(ctx.issues, fn issue -> if issue.id == cancelled.id, do: %{issue | state: "Human Review"}, else: issue end))
    poll(ctx.server)
    await(fn -> not Map.has_key?(:sys.get_state(ctx.server, 1_000).running, cancelled.id) end)
    refute started?(ctx, queued)
    during = :sys.get_state(ctx.server, 20_000)
    assert map_size(during.running) == 2
    for issue <- retained, do: assert(during.running[issue.id].pid == before.running[issue.id].pid)
    path = Path.join(Config.agent_state_root(), "claude-pause.json")
    expired = path |> File.read!() |> Jason.decode!() |> Map.put("resets_at", System.system_time(:second) - 1)
    File.write!(path, Jason.encode!(expired))
    poll(ctx.server)
    await(fn -> started?(ctx, queued) end)
    assert :ready = SymphonyElixir.AgentPause.current()
    assert map_size(:sys.get_state(ctx.server, 20_000).running) == 3
    stop_workers(ctx)
  end

  @tag skip: System.get_env("SYMPHONY_TEST_CLAUDE_CONCURRENCY") != "1"
  test "healthy explicit recheck restores configured concurrency and recoverable retries", ctx do
    profile = String.duplicate("a", 64)
    assert :ok = SymphonyElixir.AgentPause.record(:auth_required, %{profile: profile})
    assert {:ok, %{draining: false}} = GenServer.call(ctx.server, {:agent_control, :recheck, false})
    assert :ready = SymphonyElixir.AgentPause.current()
    issues = Enum.take(ctx.issues, 3)
    set_issues(issues)
    poll(ctx.server)
    await(fn -> Enum.all?(issues, &started?(ctx, &1)) end)
    before = :sys.get_state(ctx.server, 20_000)
    assert map_size(before.running) == 3
    [failed | retained] = issues
    path = Path.join([ctx.root, "workspaces", failed.identifier, "fail-once"])
    File.write!(path, "fail")
    await(fn -> Map.has_key?(:sys.get_state(ctx.server, 20_000).retry_attempts, failed.id) end)
    File.rm!(path)
    await(fn -> turn_count(ctx, failed) == 2 end)
    after_retry = :sys.get_state(ctx.server, 20_000)
    assert map_size(after_retry.running) == 3
    for issue <- retained, do: assert(after_retry.running[issue.id].pid == before.running[issue.id].pid)
    assert after_retry.running[failed.id].workspace_path == before.running[failed.id].workspace_path
    assert :ready = SymphonyElixir.AgentPause.current()
    stop_workers(ctx)
  end

  defp stop_workers(ctx) do
    set_issues(Enum.map(ctx.issues, &%{&1 | state: "Human Review"}))
    poll(ctx.server)
    await(fn -> :sys.get_state(ctx.server, 1_000).running == %{} end)
  end

  defp set_issues(issues), do: Application.put_env(:symphony_elixir, :memory_tracker_issues, issues)

  defp poll(server) do
    send(server, :run_poll_cycle)
    :sys.get_state(server, 20_000)
  end

  defp started?(ctx, issue), do: turn_count(ctx, issue) > 0

  defp turn_count(ctx, issue) do
    case File.read(Path.join([ctx.root, "workspaces", issue.identifier, "turns"])) do
      {:ok, contents} -> length(String.split(contents, "\n", trim: true))
      _ -> 0
    end
  end

  defp await(check, remaining \\ 1_000)
  defp await(_check, 0), do: flunk("concurrent worker state did not advance")

  defp await(check, remaining) do
    if check.(),
      do: :ok,
      else:
        (
          Process.sleep(10)
          await(check, remaining - 1)
        )
  end
end
