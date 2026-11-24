defmodule SymphonyElixir.DrainedRetryTest do
  use SymphonyElixir.TestSupport

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

  defmodule SourceRetryClient do
    def fetch_issues_by_states(_states), do: {:ok, []}

    def fetch_issues_by_ids(_ids) do
      case Process.get(__MODULE__) do
        {issue, store, owner, 1} ->
          :sys.replace_state(store, &%{&1 | source_block: :missing_workflow})
          send(owner, :source_rejected_during_retry_refresh)
          {:ok, [issue]}

        {issue, store, owner, 0} ->
          Process.put(__MODULE__, {issue, store, owner, 1})
          {:ok, [issue]}

        nil ->
          {:ok, []}
      end
    end
  end

  test "a source rejection observed during tracker refresh keeps the waiting retry claimed" do
    previous_client = Application.get_env(:symphony_elixir, :linear_client_module)
    Application.put_env(:symphony_elixir, :linear_client_module, SourceRetryClient)
    write_workflow_file!(Workflow.workflow_file_path(), poll_interval_ms: 60_000)
    issue = %Issue{id: "queued", identifier: "MT-QUEUED", title: "Eligible retry", state: "In Progress", dispatchable: true}
    store = Process.whereis(WorkflowStore)
    original = :sys.get_state(store)
    source = %{project: "fixture", branch: "fixture", repository: "https://github.com/example/fixture.git"}
    key = "SYMPHONY_REMOTE_REPOSITORY"
    previous = System.get_env(key)
    System.put_env(key, source.repository)
    owner = self()

    try do
      :sys.replace_state(store, &%{&1 | source: source})
      {:ok, pid} = Orchestrator.start_link(name: __MODULE__.SourceRetryRunner)

      try do
        token = make_ref()

        :sys.replace_state(pid, fn state ->
          Process.put(SourceRetryClient, {issue, store, owner, 0})
          %{state | claimed: MapSet.new(["queued"]), retry_attempts: %{"queued" => %{attempt: 2, retry_token: token}}}
        end)

        send(pid, {:retry_issue, "queued", token})
        assert_receive :source_rejected_during_retry_refresh
        state = :sys.get_state(pid)
        assert state.running == %{}
        assert state.retry_attempts["queued"].attempt == 2
        assert state.retry_attempts["queued"].retry_token != token
        assert MapSet.member?(state.claimed, "queued")

        :sys.replace_state(store, &%{&1 | source_block: nil})
        assert {:ok, ^issue} = Orchestrator.revalidate_issue_for_dispatch_for_test(issue, fn _ -> {:ok, [issue]} end)
      after
        GenServer.stop(pid)
      end
    after
      restore_env(key, previous)
      :sys.replace_state(store, fn _ -> original end)

      if previous_client do
        Application.put_env(:symphony_elixir, :linear_client_module, previous_client)
      else
        Application.delete_env(:symphony_elixir, :linear_client_module)
      end
    end
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
