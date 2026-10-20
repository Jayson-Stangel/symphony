defmodule SymphonyElixir.AgentReadinessTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixir.{AgentPause, AgentReadiness}

  setup do
    SymphonyElixir.TestSupport.isolate_inference_environment()
    root = Path.join(System.tmp_dir!(), "symphony-readiness-#{System.unique_integer([:positive])}")
    File.mkdir_p!(root)
    keys = [:coding_agent, :agent_state_root]
    previous = Map.new(keys, &{&1, Application.fetch_env(:symphony_elixir, &1)})
    Application.put_env(:symphony_elixir, :coding_agent, :claude)
    Application.put_env(:symphony_elixir, :agent_state_root, Path.join(root, "state"))
    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", poll_interval_ms: 60_000)
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [])
    node = Path.join(root, "native-check")
    helper = Path.join(root, "output.json")
    File.write!(node, "#!/bin/sh\ncat \"$1\"\n")
    File.chmod!(node, 0o755)

    on_exit(fn ->
      Enum.each(previous, fn
        {key, {:ok, value}} -> Application.put_env(:symphony_elixir, key, value)
        {key, :error} -> Application.delete_env(:symphony_elixir, key)
      end)

      File.rm_rf!(root)
    end)

    %{root: root, helper: helper, node: node, opts: [node_path: node, helper_path: helper]}
  end

  test "model-free readiness validates protocol, bounded diagnostics, profile and attestation metadata", ctx do
    good = ready()

    for value <- [
          good,
          Map.put(good, "profile", "invalid"),
          Map.put(good, "attested_at", "invalid"),
          Map.put(good, "protocol", 99),
          Map.put(good, "sdk", String.duplicate("a", 129)),
          Map.put(good, "cli", nil),
          %{"code" => "auth_required"},
          %{"code" => "secret-canary"}
        ] do
      File.write!(ctx.helper, Jason.encode!(value))
      result = AgentReadiness.check(ctx.opts)
      assert result.ready == (value == good)
      refute inspect(result) =~ "secret-canary"
    end

    for data <- ["invalid", "[]", String.duplicate("secret-canary", 1000)] do
      File.write!(ctx.helper, data)
      assert %{ready: false, code: "runtime_unavailable"} = AgentReadiness.check(ctx.opts)
    end

    assert %{ready: false} = AgentReadiness.check(Keyword.put(ctx.opts, :helper_path, "/missing"))
    File.write!(ctx.helper, Jason.encode!(good))
    File.chmod!(ctx.node, 0o644)
    assert %{ready: false} = AgentReadiness.check(ctx.opts)
    previous = System.get_env("ANTHROPIC_API_KEY")

    try do
      System.put_env("ANTHROPIC_API_KEY", "secret-canary")
      assert %{ready: false, code: "conflicting_inference_environment"} = AgentReadiness.check(ctx.opts)
    after
      restore_env("ANTHROPIC_API_KEY", previous)
    end
  end

  test "compatible dependency upgrades remain ready under the existing protocol contract", ctx do
    upgraded = ready() |> Map.put("sdk", "0.999.0") |> Map.put("cli", "9.999.0")
    File.write!(ctx.helper, Jason.encode!(upgraded))
    assert %{ready: true, sdk: "0.999.0", cli: "9.999.0", protocol: 1} = AgentReadiness.check(ctx.opts)

    for bad <- [Map.delete(upgraded, "protocol"), Map.put(upgraded, "policy", 99), Map.put(upgraded, "sdk", ""), Map.put(upgraded, "cli", String.duplicate("a", 129))] do
      File.write!(ctx.helper, Jason.encode!(bad))
      assert %{ready: false, code: "runtime_unavailable"} = AgentReadiness.check(ctx.opts)
    end
  end

  test "Codex admission and resume ignore retained Claude pauses", ctx do
    Application.put_env(:symphony_elixir, :coding_agent, :codex)

    for kind <- [:auth_required, :quota_exhausted] do
      File.rm_rf!(Path.join(ctx.root, "state"))
      assert :ok = AgentPause.record(kind, %{profile: String.duplicate("a", 64), resets_at: System.system_time(:second) + 600})
      path = Path.join(ctx.root, "state/claude-pause.json")
      evidence = File.read!(path)
      {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe)
      assert %{ready: true, provider: "codex"} = Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.admission
      assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.pause == nil
      assert {:ok, _} = GenServer.call(pid, {:agent_control, :drain, false})
      assert {:ok, %{admission: %{ready: true}}} = GenServer.call(pid, {:agent_control, :recheck, false})
      assert File.read!(path) == evidence
      GenServer.stop(pid)
    end
  end

  test "resume refuses auth and policy pauses while quota cooldown is reported explicitly", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)

    for kind <- [:auth_required, :policy_blocked] do
      File.rm_rf!(Path.join(ctx.root, "state"))
      assert :ok = AgentPause.record(kind, %{profile: String.duplicate("a", 64)})
      assert {:ok, _} = GenServer.call(pid, {:agent_control, :drain, false})
      assert {:error, :agent_not_ready} = GenServer.call(pid, {:agent_control, :resume, false})
      assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
      assert {:paused, _} = AgentPause.current()
    end

    File.rm_rf!(Path.join(ctx.root, "state"))
    reset = System.system_time(:second) + 600
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: String.duplicate("a", 64), resets_at: reset})
    assert {:ok, %{draining: false, admission: %{ready: false, reason: "quota_exhausted", retry_at: ^reset}}} = GenServer.call(pid, {:agent_control, :resume, false})
    assert %{ready: false, reason: "quota_exhausted"} = Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.admission
    assert {:paused, _} = AgentPause.current()
  end

  test "drain survives orchestrator restart and account changes block explicit recovery", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    assert {:ok, %{draining: true, running: 0}} = GenServer.call(pid, {:agent_control, :drain, false})
    guard = Path.join([ctx.root, "state", "agent-upgrade-unresumed"])
    File.write!(guard, "unresumed\n")
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    GenServer.stop(pid)
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    File.write!(ctx.helper, Jason.encode!(ready(String.duplicate("b", 64))))
    assert {:error, :agent_not_ready} = GenServer.call(pid, {:agent_control, :resume, false})
    assert File.exists?(guard)
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    File.write!(ctx.helper, Jason.encode!(ready()))
    assert :ok = AgentPause.record(:auth_required, %{profile: String.duplicate("a", 64)})
    assert {:ok, %{draining: false}} = GenServer.call(pid, {:agent_control, :recheck, false})
    refute File.exists?(guard)
    assert {:ok, %{draining: true}} = GenServer.call(pid, {:agent_control, :drain, false})
    refute File.exists?(guard)
    assert {:probe, _} = AgentPause.current()
    assert {:error, :invalid_action} = GenServer.call(pid, {:agent_control, :unknown, false})
    GenServer.stop(pid)
  end

  test "resume stays drained if the durable upgrade marker cannot be invalidated", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    guard = Path.join([ctx.root, "state", "agent-upgrade-unresumed"])
    File.mkdir_p!(guard)
    File.write!(Path.join([ctx.root, "state", "agent-draining"]), "draining\n")
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    assert {:error, :agent_not_ready} = GenServer.call(pid, {:agent_control, :resume, false})
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    File.rmdir!(guard)
    assert {:ok, %{draining: false}} = GenServer.call(pid, {:agent_control, :resume, false})
  end

  test "unready startup does not clean terminal workspaces or begin work", ctx do
    workspace_root = Path.join(ctx.root, "workspaces")
    marker = Path.join(workspace_root, "PROBE-1/retained")
    File.mkdir_p!(Path.dirname(marker))
    File.write!(marker, "untracked work")
    issue = %Issue{id: "one", identifier: "PROBE-1", title: "Retain", state: "Done", dispatchable: false}
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [issue])
    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", workspace_root: workspace_root)
    File.write!(ctx.helper, Jason.encode!(%{code: "auth_required"}))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    snapshot = Orchestrator.snapshot(__MODULE__.Probe, 5_000)
    assert snapshot.agent.readiness.ready == false
    assert snapshot.running == []
    assert File.read!(marker) == "untracked work"
    GenServer.stop(pid)
  end

  test "Codex startup and restart keep legacy tracker evidence local to its issue", ctx do
    Application.put_env(:symphony_elixir, :coding_agent, :codex)
    profile = AgentPause.codex_profile()
    assert %{ready: true, provider: "codex", profile: ^profile} = AgentReadiness.check()
    tools = Path.join(ctx.root, "state/claude-tools")
    File.mkdir_p!(tools)
    marker = Path.join(tools, "codex-write.json")
    File.write!(marker, Jason.encode!(%{issue: "one", tool: "linear_graphql"}))
    File.write!(Path.join(ctx.root, "state/claude-pause.json"), Jason.encode!(%{version: 1, kind: "tool_outcome_uncertain", phase: "waiting", profile: profile, resets_at: nil}))
    write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory", workspace_root: Path.join(ctx.root, "workspaces"))
    issue = %Issue{id: "one", identifier: "PROBE-1", title: "Recover", state: "In Progress"}
    other = %{issue | id: "two", identifier: "PROBE-2"}

    for _restart <- 1..2 do
      {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe)

      try do
        snapshot = Orchestrator.snapshot(__MODULE__.Probe, 5_000)
        assert snapshot.agent.pause == nil
        assert :ready = AgentPause.current()
        assert PromptBuilder.build_prompt(issue) =~ "Legacy recovery evidence: 1"
        refute PromptBuilder.build_prompt(other) =~ "Legacy recovery evidence:"
        assert PromptBuilder.build_prompt(other) =~ "## Tracker recovery"
        assert File.regular?(marker)
      after
        GenServer.stop(pid)
      end
    end
  end

  defp ready(profile \\ String.duplicate("a", 64)) do
    %{
      "ready" => true,
      "protocol" => 1,
      "profile" => profile,
      "sdk" => "0.3.303",
      "cli" => "2.1.303",
      "policy" => 1,
      "overages_disabled_attested" => true,
      "attested_at" => DateTime.to_iso8601(DateTime.utc_now())
    }
  end
end
