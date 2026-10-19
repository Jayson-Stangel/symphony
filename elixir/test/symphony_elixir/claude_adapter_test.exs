defmodule SymphonyElixir.ClaudeAdapterTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixir.{AgentPause, Claude.Adapter}

  @helper Path.expand("../../../runner/claude/test/fixtures/port-probe.mjs", __DIR__)

  defmodule BoundTool do
    @spec execute_agent_tool(String.t(), map(), keyword()) :: map()
    def execute_agent_tool("probe_tool", args, opts) do
      if args["query"] == "tool-uncertain", do: raise("tracker response lost")
      if args["query"] == "tool-throw", do: throw(:response_lost)
      settings = Keyword.fetch!(opts, :tracker_settings)
      send(settings.owner, {:bound_tool_executed, settings.tag, args, Keyword.get(opts, :issue)})
      if args["query"] == "tool-close", do: Port.close(settings.port)

      result(args["query"])
    end

    defp result(query) do
      case query do
        "tool-shape" -> :unexpected
        "tool-failed" -> %{"success" => false, "output" => "transport response lost"}
        "tool-read-error" -> %{"success" => false, "output" => "read response lost"}
        "tool-definite-error" -> %{"success" => false, "output" => Jason.encode!(%{status: 422})}
        "tool-server-error" -> %{"success" => false, "output" => Jason.encode!(%{status: 503})}
        _ -> %{"success" => true, "output" => "tracker result"}
      end
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

  test "account changes cannot switch the listener's pinned native identity", ctx do
    assert {:error, {:agent_pause, :policy_blocked, _}} =
             Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :expected_profile, String.duplicate("b", 64)))

    assert {:paused, %{"kind" => "policy_blocked"}} = AgentPause.current()
  end

  test "a transient probe failure leaves a durable blocker instead of spending another probe", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    File.write!(Path.join(ctx.workspace, "peer-mode"), "events")
    assert {:ok, session} = Adapter.start_session(ctx.workspace, Keyword.put(ctx.opts, :helper_path, helper))
    profile = session.identity["profile"]
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: profile})
    assert :ok = AgentPause.explicit_recheck(profile)

    try do
      assert {:error, :claude_transient_error} = Adapter.run_turn(session, "transient_error", ctx.issue, [])
      assert {:paused, %{"kind" => "probe_inflight"}} = AgentPause.claim_due_probe()
      assert {:error, {:agent_pause, :policy_blocked, _}} = Adapter.run_turn(session, "must not run", ctx.issue, [])
    after
      Adapter.stop_session(session)
    end
  end

  test "unqualified handshakes and invalid native ownership cannot start a session", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    opts = Keyword.put(ctx.opts, :helper_path, helper)

    for mode <- ["auth-start", "error-start", "bad-version", "bad-profile", "bad-owned", "bad-pid"] do
      File.write!(Path.join(ctx.workspace, "peer-mode"), mode)
      assert {:error, {:agent_pause, _, _}} = Adapter.start_session(ctx.workspace, opts)
    end

    assert {:error, :claude_runtime_missing} = Adapter.start_session(ctx.workspace, Keyword.put(opts, :node_path, "/missing"))
  end

  test "uncorrelated, oversized and invalid stream frames fail closed", ctx do
    helper = Path.expand("../../../runner/claude/test/fixtures/adversarial-peer.mjs", __DIR__)
    File.write!(Path.join(ctx.workspace, "peer-mode"), "events")

    for prompt <- ["oversized", "array", "wrong-session", "usage-invalid", "usage-array", "unexpected", "ack-without-call", "unbound-tool", "nonsense", "exit"] do
      File.rm(Path.join(ctx.root, "state/claude-pause.json"))
      opts = Keyword.put(ctx.opts, :helper_path, helper)
      assert {:ok, session} = Adapter.start_session(ctx.workspace, opts)

      try do
        assert {:error, {:agent_pause, _, _}} = Adapter.run_turn(session, prompt, ctx.issue, [])
      after
        Adapter.stop_session(session)
      end
    end
  end

  test "lost tracker replies and missing acknowledgements retain uncertain intents", ctx do
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

    for prompt <- ["tool-noack", "tool-uncertain", "tool-throw", "tool-shape", "tool-close", "tool-failed", "tool-server-error", "tool-definite-error", "tool-read-error"] do
      File.rm_rf!(Path.join(ctx.root, "state"))
      assert {:ok, session} = Adapter.start_session(ctx.workspace, opts)
      binding = put_in(session.binding.tracker_settings[:port], session.port)

      try do
        result = Adapter.run_turn(binding, prompt, ctx.issue, [])

        if prompt in ["tool-definite-error", "tool-read-error"] do
          assert {:ok, _} = result
          assert :ready = AgentPause.current()
        else
          assert {:error, {:agent_pause, :tool_outcome_uncertain, _}} = result
          assert {:paused, %{"kind" => "tool_outcome_uncertain"}} = AgentPause.current()
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
    assert {:error, {:agent_pause, :protocol_error, _}} = Adapter.run_turn(session, "closed", ctx.issue, [])
    assert :ok = Adapter.stop_session(session)
  end

  test "a different account's probe cannot run and unwritable probe completion cannot clear policy", ctx do
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)
    other = String.duplicate("b", 64)
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: other})
    assert :ok = AgentPause.explicit_recheck(other)
    assert {:error, {:agent_pause, :policy_blocked, _}} = Adapter.run_turn(session, "must not run", ctx.issue, [])
    File.rm!(Path.join(ctx.root, "state/claude-pause.json"))
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: String.duplicate("a", 64)})
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

  test "existing tracker binding executes only through Elixir and clears durable intent after ack", ctx do
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

  test "quota survives helper replacement and prevents another turn", ctx do
    assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)

    try do
      assert {:error, {:agent_pause, :quota_exhausted, _}} = Adapter.run_turn(session, "quota", ctx.issue, [])
      assert {:paused, %{"kind" => "quota_exhausted", "resets_at" => nil}} = AgentPause.current()
      assert {:error, {:agent_pause, :quota_exhausted, _}} = Adapter.run_turn(session, "no request", ctx.issue, [])
    after
      Adapter.stop_session(session)
    end

    assert {:paused, _} = AgentPause.current()
  end

  test "partial output and malformed frames never count as completion", ctx do
    for prompt <- ["partial", "malformed"] do
      assert :ok = AgentPause.explicit_recheck(String.duplicate("a", 64))
      assert {:ok, session} = Adapter.start_session(ctx.workspace, ctx.opts)

      try do
        assert {:error, {:agent_pause, _, _}} = Adapter.run_turn(session, prompt, ctx.issue, [])
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
      assert {:error, {:agent_pause, :protocol_error, _}} = Adapter.run_turn(session, "silence", ctx.issue, [])
    after
      Adapter.stop_session(session)
    end
  end

  test "SSH and unsafe source workspace are rejected before any helper launch", ctx do
    assert {:error, :claude_ssh_unsupported} = Adapter.start_session(ctx.workspace, worker_host: "remote")
    assert {:error, {:invalid_workspace_cwd, :workspace_root, _}} = Adapter.start_session(Path.dirname(ctx.workspace), ctx.opts)
  end
end
