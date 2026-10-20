defmodule SymphonyElixir.Linear.Operation do
  @moduledoc "Conservative operation classification from a parsed GraphQL document."

  alias Absinthe.Language.Source
  alias Absinthe.Phase.Parse

  @request_error_codes ~w(GRAPHQL_VALIDATION_FAILED GRAPHQL_PARSE_FAILED)
  @codes ~w(INVALID_INPUT INPUT_ERROR BAD_USER_INPUT GRAPHQL_VALIDATION_FAILED GRAPHQL_PARSE_FAILED AUTHENTICATION_ERROR AUTHORIZATION_ERROR RATELIMITED RATE_LIMITED INTERNAL_SERVER_ERROR)
  @transport_reasons ~w(timeout closed econnrefused econnreset nxdomain enetunreach ehostunreach)a

  @spec error_codes(term()) :: [String.t()]
  def error_codes(%{"errors" => errors}) when is_list(errors) do
    for(%{"extensions" => %{"code" => code}} <- errors, code in @codes, do: code)
    |> Enum.uniq()
    |> Enum.sort()
  end

  def error_codes(_response), do: []

  @doc "Classify the response envelope, never error-message text, to distinguish rejection from execution."
  @spec response_kind(term()) :: String.t()
  def response_kind(%{"errors" => errors} = response) when is_list(errors) and errors != [] do
    cond do
      not Enum.all?(errors, &valid_error?/1) -> "malformed"
      request_errors?(response, errors) -> "request_error"
      true -> "execution_error"
    end
  end

  def response_kind(%{"data" => data} = response) when is_map(data) do
    if Map.get(response, "errors", []) == [], do: "success", else: "malformed"
  end

  def response_kind(_response), do: "malformed"

  defp valid_error?(%{"message" => message}), do: is_binary(message)
  defp valid_error?(_error), do: false

  defp request_errors?(response, errors) do
    # GraphQL request errors have no data entry. Null data can instead mean a
    # resolver failed after a write; accept that shape only for explicit parse
    # or schema-validation errors, never generic input/authorization codes.
    no_execution_path = Enum.all?(errors, &(not Map.has_key?(&1, "path")))

    no_execution_path and
      (not Map.has_key?(response, "data") or
         (is_nil(response["data"]) and Enum.all?(errors, &request_error_code?/1)))
  end

  defp request_error_code?(%{"extensions" => %{"code" => code}}), do: code in @request_error_codes
  defp request_error_code?(_error), do: false

  @spec transport_reason(term()) :: String.t()
  def transport_reason({:linear_api_request, reason}), do: transport_reason(reason)
  def transport_reason(%{reason: reason}), do: transport_reason(reason)
  def transport_reason(reason) when reason in @transport_reasons, do: Atom.to_string(reason)
  def transport_reason(_reason), do: "other"

  @spec describe(term()) :: map()
  def describe(arguments) do
    query = query(arguments)

    if is_binary(query) and String.trim(query) != "" do
      query = String.trim(query)
      %{"operation" => kind(query), "document_sha256" => Base.encode16(:crypto.hash(:sha256, query), case: :lower)}
    else
      %{"operation" => "local"}
    end
  end

  defp query(value) when is_binary(value), do: value
  defp query(value) when is_map(value), do: Map.get(value, "query") || Map.get(value, :query)
  defp query(_value), do: nil

  defp kind(query) do
    case Parse.run(%Source{body: query}, token_limit: 20_000) do
      {:ok, %{input: %{definitions: definitions}}} -> operation_kind(definitions)
      _ -> "unknown"
    end
  end

  defp operation_kind(definitions) do
    operations = for %Absinthe.Language.OperationDefinition{operation: operation} <- definitions, do: operation

    case Enum.uniq(operations) do
      [:query] -> "query"
      [:mutation] -> "mutation"
      [:subscription] -> "subscription"
      _ -> "unknown"
    end
  end
end
