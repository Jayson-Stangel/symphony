defmodule SymphonyElixir.LinearTransportTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixir.Linear.AgentTool

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

  test "Codex receives Linear mutation failures once per call without an account pause", ctx do
    query = "mutation { attachmentLinkGitHubPR(issueId: \"one\", url: \"https://github.com/example/repo/pull/132\") { success } }"

    for mode <- ["startup", "streaming"] do
      trace = configure_peer(ctx, mode, query)
      Process.put(:linear_calls, 0)

      executor = fn "linear_graphql", args ->
        count = Process.get(:linear_calls)
        Process.put(:linear_calls, count + 1)

        response =
          if count == 0 do
            %{"data" => nil, "errors" => [%{"message" => "invalid attachment", "extensions" => %{"code" => "INPUT_ERROR"}}]}
          else
            %{"data" => %{"attachmentLinkGitHubPR" => %{"success" => true}}}
          end

        AgentTool.execute("linear_graphql", args, linear_client: fn _, _, _ -> {:ok, response} end)
      end

      assert {:ok, session} = AppServer.start_session(ctx.workspace)

      try do
        assert {:ok, _} = AppServer.run_turn(session, "probe", ctx.issue, tool_executor: executor)
      after
        AppServer.stop_session(session)
      end

      replies = trace |> File.read!() |> String.split("\n", trim: true) |> Enum.map(&Jason.decode!/1) |> Enum.filter(&(&1["id"] in [101, 102]))
      assert [%{"result" => %{"success" => false} = rejected}, %{"result" => %{"success" => true}}] = replies
      assert Jason.decode!(rejected["output"])["errors"] |> hd() |> get_in(["extensions", "code"]) == "INPUT_ERROR"
      assert Enum.all?(replies, &(Map.keys(&1["result"]) |> Enum.sort() == ["contentItems", "output", "success"]))
      assert Process.get(:linear_calls) == 2
      refute File.exists?(Path.join(ctx.root, "state/claude-pause.json"))
      assert Path.wildcard(Path.join(ctx.root, "state/claude-tools/*.json")) == []
    end
  end

  test "Codex can run with retained legacy pause metadata without deleting reconciliation evidence", ctx do
    configure_peer(ctx, "streaming", "query { viewer { id } }")
    marker = Path.join(ctx.root, "state/claude-tools/legacy-write.json")
    File.mkdir_p!(Path.dirname(marker))
    original = Jason.encode!(%{version: 1, issue: ctx.issue.id, tool: "linear_graphql"})
    File.write!(marker, original)
    File.write!(Path.join(ctx.root, "state/claude-pause.json"), Jason.encode!(%{kind: "tool_outcome_uncertain"}))
    assert {:ok, session} = AppServer.start_session(ctx.workspace)

    try do
      executor = fn "linear_graphql", args ->
        AgentTool.execute("linear_graphql", args, linear_client: fn _, _, _ -> {:ok, %{"data" => %{}}} end)
      end

      assert {:ok, _} = AppServer.run_turn(session, "probe", ctx.issue, tool_executor: executor)
      assert File.read!(marker) == original
      assert Jason.decode!(File.read!(Path.join(ctx.root, "state/claude-pause.json")))["kind"] == "tool_outcome_uncertain"
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
