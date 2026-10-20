defmodule SymphonyElixir.AgentSelectionTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixir.CodingAgent
  alias SymphonyElixir.Config.Schema

  setup do
    previous = Application.fetch_env(:symphony_elixir, :coding_agent)
    Application.delete_env(:symphony_elixir, :coding_agent)

    on_exit(fn ->
      case previous do
        {:ok, provider} -> Application.put_env(:symphony_elixir, :coding_agent, provider)
        :error -> Application.delete_env(:symphony_elixir, :coding_agent)
      end
    end)

    :ok
  end

  test "default Codex limits and startup-selected Claude survive workflow reload" do
    options = [max_concurrent_agents: 7, max_concurrent_agents_by_state: %{"Todo" => 4}]
    write_workflow_file!(Workflow.workflow_file_path(), options)
    assert Config.coding_agent() == :codex
    assert Config.max_concurrent_agents() == 7
    assert Config.max_concurrent_agents_for_state("Todo") == 4

    Application.put_env(:symphony_elixir, :coding_agent, :claude)
    assert Config.max_concurrent_agents() == 1
    assert Config.max_concurrent_agents_for_state("Todo") == 1
    assert Config.max_concurrent_agents_for_state(nil) == 1
    write_workflow_file!(Workflow.workflow_file_path(), max_concurrent_agents: 3)
    assert Config.coding_agent() == :claude
    assert Config.max_concurrent_agents() == 1
    assert Config.agent_stall_timeout_ms() == 300_000
    assert {:error, {:invalid_workspace_cwd, :outside_workspace_root, _, _}} = CodingAgent.start_session("/unused")
  end

  test "Claude rejects SSH workflow settings before any remote workspace can be created" do
    Application.put_env(:symphony_elixir, :coding_agent, :claude)
    assert {:ok, settings} = Schema.parse(%{"tracker" => %{"kind" => "memory"}, "worker" => %{"ssh_hosts" => ["remote"]}})
    assert {:error, :claude_ssh_unsupported} = Config.validate_settings(settings)
  end

  test "invalid startup provider fails closed" do
    Application.put_env(:symphony_elixir, :coding_agent, :unknown)
    assert_raise ArgumentError, "Unsupported coding agent", fn -> Config.coding_agent() end
  end

  test "Claude schema delegates nonblank model IDs to native runtime, with Sonnet as default" do
    assert {:ok, defaults} = Schema.parse(%{})
    assert defaults.claude.model == "sonnet"

    for model <- ~w(sonnet opus haiku fable claude-opus-5-5 claude-sonnet-5-5 claude-haiku-5-5 claude-fable-5-1 future-native-model-99 opusplan Opus) do
      assert {:ok, config} = Schema.parse(%{"claude" => %{"model" => model}})
      assert config.claude.model == model
    end

    for model <- ["", "  ", String.duplicate("a", 257), %{}, 123] do
      assert {:error, {:invalid_workflow_config, message}} = Schema.parse(%{"claude" => %{"model" => model}})
      assert message =~ "claude.model"
    end
  end

  test "Codex model and reasoning effort are optional native settings" do
    assert {:ok, defaults} = Schema.parse(%{})
    assert defaults.codex.model == nil
    assert defaults.codex.reasoning_effort == nil

    for effort <- ~w(none minimal low medium high xhigh max ultra future-effort) do
      assert {:ok, config} = Schema.parse(%{"codex" => %{"model" => "custom-model", "reasoning_effort" => effort}})
      assert config.codex.model == "custom-model"
      assert config.codex.reasoning_effort == effort
    end

    for field <- ["model", "reasoning_effort"], value <- ["", "  ", %{}, String.duplicate("a", 257)] do
      assert {:error, {:invalid_workflow_config, message}} = Schema.parse(%{"codex" => %{field => value}})
      assert message =~ "codex.#{field}"
    end
  end

  test "Codex model and reasoning effort reload together and retain last good settings" do
    for {model, effort, expected_model, expected_effort} <- [
          {"gpt-example", "high", "gpt-example", "high"},
          {"gpt-other", "xhigh", "gpt-other", "xhigh"},
          {"", "low", "gpt-other", "xhigh"},
          {nil, nil, nil, nil}
        ] do
      write_workflow_file!(Workflow.workflow_file_path(), codex_model: model, codex_reasoning_effort: effort)
      assert Config.settings!().codex.model == expected_model
      assert Config.settings!().codex.reasoning_effort == expected_effort
      snapshot = Orchestrator.snapshot()
      assert snapshot.agent.model == expected_model
      assert snapshot.agent.effort == expected_effort
      assert snapshot.agent.model_source == if(expected_model, do: "configured", else: "last_session")
    end
  end

  test "Claude effort is optional, accepts SDK levels and rejects invalid settings" do
    assert {:ok, defaults} = Schema.parse(%{})
    assert defaults.claude.effort == nil

    for effort <- [nil, "low", "medium", "high", "xhigh", "max"] do
      assert {:ok, config} = Schema.parse(%{"claude" => %{"effort" => effort}})
      assert config.claude.effort == effort
    end

    for effort <- ["", "auto", "ultracode", "High", "ultra", 42, %{}] do
      assert {:error, {:invalid_workflow_config, message}} = Schema.parse(%{"claude" => %{"effort" => effort}})
      assert message =~ "claude.effort"
    end
  end

  test "Claude effort follows workflow reload and retains the last valid value" do
    Application.put_env(:symphony_elixir, :coding_agent, :claude)

    for {value, expected} <- [{"high", "high"}, {"low", "low"}, {"invalid", "low"}, {"null", nil}] do
      File.write!(Workflow.workflow_file_path(), "---\ntracker:\n  kind: memory\nclaude:\n  effort: #{value}\n---\nPrompt\n")
      WorkflowStore.force_reload()
      assert Config.settings!().claude.effort == expected
      assert Orchestrator.snapshot().agent.effort == expected
    end
  end

  test "Claude schema rejects raw authentication and options instead of ignoring them" do
    assert {:ok, config} = Schema.parse(%{"claude" => %{"model" => "sonnet", "max_concurrent_agents" => 1}})
    assert config.claude.max_concurrent_agents == 1
    assert config.codex.live_max_total_tokens == 0

    for settings <- [%{"api_key" => "secret-canary"}, %{"command" => "injected"}, %{"max_concurrent_agents" => 0}, %{"model" => ""}] do
      assert {:error, {:invalid_workflow_config, message}} = Schema.parse(%{"claude" => settings})
      assert message =~ "claude."
      refute message =~ "secret-canary"
    end
  end

  test "CLI selects provider before startup and rejects duplicates before any side effects" do
    owner = self()

    deps = %{
      file_regular?: fn _ ->
        send(owner, :file_checked)
        true
      end,
      set_workflow_file_path: fn _ -> :ok end,
      set_logs_root: fn _ -> :ok end,
      set_server_port_override: fn _ -> :ok end,
      set_coding_agent: fn provider ->
        send(owner, {:selected, provider})
        :ok
      end,
      ensure_all_started: fn ->
        send(owner, :started)
        {:ok, []}
      end
    }

    ack = "--i-understand-that-this-will-be-running-without-the-usual-guardrails"
    assert :ok = CLI.evaluate([ack, "--agent", "claude"], deps)
    assert_received {:selected, :claude}
    assert_received :file_checked
    assert_received :started
    assert :ok = CLI.evaluate([ack], deps)
    assert_received {:selected, :codex}
    assert_received :file_checked
    assert_received :started

    for args <- [["--agent", "unknown"], ["--agent"], ["--agent", "claude", "--agent", "codex"]] do
      assert {:error, _} = CLI.evaluate([ack | args], deps)
      refute_received :file_checked
      refute_received :started
      refute_received {:selected, _}
    end
  end
end
