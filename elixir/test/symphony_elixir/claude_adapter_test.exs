defmodule SymphonyElixir.ClaudeAdapterTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixir.{AgentPause, Claude.Adapter, Linear.AgentTool}

  @helper Path.expand("../../../runner/claude/test/fixtures/port-probe.mjs", __DIR__)

  defmodule BoundTool do
    alias SymphonyElixir.Linear.AgentTool

    @spec execute_agent_tool(String.t(), map(), keyword()) :: map()
    def execute_agent_tool("probe_tool", args, opts) do
      if args["query"] == "tool-uncertain", do: raise("tracker response lost")
      if args["query"] == "tool-throw", do: throw(:response_lost)
      settings = Keyword.fetch!(opts, :tracker_settings)
      send(settings.owner, {:bound_tool_executed, settings.tag, args, Keyword.get(opts, :issue)})
      if args["query"] == "tool-close", do: Port.close(settings.port)

      result(args["query"])
    end

    defp result("tool-linear-definite-error"), do: linear_error({:error, {:linear_api_status, 400}})
    defp result("tool-linear-server-error"), do: linear_error({:error, {:linear_api_status, 503}})

    defp result("tool-linear-read-error"), do: linear_error({:ok, %{"data" => nil, "errors" => [%{"message" => "read failed", "extensions" => %{"code" => "INPUT_ERROR"}}]}}, "query { viewer { id } }")
    defp result("tool-linear-read-timeout"), do: linear_error({:error, {:linear_api_request, :timeout}}, "query { viewer { id } }")
    defp result("tool-linear-local-error"), do: AgentTool.execute("linear_graphql", %{}, linear_client: fn _, _, _ -> raise "must not reach network" end)

    defp result("tool-linear-partial-error") do
      response = %{
        "data" => %{"issueUpdate" => nil},
        "errors" => [%{"message" => "partial mutation failure"}],
        "error" => %{"status" => 400}
      }

      linear_error({:ok, response})
    end

    defp result(query) do
      case query do
        "tool-invalid-success" -> %{"success" => "invalid", "output" => "malformed reply", "execution" => %{"certainty" => "confirmed"}}
        "tool-shape" -> :unexpected
        "tool-failed" -> %{"success" => false, "output" => "transport response lost"}
        "tool-read-error" -> %{"success" => false, "output" => "read response lost"}
        "tool-definite-error" -> %{"success" => false, "output" => Jason.encode!(%{status: 422})}
        "tool-server-error" -> %{"success" => false, "output" => Jason.encode!(%{status: 503})}
        _ -> %{"success" => true, "output" => "tracker result"}
      end
    end

    defp linear_error(response, query \\ "mutation { issueUpdate(id: \"one\", input: {title: \"x\"}) { success } }") do
      AgentTool.execute("linear_graphql", %{"query" => query}, linear_client: fn _q, _v, _opts -> response end)
    end
  end

  defmodule RecoverableLinearTool do
    alias SymphonyElixir.Linear.AgentTool

    @spec execute_agent_tool(String.t(), map(), keyword()) :: map()
    def execute_agent_tool("linear_graphql", args, opts) do
      settings = Keyword.fetch!(opts, :tracker_settings)
      send(settings.owner, {:linear_recovery_call, args["query"]})

      response =
        case args["query"] do
          "mutation { rejected }" -> %{"errors" => [%{"message" => "Unknown field rejected"}]}
          "mutation { corrected }" -> %{"data" => %{"corrected" => %{"success" => true}}}
          "mutation { uncertain }" -> %{"data" => %{"issueUpdate" => nil}, "errors" => [%{"message" => "partial write"}]}
          ~s|query { issue(id: "one") { id title } }| -> %{"data" => %{"issue" => %{"id" => "one", "title" => "x"}}}
        end

      AgentTool.execute("linear_graphql", args, linear_client: fn _, _, _ -> {:ok, response} end)
    end
  end

  setup do
    SymphonyElixir.TestSupport.isolate_inference_environment()
    root = Path.join(System.tmp_dir!(), "symphony-claude-adapter-#{System.unique_integer([:positive])}")
    workspace = Path.join(root, "workspaces/issue")
    File.mkdir_p!(workspace)
    previous = Application.fetch_env(:symphony_elixir, :agent_state_root)
    Application.put_env(:symphony_elixir, :agent_state_root, Path.join(root, "state"))
    write_workflow_file!(Workflow.workflow_file_path(), workspace_root: Path.join(root, "workspaces"), tracker_kind: "memory")

    on_exit(fn ->
      case previous do
        {:ok, value} -> Application.put_env(:symphony_elixir, :agent_state_root, value)
        :error -> Application.delete_env(:symphony_elixir, :agent_state_root)
      end

      File.rm_rf!(root)
    end)

    opts = [node_path: System.find_executable("node"), helper_path: @helper, cleanup_path: Path.expand("../../../runner/claude/cleanup.mjs", __DIR__)]
    %{root: root, workspace: workspace, opts: opts, issue: %Issue{id: "one", identifier: "PROBE-1", title: "Probe"}}
  end

  test "configured effort reaches the SDK and workflow reload changes only new sessions", ctx do
    contents = File.read!(Workflow.workflow_file_path())
    File.write!(Workflow.workflow_file_path(), String.replace(contents, "---\n", "---\nclaude:\n  effort: xhigh\n", global: false))
    WorkflowStore.force_reload()
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)

    try do
      File.write!(Workflow.workflow_file_path(), String.replace(contents, "---\n", "---\nclaude:\n  effort: low\n", global: false))
      WorkflowStore.force_reload()
      assert {:ok, _} = Adapter.run_turn(session, "effort:xhigh", ctx.issue, [])
      assert {:ok, next_session} = Adapter.start_session(ctx.workspace, ctx.opts)

      try do
        assert {:ok, _} = Adapter.run_turn(next_session, "effort:low", ctx.issue, [])
      after
        Adapter.stop_session(next_session)
      end
    after
      Adapter.stop_session(session)
    end
  end

  test "Claude receives a mutation rejection and corrects it through the production helper", ctx do
    binding = %{
      adapter: RecoverableLinearTool,
      tracker_settings: %{owner: self()},
      tool_specs: AgentTool.tool_specs()
    }

    assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :tool_binding, binding))

    try do
      assert {:ok, _} = Adapter.run_turn(session, "linear-recovery", ctx.issue, [])
      assert_receive {:linear_recovery_call, "mutation { rejected }"}
      assert_receive {:linear_recovery_call, "mutation { corrected }"}
      assert :ready = AgentPause.current()
      assert Path.wildcard(Path.join(ctx.root, "state/claude-tools/*.json")) == []
    after
      Adapter.stop_session(session)
    end
  end

  test "Claude receives an uncertain mutation error and reconciles in the same session", ctx do
    binding = %{adapter: RecoverableLinearTool, tracker_settings: %{owner: self()}, tool_specs: AgentTool.tool_specs()}
    assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :tool_binding, binding))

    try do
      assert {:ok, _} = Adapter.run_turn(session, "linear-uncertain-recovery", ctx.issue, [])
      assert_receive {:linear_recovery_call, "mutation { uncertain }"}
      assert_receive {:linear_recovery_call, ~s|query { issue(id: "one") { id title } }|}
      assert :ready = AgentPause.current()
    after
      Adapter.stop_session(session)
    end
  end

  test "account changes cannot switch the listener's pinned native identity", ctx do
    assert {:error, {:agent_pause, :policy_blocked, _}} =
             Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :expected_profile, String.duplicate("b", 64)))

    assert {:paused, %{"kind" => "policy_blocked"}} = AgentPause.current()
  end

  test "a transient error after quota recovery remains eligible for scheduler backoff", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    File.write!(Path.join(ctx.workspace, "peer-mode"), "events")
    assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :helper_path, helper))
    profile = session.identity["profile"]
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: profile})
    assert :ok = AgentPause.explicit_recheck(profile)

    try do
      assert {:error, :claude_transient_error} = Adapter.run_turn(session, "transient_error", ctx.issue, [])
      assert :ready = AgentPause.current()
    after
      Adapter.stop_session(session)
    end
  end

  test "unqualified handshakes and invalid native ownership cannot start a session", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    opts = Keyword.put(ctx.opts, :helper_path, helper)

    for mode <- ["auth-hello", "auth-start", "error-start", "bad-version", "bad-protocol", "bad-policy", "bad-sdk", "bad-profile", "bad-owned", "bad-pid"] do
      File.write!(Path.join(ctx.workspace, "peer-mode"), mode)
      assert {:error, {:agent_pause, _, _}} = Adapter.start_session(ctx.workspace, opts)
    end

    assert {:error, :claude_runtime_missing} = Adapter.start_session(ctx.workspace, Keyword.put(opts, :node_path, "/missing"))
  end

  test "compatible helper dependency metadata upgrades do not require an Elixir version update", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    File.write!(Path.join(ctx.workspace, "peer-mode"), "future-runtime")
    assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :helper_path, helper))
    assert :ok = Adapter.stop_session(session)
    assert :ready = AgentPause.current()
  end

  test "startup protocol failure retries only after a correlated cleanup acknowledgement", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)

    for {mode, code} <- [{"protocol-start", "protocol_error"}, {"timeout-start", "native_initialization_timeout"}, {"stopped-start", "protocol_error"}] do
      File.write!(Path.join(ctx.workspace, "peer-mode"), mode)
      assert {:error, {:claude_startup_failed, ^code}} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :helper_path, helper))
    end

    assert :ready = AgentPause.current()
    refute File.exists?(Path.join(ctx.root, "state/claude-cleanup-unconfirmed"))
    File.write!(Path.join(ctx.workspace, "peer-mode"), "events")
    assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :helper_path, helper))
    assert :ok = Adapter.stop_session(session)
  end

  test "lost or foreign startup cleanup acknowledgement retains the cause and blocks recovery", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)

    for mode <- ["lost-start", "wrong-stop", "wrong-session-stop", "silent-stop"] do
      File.write!(Path.join(ctx.workspace, "peer-mode"), mode)

      assert {:error, {:agent_pause, :policy_blocked, %{startup_code: "protocol_error", cleanup: :unconfirmed}}} =
               Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :helper_path, helper))

      assert File.exists?(Path.join(ctx.root, "state/claude-cleanup-unconfirmed"))
      assert {:paused, %{"kind" => "policy_blocked"}} = AgentPause.current()
    end
  end

  @tag skip: :os.type() != {:unix, :linux}
  test "Linux startup rejection confirms real native process cleanup before retry result", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/startup-failure-probe.mjs", __DIR__)

    for mode <- ["initialization", "stream", "eof"] do
      File.write!(Path.join(ctx.workspace, "startup-fail-mode"), mode)
      assert {:error, {:claude_startup_failed, "protocol_error"}} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :helper_path, helper))
      owned = ctx.workspace |> Path.join("startup-owned.json") |> File.read!() |> Jason.decode!()

      assert is_integer(owned["pid"]) and owned["pid"] > 1
      assert is_binary(owned["start"])

      {output, 0} =
        System.cmd(
          System.find_executable("node"),
          ["--input-type=module", "-e", "import {ownedGroupAlive} from './runner/claude/processes.mjs'; console.log(ownedGroupAlive(JSON.parse(process.argv[1])));", Jason.encode!(owned)],
          cd: Path.expand("..", File.cwd!())
        )

      assert String.trim(output) == "false"
      assert :ready = AgentPause.current()
      refute File.exists?(Path.join(ctx.root, "state/claude-cleanup-unconfirmed"))
    end
  end

  test "uncorrelated, oversized and invalid stream frames fail closed", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    File.write!(Path.join(ctx.workspace, "peer-mode"), "events")

    for prompt <- ["oversized", "array", "wrong-session", "usage-invalid", "usage-array", "unexpected", "ack-without-call", "unbound-tool", "nonsense", "exit"] do
      File.rm(Path.join(ctx.root, "state/claude-pause.json"))
      opts = Keyword.put(ctx.opts, :helper_path, helper)
      assert {:ok, session} = Adapter.start_session(ctx.workspace, opts)

      try do
        assert {:error, {:agent_blocked, _}} = Adapter.run_turn(session, prompt, ctx.issue, [])
        assert :ready = AgentPause.current()
      after
        Adapter.stop_session(session)
      end
    end
  end

  test "missing tool acknowledgements block only the affected issue", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    File.write!(Path.join(ctx.workspace, "peer-mode"), "events")

    spec = %{
      "name" => "probe_tool",
      "description" => "probe",
      "inputSchema" => %{"type" => "object", "additionalProperties" => false, "properties" => %{"query" => %{"type" => "string"}}, "required" => ["query"]}
    }

    binding = %{
      adapter: BoundTool,
      tracker_settings: %{owner: self(), tag: "probe"},
      secret_environment_names: [],
      tool_specs: [spec]
    }

    opts = ctx.opts |> Keyword.put(:helper_path, helper) |> Keyword.put(:tool_binding, binding)

    for prompt <- [
          "tool-noack",
          "tool-uncertain",
          "tool-throw",
          "tool-shape",
          "tool-invalid-success",
          "tool-close",
          "tool-failed",
          "tool-server-error",
          "tool-definite-error",
          "tool-read-error",
          "tool-linear-definite-error",
          "tool-linear-server-error",
          "tool-linear-partial-error",
          "tool-linear-read-error",
          "tool-linear-read-timeout",
          "tool-linear-local-error"
        ] do
      File.rm_rf!(Path.join(ctx.root, "state"))
      assert {:ok, session} = Adapter.start_session(ctx.workspace, opts)
      binding = put_in(session.binding.tracker_settings[:port], session.port)

      try do
        result = Adapter.run_turn(binding, prompt, ctx.issue, [])

        if prompt in [
             "tool-definite-error",
             "tool-read-error",
             "tool-linear-definite-error",
             "tool-linear-server-error",
             "tool-linear-partial-error",
             "tool-linear-read-error",
             "tool-linear-read-timeout",
             "tool-linear-local-error"
           ] do
          assert {:ok, _} = result
          assert :ready = AgentPause.current()
        else
          assert {:error, {:agent_blocked, _}} = result
          assert :ready = AgentPause.current()
        end
      after
        Adapter.stop_session(session)
      end
    end
  end

  test "closed ports, unclean helper shutdown and explicit interrupts require bounded cleanup", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    cleanup = Path.join(ctx.root, "cleanup-ok.mjs")
    File.write!(cleanup, "process.exit(0);\n")

    for mode <- ["valid-owned", "exit-on-stop", "invalid-stop"] do
      File.write!(Path.join(ctx.workspace, "peer-mode"), mode)
      opts = ctx.opts |> Keyword.put(:helper_path, helper) |> Keyword.put(:cleanup_path, cleanup)
      assert {:ok, session} = Adapter.start_session(ctx.workspace, opts)
      # The fake cleanup owns no process; only the parent transport is under test.
      session = %{session | owned_native: [%{"pid" => 999_999, "start" => "0"}]}
      assert :ok = Adapter.stop_session(session)
    end

    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
    assert :ok = Adapter.interrupt(session, :operator)
    assert {:error, {:agent_blocked, "protocol_error"}} = Adapter.run_turn(session, "closed", ctx.issue, [])
    assert :ok = Adapter.stop_session(session)
  end

  test "scheduler handoff cancels a real waiting Port without recording a protocol failure", ctx do
    assert :ok = AgentPause.record(:policy_blocked, %{profile: String.duplicate("a", 64)})
    assert :ok = AgentPause.explicit_recheck(String.duplicate("a", 64))
    parent = self()

    worker =
      Task.async(fn ->
        assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
        send(parent, {:waiting_session, session})

        try do
          Adapter.run_turn(session, "silence", ctx.issue,
            on_message: fn
              %{event: :session_started} -> send(parent, :waiting_turn)
              _ -> :ok
            end
          )
        after
          Adapter.stop_session(session)
        end
      end)

    assert_receive {:waiting_session, session}, 1_000
    assert_receive :waiting_turn, 1_000
    assert :ok = Adapter.interrupt(session, :issue_state_change)
    assert {:error, :claude_cancelled} = Task.await(worker, 10_000)
    assert :ready = AgentPause.current()
  end

  test "quota recovery handoff cancels a real waiting Port without a quota latch", ctx do
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: String.duplicate("a", 64)})
    parent = self()

    worker =
      Task.async(fn ->
        assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
        send(parent, {:waiting_session, session})

        try do
          Adapter.run_turn(session, "silence", ctx.issue,
            on_message: fn
              %{event: :session_started} -> send(parent, :waiting_turn)
              _ -> :ok
            end
          )
        after
          Adapter.stop_session(session)
        end
      end)

    assert_receive {:waiting_session, session}, 1_000
    assert_receive :waiting_turn, 1_000
    assert :ok = Adapter.interrupt(session, :issue_state_change)
    assert {:error, :claude_cancelled} = Task.await(worker, 10_000)
    assert :ready = AgentPause.current()
  end

  test "a different account's probe cannot run and unwritable probe completion cannot clear policy", ctx do
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
    other = String.duplicate("b", 64)
    assert :ok = AgentPause.record(:policy_blocked, %{profile: other})
    assert :ok = AgentPause.explicit_recheck(other)
    assert {:error, {:agent_pause, :policy_blocked, _}} = Adapter.run_turn(session, "must not run", ctx.issue, [])
    File.rm!(Path.join(ctx.root, "state/claude-pause.json"))
    assert :ok = AgentPause.record(:policy_blocked, %{profile: String.duplicate("a", 64)})
    assert :ok = AgentPause.explicit_recheck(String.duplicate("a", 64))
    state_root = Path.join(ctx.root, "state")
    File.chmod!(state_root, 0o500)

    try do
      assert {:error, {:agent_pause, :policy_blocked, _}} = Adapter.run_turn(session, "must not run", ctx.issue, [])
    after
      File.chmod!(state_root, 0o700)
    end

    callback = fn
      %{event: :session_started} -> File.chmod!(state_root, 0o500)
      _ -> :ok
    end

    try do
      assert {:error, {:agent_pause, :policy_blocked, _}} = Adapter.run_turn(session, "success", ctx.issue, on_message: callback)
    after
      File.chmod!(state_root, 0o700)
      Adapter.stop_session(session)
    end
  end

  test "orchestrator Human Review reconciliation keeps the next issue dispatchable", ctx do
    profile = String.duplicate("a", 64)
    assert :ok = AgentPause.record(:policy_blocked, %{profile: profile})
    assert :ok = AgentPause.explicit_recheck(profile)
    parent = self()

    {pid, ref} =
      spawn_monitor(fn ->
        {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
        send(parent, {:handoff_session, session})

        try do
          Adapter.run_turn(session, "silence", ctx.issue,
            on_message: fn
              %{event: :session_started} -> send(parent, :handoff_turn)
              _ -> :ok
            end
          )
        after
          Adapter.stop_session(session)
        end
      end)

    assert_receive {:handoff_session, session}, 1_000
    assert_receive :handoff_turn, 1_000

    entry = %{
      pid: pid,
      ref: ref,
      identifier: ctx.issue.identifier,
      issue: ctx.issue,
      started_at: DateTime.utc_now(),
      agent_session: %{adapter: Adapter, native: session}
    }

    state = %Orchestrator.State{
      running: %{ctx.issue.id => entry},
      claimed: MapSet.new([ctx.issue.id]),
      codex_totals: %{input_tokens: 0, output_tokens: 0, total_tokens: 0, seconds_running: 0}
    }

    issue = %{ctx.issue | state: "Human Review", dispatchable: true}
    state = Orchestrator.reconcile_issue_states_for_test([issue], state)
    assert state.running == %{}
    refute MapSet.member?(state.claimed, ctx.issue.id)
    assert AgentPause.current() == :ready
  end

  test "missing cleanup executables do not falsely confirm an explicit interrupt", ctx do
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
    failed = %{session | node: "/missing-node", owned_native: [%{"pid" => 999_999, "start" => "0"}]}
    assert {:error, :cleanup_unconfirmed} = Adapter.interrupt(failed, :operator)
    # A fixture with no native subprocess still closes its owned Port on platforms
    # where /proc is absent. No external process can be targeted by this branch.
    assert :ok = Adapter.interrupt(%{session | helper_identity: nil, owned_native: []}, :operator)
  end

  test "typed issue blockers and transient failures remain distinct from account pauses", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    File.write!(Path.join(ctx.workspace, "peer-mode"), "events")

    for {prompt, expected} <- [
          {"permission_blocked", {:agent_blocked, "permission_blocked"}},
          {"agent_turn_limit", {:agent_blocked, "agent_turn_limit"}},
          {"transient_error", :claude_transient_error},
          {"cancelled", :claude_cancelled}
        ] do
      assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :helper_path, helper))

      try do
        assert {:error, ^expected} = Adapter.run_turn(session, prompt, ctx.issue, [])
        assert AgentPause.current() == :ready
      after
        Adapter.stop_session(session)
      end
    end
  end

  test "unconfirmed process cleanup requires updater recovery before readiness", ctx do
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
    Port.close(session.port)
    fake_cleanup = Path.join(ctx.root, "cleanup.mjs")
    File.write!(fake_cleanup, "process.exit(1);\n")
    session = %{session | owned_native: [%{"pid" => 999_999, "start" => "0"}], cleanup: fake_cleanup}
    assert {:error, :cleanup_unconfirmed} = Adapter.stop_session(session)
    assert File.regular?(Path.join(ctx.root, "state/claude-cleanup-unconfirmed"))
    previous = Application.fetch_env(:symphony_elixir, :coding_agent)

    try do
      Application.put_env(:symphony_elixir, :coding_agent, :claude)
      assert %{ready: false, code: "cleanup_restart_required"} = SymphonyElixir.AgentReadiness.check()
    after
      case previous do
        {:ok, value} -> Application.put_env(:symphony_elixir, :coding_agent, value)
        :error -> Application.delete_env(:symphony_elixir, :coding_agent)
      end
    end
  end

  test "Claude cumulative usage reaches the existing absolute-total reducer without double counting", ctx do
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
    owner = self()
    assert {:ok, _} = Adapter.run_turn(session, "usage", ctx.issue, on_message: fn update -> send(owner, {:usage_probe, update}) end)
    assert_received {:usage_probe, %{event: :token_usage_updated} = update}
    entry = %{session_id: nil, codex_input_tokens: 0, codex_output_tokens: 0, codex_total_tokens: 0, codex_app_server_pid: nil, issue: ctx.issue}
    state = %Orchestrator.State{running: %{ctx.issue.id => entry}, codex_totals: %{input_tokens: 0, output_tokens: 0, total_tokens: 0, seconds_running: 0}}
    assert {:noreply, state} = Orchestrator.handle_info({:codex_worker_update, ctx.issue.id, update}, state)
    assert state.codex_totals.total_tokens == 30
    assert state.agent_usage_observed
    assert {:noreply, state} = Orchestrator.handle_info({:codex_worker_update, ctx.issue.id, update}, state)
    assert state.codex_totals.total_tokens == 30
    assert :ok = Adapter.stop_session(session)
  end

  test "helper progress reaches the reducer and both dashboards without provider contents", ctx do
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
    owner = self()
    name = Module.concat(__MODULE__, :ProgressDelivery)
    server = start_supervised!({Orchestrator, name: name})

    try do
      assert {:ok, _} = Adapter.run_turn(session, "progress", ctx.issue, on_message: &send(owner, {:progress_probe, &1}))
      entry = %{session_id: nil, issue: ctx.issue, started_at: DateTime.utc_now()}
      state = %Orchestrator.State{running: %{ctx.issue.id => entry}, codex_totals: %{input_tokens: 0, output_tokens: 0, total_tokens: 0, seconds_running: 0}}

      for expected <- [
            "Waiting for agent activity",
            "Requested command",
            "Waiting for command",
            "Waiting for retry",
            "Compacting context",
            "Waiting for agent activity",
            "Requested file or web read",
            "Requested file edit",
            "Requested search",
            "Requested tool",
            "Agent response received"
          ] do
        assert_receive {:progress_probe, %{event: :agent_progress} = update}
        assert {:noreply, state} = Orchestrator.handle_info({:codex_worker_update, ctx.issue.id, update}, state)
        message = state.running[ctx.issue.id].last_codex_message
        assert SymphonyElixir.StatusDashboard.humanize_codex_message(message) == expected
        # The web presenter uses this same formatter for last_message/recent events.
        assert update.payload == expected
        refute inspect(update) =~ "SECRET"
        entry = Map.put(state.running[ctx.issue.id], :identifier, ctx.issue.identifier)
        state = %{state | running: %{ctx.issue.id => entry}}
        :sys.replace_state(server, fn _ -> state end)
        payload = SymphonyElixirWeb.Presenter.state_payload(name, 1_000)
        assert hd(payload.running).last_message == expected
      end
    after
      Adapter.stop_session(session)
    end
  end

  test "missing and untrusted progress details have a safe fallback", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    File.write!(Path.join(ctx.workspace, "peer-mode"), "events")
    assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :helper_path, helper))
    owner = self()

    try do
      assert {:ok, _} = Adapter.run_turn(session, "progress-fallback", ctx.issue, on_message: &send(owner, {:fallback_probe, &1}))

      for expected <- ["Waiting for agent activity", "Waiting for agent activity", "Waiting for tool"] do
        assert_receive {:fallback_probe, %{event: :agent_progress, payload: ^expected, productive: false} = update}
        refute inspect(update) =~ "SECRET"
      end

      for missing <- [nil, "", %{"raw" => "SECRET"}] do
        assert SymphonyElixir.StatusDashboard.humanize_codex_message(%{event: :agent_progress, message: missing}) == "Waiting for agent activity"
      end
    after
      Adapter.stop_session(session)
    end
  end

  test "actual provider tool contracts initialize the production MCP bridge", ctx do
    adapters = [
      SymphonyElixir.GitHub.Adapter,
      SymphonyElixir.GitLab.Adapter,
      SymphonyElixir.Asana.Adapter,
      SymphonyElixir.Jira.Adapter,
      SymphonyElixir.Linear.Adapter
    ]

    for adapter <- adapters do
      binding = %{
        adapter: adapter,
        tracker_settings: %{},
        secret_environment_names: [],
        tool_specs: adapter.agent_tool_specs()
      }

      assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :tool_binding, binding))
      assert :ok = Adapter.stop_session(session)
    end
  end

  test "real helper process completes two turns with safe progress and cumulative usage", ctx do
    assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :expected_profile, String.duplicate("a", 64)))
    owner = self()

    try do
      for prompt <- ["first", "second"] do
        assert {:ok, %{provider: :claude, session_id: id}} = Adapter.run_turn(session, prompt, ctx.issue, on_message: fn message -> send(owner, {:event, message}) end)
        assert String.starts_with?(id, "claude:")
      end

      assert_received {:event, %{event: :session_started, provider: :claude}}
      assert_received {:event, %{event: :token_usage_updated, usage: %{"total_tokens" => 30}}}
      assert Task.async(fn -> Adapter.run_turn(session, "wrong owner", ctx.issue, []) end) |> Task.await() == {:error, :wrong_session_owner}
    after
      assert :ok = Adapter.stop_session(session)
      assert :ok = Adapter.stop_session(session)
    end
  end

  test "existing tracker binding executes only through Elixir and acknowledges delivery", ctx do
    binding = %{
      adapter: BoundTool,
      tracker_settings: %{owner: self(), tag: "original"},
      secret_environment_names: [],
      tool_specs: [
        %{
          "name" => "probe_tool",
          "description" => "probe",
          "inputSchema" => %{
            "type" => "object",
            "additionalProperties" => false,
            "required" => ["query"],
            "properties" => %{"query" => %{"type" => "string"}}
          }
        }
      ]
    }

    assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :tool_binding, binding))

    try do
      write_workflow_file!(Workflow.workflow_file_path(), workspace_root: Path.join(ctx.root, "workspaces"), tracker_kind: "memory", max_turns: 3)
      assert {:ok, _} = Adapter.run_turn(session, "tool", ctx.issue, [])
      assert_received {:bound_tool_executed, "original", %{"query" => "query"}, %{id: "one"}}
      assert AgentPause.current() == :ready
    after
      Adapter.stop_session(session)
    end
  end

  test "Fable weekly quota persists its reset and recovers automatically after the cooldown after helper replacement", ctx do
    reset = System.system_time(:second) + 3_600
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)

    try do
      assert {:error, {:agent_pause, :quota_exhausted, _}} = Adapter.run_turn(session, "fable-quota:#{reset}", ctx.issue, [])
      assert {:paused, %{"kind" => "quota_exhausted", "resets_at" => ^reset}} = AgentPause.current()
      assert {:paused, _} = AgentPause.current()
      assert {:error, {:agent_pause, :quota_exhausted, _}} = Adapter.run_turn(session, "no request", ctx.issue, [])
    after
      Adapter.stop_session(session)
    end

    assert {:paused, %{"kind" => "quota_exhausted", "resets_at" => ^reset}} = AgentPause.current()
    path = Path.join(ctx.root, "state/claude-pause.json")
    expired = path |> File.read!() |> Jason.decode!() |> Map.put("resets_at", System.system_time(:second) - 1)
    File.write!(path, Jason.encode!(expired))
    assert :ready = AgentPause.current()
    assert {:ok, replacement} = Adapter.start_session(ctx.workspace, ctx.opts)

    try do
      assert {:ok, _} = Adapter.run_turn(replacement, "reset probe", ctx.issue, [])
      assert AgentPause.current() == :ready
    after
      Adapter.stop_session(replacement)
    end
  end

  test "unknown quota resets return control to scheduler retries without a durable latch", ctx do
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)

    try do
      assert {:error, {:agent_pause, :quota_exhausted, _}} = Adapter.run_turn(session, "quota", ctx.issue, [])
      assert :ready = AgentPause.current()
    after
      Adapter.stop_session(session)
    end

    assert {:ok, replacement} = Adapter.start_session(ctx.workspace, ctx.opts)

    try do
      assert {:ok, _} = Adapter.run_turn(replacement, "recovered", ctx.issue, [])
      assert :ready = AgentPause.current()
    after
      Adapter.stop_session(replacement)
    end
  end

  test "partial output and malformed frames never count as completion", ctx do
    for prompt <- ["partial", "malformed"] do
      assert :ok = AgentPause.explicit_recheck(String.duplicate("a", 64))
      assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)

      try do
        assert {:error, {:agent_blocked, _}} = Adapter.run_turn(session, prompt, ctx.issue, [])
        assert :ready = AgentPause.current()
      after
        Adapter.stop_session(session)
      end
    end
  end

  test "native authentication errors pause and silence has a bounded deadline", ctx do
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)

    try do
      assert {:error, {:agent_pause, :auth_required, _}} = Adapter.run_turn(session, "auth", ctx.issue, [])
    after
      Adapter.stop_session(session)
    end

    assert :ok = AgentPause.explicit_recheck(String.duplicate("a", 64))
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
    session = %{session | config: %{session.config | turn_timeout_ms: 30, read_timeout_ms: 10}}

    try do
      assert {:error, {:agent_blocked, "protocol_error"}} = Adapter.run_turn(session, "silence", ctx.issue, [])
    after
      Adapter.stop_session(session)
    end
  end

  test "SSH and unsafe source workspace are rejected before any helper launch", ctx do
    assert {:error, :claude_ssh_unsupported} = Adapter.start_session(ctx.workspace, worker_host: "remote")
    assert {:error, {:invalid_workspace_cwd, :workspace_root, _}} = Adapter.start_session(Path.dirname(ctx.workspace), ctx.opts)
  end
end
