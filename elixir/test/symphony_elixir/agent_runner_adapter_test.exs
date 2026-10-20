defmodule SymphonyElixir.AgentRunnerAdapterTest do
  use SymphonyElixir.TestSupport

  defmodule Probe do
    @behaviour SymphonyElixir.CodingAgent

    @impl true
    def start_session(_workspace, _opts) do
      if Process.get(:probe_start_failure) do
        {:error, :probe_startup_failure}
      else
        owner = self()
        {:ok, pid} = Agent.start_link(fn -> owner end)
        send(owner, {:probe_started, pid})
        {:ok, pid}
      end
    end

    @impl true
    def run_turn(pid, prompt, _issue, _opts) do
      owner = Agent.get(pid, & &1)
      send(owner, {:probe_turn, pid, prompt})
      if Process.get(:probe_raise), do: raise("probe raised")
      Process.get(:probe_turn_result, {:ok, %{session_id: "probe-session"}})
    end

    @impl true
    def interrupt(pid, _reason), do: stop_session(pid)

    @impl true
    def stop_session(pid) do
      if Process.alive?(pid) do
        send(Agent.get(pid, & &1), {:probe_stopped, pid})
        Agent.stop(pid)
      end

      if Process.get(:probe_cleanup_raise), do: raise("cleanup raised with sensitive text")
      Process.get(:probe_cleanup_result, :ok)
    end
  end

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-agent-adapter-#{System.unique_integer([:positive])}")

    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "memory",
      workspace_root: root,
      hook_before_run: "printf before > before",
      hook_after_run: "printf after > after"
    )

    on_exit(fn -> File.rm_rf!(root) end)
    %{root: root, issue: %Issue{id: "adapter-one", identifier: "ADAPTER-1", title: "Probe", state: "In Progress", dispatchable: true}}
  end

  test "worker continues in one native session and cleans up once", ctx do
    fetcher = fn _ids ->
      case Process.get(:probe_fetch_count, 0) do
        0 ->
          Process.put(:probe_fetch_count, 1)
          {:ok, [ctx.issue]}

        _ ->
          {:ok, []}
      end
    end

    assert :ok = AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe, issue_state_fetcher: fetcher)
    assert_received {:probe_started, pid}
    assert_received {:probe_turn, ^pid, _first}
    assert_received {:probe_turn, ^pid, continuation}
    assert continuation =~ "continuation turn #2"
    assert_received {:probe_stopped, ^pid}
    refute_received {:probe_stopped, _}
    assert File.read!(Path.join([ctx.root, "ADAPTER-1", "before"])) == "before"
    assert File.read!(Path.join([ctx.root, "ADAPTER-1", "after"])) == "after"
  end

  test "raised turn still closes native session and runs after hook", ctx do
    Process.put(:probe_raise, true)
    assert_raise RuntimeError, "probe raised", fn -> AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe) end
    assert_received {:probe_started, pid}
    assert_received {:probe_stopped, ^pid}
    refute Process.alive?(pid)
    assert File.read!(Path.join([ctx.root, "ADAPTER-1", "after"])) == "after"
  end

  test "attempt allowance returns normally after cleanup without replacing the native session", ctx do
    assert :ok = AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe, max_turns: 1, issue_state_fetcher: fn _ -> {:ok, [ctx.issue]} end)

    assert_received {:probe_started, pid}
    assert_received {:probe_stopped, ^pid}
    refute Process.alive?(pid)
    refute_received {:probe_started, _}
  end

  test "recoverable delivery failure exits only after cleanup and retains readback context", ctx do
    reason = {:agent_blocked, "tracker_delivery_failed"}
    Process.put(:probe_turn_result, {:error, reason})
    assert ^reason = catch_exit(AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe))
    assert_received {:probe_started, pid}
    assert_received {:probe_turn, ^pid, prompt}
    assert prompt =~ "read the target before retrying"
    assert prompt =~ "If the intended\nchange already exists, continue without duplicating it"
    assert_received {:probe_stopped, ^pid}
    refute Process.alive?(pid)
    refute_received {:probe_turn, _, _}
    assert File.read!(Path.join([ctx.root, "ADAPTER-1", "after"])) == "after"
  end

  test "unconfirmed cleanup preserves recoverable failure separately and runs after hook", ctx do
    Process.put(:probe_turn_result, {:error, {:agent_blocked, "protocol_error"}})
    Process.put(:probe_cleanup_result, {:error, :cleanup_unconfirmed})

    assert {:agent_replacement_required, %{primary: {:agent_blocked, :protocol_error}, cleanup: :cleanup_unconfirmed}} =
             catch_exit(AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe))

    assert_received {:probe_started, pid}
    assert_received {:probe_stopped, ^pid}
    refute_received {:probe_stopped, _}
    assert File.read!(Path.join([ctx.root, "ADAPTER-1", "after"])) == "after"
  end

  test "cleanup failure records a successful primary outcome without pretending completion", ctx do
    Process.put(:probe_cleanup_result, {:error, :cleanup_unconfirmed})

    assert {:agent_replacement_required, %{primary: :completed, cleanup: :cleanup_unconfirmed}} =
             catch_exit(AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe, issue_state_fetcher: fn _ -> {:ok, []} end))
  end

  test "raised primary and cleanup exceptions retain separate bounded causes", ctx do
    Process.put(:probe_raise, true)
    Process.put(:probe_cleanup_raise, true)

    assert {:agent_replacement_required, %{primary: {:error, RuntimeError}, cleanup: :cleanup_failed}} =
             catch_exit(AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe))

    assert_received {:probe_stopped, _}
    refute_received {:probe_stopped, _}
  end

  test "unknown arbitrary native error payload is not exposed by cleanup diagnostics", ctx do
    Process.put(:probe_turn_result, {:error, {:agent_blocked, String.duplicate("sensitive", 1_000)}})
    Process.put(:probe_cleanup_result, {:error, :cleanup_unconfirmed})

    assert {:agent_replacement_required, %{primary: {:agent_blocked, :unknown}, cleanup: :cleanup_unconfirmed}} =
             catch_exit(AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe))
  end

  test "startup failure still runs after hook without inventing cleanup", ctx do
    Process.put(:probe_start_failure, true)
    assert_raise RuntimeError, ~r/probe_startup_failure/, fn -> AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe) end
    refute_received {:probe_started, _}
    refute_received {:probe_stopped, _}
    assert File.read!(Path.join([ctx.root, "ADAPTER-1", "after"])) == "after"
  end
end
