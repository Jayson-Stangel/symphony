defmodule SymphonyElixir.CodexSandboxReadinessTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixir.Codex.SandboxReadiness

  test "bound command carries one session's chain with shell-safe argument boundaries" do
    names = ["SYMPHONY_CONTAINERIZED", "SYMPHONY_CODEX_CONFIG_PROFILE", "SYMPHONY_CODEX_CONFIG_SHA256", "SYMPHONY_CODEX_CONFIG_OWNER", "PATH"]
    previous = Map.new(names, &{&1, System.get_env(&1)})
    on_exit(fn -> Enum.each(previous, fn {key, value} -> restore_env(key, value) end) end)
    directory = Path.join(System.tmp_dir!(), "qualified-chain-'#{System.unique_integer([:positive])}")
    File.mkdir_p!(directory)
    on_exit(fn -> File.rm_rf(directory) end)
    executable = Path.join(directory, "codex")
    File.write!(executable, "#!/bin/sh\nexit 0\n")
    File.chmod!(executable, 0o700)
    System.put_env("PATH", directory <> ":" <> System.get_env("PATH"))
    System.put_env("SYMPHONY_CONTAINERIZED", "1")
    System.put_env("SYMPHONY_CODEX_CONFIG_PROFILE", "trust-ro-v2")
    System.put_env("SYMPHONY_CODEX_CONFIG_OWNER", "1000")
    System.put_env("SYMPHONY_CODEX_CONFIG_SHA256", String.duplicate("a", 64))
    settings = Config.settings!()
    settings = %{settings | codex: %{settings.codex | command: "codex app-server"}}
    chain = %{"node" => %{"path" => "/qualified/node"}, "frontend" => %{"path" => executable}}
    assert {:ok, command} = SandboxReadiness.bound_command("original", directory, settings, chain)
    {json, 0} = System.cmd("python3", ["-I", "-c", "import json,shlex,sys; print(json.dumps(shlex.split(sys.argv[1])))", command])
    argv = Jason.decode!(json)
    assert Enum.slice(argv, 1, 2) == ["-I", "-c"]
    input = argv |> Enum.at(4) |> Jason.decode!()
    assert input["operation"] == "exec"
    assert input["snapshot_profile"] == "trust-ro-v2"
    assert input["expected_snapshot_uid"] == 1000
    fingerprint = SandboxReadiness.fingerprint(settings)
    System.put_env("SYMPHONY_CODEX_CONFIG_OWNER", "1001")
    refute fingerprint == SandboxReadiness.fingerprint(settings)
    assert input["expected_launch_chain"] == chain
    assert input["expected_image_chain"] == Map.delete(chain, "node")
    assert input["workspace"] == directory
    assert input["expected_executable"] == executable
    assert Enum.take(argv, -2) == ["codex", "app-server"]
  end

  test "managed qualification requires a session chain and binds only a resolvable launcher" do
    names = ["SYMPHONY_CONTAINERIZED", "SYMPHONY_CODEX_CONFIG_PROFILE", "SYMPHONY_CODEX_CONFIG_SHA256", "SYMPHONY_CODEX_CONFIG_OWNER", "PATH"]
    previous = Map.new(names, &{&1, System.get_env(&1)})
    on_exit(fn -> Enum.each(previous, fn {key, value} -> restore_env(key, value) end) end)
    directory = Path.join(System.tmp_dir!(), "session-chain-#{System.unique_integer([:positive])}")
    File.mkdir_p!(directory)
    on_exit(fn -> File.rm_rf(directory) end)
    executable = Path.join(directory, "native-codex")
    File.write!(executable, "#!/bin/sh\nexit 0\n")
    File.chmod!(executable, 0o700)
    File.ln_s!(executable, Path.join(directory, "codex"))
    timeout = Path.join(directory, "timeout")
    chain = %{"node" => %{"path" => "/qualified/node"}}
    # The subprocess fixture represents the protected probe protocol, not native proof.
    response = fn payload ->
      File.write!(timeout, "#!/bin/sh\nprintf '%s' '" <> Jason.encode!(payload) <> "'\nexit " <> if(payload.ready, do: "0", else: "1") <> "\n")
      File.chmod!(timeout, 0o700)
    end

    System.put_env("PATH", directory <> ":" <> System.get_env("PATH"))
    System.put_env("SYMPHONY_CONTAINERIZED", "1")
    System.put_env("SYMPHONY_CODEX_CONFIG_PROFILE", "trust-ro-v2")
    System.put_env("SYMPHONY_CODEX_CONFIG_OWNER", "invalid")
    System.put_env("SYMPHONY_CODEX_CONFIG_SHA256", String.duplicate("a", 64))
    settings = Config.settings!()
    settings = %{settings | workspace: %{settings.workspace | root: directory}, codex: %{settings.codex | command: "codex"}}
    response.(%{ready: true, protocol: 1, launch_chain: chain})
    assert {:ok, command} = SandboxReadiness.qualified_command("original", directory, settings)
    {json, 0} = System.cmd("python3", ["-I", "-c", "import json,shlex,sys; print(json.dumps(shlex.split(sys.argv[1])))", command])
    argv = Jason.decode!(json)
    input = argv |> Enum.at(4) |> Jason.decode!()
    assert input["expected_launch_chain"] == chain
    assert input["expected_executable"] == executable
    assert input["expected_snapshot_uid"] == nil
    assert List.last(argv) == "codex"
    response.(%{ready: true, protocol: 1})
    assert {:error, "sandbox_unqualified_config"} = SandboxReadiness.qualified_command("original", directory, settings)
    response.(%{ready: false, protocol: 1, code: "sandbox_launch_unavailable"})
    assert {:error, "sandbox_launch_unavailable"} = SandboxReadiness.qualified_command("original", directory, settings)
    System.put_env("PATH", directory)
    assert {:error, "sandbox_launch_unavailable"} = SandboxReadiness.bound_command("original", directory, settings, chain)
    System.put_env("PATH", previous["PATH"])
    missing = %{settings | codex: %{settings.codex | command: Path.join(directory, "missing-codex")}}
    assert {:error, "sandbox_unqualified_config"} = SandboxReadiness.bound_command("original", directory, missing, chain)
    System.put_env("PATH", directory <> ":" <> previous["PATH"])
    File.rm!(Path.join(directory, "codex"))

    for index <- 0..17 do
      target = if index == 17, do: executable, else: Path.join(directory, "link-#{index + 1}")
      File.ln_s!(target, Path.join(directory, "link-#{index}"))
    end

    File.ln_s!(Path.join(directory, "link-0"), Path.join(directory, "codex"))
    linked = %{settings | codex: %{settings.codex | command: Path.join(directory, "codex")}}
    assert {:error, "sandbox_unqualified_config"} = SandboxReadiness.bound_command("original", directory, linked, chain)
    System.delete_env("SYMPHONY_CONTAINERIZED")
    assert {:ok, "original"} = SandboxReadiness.qualified_command("original", directory, settings)
  end

  test "managed container policy grants only owned Git metadata and keeps native policy unchanged" do
    previous = System.get_env("SYMPHONY_CONTAINERIZED")
    on_exit(fn -> restore_env("SYMPHONY_CONTAINERIZED", previous) end)
    settings = Config.settings!()
    workspace = Path.join(System.tmp_dir!(), "jay721-policy-#{System.unique_integer([:positive])}")
    File.mkdir_p!(workspace)
    {:ok, workspace} = SymphonyElixir.PathSafety.canonicalize(workspace)
    on_exit(fn -> File.rm_rf(workspace) end)
    settings = %{settings | workspace: %{settings.workspace | root: workspace}}
    System.put_env("SYMPHONY_CONTAINERIZED", "1")
    assert {:ok, runtime} = Config.resolve_codex_runtime_settings(settings, workspace, [])
    assert runtime.thread_sandbox == "workspace-write"
    assert runtime.turn_sandbox_policy["writableRoots"] == [workspace, workspace <> "/.git"]
    assert runtime.turn_sandbox_policy["networkAccess"] == false

    explicit = %{
      settings
      | codex: %{
          settings.codex
          | turn_sandbox_policy: %{
              "type" => "workspaceWrite",
              "networkAccess" => true,
              "writableRoots" => [workspace]
            }
        }
    }

    assert {:ok, custom} = Config.resolve_codex_runtime_settings(explicit, workspace, [])
    assert custom.turn_sandbox_policy["networkAccess"] == true
    assert custom.turn_sandbox_policy["writableRoots"] == [workspace, workspace <> "/.git"]

    assert {:error, {:unsafe_turn_sandbox_policy, :unowned_git_metadata}} =
             Config.resolve_codex_runtime_settings(settings, "/outside-owned-workspaces", [])

    git = Path.join(workspace, ".git")
    File.mkdir_p!(git)
    assert {:ok, _runtime} = Config.resolve_codex_runtime_settings(settings, workspace, [])
    File.rmdir!(git)
    File.write!(git, "gitdir: /outside-owned-workspace")

    assert {:error, {:unsafe_turn_sandbox_policy, :unowned_git_metadata}} =
             Config.resolve_codex_runtime_settings(settings, workspace, [])

    File.rm!(git)
    File.ln_s!("/outside-owned-workspace", git)

    assert {:error, {:unsafe_turn_sandbox_policy, :unowned_git_metadata}} =
             Config.resolve_codex_runtime_settings(settings, workspace, [])

    File.rm!(git)

    assert {:ok, remote} = Config.resolve_codex_runtime_settings(settings, workspace, remote: true)
    assert remote.turn_sandbox_policy["writableRoots"] == [workspace]
    System.delete_env("SYMPHONY_CONTAINERIZED")
    assert {:ok, native} = Config.resolve_codex_runtime_settings(settings, workspace, [])
    assert native.turn_sandbox_policy["writableRoots"] == [workspace]
  end

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

  test "managed direct launches re-attest off scheduler against the configured root" do
    previous = System.get_env("SYMPHONY_CONTAINERIZED")
    on_exit(fn -> restore_env("SYMPHONY_CONTAINERIZED", previous) end)
    System.put_env("SYMPHONY_CONTAINERIZED", "1")
    settings = Config.settings!()
    task = Path.join(Config.local_workspace_root(settings), "retained-sibling-task")
    on_exit(fn -> File.rm_rf(task) end)

    assert {:error, "sandbox_unqualified_config"} =
             SandboxReadiness.check_workspace(task, settings,
               execute: fn _, args, _ ->
                 input = args |> List.last() |> Jason.decode!()
                 assert input["workspace"] == task
                 assert input["managed_workspace"] == Config.local_workspace_root(settings)
                 {~s({"ready":false,"code":"sandbox_unqualified_config","protocol":1}), 1}
               end
             )
  end
end
