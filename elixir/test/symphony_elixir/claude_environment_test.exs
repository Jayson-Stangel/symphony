defmodule SymphonyElixir.ClaudeEnvironmentTest do
  use SymphonyElixir.TestSupport

  test "only declared tool values are transported, never installed as helper startup variables" do
    source = environment()
    assert {:ok, pairs} = Config.claude_child_environment(source, ["CUSTOM_TRACKER"])
    env = Map.new(pairs)
    assert %{"names" => ["AWS_ACCESS_KEY_ID", "AWS_REGION", "OPTIONAL"], "reserved" => ["CUSTOM_TRACKER"], "values" => values} = Jason.decode!(env["SYMPHONY_TOOL_ENVIRONMENT"])
    assert values == %{"AWS_ACCESS_KEY_ID" => "tool-sentinel", "AWS_REGION" => "us-east-1"}

    for name <- ~w(AWS_ACCESS_KEY_ID AWS_REGION CUSTOM_TRACKER ANTHROPIC_API_KEY NODE_OPTIONS SYMPHONY_TOOL_ENV) do
      assert env[name] == nil
    end

    assert env["HOME"] == "/home/claude"
  end

  test "unencodable selected data returns only a bounded transport error" do
    source = Map.put(environment(), "AWS_ACCESS_KEY_ID", <<255>>)
    assert {:error, :invalid_tool_environment} = Config.claude_child_environment(source, [])
  end

  test "serialized tool transport bounds bytes and preserves small Unicode" do
    source = Map.put(environment(), "AWS_ACCESS_KEY_ID", "日本語 🔑")
    assert {:ok, pairs} = Config.claude_child_environment(source, [])
    envelope = Map.new(pairs)["SYMPHONY_TOOL_ENVIRONMENT"]
    assert Jason.decode!(envelope)["values"]["AWS_ACCESS_KEY_ID"] == "日本語 🔑"

    oversized = Map.put(source, "AWS_ACCESS_KEY_ID", String.duplicate("界", 50_000))
    encoded = Jason.encode!(%{names: ["AWS_ACCESS_KEY_ID"], values: Map.take(oversized, ["AWS_ACCESS_KEY_ID"]), reserved: []})
    assert String.length(encoded) < 65_536
    assert byte_size(encoded) > 131_072
    assert {:error, :invalid_tool_environment} = Config.claude_child_environment(oversized, [])
  end

  test "cleanup never receives selected tools or the internal envelope" do
    source = environment() |> Map.put("SYMPHONY_TOOL_ENVIRONMENT", "secret-canary") |> Map.put("SYMPHONY_GIT_TOKEN", "git-canary")
    previous = Map.new(source, fn {name, _} -> {name, System.get_env(name)} end)
    System.put_env(source)

    try do
      env = Map.new(Config.claude_cleanup_environment())
      for name <- Map.keys(source), do: assert(env[name] == nil)
    after
      Enum.each(previous, fn
        {name, nil} -> System.delete_env(name)
        {name, value} -> System.put_env(name, value)
      end)
    end
  end

  @tag skip: not match?({:unix, :linux}, :os.type()) or not File.dir?("/opt/symphony/claude")
  test "actual native runtime keeps first-party routing while a tool child receives selected AWS credentials" do
    assert {:ok, env} = Config.claude_child_environment(environment(), ["CUSTOM_TRACKER"])
    probe = Path.expand("../../../runner/claude/test/fixtures/environment-probe.mjs", __DIR__)
    assert {output, 0} = System.cmd("node", [probe], env: env, stderr_to_stdout: true)
    assert Jason.decode!(output) == %{"toolCredentialAvailable" => true, "trackerAndLoaderAbsent" => true, "provider" => "firstParty", "modelRequests" => 0}
    refute output =~ "canary"
  end

  defp environment do
    %{
      "SYMPHONY_TOOL_ENV" => "AWS_ACCESS_KEY_ID, AWS_REGION,OPTIONAL",
      "AWS_ACCESS_KEY_ID" => "tool-sentinel",
      "AWS_REGION" => "us-east-1",
      "CUSTOM_TRACKER" => "tracker-canary",
      "ANTHROPIC_API_KEY" => "api-canary",
      "NODE_OPTIONS" => "loader-canary"
    }
  end
end
