defmodule SymphonyElixir.LinearOperationTest do
  use ExUnit.Case, async: true
  alias SymphonyElixir.Linear.Operation
  @query "query { viewer { id } }"
  @mutation ~s|mutation { issueUpdate(id: "one", input: {title: "secret-canary"}) { success } }|

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
end
