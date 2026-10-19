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

  test "unknown quota reset persists and does not create speculative probes", ctx do
    assert AgentPause.current() == :ready
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile})
    assert {:paused, pause} = AgentPause.current()
    assert pause["resets_at"] == nil
    assert {:paused, ^pause} = AgentPause.claim_due_probe()
    assert File.regular?(Path.join(ctx.root, "claude-pause.json"))
    # State comes entirely from disk, independent of an agent/scheduler process.
    assert Task.async(fn -> AgentPause.current() end) |> Task.await() == {:paused, pause}
    assert :ok = AgentPause.explicit_recheck(@profile)
    assert {:probe, _} = AgentPause.current()
    assert :ok = AgentPause.complete_probe(@profile)
    assert AgentPause.current() == :ready
  end

  test "reliable reset permits one persisted serialized probe, and a failed probe re-pauses", ctx do
    now = System.system_time(:second)
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile, resets_at: now + 60})
    assert {:error, :reset_not_due} = AgentPause.explicit_recheck(@profile)
    assert {:paused, _} = AgentPause.claim_due_probe()
    path = Path.join(ctx.root, "claude-pause.json")
    value = path |> File.read!() |> Jason.decode!() |> Map.put("resets_at", now - 1)
    File.write!(path, Jason.encode!(value))
    assert {:probe, _} = AgentPause.claim_due_probe()
    assert {:probe, _} = AgentPause.claim_due_probe()
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile, resets_at: now - 1})
    assert {:paused, %{"resets_at" => nil}} = AgentPause.claim_due_probe()
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

  test "tracker call intent survives a crash and needs explicit reconciliation" do
    assert :ok = AgentPause.begin_tool(@profile, "issue-one", "tool-one", "probe")
    assert {:error, :eexist} = AgentPause.begin_tool(@profile, "issue-one", "tool-one", "probe")
    assert {:paused, %{"kind" => "tool_outcome_uncertain"}} = AgentPause.current()
    assert {:error, :tool_reconciliation_required} = AgentPause.explicit_recheck(@profile)
    assert :ok = AgentPause.explicit_recheck(@profile, confirm_tools_reconciled: true)
    assert AgentPause.current() == :ready
    assert :ok = AgentPause.begin_tool(@profile, "issue-one", "tool-two", "probe")
    assert :ok = AgentPause.finish_tool("tool-two")
    assert AgentPause.current() == :ready
    assert {:error, :invalid_tool_id} = AgentPause.begin_tool(@profile, "issue-one", "../escape", "probe")
    assert {:error, :invalid_tool_id} = AgentPause.finish_tool(nil)
  end

  test "a due quota probe cannot proceed when durable state cannot be written", ctx do
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile})
    path = Path.join(ctx.root, "claude-pause.json")
    value = path |> File.read!() |> Jason.decode!() |> Map.put("resets_at", System.system_time(:second) - 1)
    File.write!(path, Jason.encode!(value))
    File.chmod!(ctx.root, 0o500)

    try do
      assert {:paused, %{"kind" => "policy_blocked"}} = AgentPause.claim_due_probe()
    after
      File.chmod!(ctx.root, 0o700)
    end
  end

  test "failed filesystem operations never clear blockers or pending tools", ctx do
    assert {:error, :invalid_profile} = AgentPause.explicit_recheck(12)
    assert :ok = AgentPause.record(:quota_exhausted, %{profile: @profile})
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
    assert {:paused, %{"kind" => "policy_blocked"}} = AgentPause.current()
    assert {:error, :unsafe_state_directory} = AgentPause.explicit_recheck(@profile)
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
    assert {:paused, %{"kind" => "policy_blocked"}} = AgentPause.current()
    assert {:error, :unknown_state_file} = AgentPause.explicit_recheck(@profile, confirm_tools_reconciled: true)
  end
end
