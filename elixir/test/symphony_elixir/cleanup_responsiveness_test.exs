defmodule SymphonyElixir.CleanupResponsivenessTest do
  use SymphonyElixir.TestSupport

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

    entries = install_workers(ctx, ["1", "2", "3"], {:claude, cleanup})
    for {_id, entry} <- entries, do: Process.exit(entry.pid, :kill)
    await(fn -> Enum.all?(1..3, &File.exists?(Path.join(ctx.root, "started-#{&1}"))) end)
    assert length(Orchestrator.snapshot(__MODULE__, 200).running) == 3
    assert {:ok, %{draining: true}} = GenServer.call(ctx.server, {:agent_control, :drain, false}, 200)
    state = :sys.get_state(ctx.server, 200)
    assert state.retry_attempts == %{}
    for n <- 1..3, do: File.write!(Path.join(ctx.root, "release-#{n}"), "release")
    await(fn -> map_size(:sys.get_state(ctx.server, 200).retry_attempts) == 3 end)
    state = :sys.get_state(ctx.server, 200)
    assert state.running == %{}

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
