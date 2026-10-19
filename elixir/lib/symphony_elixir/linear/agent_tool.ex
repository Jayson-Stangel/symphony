defmodule SymphonyElixir.Linear.AgentTool do
  @moduledoc """
  Provider-native Linear tool exposed to Codex app-server turns.
  """

  alias SymphonyElixir.Linear.{Client, Operation}

  @rejections [400, 401, 403, 404, 405, 409, 410, 412, 413, 415, 422, 429]

  @linear_graphql_tool "linear_graphql"
  @linear_graphql_description """
  Execute a raw GraphQL query or mutation against Linear using Symphony's configured auth.
  """
  @linear_graphql_input_schema %{
    "type" => "object",
    "additionalProperties" => false,
    "required" => ["query"],
    "properties" => %{
      "query" => %{
        "type" => "string",
        "description" => "GraphQL query or mutation document to execute against Linear."
      },
      "variables" => %{
        "type" => ["object", "null"],
        "description" => "Optional GraphQL variables object.",
        "additionalProperties" => true
      }
    }
  }

  @spec execute(String.t() | nil, term(), keyword()) :: map()
  def execute(tool, arguments, opts) do
    case tool do
      @linear_graphql_tool ->
        execute_linear_graphql(arguments, opts)

      other ->
        failure_response(%{
          "error" => %{
            "message" => "Unsupported dynamic tool: #{inspect(other)}.",
            "supportedTools" => supported_tool_names()
          }
        })
    end
  end

  @spec tool_specs() :: [map()]
  def tool_specs do
    [
      %{
        "name" => @linear_graphql_tool,
        "description" => @linear_graphql_description,
        "inputSchema" => @linear_graphql_input_schema
      }
    ]
  end

  defp execute_linear_graphql(arguments, opts) do
    linear_client = Keyword.get(opts, :linear_client, &Client.graphql/3)
    client_opts = Keyword.take(opts, [:tracker_settings])

    case normalize_linear_graphql_arguments(arguments) do
      {:ok, query, variables} ->
        operation = Operation.describe(query)
        response = linear_client.(query, variables, client_opts)
        execution_response(response, operation)

      {:error, reason} ->
        failure_response(tool_error_payload(reason))
        |> execution("not_executed", "local_validation", Operation.describe(arguments))
    end
  end

  defp execution_response({:ok, response}, operation) do
    result = graphql_response(response)

    certainty =
      cond do
        operation["operation"] == "query" -> "read_only"
        result["success"] and is_map(response) and is_map(response["data"]) and Map.get(response, "errors", []) == [] -> "confirmed"
        true -> "uncertain"
      end

    execution(result, certainty, if(result["success"], do: "response", else: "graphql_error"), Map.put(operation, "http_status", 200))
  end

  defp execution_response({:error, reason}, operation) do
    {certainty, category, details} = failure_certainty(reason, operation)

    failure_response(tool_error_payload(reason))
    |> execution(certainty, category, Map.merge(operation, details))
  end

  defp failure_certainty(:missing_linear_api_token, _operation), do: {"not_executed", "missing_auth", %{}}
  defp failure_certainty({:linear_api_request, :missing_linear_api_token}, operation), do: failure_certainty(:missing_linear_api_token, operation)

  defp failure_certainty({:linear_api_status, status}, operation) do
    certainty = if status in @rejections, do: "not_executed", else: uncertainty(operation)
    {certainty, "http_error", %{"http_status" => status}}
  end

  defp failure_certainty(reason, operation), do: {uncertainty(operation), "transport_error", %{"transport_reason" => Operation.transport_reason(reason)}}
  defp uncertainty(%{"operation" => "query"}), do: "read_only"
  defp uncertainty(_operation), do: "uncertain"

  defp execution(result, certainty, reason, metadata) do
    Map.put(result, "execution", Map.merge(metadata, %{"certainty" => certainty, "reason" => reason}))
  end

  defp normalize_linear_graphql_arguments(arguments) when is_binary(arguments) do
    case String.trim(arguments) do
      "" -> {:error, :missing_query}
      query -> {:ok, query, %{}}
    end
  end

  defp normalize_linear_graphql_arguments(arguments) when is_map(arguments) do
    case normalize_query(arguments) do
      {:ok, query} ->
        case normalize_variables(arguments) do
          {:ok, variables} ->
            {:ok, query, variables}

          {:error, reason} ->
            {:error, reason}
        end

      {:error, reason} ->
        {:error, reason}
    end
  end

  defp normalize_linear_graphql_arguments(_arguments), do: {:error, :invalid_arguments}

  defp normalize_query(arguments) do
    case Map.get(arguments, "query") || Map.get(arguments, :query) do
      query when is_binary(query) ->
        case String.trim(query) do
          "" -> {:error, :missing_query}
          trimmed -> {:ok, trimmed}
        end

      _ ->
        {:error, :missing_query}
    end
  end

  defp normalize_variables(arguments) do
    value = Map.get(arguments, "variables", Map.get(arguments, :variables))

    case value do
      nil -> {:ok, %{}}
      variables when is_map(variables) -> {:ok, variables}
      _ -> {:error, :invalid_variables}
    end
  end

  defp graphql_response(response) do
    success =
      case response do
        %{"errors" => errors} when is_list(errors) and errors != [] -> false
        %{errors: errors} when is_list(errors) and errors != [] -> false
        _ -> true
      end

    dynamic_tool_response(success, encode_payload(response))
  end

  defp failure_response(payload) do
    dynamic_tool_response(false, encode_payload(payload))
  end

  defp dynamic_tool_response(success, output) when is_boolean(success) and is_binary(output) do
    %{
      "success" => success,
      "output" => output,
      "contentItems" => [
        %{
          "type" => "inputText",
          "text" => output
        }
      ]
    }
  end

  defp encode_payload(payload) when is_map(payload) or is_list(payload) do
    Jason.encode!(payload, pretty: true)
  end

  defp encode_payload(payload), do: inspect(payload)

  defp tool_error_payload(:missing_query) do
    %{
      "error" => %{
        "message" => "`linear_graphql` requires a non-empty `query` string."
      }
    }
  end

  defp tool_error_payload(:invalid_arguments) do
    %{
      "error" => %{
        "message" => "`linear_graphql` expects either a GraphQL query string or an object with `query` and optional `variables`."
      }
    }
  end

  defp tool_error_payload(:invalid_variables) do
    %{
      "error" => %{
        "message" => "`linear_graphql.variables` must be a JSON object when provided."
      }
    }
  end

  defp tool_error_payload(:missing_linear_api_token) do
    %{
      "error" => %{
        "message" => "Symphony is missing Linear auth. Set `tracker.provider.api_key` in `WORKFLOW.md` or export `LINEAR_API_KEY`."
      }
    }
  end

  defp tool_error_payload({:linear_api_status, status}) do
    %{
      "error" => %{
        "message" => "Linear GraphQL request failed with HTTP #{status}.",
        "status" => status
      }
    }
  end

  defp tool_error_payload({:linear_api_request, reason}) do
    %{
      "error" => %{
        "message" => "Linear GraphQL request failed before receiving a successful response.",
        "reason" => inspect(reason)
      }
    }
  end

  defp tool_error_payload(reason) do
    %{
      "error" => %{
        "message" => "Linear GraphQL tool execution failed.",
        "reason" => inspect(reason)
      }
    }
  end

  defp supported_tool_names do
    Enum.map(tool_specs(), & &1["name"])
  end
end
