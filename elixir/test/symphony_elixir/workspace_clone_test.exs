defmodule SymphonyElixir.WorkspaceCloneTest do
  use SymphonyElixir.TestSupport
  alias SymphonyElixir.Config.Schema

  test "clone-only configuration rejects arbitrary hooks and SSH" do
    config = %{"workspace" => %{"clone_url" => "https://example.test/repo.git"}}
    assert {:ok, settings} = Schema.parse(config)
    assert settings.workspace.clone_url == "https://example.test/repo.git"

    assert {:error, {:invalid_workflow_config, message}} =
             Schema.parse(Map.put(config, "hooks", %{"after_create" => "echo setup"}))

    assert message =~ "cannot be combined"

    assert {:error, {:invalid_workflow_config, message}} =
             Schema.parse(Map.put(config, "worker", %{"ssh_hosts" => ["worker"]}))

    assert message =~ "local workspaces only"

    for value <- ["bad", 42] do
      assert {:error, _} = Schema.parse(%{"workspace" => value})
    end

    assert {:ok, blank} = Schema.parse(%{"workspace" => %{"root" => "", "clone_url" => " "}})
    assert blank.workspace.clone_url == nil
  end

  test "ordinary clone publishes ready checkout and preserves edited existing work" do
    {root, source} = clone_fixture!()
    configure_clone!(root, source)
    assert {:ok, workspace} = Workspace.create_for_issue("CLONE-1")
    assert File.read!(Path.join(workspace, "README.md")) == "source\n"
    assert File.dir?(Path.join(workspace, ".git"))
    File.write!(Path.join(workspace, "README.md"), "edited\n")
    File.rm_rf!(source)
    assert {:ok, ^workspace} = Workspace.create_for_issue("CLONE-1")
    assert File.read!(Path.join(workspace, "README.md")) == "edited\n"
    assert File.ls!(Path.join([root, "workspaces", ".symphony", "clones"])) == []
  end

  test "interrupted unpublished clone retries without reusing or deleting the old stage" do
    {root, source} = clone_fixture!()
    configure_clone!(root, source)
    original_path = System.fetch_env!("PATH")
    git = System.find_executable("git")
    wrapper_dir = Path.join(root, "bin")
    File.mkdir_p!(wrapper_dir)
    gate = Path.join(root, "first-clone")

    File.write!(Path.join(wrapper_dir, "git"), """
    #!/bin/sh
    "#{git}" "$@" || exit $?
    if mkdir "#{gate}" 2>/dev/null; then sleep 2; fi
    """)

    File.chmod!(Path.join(wrapper_dir, "git"), 0o755)
    System.put_env("PATH", wrapper_dir <> ":" <> original_path)
    on_exit(fn -> System.put_env("PATH", original_path) end)

    {pid, ref} = spawn_monitor(fn -> Workspace.create_for_issue("CLONE-2") end)
    wait_for!(fn -> File.dir?(gate) end)
    stage_root = Path.join([root, "workspaces", ".symphony", "clones"])
    [stage_name] = File.ls!(stage_root)
    stage = Path.join(stage_root, stage_name)
    assert File.read!(Path.join(stage, "README.md")) == "source\n"
    Process.exit(pid, :kill)
    assert_receive {:DOWN, ^ref, :process, ^pid, :killed}
    refute File.exists?(Path.join([root, "workspaces", "CLONE-2"]))

    assert {:ok, workspace} = Workspace.create_for_issue("CLONE-2")
    assert File.read!(Path.join(workspace, "README.md")) == "source\n"
    assert File.ls!(stage_root) == [stage_name]
    assert File.read!(Path.join(stage, "README.md")) == "source\n"
  end

  test "failed clone removes only its owned stage and hides URL and command output" do
    {root, _source} = clone_fixture!()
    # Use a deterministic local Git error rather than a network request.
    configure_clone!(root, Path.join(root, "missing-user-secret"))
    assert {:error, {:workspace_clone_failed, workspace, _status}} = Workspace.create_for_issue("CLONE-3")
    refute File.exists?(workspace)
    assert File.ls!(Path.join([root, "workspaces", ".symphony", "clones"])) == []
  end

  test "timeout retains unpublished stage and a fresh retry can publish" do
    {root, source} = clone_fixture!()
    original_path = System.fetch_env!("PATH")
    git = System.find_executable("git")
    wrapper_dir = Path.join(root, "bin")
    File.mkdir_p!(wrapper_dir)
    gate = Path.join(root, "timeout-clone")

    File.write!(Path.join(wrapper_dir, "git"), """
    #!/bin/sh
    first=0
    if mkdir "#{gate}" 2>/dev/null; then first=1; fi
    "#{git}" "$@" || exit $?
    for destination do :; done
    printf '%s\\n' "$destination" >> "#{root}/destinations"
    if [ "$first" = 1 ]; then
      sleep 0.4
      printf late > "$destination/late-output"
    fi
    """)

    File.chmod!(Path.join(wrapper_dir, "git"), 0o755)
    System.put_env("PATH", wrapper_dir <> ":" <> original_path)
    on_exit(fn -> System.put_env("PATH", original_path) end)
    write_workflow_file!(Workflow.workflow_file_path(), workspace_root: Path.join(root, "workspaces"), workspace_clone_url: source, hook_timeout_ms: 200)
    assert {:error, {:workspace_clone_timeout, workspace, 200}} = Workspace.create_for_issue("CLONE-TIMEOUT")
    refute File.exists?(workspace)
    stage_root = Path.join([root, "workspaces", ".symphony", "clones"])
    [stage_name] = File.ls!(stage_root)
    configure_clone!(root, source)
    assert {:ok, ^workspace} = Workspace.create_for_issue("CLONE-TIMEOUT")
    Process.sleep(450)
    assert File.ls!(stage_root) == [stage_name]
    assert File.read!(Path.join(workspace, "README.md")) == "source\n"
    refute File.exists?(Path.join(workspace, "late-output")), File.read!(Path.join(root, "destinations"))
    assert File.read!(Path.join([stage_root, stage_name, "late-output"])) == "late"
  end

  test "existing non-directory destination is preserved" do
    {root, source} = clone_fixture!()
    configure_clone!(root, source)
    {:ok, workspace} = SymphonyElixir.PathSafety.canonicalize(Path.join([root, "workspaces", "CLONE-FILE"]))
    File.mkdir_p!(Path.dirname(workspace))
    File.write!(workspace, "preserve")
    assert {:error, {:workspace_clone_destination_exists, ^workspace}} = Workspace.create_for_issue("CLONE-FILE")
    assert File.read!(workspace) == "preserve"
  end

  test "clone refuses metadata symlink escape and preserves existing interrupted hooks" do
    {root, source} = clone_fixture!()
    configure_clone!(root, source)
    workspaces = Path.join(root, "workspaces")
    outside = Path.join(root, "outside")
    File.mkdir_p!(workspaces)
    File.mkdir_p!(outside)
    File.ln_s!(outside, Path.join(workspaces, ".symphony"))
    assert {:error, _reason} = Workspace.create_for_issue("CLONE-4")
    assert File.ls!(outside) == []
    File.rm!(Path.join(workspaces, ".symphony"))
    marker = Path.join([workspaces, ".symphony", "bootstrap", "CLONE-4"])
    File.mkdir_p!(Path.dirname(marker))
    File.write!(marker, "interrupted arbitrary hook")
    assert {:error, {:workspace_bootstrap_interrupted, _}} = Workspace.create_for_issue("CLONE-4")
    assert File.read!(marker) == "interrupted arbitrary hook"
  end

  defp clone_fixture! do
    root = Path.join(Path.dirname(Workflow.workflow_file_path()), "clone-fixture")
    source = Path.join(root, "source")
    File.mkdir_p!(source)
    File.write!(Path.join(source, "README.md"), "source\n")

    for args <- [["init", "-b", "main"], ["add", "README.md"], ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"]] do
      {_output, 0} = System.cmd("git", args, cd: source, stderr_to_stdout: true)
    end

    {root, source}
  end

  defp configure_clone!(root, source) do
    write_workflow_file!(Workflow.workflow_file_path(), workspace_root: Path.join(root, "workspaces"), workspace_clone_url: source)
  end

  defp wait_for!(predicate, attempts \\ 200)
  defp wait_for!(_predicate, 0), do: flunk("clone did not reach interruption point")

  defp wait_for!(predicate, attempts) do
    if predicate.(),
      do: :ok,
      else:
        (
          Process.sleep(10)
          wait_for!(predicate, attempts - 1)
        )
  end
end
