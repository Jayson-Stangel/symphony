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

  test "Codex receives correctable read and mutation request errors in startup and streaming paths", ctx do
    for mode <- ["startup", "streaming"], query <- ["query { viewer { id } }", "mutation { rejected } "] do
      trace = configure_peer(ctx, mode, query)
      Process.put(:linear_calls, 0)

      executor = fn "linear_graphql", arguments ->
        count = Process.get(:linear_calls)
        Process.put(:linear_calls, count + 1)
        response = if count == 0, do: %{"errors" => [%{"message" => "Unknown field rejected"}]}, else: %{"data" => %{"issue" => %{"id" => "one", "title" => "probe"}}}
        AgentTool.execute("linear_graphql", arguments, linear_client: fn _, _, _ -> {:ok, response} end)
      end

      assert {:ok, _} = AppServer.run(ctx.workspace, "probe", ctx.issue, tool_executor: executor)
      replies = trace |> File.read!() |> String.split("\n", trim: true) |> Enum.map(&Jason.decode!/1) |> Enum.filter(&(&1["id"] in [101, 102]))
      assert [%{"result" => %{"success" => false}}, %{"result" => %{"success" => true}}] = replies
      assert Enum.all?(replies, &(Map.keys(&1["result"]) |> Enum.sort() == ["contentItems", "output", "success"]))
      assert :ready = AgentPause.current()
    end
  end

  test "Codex sees uncertain write errors and reads back in the same turn", ctx do
    for mode <- ["startup", "streaming"] do
      trace = configure_peer(ctx, mode, "mutation { issueUpdate(id: \"one\", input: {title: \"probe\"}) {success} }")

      executor = fn "linear_graphql", args ->
        response =
          if String.starts_with?(args["query"], "mutation"),
            do: %{"data" => %{"issueUpdate" => nil}, "errors" => [%{"message" => "partial write"}]},
            else: %{"data" => %{"issue" => %{"id" => "one", "title" => "probe"}}}

        AgentTool.execute("linear_graphql", args, linear_client: fn _, _, _ -> {:ok, response} end)
      end

      assert {:ok, _} = AppServer.run(ctx.workspace, "probe", ctx.issue, tool_executor: executor)
      replies = trace |> File.read!() |> String.split("\n", trim: true) |> Enum.map(&Jason.decode!/1) |> Enum.filter(&(&1["id"] in [101, 102]))
      assert [%{"result" => %{"success" => false, "output" => error}}, %{"result" => %{"success" => true}}] = replies
      assert error =~ "partial write"
      assert %{"errors" => [%{"message" => "partial write"}]} = Jason.decode!(error)
      assert :ready = AgentPause.current()
      assert Path.wildcard(Path.join(ctx.root, "state/claude-tools/*.json")) == []
    end
  end

  test "Codex lost reply blocks only the affected issue", ctx do
    configure_peer(ctx, "streaming", "mutation { write }")
    assert {:ok, session} = AppServer.start_session(ctx.workspace)

    executor = fn "linear_graphql", args ->
      result = AgentTool.execute("linear_graphql", args, linear_client: fn _, _, _ -> {:ok, %{"data" => %{}}} end)
      Port.close(session.port)
      result
    end

    try do
      assert {:error, {:agent_blocked, :tracker_delivery_failed}} = Codex.run_turn(session, "probe", ctx.issue, tool_executor: executor)
      assert :ready = AgentPause.current()
      other_issue = %{ctx.issue | id: "other-issue", identifier: "PROBE-2"}
      configure_peer(ctx, "streaming", "query { viewer { id } }")
      assert {:ok, _} = AppServer.run(ctx.workspace, "other issue", other_issue, tool_executor: fn _, _ -> %{"success" => true, "output" => "readback"} end)
    after
      AppServer.stop_session(session)
    end
  end

  test "Codex turns ignore retained Claude auth and future quota pauses without changing evidence", ctx do
    for kind <- [:auth_required, :quota_exhausted] do
      File.rm_rf!(Path.join(ctx.root, "state"))
      assert :ok = AgentPause.record(kind, %{profile: String.duplicate("a", 64), resets_at: System.system_time(:second) + 600})
      path = Path.join(ctx.root, "state/claude-pause.json")
      evidence = File.read!(path)
      configure_peer(ctx, "streaming", "query { viewer { id } }")
      assert {:ok, session} = Codex.start_session(ctx.workspace, [])

      try do
        assert {:ok, _} = Codex.run_turn(session, "probe", ctx.issue, tool_executor: fn _, _ -> %{"success" => true, "output" => "readback"} end)
      after
        Codex.stop_session(session)
      end

      assert File.read!(path) == evidence
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
