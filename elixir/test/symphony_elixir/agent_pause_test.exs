defmodule SymphonyElixir.AgentPauseTest do
  use ExUnit.Case, async: false
  alias SymphonyElixir.AgentPause

  @profile String.duplicate("a", 64)

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-pause-#{System.unique_integer([:positive])}")
    previous = Application.fetch_env(:symphony_elixir, :agent_state_root)
    Application.put_env(:symphony_elixir, :agent_state_root, root)

    on_exit(fn ->
      case previous do
        {:ok, value} -> Application.put_env(:symphony_elixir, :agent_state_root, value)
        :error -> Application.delete_env(:symphony_elixir, :agent_state_root)
      end

      File.rm_rf!(root)
    end)

    %{root: root}
  end

  test "safe startup causes survive a fresh reader and stronger winning pauses", ctx do
    details = %{profile: @profile, startup_code: AgentPause.safe_startup_code("unqualified_auth"), stage: "startup"}
    assert :ok = AgentPause.record(:auth_required, details)

    assert {:paused, %{"kind" => "auth_required", "code" => "unqualified_auth", "stage" => "startup"}} =
             Task.async(fn -> AgentPause.current() end) |> Task.await()

    assert :ok = AgentPause.record(:policy_blocked, %{profile: @profile, code: "cleanup_restart_required", stage: "cleanup"})
    winning = AgentPause.current()
    assert :ok = AgentPause.record(:auth_required, details)
    assert AgentPause.current() == winning
    assert {:paused, %{"kind" => "policy_blocked", "code" => "cleanup_restart_required", "stage" => "cleanup"}} = winning
    refute File.read!(Path.join(ctx.root, "claude-pause.json")) =~ "startup_code"
    assert :ok = AgentPause.record(:policy_blocked, %{profile: @profile})
    assert {:paused, replaced} = AgentPause.current()
    refute Map.has_key?(replaced, "code")
  end

  test "cooldown merge retains metadata from the winning reset", ctx do
    reset = System.system_time(:second) + 60
    # Optional metadata is observational; it never changes the existing reset policy.
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile, resets_at: reset, code: "runtime_unavailable", stage: "startup"})
    winning = AgentPause.current()
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile, resets_at: reset - 10, code: "protocol_error", stage: "startup"})
    assert AgentPause.current() == winning
    path = Path.join(ctx.root, "claude-pause.json")
    value = path |> File.read!() |> Jason.decode!() |> Map.put("resets_at", reset - 120)
    File.write!(path, Jason.encode!(value))
    assert :ready = AgentPause.current()
  end

  test "invalid optional diagnostics neither change pause semantics nor persist provider text", ctx do
    assert AgentPause.safe_startup_code("secret-canary") == "runtime_unavailable"
    assert :ok = AgentPause.record(:auth_required, %{profile: @profile, code: "secret-canary", stage: "startup"})
    assert {:paused, value} = AgentPause.current()
    refute Map.has_key?(value, "code")
    path = Path.join(ctx.root, "claude-pause.json")
    refute File.read!(path) =~ "secret-canary"

    for metadata <- [%{"code" => %{"secret-canary" => true}, "stage" => "startup"}, %{"code" => "auth_required", "stage" => "secret-canary"}, %{}] do
      File.write!(path, Jason.encode!(Map.merge(value, metadata)))
      assert {:paused, ^value} = Task.async(fn -> AgentPause.current() end) |> Task.await()
    end
  end

  test "unknown quota resets leave backoff to the orchestrator, including after restart", ctx do
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile})
    assert File.regular?(Path.join(ctx.root, "claude-pause.json"))
    assert :ready = Task.async(fn -> AgentPause.current() end) |> Task.await()
    assert :ok = AgentPause.begin_turn(@profile)
    assert :ready = AgentPause.current()
  end

  test "a reliable reset is a durable cooldown that expires without a probe", ctx do
    now = System.system_time(:second)
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile, resets_at: now + 60})
    assert {:error, :reset_not_due} = AgentPause.explicit_recheck(@profile)
    assert {:paused, _} = AgentPause.current()
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile})
    assert {:paused, %{"resets_at" => retained_reset}} = AgentPause.current()
    assert retained_reset == now + 60
    path = Path.join(ctx.root, "claude-pause.json")
    value = path |> File.read!() |> Jason.decode!() |> Map.put("resets_at", now - 1)
    File.write!(path, Jason.encode!(value))
    assert :ready = Task.async(fn -> AgentPause.current() end) |> Task.await()
    assert :ok = AgentPause.begin_turn(@profile)
    assert :ready = AgentPause.current()
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile, resets_at: now + 120})
    assert {:paused, %{"resets_at" => reset}} = AgentPause.current()
    assert reset == now + 120
  end

  test "authentication and policy blockers cannot be overwritten by quota or healthy in-flight completion" do
    assert :ok = AgentPause.record(:auth_required, %{profile: @profile})
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile, resets_at: System.system_time(:second) + 60})
    assert :ok = AgentPause.complete_probe(@profile)
    assert {:paused, %{"kind" => "auth_required"}} = AgentPause.current()
    assert {:error, :profile_changed} = AgentPause.explicit_recheck(String.duplicate("b", 64))
    assert :ok = AgentPause.record(:some_unknown_error, %{profile: @profile})
    assert {:paused, %{"kind" => "policy_blocked"}} = AgentPause.current()
  end

  test "legacy quota probes cannot strand handoff or restart recovery", ctx do
    File.mkdir_p!(ctx.root)

    for {kind, phase} <- [{"probe_inflight", "waiting"}, {"quota_exhausted", "probe"}] do
      File.write!(Path.join(ctx.root, "claude-pause.json"), Jason.encode!(%{version: 1, kind: kind, phase: phase, profile: @profile, resets_at: nil}))
      assert :ready = Task.async(fn -> AgentPause.current() end) |> Task.await()
      assert :ok = AgentPause.begin_turn(@profile)
      assert :ok = AgentPause.cancel_recheck(@profile)
      assert :ready = AgentPause.current()
      path = Path.join(ctx.root, "claude-pause.json")
      legacy = path |> File.read!() |> Jason.decode!() |> Map.delete("resets_at")
      File.write!(path, Jason.encode!(legacy))
      assert :ready = AgentPause.current()
    end
  end

  test "an explicit policy recheck still checks identity and durable input consumption", ctx do
    assert :ok = AgentPause.record(:policy_blocked, %{profile: @profile})
    assert :ok = AgentPause.explicit_recheck(@profile)
    assert {:error, :probe_not_available} = AgentPause.begin_turn(String.duplicate("b", 64))
    File.chmod!(ctx.root, 0o500)

    try do
      assert {:error, _} = AgentPause.begin_turn(@profile)
    after
      File.chmod!(ctx.root, 0o700)
    end

    assert :ok = AgentPause.complete_probe(@profile)
    assert :ready = AgentPause.current()
  end

  test "legacy tracker evidence survives without blocking dispatch or global recovery", ctx do
    tools = Path.join(ctx.root, "claude-tools")
    File.mkdir_p!(tools)
    path = Path.join(tools, "legacy.json")
    evidence = Jason.encode!(%{issue: "issue-one", tool: "linear_graphql", diagnostics: %{phase: "uncertain", secret: "secret-canary"}})
    File.write!(path, evidence)

    for kind <- ["tool_outcome_uncertain", "protocol_error"] do
      File.write!(Path.join(ctx.root, "claude-pause.json"), Jason.encode!(%{version: 1, kind: kind, phase: "waiting", profile: @profile, resets_at: nil}))
      assert :ready = Task.async(fn -> AgentPause.current() end) |> Task.await()
      assert AgentPause.recovery_notice("issue-one") =~ "1 tracker call"
      assert AgentPause.recovery_notice("issue-two") == ""
      refute AgentPause.recovery_notice("issue-one") =~ "secret-canary"
      assert :ok = AgentPause.record(:auth_required, %{profile: @profile})
      assert {:paused, %{"kind" => "auth_required"}} = AgentPause.current()
      assert :ok = AgentPause.explicit_recheck(@profile)
      assert :ok = AgentPause.begin_turn(@profile)
      assert :ok = AgentPause.complete_probe(@profile)
      assert File.read!(path) == evidence
    end

    assert :ok = AgentPause.record(:tool_outcome_uncertain, %{profile: @profile})
    assert :ready = AgentPause.current()
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile})
    assert :ready = AgentPause.current()
    assert :ok = AgentPause.record(:protocol_error, %{profile: @profile})
    assert :ready = AgentPause.current()
  end

  test "optional legacy evidence cleanup is explicit and unreadable evidence stays issue-local", ctx do
    tools = Path.join(ctx.root, "claude-tools")
    File.mkdir_p!(tools)
    marker = Path.join(tools, "old.json")
    File.write!(marker, Jason.encode!(%{issue: "one"}))
    assert :ok = AgentPause.explicit_recheck(@profile)
    assert File.regular?(marker)
    assert :ok = AgentPause.explicit_recheck(@profile, confirm_tools_reconciled: true)
    refute File.exists?(marker)
    File.chmod!(ctx.root, 0o000)

    try do
      assert AgentPause.recovery_notice("one") =~ "could not be read"
    after
      File.chmod!(ctx.root, 0o700)
    end

    assert :ready = AgentPause.current()
  end

  test "expected handoff clears only a non-quota recheck with no pending effects", ctx do
    assert :ok = AgentPause.record(:policy_blocked, %{profile: @profile})
    assert :ok = AgentPause.explicit_recheck(@profile)
    assert :ok = AgentPause.begin_turn(@profile)
    assert {:paused, %{"kind" => "operator_recheck_inflight"}} = AgentPause.current()
    assert :ok = AgentPause.cancel_recheck(String.duplicate("b", 64))
    File.chmod!(ctx.root, 0o500)

    try do
      assert {:error, _} = AgentPause.cancel_recheck(@profile)
    after
      File.chmod!(ctx.root, 0o700)
    end

    assert :ok = AgentPause.cancel_recheck(@profile)
    assert :ready = AgentPause.current()
    assert :ok = AgentPause.record(:policy_blocked, %{profile: @profile})
    assert :ok = AgentPause.explicit_recheck(@profile)
    assert :ok = AgentPause.begin_turn(@profile)
    assert :ok = AgentPause.complete_probe(@profile)
    assert :ready = AgentPause.current()
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile})
    assert :ok = AgentPause.begin_turn(@profile)
    assert :ok = AgentPause.cancel_recheck(@profile)
    assert :ready = AgentPause.current()
  end

  test "failed filesystem operations never clear blockers or pending tools", ctx do
    assert {:error, :invalid_profile} = AgentPause.explicit_recheck(12)
    assert :ok = AgentPause.record(:policy_blocked, %{profile: @profile})
    assert :ok = AgentPause.explicit_recheck(@profile)
    assert {:error, :pause_not_clearable} = AgentPause.complete_probe(String.duplicate("b", 64))
    File.rm!(Path.join(ctx.root, "claude-pause.json"))
    assert :ok = AgentPause.record(:auth_required, %{profile: 12})
    assert {:paused, %{"profile" => nil}} = AgentPause.current()
    tools = Path.join(ctx.root, "claude-tools")
    File.mkdir_p!(Path.join(tools, "unresolved.json"))
    assert {:error, :eperm} = AgentPause.explicit_recheck(@profile, confirm_tools_reconciled: true)
    File.rm_rf!(tools)
    File.write!(tools, "not a directory")
    assert {:paused, %{"kind" => "auth_required"}} = AgentPause.current()
    assert {:error, :unsafe_state_directory} = AgentPause.explicit_recheck(@profile, confirm_tools_reconciled: true)
    blocked = Path.join(ctx.root, "blocked")
    File.write!(blocked, "not a directory")
    Application.put_env(:symphony_elixir, :agent_state_root, blocked)
    assert {:paused, %{"kind" => "policy_blocked"}} = AgentPause.current()
    assert {:error, _} = AgentPause.record(:quota_exhausted)
    assert {:error, _} = AgentPause.explicit_recheck(@profile)
  end

  test "corrupt and oversized durable state fails closed without exposing raw values", ctx do
    File.mkdir_p!(ctx.root)
    path = Path.join(ctx.root, "claude-pause.json")

    for data <- ["not JSON", String.duplicate("secret-canary", 1000), Jason.encode!(%{version: 1, kind: "auth_required", phase: "probe", profile: @profile})] do
      File.write!(path, data)
      assert {:paused, %{"kind" => "policy_blocked"} = safe} = AgentPause.current()
      refute inspect(safe) =~ "secret-canary"
    end

    File.rm!(path)
    File.mkdir_p!(Path.join(ctx.root, "claude-tools"))
    File.write!(Path.join([ctx.root, "claude-tools", "unknown.file"]), "secret-canary")
    assert :ready = AgentPause.current()
    assert AgentPause.recovery_notice("issue") =~ "could not be read"
    assert {:error, :unknown_state_file} = AgentPause.explicit_recheck(@profile, confirm_tools_reconciled: true)
  end
end
