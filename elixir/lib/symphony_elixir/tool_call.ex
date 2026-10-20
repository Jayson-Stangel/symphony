defmodule SymphonyElixir.ToolCall do
  @moduledoc "Shared tracker outcome handling and redacted, durable tool-call diagnostics."
  require Logger
  alias SymphonyElixir.{AgentPause, Linear.Operation}

  @statuses [400, 401, 403, 404, 405, 409, 410, 412, 413, 415, 422, 429]
  @reasons ~w(local_validation missing_auth response graphql_error http_error transport_error exception response_too_large reply_failed ack_missing state_write_failed existing_pause other)
  @limit 524_288

  @spec run((-> map()), String.t(), term(), keyword()) :: {:ok, map(), map()} | {:error, atom(), map()}
  def run(execute, tool, arguments, opts) do
    context = context(tool, arguments, opts)

    case permitted(context) do
      :ok ->
        case begin_call(context) do
          :ok -> receive_result(context, invoke(execute), arguments)
          _ -> {:error, :policy_blocked, report(context, "blocked", %{"reason" => "state_write_failed"})}
        end

      {:error, kind} ->
        {:error, kind, report(context, "blocked", %{"reason" => "existing_pause"})}
    end
  end

  defp permitted(context) do
    case AgentPause.current() do
      :ready -> :ok
      {_, %{"kind" => kind, "profile" => profile}} when kind in ["operator_recheck_inflight", "probe_inflight"] and profile == context.profile -> :ok
      {_, %{"kind" => "tool_outcome_uncertain"}} -> {:error, :tool_outcome_uncertain}
      _ -> {:error, :policy_blocked}
    end
  end

  defp context(tool, arguments, opts) do
    linear = tool == "linear_graphql"
    operation = if linear, do: Operation.describe(arguments), else: %{"operation" => "unknown"}
    provider = if Keyword.fetch!(opts, :provider) == :claude, do: "claude", else: "codex"

    metadata =
      Map.merge(operation, %{
        "provider" => provider,
        "started_at" => DateTime.to_iso8601(DateTime.utc_now()),
        "issue_identifier" => Keyword.get(opts, :issue_identifier),
        "session_id" => Keyword.get(opts, :session_id),
        "thread_id" => Keyword.get(opts, :thread_id)
      })

    %{
      id: Keyword.fetch!(opts, :tool_id),
      profile: Keyword.fetch!(opts, :profile),
      issue: Keyword.fetch!(opts, :issue_id),
      tool: if(linear, do: "linear_graphql", else: "tracker_tool"),
      durable: operation["operation"] not in ["query", "local"],
      metadata: metadata,
      started: System.monotonic_time(:millisecond)
    }
  end

  defp begin_call(%{durable: false} = context) do
    report(context, "started", %{})
    :ok
  end

  defp begin_call(context) do
    AgentPause.register_tool(context.id)
    result = AgentPause.begin_tool(context.profile, context.issue, context.id, context.tool, diagnostics(context, "started", %{}))
    if result != :ok, do: AgentPause.unregister_tool(context.id)
    if result == :ok, do: report(context, "started", %{})
    result
  end

  defp invoke(execute) do
    execute.()
  rescue
    _ -> :exception
  catch
    _, _ -> :exception
  end

  defp receive_result(context, result, arguments) do
    result = if result == :exception and context.metadata["operation"] == "query", do: failed_read(), else: result
    details = result_details(result)
    context = report(context, "response_received", details)

    cond do
      not safe_result?(result, arguments, context) -> uncertain(context, details)
      result_size(result) > @limit and context.durable -> uncertain(context, %{"reason" => "response_too_large"})
      result_size(result) > @limit -> {:ok, oversized_read(), context}
      true -> safe_response(context, result)
    end
  end

  defp safe_response(context, %{"execution" => %{"certainty" => certainty}} = result) when certainty in ["read_only", "not_executed"] do
    case release(context) do
      :ok -> {:ok, result, %{context | durable: false}}
      _ -> uncertain(context, %{"reason" => "state_write_failed"})
    end
  end

  defp safe_response(context, result), do: {:ok, result, context}

  defp safe_result?(%{"execution" => %{"certainty" => certainty}}, _arguments, _context),
    do: certainty in ["confirmed", "read_only", "not_executed"]

  defp safe_result?(%{"success" => success, "output" => output}, _args, %{metadata: %{"operation" => "query"}}),
    do: is_boolean(success) and is_binary(output)

  defp safe_result?(%{"success" => true}, _args, _context), do: true
  defp safe_result?(%{"success" => false}, %{"method" => method}, _context) when method in ["GET", "get"], do: true

  defp safe_result?(%{"success" => false, "output" => output}, _args, _context) when is_binary(output) do
    case Jason.decode(output) do
      {:ok, %{"status" => status}} -> status in @statuses
      {:ok, %{"error" => %{"status" => status}} = value} when map_size(value) == 1 -> status in @statuses
      _ -> false
    end
  end

  defp safe_result?(_result, _args, _context), do: false
  defp result_size(%{"output" => output}) when is_binary(output), do: byte_size(output)
  defp result_size(_result), do: @limit + 1

  defp oversized_read do
    output = "Tracker response is too large. Narrow the query selection or page size before trying again."
    %{"success" => false, "output" => output, "contentItems" => [%{"type" => "inputText", "text" => output}]}
  end

  defp failed_read do
    %{"success" => false, "output" => "Tracker read failed before a usable response. Retry the read after checking the request.", "execution" => %{"certainty" => "read_only", "reason" => "exception"}}
  end

  defp result_details(%{"execution" => execution, "output" => output}) do
    details = if execution["reason"] in @reasons, do: %{"reason" => execution["reason"]}, else: %{}
    status = execution["http_status"]
    details = if is_integer(status) and status in 100..599, do: Map.put(details, "http_status", status), else: details
    details = Map.put(details, "graphql_codes", graphql_codes(output))
    kind = execution["graphql_result"]
    details = if kind in ~w(request_error execution_error success malformed), do: Map.put(details, "graphql_result", kind), else: details
    transport = execution["transport_reason"]
    if transport in ~w(timeout closed econnrefused econnreset nxdomain enetunreach ehostunreach other), do: Map.put(details, "transport_reason", transport), else: details
  end

  defp result_details(:exception), do: %{"reason" => "exception"}
  defp result_details(_result), do: %{}

  defp graphql_codes(output) do
    case Jason.decode(output) do
      {:ok, response} ->
        Operation.error_codes(response)

      _ ->
        []
    end
  end

  @spec accept(map()) :: :ok | {:error, term()}
  def accept(context) do
    with :ok <- release(context) do
      report(%{context | durable: false}, "accepted", %{})
      :ok
    end
  end

  @spec fail(map(), String.t()) :: {:error, atom(), map()}
  def fail(context, reason), do: uncertain(context, %{"reason" => reason})

  defp uncertain(context, details) do
    details = if details["reason"] in @reasons, do: details, else: Map.put(details, "reason", "other")
    context = report(context, "uncertain", details)
    kind = if context.durable, do: :tool_outcome_uncertain, else: :protocol_error
    AgentPause.record(kind, %{profile: context.profile})
    {:error, kind, context}
  end

  defp release(%{durable: false}), do: :ok

  defp release(context) do
    with :ok <- AgentPause.finish_tool(context.id), do: AgentPause.unregister_tool(context.id)
  end

  defp report(context, phase, details) do
    metadata = diagnostics(context, phase, details)

    Logger.info(
      "Tracker tool outcome issue_id=#{context.issue} issue_identifier=#{context.metadata["issue_identifier"]} tool_id=#{context.id} diagnostics=#{Jason.encode!(Map.merge(metadata, %{"tool_id" => context.id, "issue_id" => context.issue, "tool" => context.tool}))}"
    )

    if context.durable, do: AgentPause.annotate_tool(context.id, metadata)
    %{context | metadata: metadata}
  end

  defp diagnostics(context, phase, details) do
    context.metadata
    |> Map.merge(details)
    |> Map.merge(%{"phase" => phase, "elapsed_ms" => max(0, System.monotonic_time(:millisecond) - context.started)})
  end
end
