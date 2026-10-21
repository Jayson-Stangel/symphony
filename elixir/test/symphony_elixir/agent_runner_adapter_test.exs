defmodule SymphonyElixir.AgentRunnerAdapterTest do
  use SymphonyElixir.TestSupport

  defmodule Probe do
    @behaviour SymphonyElixir.CodingAgent

    @impl true
    def start_session(_workspace, opts) do
      if Process.get(:probe_start_failure) do
        {:error, :probe_startup_failure}
      else
        owner = self()
        {:ok, pid} = Agent.start_link(fn -> owner end)
        send(owner, {:probe_started, pid})
        send(owner, {:probe_attempt_allowance, Keyword.fetch!(opts, :max_turns)})

        native =
          case Process.get(:probe_native_context) do
            context when is_map(context) -> Map.put(context, :pid, pid)
            _ -> pid
          end

        {:ok, native}
      end
    end

    @impl true
    def run_turn(native, prompt, _issue, _opts) do
      pid = probe_pid(native)
      owner = Agent.get(pid, & &1)
      send(owner, {:probe_turn, pid, prompt})
      if Process.get(:probe_raise), do: raise("probe raised")
      if payload = Process.get(:probe_throw), do: throw(payload)
      Process.get(:probe_turn_result, {:ok, %{session_id: "probe-session"}})
    end

    @impl true
    def interrupt(pid, _reason), do: stop_session(pid)

    @impl true
    def stop_session(native) do
      pid = probe_pid(native)

      if Process.alive?(pid) do
        send(Agent.get(pid, & &1), {:probe_stopped, pid})
        Agent.stop(pid)
      end

      if Process.get(:probe_cleanup_raise), do: raise("cleanup raised with sensitive text")
      Process.get(:probe_cleanup_result, :ok)
    end

    defp probe_pid(%{pid: pid}), do: pid
    defp probe_pid(pid), do: pid
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
    assert_received {:probe_attempt_allowance, 1}
    assert_received {:probe_stopped, ^pid}
    refute Process.alive?(pid)
    refute_received {:probe_started, _}
  end

  test "native allowance yields after one turn and bounded cleanup even when cleanup is unconfirmed", ctx do
    Process.put(:probe_turn_result, {:error, :agent_turn_limit})
    Process.put(:probe_cleanup_result, {:error, :cleanup_unconfirmed})

    log =
      capture_log(fn ->
        assert :ok =
                 AgentRunner.run(ctx.issue, nil,
                   coding_agent_adapter: Probe,
                   issue_state_fetcher: fn _ -> flunk("exhausted native session must not be reused") end
                 )
      end)

    assert log =~ "Native turn allowance reached"
    assert_received {:probe_started, pid}
    assert_received {:probe_turn, ^pid, _prompt}
    assert_received {:probe_stopped, ^pid}
    refute_received {:probe_turn, _, _}
    refute_received {:probe_started, _}
    assert File.read!(Path.join([ctx.root, "ADAPTER-1", "after"])) == "after"
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

    log =
      capture_log(fn ->
        assert {:agent_blocked, "protocol_error"} = catch_exit(AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe))
      end)

    assert log =~ "primary={:agent_blocked, :protocol_error} cleanup=:cleanup_unconfirmed"
    assert log =~ "issue_id=adapter-one"

    assert_received {:probe_started, pid}
    assert_received {:probe_stopped, ^pid}
    refute_received {:probe_stopped, _}
    assert File.read!(Path.join([ctx.root, "ADAPTER-1", "after"])) == "after"
  end

  test "cleanup failure retains each bounded turn cause as the primary outcome", ctx do
    for cause <- [:claude_native_eof, :claude_native_event_error, :claude_turn_timeout] do
      Process.put(:probe_turn_result, {:error, cause})
      Process.put(:probe_cleanup_result, {:error, :cleanup_unconfirmed})

      log =
        capture_log(fn ->
          assert_raise RuntimeError, ~r/#{cause}/, fn -> AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe) end
        end)

      assert log =~ "primary=#{inspect(cause)} cleanup=:cleanup_unconfirmed"
    end
  end

  test "cleanup failure preserves successful primary outcome for ordinary continuation", ctx do
    Process.put(:probe_cleanup_result, {:error, :cleanup_unconfirmed})

    log =
      capture_log(fn ->
        assert :ok = AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe, issue_state_fetcher: fn _ -> {:ok, []} end)
      end)

    assert log =~ "primary=:completed cleanup=:cleanup_unconfirmed"
  end

  test "raised primary and cleanup exceptions retain separate bounded causes", ctx do
    Process.put(:probe_raise, true)
    Process.put(:probe_cleanup_raise, true)

    log =
      capture_log(fn ->
        assert_raise RuntimeError, "probe raised", fn -> AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe) end
      end)

    assert log =~ "primary={:error, RuntimeError} cleanup=:cleanup_failed"
    refute log =~ "sensitive"

    assert_received {:probe_stopped, _}
    refute_received {:probe_stopped, _}
  end

  test "unknown arbitrary native error payload is not exposed by cleanup diagnostics", ctx do
    Process.put(:probe_turn_result, {:error, {:agent_blocked, String.duplicate("sensitive", 1_000)}})
    Process.put(:probe_cleanup_result, {:error, :cleanup_unconfirmed})

    log =
      capture_log(fn ->
        assert {:agent_blocked, kind} = catch_exit(AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe))
        assert kind == String.duplicate("sensitive", 1_000)
      end)

    assert log =~ "primary={:agent_blocked, :unknown}"
    refute log =~ "sensitive"
  end

  test "thrown arbitrary struct-like map is not exposed by cleanup diagnostics", ctx do
    Process.put(:probe_throw, %{__struct__: String.duplicate("secret", 1_000)})
    Process.put(:probe_cleanup_result, {:error, :cleanup_unconfirmed})

    log =
      capture_log(fn ->
        assert %{__struct__: payload} = catch_throw(AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe))
        assert payload == String.duplicate("secret", 1_000)
      end)

    assert log =~ "primary=:throw cleanup=:cleanup_unconfirmed"
    refute log =~ "secret"
  end

  test "cleanup diagnostics preserve bounded captured session correlation without exposing malformed identities", ctx do
    for {context, expected} <- [
          {%{id: "native-session"}, "native-session"},
          {%{thread_id: "codex-thread"}, "codex-thread"},
          {%{id: "secret-canary payload"}, "n/a"},
          {%{id: String.duplicate("secret-canary", 100)}, "n/a"},
          {%{id: 42}, "n/a"}
        ] do
      Process.put(:probe_native_context, context)
      Process.put(:probe_raise, true)
      Process.put(:probe_cleanup_result, {:error, :cleanup_unconfirmed})

      log =
        capture_log(fn ->
          assert_raise RuntimeError, "probe raised", fn -> AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe) end
        end)

      assert log =~ "issue_id=adapter-one issue_identifier=ADAPTER-1 session_id=#{expected}"
      assert log =~ "primary={:error, RuntimeError} cleanup=:cleanup_unconfirmed"
      refute log =~ "secret-canary"
    end
  end

  test "actual pause outcomes are not converted to retry errors by cleanup failure", ctx do
    Process.put(:probe_cleanup_result, {:error, :cleanup_unconfirmed})

    for kind <- [:auth_required, :quota_exhausted, :policy_blocked] do
      reason = {:agent_pause, kind, %{profile: String.duplicate("a", 64)}}
      Process.put(:probe_turn_result, {:error, reason})

      log =
        capture_log(fn ->
          assert ^reason = catch_exit(AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe))
        end)

      assert log =~ "primary={:agent_pause, #{inspect(kind)}} cleanup=:cleanup_unconfirmed"
    end
  end

  test "startup failure still runs after hook without inventing cleanup", ctx do
    Process.put(:probe_start_failure, true)
    assert_raise RuntimeError, ~r/probe_startup_failure/, fn -> AgentRunner.run(ctx.issue, nil, coding_agent_adapter: Probe) end
    refute_received {:probe_started, _}
    refute_received {:probe_stopped, _}
    assert File.read!(Path.join([ctx.root, "ADAPTER-1", "after"])) == "after"
  end
end
