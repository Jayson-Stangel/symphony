defmodule SymphonyElixir.AgentReadinessTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixir.{AgentPause, AgentReadiness}

  setup do
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

  test "model-free readiness validates exact runtime, profile and attestation metadata", ctx do
    good = ready()

    for value <- [good, Map.put(good, "profile", "invalid"), Map.put(good, "attested_at", "invalid"), Map.put(good, "sdk", "unqualified"), %{"code" => "auth_required"}, %{"code" => "secret-canary"}] do
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

  test "drain survives orchestrator restart and account changes block explicit recovery", ctx do
    File.write!(ctx.helper, Jason.encode!(ready()))
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    assert {:ok, %{draining: true, running: 0}} = GenServer.call(pid, {:agent_control, :drain, false})
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    GenServer.stop(pid)
    {:ok, pid} = Orchestrator.start_link(name: __MODULE__.Probe, readiness_opts: ctx.opts)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    File.write!(ctx.helper, Jason.encode!(ready(String.duplicate("b", 64))))
    assert {:error, :agent_not_ready} = GenServer.call(pid, {:agent_control, :resume, false})
    assert Orchestrator.snapshot(__MODULE__.Probe, 5_000).agent.draining
    File.write!(ctx.helper, Jason.encode!(ready()))
    assert :ok = AgentPause.record(:auth_required, %{profile: String.duplicate("a", 64)})
    assert {:ok, %{draining: false}} = GenServer.call(pid, {:agent_control, :recheck, false})
    assert {:probe, _} = AgentPause.current()
    assert {:error, :invalid_action} = GenServer.call(pid, {:agent_control, :unknown, false})
    GenServer.stop(pid)
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

  defp ready(profile \\ String.duplicate("a", 64)) do
    %{"ready" => true, "profile" => profile, "sdk" => "0.3.303", "cli" => "2.1.303", "policy" => 1, "overages_disabled_attested" => true, "attested_at" => DateTime.to_iso8601(DateTime.utc_now())}
  end
end
