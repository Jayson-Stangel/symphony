defmodule SymphonyElixir.ToolCall do
  @moduledoc "Tracker result delivery and redacted diagnostics; agents own outcome reconciliation."
  require Logger
  alias SymphonyElixir.Linear.Operation

  @reasons ~w(local_validation missing_auth response graphql_error http_error transport_error exception response_too_large reply_failed ack_missing other)
  @limit 524_288

  @spec run((-> term()), String.t(), term(), keyword()) :: {:ok, map(), map()}
  def run(execute, tool, arguments, opts) do
    context = context(tool, arguments, opts)
    context = report(context, "started", %{})
    result = invoke(execute)
    context = report(context, "response_received", result_details(result))
    {:ok, deliverable_result(result), context}
  end

  defp context(tool, arguments, opts) do
    linear = tool == "linear_graphql"
    fingerprint = if linear, do: Operation.describe(arguments), else: %{}
    provider = if Keyword.fetch!(opts, :provider) == :claude, do: "claude", else: "codex"

    metadata =
      Map.merge(fingerprint, %{
        "provider" => provider,
        "started_at" => DateTime.to_iso8601(DateTime.utc_now()),
        "issue_identifier" => Keyword.get(opts, :issue_identifier),
        "session_id" => Keyword.get(opts, :session_id),
        "thread_id" => Keyword.get(opts, :thread_id)
      })

    %{
      id: Keyword.fetch!(opts, :tool_id),
      issue: Keyword.fetch!(opts, :issue_id),
      tool: if(linear, do: "linear_graphql", else: "tracker_tool"),
      metadata: metadata,
      started: System.monotonic_time(:millisecond)
    }
  end

  defp invoke(execute) do
    execute.()
  rescue
    _ -> :exception
  catch
    _, _ -> :exception
  end

  defp deliverable_result(result) do
    result = normalize_result(result)

    output =
      if deliverable_output?(result["output"]) do
        result["output"]
      else
        "Tracker response is too large or cannot be encoded. Narrow the selection or page size. A write may have executed; read back the affected targets before retrying."
      end

    success = result["success"] and output == result["output"]

    %{"success" => success, "output" => output, "contentItems" => [%{"type" => "inputText", "text" => output}]}
  end

  # Bound the JSON representation too: escaped text can exceed the helper frame
  # limit even when its raw byte count fits. Leave ample room for the envelope.
  defp deliverable_output?(output) do
    byte_size(output) <= @limit and String.valid?(output) and byte_size(Jason.encode!(output)) <= @limit
  end

  defp normalize_result(%{"success" => success, "output" => output} = result) when is_boolean(success) and is_binary(output), do: result

  defp normalize_result(_result) do
    %{"success" => false, "output" => "Tracker tool failed before a usable response was received. Check the request and read back the external state before retrying."}
  end

  defp result_details(%{"execution" => execution}) when is_map(execution) do
    details = if execution["reason"] in @reasons, do: %{"reason" => execution["reason"]}, else: %{}
    status = execution["http_status"]
    details = if is_integer(status) and status in 100..599, do: Map.put(details, "http_status", status), else: details
    details = Map.put(details, "graphql_codes", Operation.error_codes(execution["graphql_codes"]))
    transport = execution["transport_reason"]
    if transport in ~w(timeout closed econnrefused econnreset nxdomain enetunreach ehostunreach other), do: Map.put(details, "transport_reason", transport), else: details
  end

  defp result_details(:exception), do: %{"reason" => "exception"}
  defp result_details(_result), do: %{}

  @spec accept(map()) :: :ok
  def accept(context) do
    report(context, "accepted", %{})
    :ok
  end

  @spec fail(map(), String.t()) :: {:error, atom(), map()}
  def fail(context, reason) do
    {:error, :tracker_delivery_failed, report(context, "delivery_failed", %{"reason" => if(reason in @reasons, do: reason, else: "other")})}
  end

  defp report(context, phase, details) do
    metadata = diagnostics(context, phase, details)

    Logger.info(
      "Tracker tool outcome issue_id=#{context.issue} issue_identifier=#{context.metadata["issue_identifier"]} tool_id=#{context.id} diagnostics=#{Jason.encode!(Map.merge(metadata, %{"tool_id" => context.id, "issue_id" => context.issue, "tool" => context.tool}))}"
    )

    %{context | metadata: metadata}
  end

  defp diagnostics(context, phase, details) do
    context.metadata
    |> Map.merge(details)
    |> Map.merge(%{"phase" => phase, "elapsed_ms" => max(0, System.monotonic_time(:millisecond) - context.started)})
  end
end
