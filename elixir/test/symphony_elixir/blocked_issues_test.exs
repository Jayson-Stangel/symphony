defmodule SymphonyElixir.BlockedIssuesTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixirWeb.Presenter

  setup do
    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "memory",
      tracker_blocked_states: ["Blocked"],
      poll_interval_ms: 60_000,
      max_concurrent_agents: 1
    )

    :ok
  end

  test "tracker blocked issues are visible after restart without dispatching or inventing session history" do
    issue = blocked_issue()
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])

    pid = start_orchestrator()
    assert %{blocked: [entry], running: [], retrying: []} = poll(pid)
    assert entry.identifier == issue.identifier
    assert entry.state == "Blocked"
    assert entry.session_id == nil
    assert entry.blocked_at == nil
    assert :sys.get_state(pid).claimed == MapSet.new()

    assert %{counts: %{blocked: 1}, blocked: [%{issue_url: "https://example.com/blocked"}]} = Presenter.state_payload(__MODULE__, 1_000)

    assert {:ok, %{status: "blocked", blocked: %{session_id: nil}}} =
             Presenter.issue_payload(issue.identifier, __MODULE__, 1_000)

    stop_supervised!(Orchestrator)
    restarted = start_orchestrator()
    assert %{blocked: [^entry], running: [], retrying: []} = poll(restarted)
  end

  test "blocked view refreshes at capacity and removes issues that leave the blocked state" do
    issue = blocked_issue()
    active = %{issue | id: "active", identifier: "TEST-2", state: "In Progress"}
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue, active])
    pid = start_orchestrator()

    # A running issue occupies the only slot; tracker blockers must still be refreshed.
    :sys.replace_state(pid, fn state ->
      %{state | running: %{active.id => runtime_entry(active)}, claimed: MapSet.new([active.id])}
    end)

    assert %{blocked: [%{issue_id: "blocked"}], running: [_]} = poll(pid)
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{issue | state: "Todo"}, active])
    assert %{blocked: [], running: [_]} = poll(pid)
    refute MapSet.member?(:sys.get_state(pid).claimed, issue.id)
  end

  test "resolving a tracker blocker allows normal dispatch" do
    issue = blocked_issue()
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])

    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "memory",
      tracker_blocked_states: ["Blocked"],
      workspace_root: Path.join(Path.dirname(Workflow.workflow_file_path()), "workspaces"),
      codex_command: "/bin/false"
    )

    pid = start_orchestrator()
    assert %{blocked: [_], running: []} = poll(pid)
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{issue | state: "Todo"}])
    assert %{blocked: []} = poll(pid)
    state = :sys.get_state(pid)
    assert MapSet.member?(state.claimed, issue.id)
    assert Map.has_key?(state.running, issue.id) or Map.has_key?(state.retry_attempts, issue.id)
  end

  test "tracker blockers obey routing and labels and respond to configuration reload" do
    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "memory",
      tracker_blocked_states: [" waiting "],
      tracker_required_labels: ["agent"]
    )

    issue = %{blocked_issue() | state: "Waiting", labels: ["AGENT"]}
    unassigned = %{issue | id: "unassigned", dispatchable: false}
    unlabeled = %{issue | id: "unlabeled", labels: []}
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue, unassigned, unlabeled])
    pid = start_orchestrator()
    assert %{blocked: [%{issue_id: "blocked"}]} = poll(pid)

    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", tracker_blocked_states: [])
    assert %{blocked: []} = poll(pid)
  end

  test "failed tracker reads retain the last blocked view and recovery replaces it" do
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [blocked_issue()])
    pid = start_orchestrator()
    assert %{blocked: [entry]} = poll(pid)

    # Local connection refusal exercises the real adapter read error without external network access.
    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_endpoint: "http://127.0.0.1:1/graphql",
      tracker_blocked_states: ["Blocked"]
    )

    capture_log(fn -> assert %{blocked: [^entry]} = poll(pid) end)

    Application.put_env(:symphony_elixir, :memory_tracker_issues, [])
    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", tracker_blocked_states: ["Blocked"])
    assert %{blocked: []} = poll(pid)
  end

  test "runtime and tracker rows for the same issue are deduplicated with session details preserved" do
    issue = blocked_issue()
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])
    pid = start_orchestrator()
    poll(pid)

    :sys.replace_state(pid, fn state ->
      %{state | blocked: %{issue.id => Map.put(runtime_entry(issue), :error, "requires approval")}}
    end)

    assert %{blocked: [%{session_id: "session-1", state: "Blocked", error: "requires approval"}]} = Orchestrator.snapshot(__MODULE__, 1_000)
    # Reconciliation drops the runtime claim once it observes the non-active tracker state.
    assert %{blocked: [%{issue_id: "blocked"}]} = poll(pid)
  end

  test "blocked states default to disabled and cannot overlap active or terminal states" do
    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory")
    assert Config.settings!().tracker.blocked_states == []

    for blocked <- [[" todo "], ["DONE"]] do
      write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", tracker_blocked_states: blocked)
      assert {:error, {:invalid_workflow_config, message}} = Config.validate!()
      assert message =~ "tracker.blocked_states"
      assert message =~ "overlap"
    end
  end

  defp start_orchestrator do
    start_supervised!({Orchestrator, name: __MODULE__})
  end

  defp poll(pid) do
    send(pid, :run_poll_cycle)
    Orchestrator.snapshot(__MODULE__, 5_000)
  end

  defp blocked_issue do
    %Issue{id: "blocked", identifier: "TEST-1", title: "Needs access", state: "Blocked", dispatchable: true, url: "https://example.com/blocked"}
  end

  defp runtime_entry(issue) do
    %{
      issue: issue,
      identifier: issue.identifier,
      session_id: "session-1",
      codex_app_server_pid: nil,
      codex_input_tokens: 0,
      codex_output_tokens: 0,
      codex_total_tokens: 0,
      started_at: DateTime.utc_now(),
      last_codex_timestamp: nil,
      last_codex_message: nil,
      last_codex_event: nil
    }
  end
end
