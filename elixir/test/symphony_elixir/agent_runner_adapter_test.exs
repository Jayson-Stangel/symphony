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
      {:ok, %{session_id: "probe-session"}}
    end

    @impl true
    def interrupt(pid, _reason), do: stop_session(pid)

    @impl true
    def stop_session(pid) do
      if Process.alive?(pid) do
        send(Agent.get(pid, & &1), {:probe_stopped, pid})
        Agent.stop(pid)
      end

      :ok
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

  test "max-turn exit returns control after cleanup without replacing the native session", ctx do
    assert {:max_turns_reached, %{max_turns: 1}} =
             catch_exit(AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe, max_turns: 1, issue_state_fetcher: fn _ -> {:ok, [ctx.issue]} end))

    assert_received {:probe_started, pid}
    assert_received {:probe_stopped, ^pid}
    refute_received {:probe_started, _}
  end

  test "startup failure still runs after hook without inventing cleanup", ctx do
    Process.put(:probe_start_failure, true)
    assert_raise RuntimeError, ~r/probe_startup_failure/, fn -> AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe) end
    refute_received {:probe_started, _}
    refute_received {:probe_stopped, _}
    assert File.read!(Path.join([ctx.root, "ADAPTER-1", "after"])) == "after"
  end
end
