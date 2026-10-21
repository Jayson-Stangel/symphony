defmodule SymphonyElixir.CleanupResponsivenessTest do
  use SymphonyElixir.TestSupport
  require Phoenix.LiveViewTest

  defmodule InterruptProbe do
    @behaviour SymphonyElixir.CodingAgent
    @impl true
    def start_session(_, _), do: {:error, :unused}
    @impl true
    def run_turn(_, _, _, _), do: {:error, :unused}
    @impl true
    def stop_session(_), do: :ok
    @impl true
    def interrupt(native, reason) do
      send(native.observer, {:cleanup_started, native.id, self(), reason})

      receive do
        :finish_cleanup -> :ok
      after
        3_000 -> raise "cleanup fixture timed out"
      end

      Port.close(native.port)
      File.write!(Path.join(native.workspace, "cleanup-complete"), "complete")
      if native[:outcome] == :raise, do: raise("cleanup fixture error")
      :ok
    end
  end

  setup_all do
    previous = Application.fetch_env(:symphony_elixir, :workflow_file_path)
    :ok = Supervisor.terminate_child(SymphonyElixir.Supervisor, SymphonyElixir.AgentRuntimeSupervisor)

    on_exit(fn ->
      case previous do
        {:ok, path} -> Application.put_env(:symphony_elixir, :workflow_file_path, path)
        :error -> Application.delete_env(:symphony_elixir, :workflow_file_path)
      end

      WorkflowStore.force_reload()
      assert {:ok, _} = Supervisor.restart_child(SymphonyElixir.Supervisor, SymphonyElixir.AgentRuntimeSupervisor)
    end)

    :ok
  end

  setup do
    root = Path.dirname(Workflow.workflow_file_path())
    workspaces = Path.join(root, "workspaces")

    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "memory",
      workspace_root: workspaces,
      max_concurrent_agents: 4,
      poll_interval_ms: 60_000,
      hook_before_remove: "if [ -f cleanup-complete ] && [ ! -f worker-alive ]; then printf ordered >> '#{root}/removal-proof'; else printf raced >> '#{root}/removal-proof'; fi"
    )

    Application.put_env(:symphony_elixir, :memory_tracker_issues, [])
    supervisor = start_supervised!(Task.Supervisor)
    server = start_supervised!({Orchestrator, name: __MODULE__, task_supervisor: supervisor})
    :sys.replace_state(server, &%{&1 | draining: true, startup_cleanup_pending: false})
    %{root: root, server: server, supervisor: supervisor, workspaces: workspaces}
  end

  test "concurrent cancellation keeps snapshots, controls, readiness and sibling DOWN responsive", ctx do
    entries = install_workers(ctx, ["one", "two", "sibling"], :probe)
    issues = Enum.map(entries, fn {id, e} -> if id == "sibling", do: e.issue, else: %{e.issue | state: "Done"} end)
    poll(ctx.server, issues)
    assert_receive {:cleanup_started, "one", first, :issue_state_change}, 500
    assert_receive {:cleanup_started, "two", second, :issue_state_change}, 500
    assert length(Orchestrator.snapshot(__MODULE__, 200).running) == 3
    assert {:ok, %{draining: true}} = GenServer.call(ctx.server, {:agent_control, :drain, false}, 200)
    assert {:error, :workers_active} = GenServer.call(ctx.server, {:agent_control, :resume, false}, 200)

    :sys.replace_state(ctx.server, fn state ->
      task =
        Task.Supervisor.async_nolink(ctx.supervisor, fn ->
          receive do
            :finish_check -> %{ready: true, provider: "codex"}
          end
        end)

      %{state | readiness: %{ready: false, code: "fixture"}, readiness_task: task}
    end)

    send(:sys.get_state(ctx.server, 200).readiness_task.pid, :finish_check)
    Process.exit(entries["sibling"].pid, :kill)
    await(fn -> Map.has_key?(:sys.get_state(ctx.server, 200).retry_attempts, "sibling") end)
    await(fn -> :sys.get_state(ctx.server, 200).readiness_task == nil end)
    state = :sys.get_state(ctx.server, 200)
    refute state.readiness[:code] == "fixture"
    assert map_size(state.running) == 2
    assert MapSet.member?(state.claimed, "one")
    assert File.dir?(entries["one"].workspace_path)
    # Repeated tracker observations and late worker events cannot replace cleanup ownership.
    poll(ctx.server, issues)
    send(ctx.server, {:worker_agent_session, "one", %{adapter: :stale, native: %{}}})
    send(ctx.server, {:worker_runtime_info, "one", %{workspace_path: "/stale"}})
    refute_receive {:cleanup_started, _, _, _}, 50
    assert :sys.get_state(ctx.server, 200).running["one"].agent_session.adapter == InterruptProbe
    send(first, :finish_cleanup)
    send(second, :finish_cleanup)
    await(fn -> :sys.get_state(ctx.server, 200).running == %{} end)
    state = :sys.get_state(ctx.server, 200)
    refute MapSet.member?(state.claimed, "one")
    assert File.read!(Path.join(ctx.root, "removal-proof")) == "orderedordered"

    for id <- ["one", "two"] do
      refute Process.alive?(entries[id].pid)
      refute File.exists?(entries[id].workspace_path)
    end
  end

  test "actual owner losses run fallback commands concurrently and preserve retry classification", ctx do
    # Fixture evidence: the production Claude adapter executes real bounded fallback
    # subprocesses, using inert ownership metadata and a controlled cleanup peer.
    cleanup = Path.join(ctx.root, "cleanup-peer.mjs")

    File.write!(cleanup, """
    import {writeFileSync, existsSync} from 'node:fs';
    import {setTimeout as delay} from 'node:timers/promises';
    const id = JSON.parse(process.argv[2]).native[0].start;
    writeFileSync(#{Jason.encode!(ctx.root)} + '/started-' + id, 'started');
    for (let n = 0; n < 300 && !existsSync(#{Jason.encode!(ctx.root)} + '/release-' + id); n++) await delay(10);
    writeFileSync(#{Jason.encode!(ctx.root)} + '/complete-' + id, 'complete');
    """)

    config = [tracker_kind: "memory", workspace_root: ctx.workspaces, codex_stall_timeout_ms: 0]
    write_workflow_file!(Workflow.workflow_file_path(), config)
    entries = install_workers(ctx, ["1", "2", "3"], {:claude, cleanup})

    :sys.replace_state(ctx.server, fn state ->
      running =
        Map.new(state.running, fn {id, entry} ->
          {id, %{entry | started_at: DateTime.add(DateTime.utc_now(), -3_600)}}
        end)

      %{state | running: running}
    end)

    assert_accounting_snapshot(10_800, 0)
    for {_id, entry} <- entries, do: Process.exit(entry.pid, :kill)
    await(fn -> Enum.all?(1..3, &File.exists?(Path.join(ctx.root, "started-#{&1}"))) end)
    assert length(Orchestrator.snapshot(__MODULE__, 200).running) == 3
    assert {:ok, %{draining: true}} = GenServer.call(ctx.server, {:agent_control, :drain, false}, 200)
    state = :sys.get_state(ctx.server, 200)
    assert state.retry_attempts == %{}
    during = assert_accounting_snapshot(10_800, 0)
    assert during.codex_totals.seconds_running == 0
    for n <- 1..3, do: File.write!(Path.join(ctx.root, "release-#{n}"), "release")
    await(fn -> map_size(:sys.get_state(ctx.server, 200).retry_attempts) == 3 end)
    state = :sys.get_state(ctx.server, 200)
    assert state.running == %{}
    after_cleanup = assert_accounting_snapshot(10_800, 0)
    assert after_cleanup.codex_totals.seconds_running >= 10_800

    for n <- 1..3 do
      assert File.exists?(Path.join(ctx.root, "complete-#{n}"))
      assert state.retry_attempts[to_string(n)].error == "agent exited: :killed"
    end

    assert :ready = SymphonyElixir.AgentPause.current()
  end

  test "terminal tracker truth supersedes pending retry even if cleanup task dies", ctx do
    entries = install_workers(ctx, ["one"], :probe)
    issue = entries["one"].issue
    # Use the stalled-attempt route to create a pending retry on a captured session.
    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "memory",
      workspace_root: ctx.workspaces,
      codex_stall_timeout_ms: 1
    )

    :sys.replace_state(ctx.server, fn state -> put_in(state.running["one"].started_at, DateTime.add(DateTime.utc_now(), -60)) end)
    poll(ctx.server, [issue])
    assert_receive {:cleanup_started, "one", cleanup, :scheduler_stop}, 500
    assert match?({:retry, _, _}, :sys.get_state(ctx.server, 200).running["one"].cleanup_action)
    poll(ctx.server, [%{issue | state: "Done"}])
    poll(ctx.server, [%{issue | state: "Done"}])
    refute_receive {:cleanup_started, _, _, _}, 50
    assert :sys.get_state(ctx.server, 200).retry_attempts == %{}
    send(ctx.server, {:retry_issue, "one", make_ref()})
    assert :sys.get_state(ctx.server, 200).running["one"].cleanup_action == {:cancel, true}
    Process.exit(cleanup, :kill)
    await(fn -> :sys.get_state(ctx.server, 200).running == %{} end)
    state = :sys.get_state(ctx.server, 200)
    assert state.retry_attempts == %{}
    refute MapSet.member?(state.claimed, "one")
    refute File.dir?(entries["one"].workspace_path)
  end

  test "abnormal cleanup task DOWN retires its original worker and releases the claim", ctx do
    entries = install_workers(ctx, ["one"], :probe)
    poll(ctx.server, [%{entries["one"].issue | state: "Human Review"}])
    assert_receive {:cleanup_started, "one", cleanup, :issue_state_change}, 500
    Process.exit(cleanup, :kill)
    await(fn -> :sys.get_state(ctx.server, 200).running == %{} end)
    refute Process.alive?(entries["one"].pid)
    refute MapSet.member?(:sys.get_state(ctx.server, 200).claimed, "one")
  end

  test "scheduler authority restart retires its in-flight cleanup with the original worker", ctx do
    scheduler = Module.concat(__MODULE__, :RestartScheduler)
    tasks = Module.concat(__MODULE__, :RestartTasks)
    runtime = Module.concat(__MODULE__, :RestartRuntime)
    runtime_opts = [name: runtime, orchestrator_name: scheduler, task_supervisor_name: tasks]
    start_supervised!({SymphonyElixir.AgentRuntimeSupervisor, runtime_opts})
    server = Process.whereis(scheduler)
    :sys.replace_state(server, &%{&1 | draining: true, startup_cleanup_pending: false})
    isolated = %{ctx | server: server, supervisor: tasks}
    entries = install_workers(isolated, ["restart"], :probe)
    poll(server, [%{entries["restart"].issue | state: "Human Review"}])
    assert_receive {:cleanup_started, "restart", cleanup, :issue_state_change}, 500
    cleanup_monitor = Process.monitor(cleanup)
    Process.exit(server, :kill)
    assert_receive {:DOWN, ^cleanup_monitor, :process, ^cleanup, _reason}, 1_000
    await(fn -> is_pid(Process.whereis(scheduler)) and Process.whereis(scheduler) != server end)
    assert is_map(Orchestrator.snapshot(scheduler, 200))
    refute Process.alive?(entries["restart"].pid)
    assert Task.Supervisor.children(tasks) == []
  end

  for outcome <- [:cancel, :cleanup_error, :retry, :live_block, :input_block, :terminal_override] do
    @accounting_outcome outcome
    test "runtime and token totals stay continuous across #{@accounting_outcome} cleanup", ctx do
      verify_cleanup_accounting(ctx, @accounting_outcome)
    end
  end

  defp verify_cleanup_accounting(ctx, outcome) do
    stall_timeout = if outcome in [:retry, :input_block], do: 1, else: 0
    token_limit = if outcome in [:live_block, :terminal_override], do: 14, else: 0
    config = [tracker_kind: "memory", workspace_root: ctx.workspaces, codex_stall_timeout_ms: stall_timeout]
    config = Keyword.put(config, :codex_live_max_total_tokens, token_limit)
    write_workflow_file!(Workflow.workflow_file_path(), config)
    entries = install_workers(ctx, ["accounting"], :probe)

    :sys.replace_state(ctx.server, fn state ->
      entry = %{
        state.running["accounting"]
        | started_at: DateTime.add(DateTime.utc_now(), -3_600),
          codex_input_tokens: 11,
          codex_output_tokens: 3,
          codex_total_tokens: 14,
          last_codex_event: if(outcome == :input_block, do: :turn_input_required)
      }

      session = put_in(entry.agent_session.native[:outcome], if(outcome == :cleanup_error, do: :raise))
      totals = %{input_tokens: 11, output_tokens: 3, total_tokens: 14, seconds_running: 0}
      %{state | running: %{"accounting" => session}, codex_totals: totals}
    end)

    before = assert_accounting_snapshot(3_600, 14)
    issue = entries["accounting"].issue

    case outcome do
      kind when kind in [:live_block, :terminal_override] ->
        send(ctx.server, {:codex_worker_update, issue.id, %{event: :token_usage_updated, timestamp: DateTime.utc_now()}})

      kind when kind in [:retry, :input_block] ->
        poll(ctx.server, [issue])

      _ ->
        poll(ctx.server, [%{issue | state: "Human Review"}])
    end

    assert_receive {:cleanup_started, "accounting", cleanup, _reason}, 500
    during = assert_accounting_snapshot(3_600, 14)
    assert during.codex_totals.seconds_running == 0
    assert during.codex_totals == before.codex_totals
    ref = :sys.get_state(ctx.server, 200).running["accounting"].ref

    if outcome == :terminal_override do
      poll(ctx.server, [%{issue | state: "Done"}])
      Process.exit(cleanup, :kill)
    else
      send(cleanup, :finish_cleanup)
    end

    await(fn -> :sys.get_state(ctx.server, 200).running == %{} end)
    after_cleanup = assert_accounting_snapshot(3_600, 14)
    assert after_cleanup.codex_totals.seconds_running >= 3_600
    state = :sys.get_state(ctx.server, 200)
    assert Map.has_key?(state.retry_attempts, issue.id) == (outcome == :retry)
    assert Map.has_key?(state.blocked, issue.id) == outcome in [:live_block, :input_block]
    # A duplicate completion cannot transfer the same attempt to totals twice.
    send(ctx.server, {:DOWN, ref, :process, cleanup, :normal})
    assert :sys.get_state(ctx.server, 200).codex_totals == after_cleanup.codex_totals
  end

  defp assert_accounting_snapshot(expected_seconds, expected_tokens) do
    snapshot = Orchestrator.snapshot(__MODULE__, 200)
    seconds = snapshot.codex_totals.seconds_running + Enum.sum(Enum.map(snapshot.running, & &1.runtime_seconds))
    assert seconds >= expected_seconds and seconds <= expected_seconds + 2
    assert snapshot.codex_totals.total_tokens == expected_tokens
    assert snapshot.codex_totals.input_tokens + snapshot.codex_totals.output_tokens == expected_tokens
    payload = SymphonyElixirWeb.Presenter.state_payload(__MODULE__, 200)
    html = Phoenix.LiveViewTest.render_component(&SymphonyElixirWeb.DashboardLive.render/1, payload: payload, now: DateTime.utc_now())

    card =
      html
      |> Floki.parse_fragment!()
      |> Floki.find(".metric-card")
      |> Enum.find(fn node -> node |> Floki.find(".metric-label") |> Floki.text() == "Runtime" end)

    rendered = card |> Floki.find(".metric-value") |> Floki.text()
    assert rendered in Enum.map(expected_seconds..(expected_seconds + 2), &"#{div(&1, 60)}m #{rem(&1, 60)}s")
    snapshot
  end

  defp install_workers(ctx, ids, adapter) do
    observer = self()

    entries =
      Map.new(ids, fn id ->
        workspace = Path.join(ctx.workspaces, id)
        File.mkdir_p!(workspace)

        {:ok, pid} = Task.Supervisor.start_child(ctx.supervisor, fn -> worker(observer, id, workspace) end)

        assert_receive {:worker_port, ^id, port}, 500

        native =
          case adapter do
            :probe ->
              %{adapter: InterruptProbe, native: %{observer: observer, id: id, port: port, workspace: workspace}}

            {:claude, cleanup} ->
              %{
                adapter: SymphonyElixir.Claude.Adapter,
                native: %{
                  owner: pid,
                  port: port,
                  id: id,
                  node: System.find_executable("node"),
                  cleanup: cleanup,
                  helper_identity: nil,
                  owned_native: [%{"pid" => 999_999, "start" => id}]
                }
              }
          end

        issue = %Issue{id: id, identifier: id, title: id, state: "In Progress", dispatchable: true}

        entry = %{
          pid: pid,
          identifier: id,
          issue: issue,
          workspace_path: workspace,
          started_at: DateTime.utc_now(),
          agent_session: native,
          session_id: id,
          codex_app_server_pid: nil,
          codex_input_tokens: 0,
          codex_output_tokens: 0,
          codex_total_tokens: 0,
          last_codex_timestamp: nil,
          last_codex_message: nil,
          last_codex_event: nil
        }

        {id, entry}
      end)

    Application.put_env(:symphony_elixir, :memory_tracker_issues, Enum.map(entries, fn {_id, entry} -> entry.issue end))

    :sys.replace_state(ctx.server, fn state ->
      running = Map.new(entries, fn {id, entry} -> {id, Map.put(entry, :ref, Process.monitor(entry.pid))} end)
      %{state | running: running, claimed: MapSet.new(ids)}
    end)

    entries
  end

  defp worker(observer, id, workspace) do
    Process.flag(:trap_exit, true)
    port = Port.open({:spawn_executable, System.find_executable("cat") |> String.to_charlist()}, [:binary, :exit_status])
    File.write!(Path.join(workspace, "worker-alive"), "alive")
    send(observer, {:worker_port, id, port})

    try do
      receive do
        {:EXIT, _, :shutdown} -> :ok
      end
    after
      File.rm(Path.join(workspace, "worker-alive"))
      if Port.info(port), do: Port.close(port)
    end
  end

  defp poll(server, issues) do
    Application.put_env(:symphony_elixir, :memory_tracker_issues, issues)
    send(server, :run_poll_cycle)
    :sys.get_state(server, 200)
  end

  defp await(check, remaining \\ 200)
  defp await(_check, 0), do: flunk("cleanup lifecycle did not advance")

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
