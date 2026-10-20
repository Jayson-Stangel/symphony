defmodule SymphonyElixir.ToolCallTest do
  use ExUnit.Case, async: false
  import ExUnit.CaptureLog
  alias SymphonyElixir.{AgentPause, Linear.AgentTool, Linear.Operation, ToolCall}
  @query "query { viewer { id } }"
  @mutation ~s|mutation { issueUpdate(id: "one", input: {title: "secret-canary"}) { success } }|

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-tool-outcome-#{System.unique_integer([:positive])}")
    previous = Application.fetch_env(:symphony_elixir, :agent_state_root)
    Application.put_env(:symphony_elixir, :agent_state_root, root)

    on_exit(fn ->
      case previous do
        {:ok, value} -> Application.put_env(:symphony_elixir, :agent_state_root, value)
        :error -> Application.delete_env(:symphony_elixir, :agent_state_root)
      end

      File.rm_rf!(root)
    end)

    %{root: root}
  end

  test "classification parses documents rather than trusting prefixes or embedded keywords" do
    for document <- [
          @query,
          "{ viewer { id } }",
          "# mutation\nquery A { viewer { id } }",
          "fragment F on User { id } query { viewer { ...F } }",
          "query { issue(id: \"mutation { secret }\") { id } }",
          "query { issue(id: \"\"\"mutation { secret }\"\"\") { id } }",
          "query A { viewer { id } } query B { viewer { id } }"
        ] do
      assert %{"operation" => "query", "document_sha256" => hash} = Operation.describe(document)
      assert byte_size(hash) == 64
    end

    for {document, kind} <- [
          {@mutation, "mutation"},
          {"subscription { updates { id } }", "subscription"},
          {"query { viewer { id } } mutation { x }", "unknown"},
          {"fragment F on User { id }", "unknown"},
          {"not GraphQL", "unknown"},
          {"type User { id: ID }", "unknown"}
        ] do
      assert Operation.describe(%{query: document})["operation"] == kind
    end

    for arguments <- [nil, false, %{}, " ", %{query: 123}] do
      assert Operation.describe(arguments) == %{"operation" => "local"}
    end

    errors = [nil, %{"extensions" => %{"code" => "secret-canary"}}, %{"extensions" => %{"code" => "INPUT_ERROR"}}]
    assert Operation.error_codes(%{"errors" => errors}) == ["INPUT_ERROR"]
    assert Operation.error_codes(%{"errors" => "malformed"}) == []
    assert Operation.transport_reason({:linear_api_request, %{reason: :timeout}}) == "timeout"
    assert Operation.transport_reason("secret-canary") == "other"
  end

  test "both providers return read failures and local rejections without uncertain-write state", ctx do
    failures = [
      {:ok, %{"data" => nil, "errors" => [%{"extensions" => %{"code" => "INPUT_ERROR"}}]}},
      {:error, {:linear_api_status, 503}},
      {:error, {:linear_api_request, :timeout}}
    ]

    for provider <- [:codex, :claude], response <- failures do
      assert {:ok, %{"success" => false}, context} = call(@query, response, provider)
      assert :ok = ToolCall.accept(context)
      assert :ready = AgentPause.current()
    end

    for arguments <- [%{}, %{"query" => @mutation, "variables" => false}, %{"query" => @mutation, "variables" => []}] do
      assert {:ok, %{"execution" => %{"certainty" => "not_executed"}}, context} =
               run(arguments, fn ->
                 AgentTool.execute("linear_graphql", arguments, linear_client: fn _, _, _ -> flunk("local rejection reached network") end)
               end)

      assert :ok = ToolCall.accept(context)
    end

    reasons = [
      :missing_linear_api_token,
      {:linear_api_request, :missing_linear_api_token},
      {:linear_api_status, 400},
      {:linear_api_status, 422},
      {:linear_api_status, 429}
    ]

    for reason <- reasons do
      assert {:ok, _, context} = call(@mutation, {:error, reason})
      assert :ok = ToolCall.accept(context)
    end

    refute File.exists?(Path.join(ctx.root, "claude-pause.json"))
    assert {:ok, _, context} = run(%{"query" => @query}, fn -> raise "secret-canary" end)
    assert :ok = ToolCall.accept(context)
  end

  test "both providers can correct request errors without leaving a restart blocker", ctx do
    errors = [
      %{"errors" => [%{"message" => "secret-canary: variable coercion failed"}]},
      %{"errors" => [%{"message" => "validation failed", "extensions" => %{"code" => "GRAPHQL_VALIDATION_FAILED"}}], "data" => nil},
      %{"errors" => [%{"message" => "parse failed", "extensions" => %{"code" => "GRAPHQL_PARSE_FAILED"}}]}
    ]

    for provider <- [:codex, :claude], response <- errors do
      log =
        capture_log(fn ->
          assert {:ok, %{"success" => false, "execution" => %{"certainty" => "not_executed"}}, rejected} = call(@mutation, {:ok, response}, provider)
          refute rejected.durable
          assert Path.wildcard(Path.join(ctx.root, "claude-tools/*.json")) == []
          assert :ready = Task.async(fn -> AgentPause.current() end) |> Task.await()
          assert :ok = ToolCall.accept(rejected)
          assert {:ok, %{"success" => true}, corrected} = call(@mutation, {:ok, %{"data" => %{"issueUpdate" => %{"success" => true}}}}, provider)
          assert :ok = ToolCall.accept(corrected)
        end)

      refute log =~ "secret-canary"
      assert log =~ ~s("graphql_result":"request_error")
    end

    assert {:ok, _, rejected} = call(@mutation, {:ok, hd(errors)})
    assert {:error, :protocol_error, _} = ToolCall.fail(rejected, "ack_missing")
    assert Path.wildcard(Path.join(ctx.root, "claude-tools/*.json")) == []
  end

  test "request-error detection never clears partial execution or malformed errors", ctx do
    validation = %{"message" => "secret-canary", "extensions" => %{"code" => "GRAPHQL_VALIDATION_FAILED"}}

    for response <- [
          %{"data" => nil, "errors" => [%{"message" => "secret-canary", "extensions" => %{"code" => "INVALID_INPUT"}, "path" => ["issueUpdate"]}]},
          %{"data" => %{"firstWrite" => %{"success" => true}}, "errors" => [validation]},
          %{"data" => nil, "errors" => [validation, %{"message" => "other failure"}]},
          %{"errors" => [Map.put(validation, "path", ["issueUpdate"])]},
          %{"errors" => [Map.put(validation, "path", nil)]},
          %{"errors" => [nil]},
          %{"errors" => [%{"message" => 123}]},
          %{"errors" => []},
          %{"errors" => "malformed"},
          %{"data" => false, "errors" => [validation]}
        ] do
      File.rm_rf!(ctx.root)
      assert {:error, :tool_outcome_uncertain, _} = call(@mutation, {:ok, response})
      assert {:paused, _} = Task.async(fn -> AgentPause.current() end) |> Task.await()
      assert {:error, :tool_reconciliation_required} = AgentPause.explicit_recheck(AgentPause.codex_profile())
    end
  end

  test "both providers retain ambiguous writes with safe diagnostics", ctx do
    for provider <- [:codex, :claude],
        response <- [
          {:ok, %{"data" => nil, "errors" => [%{"message" => "secret-canary", "extensions" => %{"code" => "INPUT_ERROR"}}]}},
          {:ok, %{"data" => %{}, "errors" => "malformed"}},
          {:ok, %{"data" => nil}},
          {:error, {:linear_api_status, 503}},
          {:error, {:linear_api_request, %{reason: :timeout, secret: "secret-canary"}}}
        ] do
      File.rm_rf!(ctx.root)
      log = capture_log(fn -> assert {:error, :tool_outcome_uncertain, _} = call(@mutation, response, provider) end)
      assert log =~ "uncertain"
      assert log =~ "document_sha256"
      refute log =~ "secret-canary"
      assert {:paused, %{"kind" => "tool_outcome_uncertain"}} = Task.async(fn -> AgentPause.current() end) |> Task.await()
      marker = File.read!(Path.join(ctx.root, "claude-tools/call.json"))
      refute marker =~ "secret-canary"
      assert marker =~ "uncertain"
      assert {:error, :tool_reconciliation_required} = AgentPause.explicit_recheck(AgentPause.codex_profile())
      assert {:error, :tool_outcome_uncertain, _} = call(@query, {:ok, %{"data" => %{}}})
    end

    File.rm_rf!(ctx.root)
    assert {:error, :tool_outcome_uncertain, _} = run(%{"query" => @mutation}, fn -> throw(:lost) end)
    File.rm_rf!(ctx.root)
    assert {:ok, _, context} = call(@mutation, {:ok, %{"data" => %{"issueUpdate" => %{"success" => true}}}})
    assert :ready = AgentPause.current()
    assert {:error, :tool_outcome_uncertain, _} = ToolCall.fail(context, "ack_missing")
    assert File.read!(Path.join(ctx.root, "claude-tools/call.json")) =~ "ack_missing"
  end

  test "active writes coexist, but owner death exposes the durable blocker", ctx do
    parent = self()

    pid =
      spawn(fn ->
        send(parent, {:write_waiting, call(@mutation, {:ok, %{"data" => %{}}})})

        receive do
          :stop -> :ok
        end
      end)

    monitor = Process.monitor(pid)
    assert_receive {:write_waiting, {:ok, _, context}}
    assert :ready = AgentPause.current()
    assert {:error, :tool_reconciliation_required} = AgentPause.explicit_recheck(AgentPause.codex_profile())
    assert {:ok, _, read_context} = call(@query, {:ok, %{"data" => %{}}})
    assert :ok = ToolCall.accept(read_context)
    send(pid, :stop)
    assert_receive {:DOWN, ^monitor, :process, ^pid, :normal}
    assert {:paused, %{"kind" => "tool_outcome_uncertain"}} = AgentPause.current()
    assert :ok = AgentPause.annotate_tool(context.id, %{"phase" => "owner_lost"})
    assert :ok = AgentPause.explicit_recheck(AgentPause.codex_profile(), confirm_tools_reconciled: true)
    assert :ready = AgentPause.current()
    assert :ok = AgentPause.begin_tool(AgentPause.codex_profile(), "issue", "legacy", "linear_graphql")
    assert {:paused, _} = AgentPause.current()
    assert File.regular?(Path.join(ctx.root, "claude-tools/legacy.json"))
  end

  test "reply limits, protocol failures, legacy envelopes and filesystem errors fail safely", ctx do
    huge = String.duplicate("x", 524_289)
    assert {:ok, %{"success" => false}, read} = run(%{"query" => @query}, fn -> %{"success" => true, "output" => huge} end)
    assert {:error, :protocol_error, _} = ToolCall.fail(read, "secret-canary")
    assert {:error, :policy_blocked, _} = call(@query, {:ok, %{"data" => %{}}})
    assert :ok = AgentPause.explicit_recheck(AgentPause.codex_profile())
    assert :ok = AgentPause.begin_turn(AgentPause.codex_profile())
    assert {:ok, _, context} = call(@query, {:ok, %{"data" => %{}}})
    assert :ok = ToolCall.accept(context)
    assert :ok = AgentPause.complete_probe(AgentPause.codex_profile())
    assert {:error, :tool_outcome_uncertain, _} = run(%{"query" => @mutation}, fn -> %{"success" => true, "output" => huge} end)
    File.rm_rf!(ctx.root)

    for {args, result} <- [
          {%{"method" => "GET"}, %{"success" => false, "output" => "read error"}},
          {%{}, %{"success" => false, "output" => ~s({"status":422})}},
          {%{}, %{"success" => false, "output" => ~s({"error":{"status":400}})}}
        ] do
      assert {:ok, _, context} = run(args, fn -> result end, :codex, "legacy")
      assert :ok = ToolCall.accept(context)
    end

    for result <- [%{"success" => false, "output" => "unrecognized"}, %{"success" => false, "output" => ~s({"status":503})}, %{"success" => true}, nil] do
      assert {:error, :tool_outcome_uncertain, _} = run(%{}, fn -> result end, :codex, "legacy")
      File.rm_rf!(ctx.root)
    end

    assert {:ok, _, context} = call(@mutation, {:ok, %{"data" => %{}}})
    assert :ok = AgentPause.finish_tool(context.id)
    assert {:error, :enoent} = ToolCall.accept(context)
    File.rm_rf!(ctx.root)
    File.mkdir_p!(ctx.root)
    File.write!(Path.join(ctx.root, "claude-tools"), "blocked")
    assert {:error, :policy_blocked, _} = run(%{"query" => @mutation}, fn -> flunk("unsafe state dispatched") end)
    File.rm_rf!(ctx.root)
    File.mkdir_p!(ctx.root)
    File.chmod!(ctx.root, 0o500)

    try do
      assert {:error, :policy_blocked, _} = run(%{"query" => @mutation}, fn -> flunk("intent not persisted") end)
    after
      File.chmod!(ctx.root, 0o700)
    end

    File.rm_rf!(ctx.root)

    assert {:error, :tool_outcome_uncertain, _} =
             run(%{"query" => @mutation}, fn ->
               AgentPause.finish_tool("call")
               AgentTool.execute("linear_graphql", %{"query" => @query}, linear_client: fn _, _, _ -> {:ok, %{"data" => %{}}} end)
             end)

    assert {:error, _} = AgentPause.annotate_tool("missing", %{})
    assert {:error, :invalid_tool_id} = AgentPause.annotate_tool("../escape", %{})
  end

  defp call(query, response, provider \\ :codex) do
    arguments = %{"query" => query}
    run(arguments, fn -> AgentTool.execute("linear_graphql", arguments, linear_client: fn _, _, _ -> response end) end, provider)
  end

  defp run(arguments, execute, provider \\ :codex, tool \\ "linear_graphql") do
    ToolCall.run(execute, tool, arguments, issue_id: "issue", tool_id: "call", provider: provider, profile: AgentPause.codex_profile())
  end
end
