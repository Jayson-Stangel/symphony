defmodule SymphonyElixir.TurnLimitBlockTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixir.Linear.Client

  defmodule LinearClient do
    alias SymphonyElixir.Tracker.Memory

    def fetch_issues_by_states(_), do: {:ok, []}
    def fetch_issues_by_ids(ids), do: Memory.fetch_issues_by_ids(ids)
    def block_issue(id), do: Application.fetch_env!(:symphony_elixir, :test_block_issue).(id)
  end

  setup do
    previous = Application.get_env(:symphony_elixir, :linear_client_module)
    Application.put_env(:symphony_elixir, :linear_client_module, LinearClient)

    on_exit(fn ->
      if previous do
        Application.put_env(:symphony_elixir, :linear_client_module, previous)
      else
        Application.delete_env(:symphony_elixir, :linear_client_module)
      end

      Application.delete_env(:symphony_elixir, :test_block_issue)
    end)

    :ok
  end

  test "max-turn exit syncs Linear and retries failures without redispatch" do
    owner = self()

    Application.put_env(:symphony_elixir, :test_block_issue, fn id ->
      send(owner, {:block_requested, id, :offline})
      {:error, :offline}
    end)

    pid = start_supervised!({Orchestrator, name: __MODULE__})
    ref = make_ref()
    issue = %Issue{id: "limit", identifier: "TEST-1", state: "In Progress", dispatchable: true}

    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])

    :sys.replace_state(pid, fn state ->
      %{state | running: %{issue.id => %{pid: owner, ref: ref, identifier: issue.identifier, issue: issue, started_at: DateTime.utc_now()}}, claimed: MapSet.new([issue.id])}
    end)

    send(pid, {:DOWN, ref, :process, owner, {:max_turns_reached, %{max_turns: 20}}})
    assert_receive {:block_requested, "limit", :offline}
    state = :sys.get_state(pid)
    assert state.blocked[issue.id].sync_tracker_block
    assert state.running == %{}
    assert state.retry_attempts == %{}

    Application.put_env(:symphony_elixir, :test_block_issue, fn id ->
      send(owner, {:block_requested, id})
      :ok
    end)

    send(pid, :run_poll_cycle)
    state = :sys.get_state(pid)
    assert_receive {:block_requested, "limit"}
    assert state.blocked[issue.id].sync_tracker_block == :synced
    send(pid, :run_poll_cycle)
    resumed = :sys.get_state(pid)
    assert resumed.blocked == %{}
    refute_receive {:block_requested, _}
    state = put_in(state.blocked[issue.id].sync_tracker_block, true)
    state = Orchestrator.reconcile_blocked_issue_states_for_test([%{issue | state: "Human Review"}], state)
    assert state.blocked == %{}
    refute_receive {:block_requested, _}
  end

  test "live turn cap stops the worker and synchronizes Linear" do
    write_workflow_file!(Workflow.workflow_file_path(), codex_live_max_turns: 2)
    owner = self()

    Application.put_env(:symphony_elixir, :test_block_issue, fn id ->
      send(owner, {:block_requested, id})
      :ok
    end)

    supervisor = start_supervised!({Task.Supervisor, name: Module.concat(__MODULE__, Tasks)})

    {:ok, worker} =
      Task.Supervisor.start_child(supervisor, fn ->
        receive do
          :done -> :ok
        end
      end)

    pid = start_supervised!({Orchestrator, name: __MODULE__, task_supervisor: supervisor})
    issue = %Issue{id: "live-limit", identifier: "TEST-2", state: "In Progress", dispatchable: true}
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])

    :sys.replace_state(pid, fn state ->
      entry = %{
        pid: worker,
        ref: make_ref(),
        identifier: issue.identifier,
        issue: issue,
        started_at: DateTime.utc_now(),
        session_id: "thread-live",
        turn_count: 2,
        codex_input_tokens: 0,
        codex_output_tokens: 0,
        codex_total_tokens: 0
      }

      %{state | running: %{issue.id => entry}, claimed: MapSet.new([issue.id])}
    end)

    send(pid, {:codex_worker_update, issue.id, %{event: :notification, payload: %{}, timestamp: DateTime.utc_now()}})
    assert_receive {:block_requested, "live-limit"}
    state = :sys.get_state(pid)
    refute Process.alive?(worker)
    assert state.running == %{}
    assert state.retry_attempts == %{}
    assert state.blocked[issue.id].sync_tracker_block == :synced
  end

  test "Linear resolves Blocked in the issue team and checks mutation success" do
    owner = self()

    request = fn payload, _ ->
      send(owner, payload)
      body = if payload["query"] =~ "SymphonyBlockContext", do: context("In Progress", [%{"id" => "blocked-state"}]), else: %{"data" => %{"issueUpdate" => %{"success" => true}}}
      {:ok, %{status: 200, body: body}}
    end

    assert :ok = Client.block_issue("issue-1", request_fun: request)
    assert_receive %{"variables" => %{"id" => "issue-1"}}
    assert_receive %{"variables" => %{"id" => "issue-1", "stateId" => "blocked-state"}}
  end

  test "Linear preserves non-active issues and reports missing states and API errors" do
    for state <- ["Blocked", "Done", "Human Review"] do
      assert :ok = Client.block_issue("issue", request_fun: read_only_request(context(state, [])))
    end

    for nodes <- [[], [%{"id" => "one"}, %{"id" => "two"}]] do
      request = read_only_request(context("In Progress", nodes))
      assert {:error, :linear_blocked_state_missing_or_ambiguous} = Client.block_issue("issue", request_fun: request)
    end

    for body <- [%{"errors" => [%{"message" => "denied"}]}, %{"data" => %{"issue" => nil}}] do
      assert {:error, :linear_block_context_failed} = Client.block_issue("issue", request_fun: read_only_request(body))
    end

    failed_results = [%{"data" => %{"issueUpdate" => %{"success" => false}}}, %{"errors" => []}]

    for result <- failed_results do
      request = fn payload, _ ->
        body =
          if payload["query"] =~ "SymphonyBlockContext" do
            context("In Progress", [%{"id" => "blocked"}])
          else
            result
          end

        {:ok, %{status: 200, body: body}}
      end

      assert {:error, :linear_block_update_failed} = Client.block_issue("issue", request_fun: request)
    end
  end

  defp context(state, nodes), do: %{"data" => %{"issue" => %{"state" => %{"name" => state}, "team" => %{"states" => %{"nodes" => nodes}}}}}

  defp read_only_request(body) do
    fn payload, _ ->
      assert payload["query"] =~ "SymphonyBlockContext"
      {:ok, %{status: 200, body: body}}
    end
  end
end
