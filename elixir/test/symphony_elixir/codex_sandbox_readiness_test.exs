defmodule SymphonyElixir.CodexSandboxReadinessTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixir.Codex.SandboxReadiness

  test "disposable diagnostics qualify real sandbox settings without tracker credentials" do
    previous = System.get_env("LINEAR_API_KEY")
    System.delete_env("LINEAR_API_KEY")
    on_exit(fn -> restore_env("LINEAR_API_KEY", previous) end)
    write_workflow_file!(Workflow.workflow_file_path(), tracker_api_token: "$LINEAR_API_KEY", codex_command: "credential-free-codex app-server")
    assert {:error, :missing_linear_api_token} = Config.validate!()

    assert :ok =
             SandboxReadiness.check(
               credential_free: true,
               execute: fn _, args, _ ->
                 input = args |> List.last() |> Jason.decode!()
                 assert input["policy"]["type"] == "workspaceWrite"
                 {:ok, root} = SymphonyElixir.PathSafety.canonicalize(input["workspace"])
                 assert input["policy"]["writableRoots"] == [root]
                 assert input["command"] == "credential-free-codex app-server"
                 {~s({"ready":true,"protocol":1}), 0}
               end
             )

    assert {:error, :missing_linear_api_token} = Config.validate!()
    refute SandboxReadiness.qualified?()
    write_workflow_file!(Workflow.workflow_file_path(), tracker_api_token: "$LINEAR_API_KEY", codex_thread_sandbox: "danger-full-access")
    assert {:error, "sandbox_unsupported_policy"} = SandboxReadiness.check(credential_free: true)

    write_workflow_file!(Workflow.workflow_file_path(), tracker_api_token: "$LINEAR_API_KEY")

    assert {:error, "sandbox_check_failed"} =
             SandboxReadiness.check(
               credential_free: true,
               execute: fn _, _, _ ->
                 write_workflow_file!(Workflow.workflow_file_path(), tracker_api_token: "$LINEAR_API_KEY", codex_command: "changed-during-probe app-server")
                 {~s({"ready":true,"protocol":1}), 0}
               end
             )
  end

  test "unqualified application startup is blocked and checks pin a process-local input receipt" do
    Application.delete_env(:symphony_elixir, :codex_sandbox_receipt)
    refute SandboxReadiness.qualified?()

    assert :ok =
             SandboxReadiness.check(
               execute: fn _, args, _ ->
                 input = args |> List.last() |> Jason.decode!()
                 assert input["policy"]["type"] == "workspaceWrite"
                 assert input["command"] == "codex app-server"
                 {~s({"ready":true,"protocol":1}), 0}
               end
             )

    assert SandboxReadiness.qualified?()
    old = Config.settings!()
    write_workflow_file!(Workflow.workflow_file_path(), codex_command: "different-codex app-server")
    refute SandboxReadiness.qualified?(nil, old)
    # A captured, qualified snapshot remains distinct from subsequent reloads.
    assert SandboxReadiness.qualified?()
  end

  test "wrapped sessions recheck actual post-hook workspace without replacing the startup receipt" do
    settings = Config.settings!()
    wrapped = %{settings | codex: %{settings.codex | command: "bash scripts/with-node.sh codex app-server"}}
    workspace = Path.join(System.tmp_dir!(), "wrapped-task-#{System.unique_integer([:positive])}")
    on_exit(fn -> File.rm_rf(workspace) end)

    assert SandboxReadiness.qualified?()

    for code <- ["sandbox_ready", "sandbox_launch_failed"] do
      result =
        SandboxReadiness.check_workspace(workspace, wrapped,
          execute: fn _, args, _ ->
            input = args |> List.last() |> Jason.decode!()
            assert input["workspace"] == workspace
            assert input["launch_root"] == workspace
            {:ok, canonical} = SymphonyElixir.PathSafety.canonicalize(workspace)
            assert input["policy"]["writableRoots"] == [canonical]
            assert input["command"] == wrapped.codex.command
            {Jason.encode!(%{ready: code == "sandbox_ready", code: code, protocol: 1}), if(code == "sandbox_ready", do: 0, else: 1)}
          end
        )

      assert result == if(code == "sandbox_ready", do: :ok, else: {:error, code})
      assert SandboxReadiness.qualified?()
    end

    assert :ok = SandboxReadiness.check_workspace(workspace, settings, execute: fn _, _, _ -> flunk("direct launch must retain its existing startup receipt") end)

    empty = %{settings | codex: %{settings.codex | command: ""}}
    assert {:error, "sandbox_unsupported_command"} = SandboxReadiness.check_workspace(workspace, empty)

    assert {:error, "sandbox_check_failed"} =
             SandboxReadiness.check_workspace(workspace, wrapped, execute: fn _, _, _ -> raise "worker probe unavailable" end)

    assert SandboxReadiness.qualified?()
  end

  test "failed or indeterminate requalification always removes prior success" do
    for {output, status, code} <- [
          {~s({"ready":false,"code":"sandbox_namespace_denied"}), 1, "sandbox_namespace_denied"},
          {"native secret output", 1, "sandbox_check_failed"},
          {"", 124, "sandbox_timeout"},
          {"", 137, "sandbox_timeout"},
          {"{}", 0, "sandbox_check_failed"}
        ] do
      assert {:error, ^code} = SandboxReadiness.check(execute: fn _, _, _ -> {output, status} end)
      refute SandboxReadiness.qualified?()
    end

    assert {:error, "sandbox_check_dependency_missing_python3"} = SandboxReadiness.check(python_path: nil)
    assert {:error, "sandbox_check_failed"} = SandboxReadiness.check(execute: fn _, _, _ -> raise "fixture exception" end)
  end

  test "policy changes and nested native config invalidate the receipt before session launch" do
    root = Path.join(System.tmp_dir!(), "jay704-settings-#{System.unique_integer([:positive])}")
    File.mkdir_p!(Path.join(root, ".codex"))
    on_exit(fn -> File.rm_rf(root) end)
    File.write!(Path.join(root, ".codex/config.toml"), "[features]\n")
    refute SandboxReadiness.qualified?(root)
    assert SandboxReadiness.qualified?()
    assert SandboxReadiness.qualified?(nil, nil)
    refute SandboxReadiness.qualified?(nil, %{})
    write_workflow_file!(Workflow.workflow_file_path(), codex_thread_sandbox: "danger-full-access")
    assert {:error, "sandbox_unsupported_policy"} = SandboxReadiness.check()
    refute SandboxReadiness.qualified?()
  end
end
