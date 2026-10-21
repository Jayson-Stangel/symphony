defmodule SymphonyElixir.CommonCancellationTest do
  use SymphonyElixir.TestSupport

  defmodule Probe do
    @behaviour SymphonyElixir.CodingAgent
    @impl true
    def start_session(_workspace, _opts), do: {:error, :unused}
    @impl true
    def run_turn(_native, _prompt, _issue, _opts), do: {:error, :unused}
    @impl true
    def stop_session(_native), do: :ok

    @impl true
    def interrupt(native, reason) do
      send(native.observer, {:interrupted, reason, Process.alive?(native.owner), Port.info(native.port, :connected)})

      if native.outcome == :ok do
        Port.close(native.port)
        File.write!(Path.join(native.workspace, "interrupted"), "confirmed")
      end

      if native.outcome == :raise, do: raise("secret-canary cleanup exception")
      native.outcome
    end
  end

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-common-cancellation-#{System.unique_integer([:positive])}")
    workspace = Path.join(root, "CANCEL-1")
    alive = Path.join(root, "worker-alive")
    removed = Path.join(root, "removal-proof")
    File.mkdir_p!(workspace)

    write_workflow_file!(Workflow.workflow_file_path(),
      workspace_root: root,
      tracker_kind: "memory",
      hook_before_remove: """
      if [ -f interrupted ] && [ ! -f "#{alive}" ]; then
        printf ordered > "#{removed}"
      else
        printf task-only > "#{removed}"
      fi
      """
    )

    supervisor = start_supervised!(Task.Supervisor)
    observer = self()

    {:ok, owner} =
      Task.Supervisor.start_child(supervisor, fn ->
        Process.flag(:trap_exit, true)
        port = Port.open({:spawn_executable, System.find_executable("cat") |> String.to_charlist()}, [:binary, :exit_status])
        File.write!(alive, "alive")
        send(observer, {:native_port, port})

        try do
          receive do
            {:EXIT, _parent, :shutdown} -> :ok
          end
        after
          File.rm(alive)
          if Port.info(port), do: Port.close(port)
        end
      end)

    assert_receive {:native_port, port}, 1_000
    issue = %Issue{id: "common-cancel", identifier: "CANCEL-1", title: "Cancellation", state: "In Progress", dispatchable: true}

    entry = %{
      pid: owner,
      ref: Process.monitor(owner),
      issue: issue,
      identifier: issue.identifier,
      workspace_path: workspace,
      started_at: DateTime.utc_now()
    }

    native = %{owner: owner, port: port, observer: observer, workspace: workspace, outcome: :ok}

    state = %Orchestrator.State{
      task_supervisor: supervisor,
      running: %{issue.id => entry},
      claimed: MapSet.new([issue.id]),
      codex_totals: %{input_tokens: 0, output_tokens: 0, total_tokens: 0, seconds_running: 0}
    }

    on_exit(fn -> File.rm_rf!(root) end)

    %{
      state: state,
      issue: issue,
      entry: entry,
      native: native,
      owner: owner,
      port: port,
      workspace: workspace,
      alive: alive,
      removed: removed
    }
  end

  test "a captured non-Claude callback interrupts before task retirement and workspace removal", ctx do
    state = capture_session(ctx, ctx.native)
    state = Orchestrator.reconcile_issue_states_for_test([%{ctx.issue | state: "Done"}], state) |> finish_cleanup(ctx.issue.id)
    owner = ctx.owner
    assert_receive {:interrupted, :issue_state_change, true, {:connected, ^owner}}
    refute Process.alive?(owner)
    assert Port.info(ctx.port) == nil
    assert File.read!(ctx.removed) == "ordered"
    refute File.exists?(ctx.workspace)
    assert state.running == %{}
    refute MapSet.member?(state.claimed, ctx.issue.id)
  end

  test "the captured Codex callback closes a worker-owned port and remains idempotent", ctx do
    session = %{adapter: SymphonyElixir.CodingAgent.Codex, native: %{port: ctx.port}}
    entry = Map.put(ctx.entry, :agent_session, session)
    state = %{ctx.state | running: %{ctx.issue.id => entry}}

    state = Orchestrator.reconcile_issue_states_for_test([%{ctx.issue | state: "Done"}], state) |> finish_cleanup(ctx.issue.id)
    assert Port.info(ctx.port) == nil
    refute Process.alive?(ctx.owner)
    assert :ok = SymphonyElixir.CodingAgent.interrupt(session, :cancelled)
    assert :ok = SymphonyElixir.CodingAgent.stop_session(session)
    assert state.running == %{}
    refute MapSet.member?(state.claimed, ctx.issue.id)
    refute File.exists?(ctx.workspace)
  end

  for {label, outcome} <- [{"error", {:error, :cleanup_unconfirmed}}, {"exception", :raise}] do
    @cleanup_outcome outcome
    test "a captured callback #{label} still retires the task and reconciles its workspace", ctx do
      ctx = %{ctx | entry: Map.put(ctx.entry, :session_id, "cancel-session")}
      state = capture_session(ctx, %{ctx.native | outcome: @cleanup_outcome})

      log =
        capture_log(fn ->
          state = Orchestrator.reconcile_issue_states_for_test([%{ctx.issue | state: "Done"}], state) |> finish_cleanup(ctx.issue.id)
          assert state.running == %{}
          refute MapSet.member?(state.claimed, ctx.issue.id)
        end)

      assert_receive {:interrupted, :issue_state_change, true, _port_owner}
      assert log =~ "issue_id=common-cancel issue_identifier=CANCEL-1 session_id=cancel-session"
      assert log =~ "cleanup=cleanup_unconfirmed"
      refute log =~ "secret-canary"
      refute Process.alive?(ctx.owner)
      assert Port.info(ctx.port) == nil
      refute File.exists?(ctx.alive)
      refute File.dir?(ctx.workspace)
      assert File.read!(ctx.removed) == "task-only"
    end
  end

  test "an unpublished session retains the existing task-only cancellation path", ctx do
    state = Orchestrator.reconcile_issue_states_for_test([%{ctx.issue | state: "Done"}], ctx.state)
    refute_receive {:interrupted, _, _, _}
    refute Process.alive?(ctx.owner)
    assert File.read!(ctx.removed) == "task-only"
    refute File.exists?(ctx.workspace)
    assert state.running == %{}
  end

  defp finish_cleanup(state, issue_id) do
    ref = state.running[issue_id].ref
    assert_receive {:DOWN, ^ref, :process, _pid, _reason} = down, 2_000
    assert {:noreply, state} = Orchestrator.handle_info(down, state)
    state
  end

  defp capture_session(ctx, native) do
    entry = Map.put(ctx.entry, :agent_session, %{adapter: Probe, native: native})
    %{ctx.state | running: %{ctx.issue.id => entry}}
  end
end
