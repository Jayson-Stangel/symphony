defmodule SymphonyElixir.RemoteWorkflowTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixir.Linear.Client
  alias SymphonyElixir.{PromptBuilder, RemoteWorkflow}
  alias SymphonyElixir.Tracker.Issue
  alias SymphonyElixirWeb.ObservabilityPubSub

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-remote-test-#{System.unique_integer([:positive])}")
    File.mkdir_p!(root)
    on_exit(fn -> File.rm_rf!(root) end)
    git!(root, ["init", "-q", "-b", "watched/nested"])
    git!(root, ["config", "user.name", "Workflow fixture"])
    git!(root, ["config", "user.email", "workflow@example.test"])
    git!(root, ["config", "uploadpack.allowFilter", "true"])
    git!(root, ["config", "uploadpack.allowAnySHA1InWant", "true"])

    bytes = workflow("registered-linear", "first prompt")
    branch = "watched/nested"
    commit = commit!(root, bytes)
    remote = %{repository: "file://" <> Path.join(root, ".git"), branch: branch, project: "registered-linear"}
    source = %{remote | repository: "https://github.com/example/project.git"}
    previous_token = System.get_env("LINEAR_API_KEY")
    previous_state_root = Application.get_env(:symphony_elixir, :agent_state_root)
    System.put_env("LINEAR_API_KEY", "fixture-key")
    Application.put_env(:symphony_elixir, :agent_state_root, Path.join(root, "state"))

    on_exit(fn ->
      restore_environment("LINEAR_API_KEY", previous_token)

      if previous_state_root do
        Application.put_env(:symphony_elixir, :agent_state_root, previous_state_root)
      else
        Application.delete_env(:symphony_elixir, :agent_state_root)
      end
    end)

    %{root: root, remote: remote, source: source, commit: commit, bytes: bytes}
  end

  test "reads an exact regular root blob and resolves the remote default", ctx do
    assert {:ok, %{branch: "watched/nested", commit: ctx.commit, content: ctx.bytes}} ==
             RemoteWorkflow.fetch(%{ctx.remote | branch: ""})

    digest = git!(ctx.root, ["rev-parse", "HEAD:WORKFLOW.md"])
    {prefix, name} = String.split_at(digest, 2)
    object = Path.join([ctx.root, ".git", "objects", prefix, name])
    File.rename!(object, object <> ".hidden")
    assert {:error, :unreachable} = RemoteWorkflow.fetch(ctx.remote)
    File.rename!(object <> ".hidden", object)

    assert {:error, :unreachable} = RemoteWorkflow.fetch(Map.put(ctx.remote, :scratch_root, object))
    git!(ctx.root, ["checkout", "-q", "--detach"])
    assert {:error, :missing_branch} = RemoteWorkflow.fetch(%{ctx.remote | branch: ""})
    git!(ctx.root, ["switch", "-q", ctx.remote.branch])

    git!(ctx.root, ["branch", "team/refs/heads/main"])
    assert {:error, :missing_branch} = RemoteWorkflow.fetch(%{ctx.remote | branch: "main"})
    git!(ctx.root, ["branch", "main"])
    assert {:ok, %{commit: commit}} = RemoteWorkflow.fetch(%{ctx.remote | branch: "main"})
    assert commit == ctx.commit

    git!(ctx.root, ["switch", "-qc", "new-default"])
    newer = commit!(ctx.root, workflow("registered-linear", "new default"))
    assert {:ok, %{branch: "new-default", commit: ^newer}} = RemoteWorkflow.fetch(%{ctx.remote | branch: ""})
    assert {:ok, :unchanged} = RemoteWorkflow.fetch(ctx.remote, ctx.commit)
    assert {:error, :invalid_branch} = RemoteWorkflow.fetch(%{ctx.remote | branch: "bad../ref"})
    assert {:error, :missing_branch} = RemoteWorkflow.fetch(%{ctx.remote | branch: "absent"})
    assert {:error, :unreachable} = RemoteWorkflow.fetch(%{ctx.remote | repository: "file:///nonexistent-jay995"})
    assert {:error, :unreachable} = RemoteWorkflow.fetch(%{ctx.remote | repository: "file:///nonexistent-jay995", branch: ""})

    git!(ctx.root, ["rm", "-q", "WORKFLOW.md"])
    git!(ctx.root, ["commit", "-qm", "no workflow"])
    assert {:error, :missing_workflow} = RemoteWorkflow.fetch(%{ctx.remote | branch: "new-default"})

    File.ln_s!("unused", Path.join(ctx.root, "WORKFLOW.md"))
    git!(ctx.root, ["add", "WORKFLOW.md"])
    git!(ctx.root, ["commit", "-qm", "symlink"])
    assert {:error, :invalid_workflow} = RemoteWorkflow.fetch(%{ctx.remote | branch: "new-default"})
    File.rm!(Path.join(ctx.root, "WORKFLOW.md"))

    commit!(ctx.root, :binary.copy("x", 1_048_577))
    assert {:error, :invalid_workflow} = RemoteWorkflow.fetch(%{ctx.remote | branch: "new-default"})
  end

  test "binds accepted bytes to the registered project, endpoint and credential", ctx do
    fetch = fn _ -> RemoteWorkflow.fetch(ctx.remote) end
    assert {:ok, checked} = WorkflowStore.prepare_remote(ctx.source, false, fetch)
    refute File.exists?(checked.path)
    assert {:ok, state} = WorkflowStore.prepare_remote(ctx.source, true, fetch)
    assert state.commit == ctx.commit
    assert state.stamp == digest(ctx.bytes)
    assert File.read!(state.path) == ctx.bytes
    assert {:error, :invalid_registration} = WorkflowStore.prepare_remote(ctx.remote, true, fetch)
    assert {:error, :invalid_registration} = WorkflowStore.prepare_remote(ctx.remote, false)
    assert {:error, :missing_branch} = WorkflowStore.prepare_remote(ctx.source, true, fn _ -> {:error, :missing_branch} end)

    candidates = [
      {workflow("other", "wrong project"), :linear_project_mismatch},
      {workflow("registered-linear", "wrong endpoint", "    endpoint: https://example.test/graphql\n"), :linear_endpoint_mismatch},
      {workflow("registered-linear", "wrong credential", "    api_key: another-key\n"), :linear_credential_mismatch},
      {workflow("registered-linear", "{% unclosed %}"), :invalid_workflow},
      {workflow("registered-linear", "{{ issue.typo }}"), :invalid_workflow},
      {workflow("registered-linear", "{% if attempt %}{{ issue.typo }}{% endif %}"), :invalid_workflow},
      {workflow("registered-linear", "{{ issue.title | unknown_filter }}"), :invalid_workflow},
      {workflow("registered-linear", "{% for n in (1..100000000) limit:1 %}{{ n }}{% endfor %}"), :invalid_workflow},
      {"---\ntracker: [\n---\n", :invalid_workflow}
    ]

    for {bytes, error} <- candidates do
      commit!(ctx.root, bytes)
      assert {:error, ^error} = WorkflowStore.prepare_remote(ctx.source, true, fetch)
      assert File.read!(state.path) == ctx.bytes
    end

    wrapped = String.replace(ctx.bytes, "\n---\n", "\ncodex:\n  command: bash scripts/launch.sh codex app-server\n---\n")
    commit!(ctx.root, wrapped)
    assert {:error, :remote_codex_wrapper_unsupported} = WorkflowStore.prepare_remote(ctx.source, true, fetch)

    commit!(ctx.root, ctx.bytes)
    File.rm_rf!(Path.dirname(state.path))
    File.write!(Path.dirname(state.path), "blocked directory")
    assert {:error, :workflow_storage_unavailable} = WorkflowStore.prepare_remote(ctx.source, true, fetch)
  end

  test "reloads from Git while retaining accepted state through source failures", ctx do
    fetch = fn _ -> RemoteWorkflow.fetch(ctx.remote) end
    assert {:ok, state} = WorkflowStore.prepare_remote(ctx.source, true, fetch)
    # Start the store the way the listener does, with its already verified
    # boot snapshot and a local transport in place of a private GitHub repo.
    store = Process.whereis(WorkflowStore)
    old = :sys.get_state(store)
    Application.put_env(:symphony_elixir, :prepared_remote_workflow, %{state | source: Map.put(ctx.remote, :scratch_root, Path.dirname(state.path))})
    assert :ok = Supervisor.terminate_child(SymphonyElixir.Supervisor, WorkflowStore)
    System.put_env("SYMPHONY_REMOTE_REPOSITORY", ctx.source.repository)

    try do
      assert WorkflowStore.admission_block() == "workflow_source_unavailable"
      prepared = Application.get_env(:symphony_elixir, :prepared_remote_workflow)
      Application.delete_env(:symphony_elixir, :prepared_remote_workflow)
      assert {:stop, :missing_remote_boot_workflow} = WorkflowStore.init([])
      Application.put_env(:symphony_elixir, :prepared_remote_workflow, prepared)
      assert {:ok, store} = Supervisor.restart_child(SymphonyElixir.Supervisor, WorkflowStore)
      assert %{commit: ctx.commit, source_block: nil} == Map.take(WorkflowStore.status(), [:commit, :source_block])
      assert WorkflowStore.admission_block() == nil
      assert {:ok, %Config.Schema{}} = WorkflowStore.settings()
      assert {:ok, %{prompt: "first prompt"}} = WorkflowStore.current()
      assert :ok = WorkflowStore.force_reload()

      :ok = ObservabilityPubSub.subscribe()
      hold = Path.join(Config.agent_state_root(), "agent-draining")
      File.write!(hold, "migration:#{ctx.commit}\n")
      send(store, :poll)
      assert :sys.get_state(store).fetch == nil
      File.rm!(hold)

      next = workflow(ctx.source.project, "changed prompt for {{ issue.identifier }}")
      next_commit = commit!(ctx.root, next)
      assert %{commit: ^next_commit, status: "loaded"} = check_now(store)
      assert_receive :observability_updated
      assert WorkflowStore.status().sha256 == digest(next)
      assert File.read!(state.path) == next
      assert PromptBuilder.build_prompt(%Issue{id: "fixture", identifier: "TEST-1"}) =~ "changed prompt for TEST-1"
      assert %{status: "loaded", accepted_at: accepted_at} = check_now(store)
      source_checked_at = WorkflowStore.status().last_successful_source_check_at

      hidden = Path.join(ctx.root, "hidden")
      File.rename!(Path.join(ctx.root, ".git"), hidden)
      status = check_now(store)
      assert %{status: "stale", accepted_at: ^accepted_at, last_successful_source_check_at: ^source_checked_at} = status
      assert WorkflowStore.admission_block() == nil
      assert :ok = WorkflowStore.force_reload()
      File.rename!(hidden, Path.join(ctx.root, ".git"))

      commit!(ctx.root, workflow("different-project", "rejected change"))
      assert %{status: "rejected", commit: ^next_commit, source_block: :linear_project_mismatch} = check_now(store)
      assert WorkflowStore.admission_block() == "workflow_source_rejected"
      assert WorkflowStore.force_reload() == {:error, :linear_project_mismatch}
      File.rename!(Path.join(ctx.root, ".git"), hidden)
      assert %{status: "rejected", source_error: :unreachable, source_block: :linear_project_mismatch} = check_now(store)
      File.rename!(hidden, Path.join(ctx.root, ".git"))

      git!(ctx.root, ["revert", "--no-edit", "HEAD"])
      recovered = git!(ctx.root, ["rev-parse", "HEAD"])
      assert %{status: "loaded", commit: ^recovered, source_block: nil} = check_now(store)
      assert WorkflowStore.status().sha256 == digest(next)
      assert WorkflowStore.admission_block() == nil

      File.rm!(state.path)
      File.mkdir!(state.path)
      commit!(ctx.root, workflow(ctx.source.project, "after storage recovery"))
      assert %{status: "rejected", commit: ^recovered, source_block: :workflow_storage_unavailable} = check_now(store)
      File.rmdir!(state.path)
      assert %{status: "loaded", source_block: nil} = check_now(store)

      git!(ctx.root, ["switch", "-qc", "elsewhere"])
      git!(ctx.root, ["branch", "-D", ctx.remote.branch])
      assert %{status: "rejected", source_block: :missing_branch} = check_now(store)
    after
      System.delete_env("SYMPHONY_REMOTE_REPOSITORY")
      :ok = Supervisor.terminate_child(SymphonyElixir.Supervisor, WorkflowStore)
      assert {:ok, store} = Supervisor.restart_child(SymphonyElixir.Supervisor, WorkflowStore)
      :sys.replace_state(store, fn _ -> old end)
    end
  end

  test "a failed asynchronous source check leaves the accepted workflow stale", ctx do
    assert {:ok, state} = WorkflowStore.prepare_remote(ctx.source, false, fn _ -> RemoteWorkflow.fetch(ctx.remote) end)
    ref = make_ref()
    source_check = %{state | fetch: {self(), ref}}
    assert {:noreply, result} = WorkflowStore.handle_info({:DOWN, ref, :process, self(), :killed}, source_check)
    assert result.status == "stale"
    assert result.commit == ctx.commit
    assert result.source_block == nil
  end

  test "the shipped workflow can render normal and retry prompts" do
    path = Path.expand("../../WORKFLOW.md", __DIR__)
    assert {:ok, workflow} = Workflow.load(path)
    assert :ok = PromptBuilder.validate_template(workflow.prompt_template)
  end

  test "the Linear project probe checks the exact slug through the selected token", ctx do
    assert {:ok, state} = WorkflowStore.prepare_remote(ctx.source, false, fn _ -> RemoteWorkflow.fetch(ctx.remote) end)

    assert :ok =
             Client.verify_project(state.settings.tracker,
               request_fun: fn payload, headers ->
                 assert payload["variables"] == %{projectSlug: ctx.source.project}
                 assert {"Authorization", "fixture-key"} in headers
                 {:ok, %{status: 200, body: %{"data" => %{"projects" => %{"nodes" => [%{"id" => "id", "slugId" => ctx.source.project}]}}}}}
               end
             )

    assert {:error, :linear_project_unverified} =
             Client.verify_project(state.settings.tracker,
               request_fun: fn _, _ -> {:ok, %{status: 200, body: %{"data" => %{"projects" => %{"nodes" => []}}}}} end
             )
  end

  defp check_now(store) do
    send(store, :poll)
    wait_for_poll(store, 100)
    WorkflowStore.status()
  end

  defp wait_for_poll(store, attempts) when attempts > 0 do
    if :sys.get_state(store).fetch do
      Process.sleep(20)
      wait_for_poll(store, attempts - 1)
    end
  end

  defp wait_for_poll(_store, 0), do: flunk("remote source check did not complete")

  defp commit!(root, bytes) do
    File.write!(Path.join(root, "WORKFLOW.md"), bytes)
    git!(root, ["add", "--", "WORKFLOW.md"])
    git!(root, ["commit", "-qm", "workflow change"])
    git!(root, ["rev-parse", "HEAD"])
  end

  defp git!(root, args) do
    {out, 0} = System.cmd("git", ["-C", root | args], stderr_to_stdout: true)
    String.trim(out)
  end

  defp workflow(project, prompt, extra \\ "") do
    "---\ntracker:\n  kind: linear\n  provider:\n    project_slug: #{project}\n#{extra}---\n\n#{prompt}\n"
  end

  defp digest(bytes), do: Base.encode16(:crypto.hash(:sha256, bytes), case: :lower)

  defp restore_environment(key, nil), do: System.delete_env(key)
  defp restore_environment(key, value), do: System.put_env(key, value)
end
