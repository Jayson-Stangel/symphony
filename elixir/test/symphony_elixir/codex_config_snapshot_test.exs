defmodule SymphonyElixir.Codex.ConfigSnapshotTest do
  use ExUnit.Case, async: true

  alias SymphonyElixir.Codex.ConfigSnapshot

  test "runs only the embedded isolated exporter and returns its bounded schema" do
    owner = self()

    value = %{
      "schema" => 1,
      "mode" => "codex-config-snapshot",
      "profile" => "trust-ro-v2",
      "config_base64" => "",
      "sha256" => Base.encode16(:crypto.hash(:sha256, ""), case: :lower),
      "source" => %{"present" => false}
    }

    execute = fn python, args, options ->
      send(owner, {:export, python, args, options})
      {Jason.encode!(value), 0}
    end

    assert {:ok, ^value} = ConfigSnapshot.run(python_path: "/usr/bin/python3", execute: execute)
    assert_received {:export, "/usr/bin/python3", ["-I", "-c", program], []}
    assert program =~ "def export_config_snapshot("
    assert program =~ "def validate_config_bytes("
    assert program =~ "Path(\"/root/.codex\")"
    assert program =~ "Path(\"/workspaces\")"
    refute program =~ "auth.json"
  end

  test "finds the image Python without invoking any application dependency" do
    execute = fn python, ["-I", "-c", _program], [] ->
      assert is_binary(python)
      {"{}", 0}
    end

    assert {:error, "sandbox_unqualified_config"} = ConfigSnapshot.run(execute: execute)
  end

  test "missing Python, failure, malformed or oversized output have one fixed refusal" do
    assert {:error, "sandbox_unqualified_config"} = ConfigSnapshot.run(python_path: nil)

    for result <- [
          {"private arbitrary diagnostic", 1},
          {"not JSON", 0},
          {"{}", 0},
          {String.duplicate("x", 90_001), 0}
        ] do
      assert {:error, "sandbox_unqualified_config"} =
               ConfigSnapshot.run(python_path: "/trusted/python", execute: fn _, _, _ -> result end)
    end

    assert {:error, "sandbox_unqualified_config"} =
             ConfigSnapshot.run(python_path: "/trusted/python", execute: fn _, _, _ -> raise "private diagnostic" end)
  end
end
