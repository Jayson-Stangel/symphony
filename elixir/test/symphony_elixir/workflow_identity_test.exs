defmodule SymphonyElixir.WorkflowIdentityTest do
  use SymphonyElixir.TestSupport

  test "startup attests exact bytes and unchanged contents retain acceptance time" do
    path = Workflow.workflow_file_path()
    bytes = File.read!(path)
    status = WorkflowStore.status()
    assert status.sha256 == digest(bytes)
    assert status.status == "loaded"
    assert {:ok, _, _} = DateTime.from_iso8601(status.accepted_at)
    assert {:ok, _, _} = DateTime.from_iso8601(status.checked_at)
    assert WorkflowStore.status().accepted_at == status.accepted_at

    assert :ok = Supervisor.terminate_child(SymphonyElixir.Supervisor, WorkflowStore)
    assert WorkflowStore.status() == %{sha256: nil, status: "unavailable", accepted_at: nil, checked_at: nil}
    assert {:ok, _pid} = Supervisor.restart_child(SymphonyElixir.Supervisor, WorkflowStore)
    assert WorkflowStore.status().sha256 == digest(bytes)
  end

  test "atomic prompt-only replacements advance accepted identity without changing settings" do
    path = Workflow.workflow_file_path()
    original = File.read!(path)
    settings = Config.settings!()

    for size <- [50_000, 128, 64_000, 32, 43_351, 256] do
      bytes = original <> String.duplicate("x", size) <> "\nTRAILING_SENTINEL_#{size}\n"
      replace(path, bytes)
      assert :ok = WorkflowStore.force_reload()
      assert {:ok, workflow} = Workflow.current()
      assert String.ends_with?(workflow.prompt, "TRAILING_SENTINEL_#{size}")
      assert WorkflowStore.status().sha256 == digest(bytes)
      assert Config.settings!() == settings
    end
  end

  test "parse, schema, semantic and read rejection never attest rejected bytes; recovery clears status" do
    path = Workflow.workflow_file_path()
    original = File.read!(path)
    accepted = WorkflowStore.status()
    assert {:ok, workflow} = Workflow.current()

    for invalid <- [
          "---\ntracker: [\n---\nPRIVATE_BROKEN_PROMPT\n",
          "---\npolling:\n  interval_ms: nope\n---\nPRIVATE_SCHEMA_PROMPT\n",
          "---\ntracker:\n  kind: linear\n  api_key: PRIVATE_TOKEN\n---\nPRIVATE_SEMANTIC_PROMPT\n"
        ] do
      replace(path, invalid)
      assert {:error, _} = WorkflowStore.force_reload()
      assert {:ok, ^workflow} = Workflow.current()
      status = WorkflowStore.status()
      assert status.sha256 == accepted.sha256
      assert status.accepted_at == accepted.accepted_at
      assert status.status == "rejected"
      refute status.sha256 == digest(invalid)
      assert Enum.sort(Map.keys(status)) == [:accepted_at, :checked_at, :sha256, :status]
      refute inspect(status) =~ "PRIVATE"
    end

    File.rm!(path)
    assert {:error, {:missing_workflow_file, ^path, :enoent}} = WorkflowStore.force_reload()
    assert WorkflowStore.status().sha256 == accepted.sha256
    assert WorkflowStore.status().status == "rejected"

    replace(path, original)
    assert :ok = WorkflowStore.force_reload()
    assert WorkflowStore.status().status == "loaded"
    assert WorkflowStore.status().sha256 == accepted.sha256

    recovered = original <> "\nRECOVERED_PROMPT\n"
    replace(path, recovered)
    assert :ok = WorkflowStore.force_reload()
    assert WorkflowStore.status().sha256 == digest(recovered)
    assert {:ok, %{prompt: prompt}} = Workflow.current()
    assert String.ends_with?(prompt, "RECOVERED_PROMPT")
  end

  test "accepted identity describes the exact bytes passed to the parser" do
    path = Workflow.workflow_file_path()
    bytes = File.read!(path) <> "\nONE_CAPTURED_READ\n"
    pid = Process.whereis(WorkflowStore)
    :erlang.trace_pattern({Workflow, :parse, 1}, true, [:local])
    :erlang.trace(pid, true, [:call, {:tracer, self()}])

    try do
      replace(path, bytes)
      assert :ok = WorkflowStore.force_reload()
      ref = :erlang.trace_delivered(pid)
      assert_receive {:trace_delivered, ^pid, ^ref}
      assert_receive {:trace, ^pid, :call, {Workflow, :parse, [^bytes]}}
      state = :sys.get_state(pid)
      assert state.stamp == digest(bytes)
      assert String.ends_with?(state.workflow.prompt, "ONE_CAPTURED_READ")
    after
      :erlang.trace(pid, false, [:call])
      :erlang.trace_pattern({Workflow, :parse, 1}, false, [:local])
    end
  end

  test "parsing captured bytes is independent of later source replacement" do
    path = Workflow.workflow_file_path()
    captured = File.read!(path) <> "\nCAPTURED_PROMPT\n"
    replace(path, "---\ntracker: [\n---\nREPLACEMENT\n")
    assert {:ok, %{prompt: prompt}} = Workflow.parse(captured)
    assert String.ends_with?(prompt, "CAPTURED_PROMPT")
    assert {:error, _} = Workflow.load(path)
  end

  defp replace(path, bytes) do
    File.write!(path <> ".next", bytes)
    File.rename!(path <> ".next", path)
  end

  defp digest(bytes), do: :crypto.hash(:sha256, bytes) |> Base.encode16(case: :lower)
end
