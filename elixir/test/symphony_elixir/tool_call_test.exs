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

  test "request fingerprints correlate documents without parsing or inferring execution" do
    for document <- [@query, @mutation, "# mutation\nquery A { viewer { id } }", "query { issue(id: \"mutation { secret }\") { id } }", "not GraphQL"] do
      expected = Base.encode16(:crypto.hash(:sha256, document), case: :lower)
      assert Operation.describe(document) == %{"document_sha256" => expected}
      assert Operation.describe(%{query: "  " <> document <> "  "}) == %{"document_sha256" => expected}
    end

    for arguments <- [nil, false, %{}, " ", %{query: 123}] do
      assert Operation.describe(arguments) == %{}
    end

    errors = [nil, %{"extensions" => %{"code" => "secret-canary"}}, %{"extensions" => %{"code" => "INPUT_ERROR"}}]
    assert Operation.error_codes(%{"errors" => errors}) == ["INPUT_ERROR"]
    assert Operation.error_codes(%{"errors" => "malformed"}) == []
    assert Operation.transport_reason({:linear_api_request, %{reason: :timeout}}) == "timeout"
    assert Operation.transport_reason("secret-canary") == "other"
  end

  test "both providers return all tracker outcomes and permit a reconciliation read", ctx do
    responses = [
      {:ok, %{"data" => nil, "errors" => [%{"message" => "secret-canary", "extensions" => %{"code" => "INVALID_INPUT"}}]}},
      {:ok, %{"data" => %{"firstWrite" => %{"success" => true}}, "errors" => [%{"message" => "second failed"}]}},
      {:ok, %{"errors" => [%{"message" => "unknown field"}]}},
      {:ok, %{"errors" => [nil]}},
      {:error, {:linear_api_request, :missing_linear_api_token}},
      {:error, {:linear_api_status, 400}},
      {:error, {:linear_api_status, 503}},
      {:error, {:linear_api_request, %{reason: :timeout, secret: "secret-canary"}}}
    ]

    for provider <- [:codex, :claude], response <- responses do
      log =
        capture_log(fn ->
          assert {:ok, %{"success" => false, "output" => output}, context} = call(@mutation, response, provider)
          assert is_binary(output)
          assert :ok = ToolCall.accept(context)
          assert {:ok, %{"success" => true}, read} = call(@query, {:ok, %{"data" => %{}}}, provider)
          assert :ok = ToolCall.accept(read)
          assert :ready = Task.async(fn -> AgentPause.current() end) |> Task.await()
        end)

      assert log =~ "document_sha256"
      refute log =~ "secret-canary"
    end

    refute File.exists?(ctx.root)
  end

  test "diagnostics retain bounded correlation without certainty or response reparsing" do
    response = %{"data" => nil, "errors" => [%{"message" => "secret-canary", "extensions" => %{"code" => "INVALID_INPUT"}}]}
    result = AgentTool.execute("linear_graphql", %{"query" => @mutation}, linear_client: fn _, _, _ -> {:ok, response} end)
    assert result["execution"] == %{"http_status" => 200, "graphql_codes" => ["INVALID_INPUT"], "reason" => "graphql_error"}

    log =
      capture_log(fn ->
        assert {:ok, %{"output" => "not-json-secret-canary"}, context} =
                 run(
                   %{"query" => @mutation},
                   fn ->
                     %{result | "output" => "not-json-secret-canary", "execution" => Map.put(result["execution"], "graphql_codes", ["INVALID_INPUT", "secret-canary"])}
                   end,
                   :claude
                 )

        assert context.metadata["graphql_codes"] == ["INVALID_INPUT"]
        assert context.metadata["provider"] == "claude"
        assert context.metadata["phase"] == "response_received"
        assert context.id == "call"
        assert context.issue == "issue"
      end)

    assert log =~ "INVALID_INPUT"
    assert log =~ "document_sha256"
    refute log =~ "secret-canary"
    refute log =~ "certainty"
    refute log =~ "graphql_result"
    refute log =~ "\"operation\""
  end

  test "original errors reach the agent while diagnostics omit response bodies" do
    response = %{"data" => %{"issueUpdate" => nil}, "errors" => [%{"message" => "secret-canary: target invalid"}]}

    log =
      capture_log(fn ->
        assert {:ok, %{"output" => output}, _} = call(@mutation, {:ok, response})
        assert output =~ "secret-canary: target invalid"
      end)

    refute log =~ "secret-canary"
  end

  test "exceptions, malformed envelopes and oversized results become bounded actionable errors", ctx do
    for provider <- [:codex, :claude],
        execute <- [
          fn -> raise "secret-canary" end,
          fn -> throw(:secret_canary) end,
          fn -> nil end,
          fn -> %{"success" => "invalid", "output" => "bad"} end,
          fn -> %{"success" => true, "output" => String.duplicate("x", 524_289)} end
        ] do
      log =
        capture_log(fn ->
          assert {:ok, %{"success" => false, "output" => output}, context} = run(%{"query" => @mutation}, execute, provider)
          assert byte_size(output) <= 524_288
          assert is_binary(output)
          assert {:error, :tracker_delivery_failed, _} = ToolCall.fail(context, "ack_missing")
          assert :ready = AgentPause.current()
        end)

      refute log =~ "secret-canary"
    end

    refute File.exists?(ctx.root)
  end

  test "escaped and invalid text returns a deliverable recovery response" do
    for output <- [String.duplicate("\"", 524_288), String.duplicate(<<0>>, 100_000), <<255>>] do
      assert {:ok, result, _context} = run(%{}, fn -> %{"success" => true, "output" => output} end, :claude)
      assert result["success"] == false
      assert result["output"] =~ "read back"
      frame = %{v: 1, type: "tool_result", session_id: "session", request_id: "request", tool_id: "tool", result: Map.take(result, ["success", "output"])}
      assert byte_size(Jason.encode!(frame)) < 1_048_576
      assert :ready = AgentPause.current()
    end
  end

  test "read, local and legacy tool failures remain deliverable" do
    for {arguments, tool} <- [{%{"query" => @query}, "linear_graphql"}, {%{}, "linear_graphql"}, {%{"method" => "PATCH"}, "legacy"}] do
      execute = fn -> %{"success" => false, "output" => "original failure"} end
      assert {:ok, %{"success" => false, "output" => output}, context} = run(arguments, execute, :codex, tool)
      assert output =~ "original failure"
      assert :ok = ToolCall.accept(context)
    end

    assert {:ok, %{"success" => true}, context} = call(@mutation, {:ok, %{"data" => %{}}})
    assert :ok = ToolCall.accept(context)
    assert {:error, :tracker_delivery_failed, _} = ToolCall.fail(context, "secret-canary")
    assert :ready = AgentPause.current()
  end

  defp call(query, response, provider \\ :codex) do
    arguments = %{"query" => query}
    run(arguments, fn -> AgentTool.execute("linear_graphql", arguments, linear_client: fn _, _, _ -> response end) end, provider)
  end

  defp run(arguments, execute, provider, tool \\ "linear_graphql") do
    ToolCall.run(execute, tool, arguments, issue_id: "issue", tool_id: "call", provider: provider, profile: AgentPause.codex_profile())
  end
end
