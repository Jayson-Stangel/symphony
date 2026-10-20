defmodule SymphonyElixir.Linear.Operation do
  @moduledoc "Conservative operation classification from a parsed GraphQL document."

  alias Absinthe.Language.Source
  alias Absinthe.Phase.Parse

  @codes ~w(INPUT_ERROR BAD_USER_INPUT GRAPHQL_VALIDATION_FAILED GRAPHQL_PARSE_FAILED AUTHENTICATION_ERROR AUTHORIZATION_ERROR RATELIMITED RATE_LIMITED INTERNAL_SERVER_ERROR)
  @transport_reasons ~w(timeout closed econnrefused econnreset nxdomain enetunreach ehostunreach)a

  @spec error_codes(term()) :: [String.t()]
  def error_codes(%{"errors" => errors}) when is_list(errors) do
    for(%{"extensions" => %{"code" => code}} <- errors, code in @codes, do: code)
    |> Enum.uniq()
    |> Enum.sort()
  end

  def error_codes(_response), do: []

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
