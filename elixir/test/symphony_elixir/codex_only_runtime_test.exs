defmodule SymphonyElixir.CodexOnlyRuntimeTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixir.{CLI, Config.Schema}

  test "unsupported inherited selection fails before starting any runtime" do
    previous = System.get_env("SYMPHONY_CODING_AGENT")
    on_exit(fn -> restore_env("SYMPHONY_CODING_AGENT", previous) end)
    System.put_env("SYMPHONY_CODING_AGENT", "claude")
    assert {:error, message} = CLI.evaluate([], %{})
    assert message =~ "supports Codex only"

    for config <- [%{"claude" => %{}}, %{"coding_agent" => "claude"}, %{claude: %{model: "sonnet"}}] do
      assert {:error, {:invalid_workflow_config, message}} = Schema.parse(config)
      assert message =~ "supports Codex only"
    end
  end

  test "a queued retry cannot dispatch after the updater drains" do
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [])
    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", poll_interval_ms: 60_000)
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.RetryRunner)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    assert {:ok, %{draining: true}} = GenServer.call(pid, {:agent_control, :drain, false})
    issue = %Issue{id: "queued", identifier: "MT-QUEUED", title: "Eligible retry", state: "In Progress"}
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])
    token = make_ref()

    :sys.replace_state(pid, fn state ->
      %{state | claimed: MapSet.put(state.claimed, "queued"), retry_attempts: %{"queued" => %{attempt: 1, retry_token: token}}}
    end)

    send(pid, {:retry_issue, "queued", token})
    state = :sys.get_state(pid)
    assert state.draining
    assert state.running == %{}
    assert state.retry_attempts == %{}
    refute MapSet.member?(state.claimed, "queued")
  end

  test "durable drain and updater resume survive restart without reading legacy tool pauses" do
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [])
    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", poll_interval_ms: 60_000)
    state_root = Config.agent_state_root()
    File.mkdir_p!(state_root)
    legacy = Path.join(state_root, "claude-pause.json")
    File.write!(legacy, "legacy reconciliation evidence")
    name = __MODULE__.Runner
    {:ok, pid} = Orchestrator.start_link(name: name)
    assert {:ok, %{draining: true, running: 0}} = GenServer.call(pid, {:agent_control, :drain, false})
    GenServer.stop(pid)
    {:ok, pid} = Orchestrator.start_link(name: name)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    assert Orchestrator.snapshot(name, 5_000).agent.draining
    assert {:ok, %{draining: false}} = GenServer.call(pid, {:agent_control, :resume, false})
    refute Orchestrator.snapshot(name, 5_000).agent.draining
    assert File.read!(legacy) == "legacy reconciliation evidence"
    assert {:error, :invalid_action} = GenServer.call(pid, {:agent_control, :unsupported, false})
  end
end
