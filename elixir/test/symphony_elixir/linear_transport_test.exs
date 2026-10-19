defmodule SymphonyElixir.LinearTransportTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixir.{AgentPause, CodingAgent.Codex, Linear.AgentTool}

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-linear-transport-#{System.unique_integer([:positive])}")
    workspace = Path.join(root, "workspaces/issue")
    File.mkdir_p!(workspace)
    previous = Application.fetch_env(:symphony_elixir, :agent_state_root)
    Application.put_env(:symphony_elixir, :agent_state_root, Path.join(root, "state"))

    on_exit(fn ->
      case previous do
        {:ok, value} -> Application.put_env(:symphony_elixir, :agent_state_root, value)
        :error -> Application.delete_env(:symphony_elixir, :agent_state_root)
      end

      File.rm_rf!(root)
    end)

    %{root: root, workspace: workspace, issue: %Issue{id: "linear-issue", identifier: "PROBE-1", title: "Linear outcome"}}
  end

  test "Codex receives a correctable read error and can issue a second call in startup and streaming paths", ctx do
    for mode <- ["startup", "streaming"] do
      trace = configure_peer(ctx, mode, "query { viewer { id } }")
      Process.put(:linear_calls, 0)

      executor = fn "linear_graphql", arguments ->
        count = Process.get(:linear_calls)
        Process.put(:linear_calls, count + 1)
        response = if count == 0, do: %{"data" => nil, "errors" => [%{"extensions" => %{"code" => "INPUT_ERROR"}}]}, else: %{"data" => %{"viewer" => %{"id" => "one"}}}
        AgentTool.execute("linear_graphql", arguments, linear_client: fn _, _, _ -> {:ok, response} end)
      end

      assert {:ok, _} = AppServer.run(ctx.workspace, "probe", ctx.issue, tool_executor: executor)
      replies = trace |> File.read!() |> String.split("\n", trim: true) |> Enum.map(&Jason.decode!/1) |> Enum.filter(&(&1["id"] in [101, 102]))
      assert [%{"result" => %{"success" => false}}, %{"result" => %{"success" => true}}] = replies
      assert Enum.all?(replies, &(Map.keys(&1["result"]) |> Enum.sort() == ["contentItems", "output", "success"]))
      assert :ready = AgentPause.current()
    end
  end

  test "Codex withholds uncertain mutation results, blocks subsequent turns, and requires reconciliation", ctx do
    for mode <- ["startup", "streaming"] do
      File.rm_rf!(Path.join(ctx.root, "state"))
      trace = configure_peer(ctx, mode, "mutation { issueUpdate(id: \"one\", input: {title: \"probe\"}) {success} }")

      executor = fn "linear_graphql", args ->
        AgentTool.execute("linear_graphql", args, linear_client: fn _, _, _ -> {:ok, %{"data" => %{"issueUpdate" => nil}, "errors" => [%{"message" => "partial write"}]}} end)
      end

      assert {:ok, session} = AppServer.start_session(ctx.workspace)

      try do
        assert {:error, {:agent_pause, :tool_outcome_uncertain, _}} = Codex.run_turn(session, "probe", ctx.issue, tool_executor: executor)
        refute File.read!(trace) =~ ~s("id":101)

        assert {:error, {:agent_pause, :tool_outcome_uncertain, _}} =
                 Codex.run_turn(session, "probe", ctx.issue, tool_executor: fn _, _ -> flunk("replayed uncertain write") end)

        assert {:error, :tool_reconciliation_required} = AgentPause.explicit_recheck(AgentPause.codex_profile())
        assert :ok = AgentPause.explicit_recheck(AgentPause.codex_profile(), confirm_tools_reconciled: true)
        assert {:probe, _} = AgentPause.current()
        assert :ok = AgentPause.begin_turn(AgentPause.codex_profile())
        assert :ok = Codex.interrupt(session, :issue_state_change)
        assert :ready = AgentPause.current()
      after
        AppServer.stop_session(session)
      end
    end
  end

  test "Codex lost reply keeps a confirmed mutation intent with delivery diagnostics", ctx do
    configure_peer(ctx, "streaming", "mutation { issueUpdate(id: \"one\", input: {title: \"probe\"}) {success} }")
    assert {:ok, session} = AppServer.start_session(ctx.workspace)

    executor = fn "linear_graphql", args ->
      result = AgentTool.execute("linear_graphql", args, linear_client: fn _, _, _ -> {:ok, %{"data" => %{}}} end)
      Port.close(session.port)
      result
    end

    try do
      assert {:error, {:agent_pause, :tool_outcome_uncertain, _}} = Codex.run_turn(session, "probe", ctx.issue, tool_executor: executor)
      [marker] = Path.wildcard(Path.join(ctx.root, "state/claude-tools/*.json"))
      assert marker |> File.read!() |> Jason.decode!() |> get_in(["diagnostics", "reason"]) == "reply_failed"
    after
      AppServer.stop_session(session)
    end
  end

  defp configure_peer(ctx, mode, query) do
    trace = Path.join(ctx.root, "#{mode}.jsonl")
    File.write!(trace, "")
    peer = Path.expand("../fixtures/linear-tool-peer.mjs", __DIR__)

    command =
      Enum.map_join([System.find_executable("node"), peer, mode, query, trace], " ", fn arg ->
        "'" <> String.replace(arg, "'", "'\\''") <> "'"
      end)

    write_workflow_file!(Workflow.workflow_file_path(), workspace_root: Path.join(ctx.root, "workspaces"), codex_command: command)
    trace
  end
end
