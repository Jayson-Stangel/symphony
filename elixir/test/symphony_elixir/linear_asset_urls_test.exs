defmodule SymphonyElixir.LinearAssetUrlsTest do
  use ExUnit.Case, async: true

  alias SymphonyElixir.Linear.{AgentTool, Client}

  @query "query { issue(id: \"issue-one\") { attachments { nodes { url } } } }"
  @settings %{api_key: "asset-auth-canary", endpoint: "https://linear.example.test/graphql"}

  test "worker requests obtain short-lived download URLs without exposing auth or accepting caller headers" do
    asset = "https://uploads.linear.app/fixture/evidence.png?signature=fixture"

    for arguments <- [@query, %{"query" => @query, "variables" => %{}, "headers" => %{"public-file-urls-expire-in" => "999999"}}] do
      result =
        AgentTool.execute("linear_graphql", arguments,
          tracker_settings: @settings,
          linear_client: fn query, variables, opts ->
            assert opts[:tracker_settings] == @settings

            Client.graphql(
              query,
              variables,
              Keyword.put(opts, :request_fun, fn payload, headers ->
                assert payload["query"] == @query
                assert {"Authorization", "asset-auth-canary"} in headers
                assert {"public-file-urls-expire-in", "300"} in headers
                refute {"public-file-urls-expire-in", "999999"} in headers
                {:ok, %{status: 200, body: %{"data" => %{"issue" => %{"attachments" => %{"nodes" => [%{"url" => asset}]}}}}}}
              end)
            )
          end
        )

      assert result["success"]
      assert result["output"] =~ asset
      refute result["output"] =~ "asset-auth-canary"
    end
  end

  test "ordinary tracker reads do not request signed storage URLs" do
    assert {:ok, %{"data" => %{}}} =
             Client.graphql(@query, %{},
               tracker_settings: @settings,
               request_fun: fn _payload, headers ->
                 refute Enum.any?(headers, fn {name, _} -> name == "public-file-urls-expire-in" end)
                 {:ok, %{status: 200, body: %{"data" => %{}}}}
               end
             )
  end
end
