defmodule SymphonyElixir.ClaudeSkillsTest do
  use ExUnit.Case, async: true
  alias SymphonyElixir.Claude.{Instructions, Skills}

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-skills-#{System.unique_integer([:positive])}")
    File.mkdir_p!(Path.join(root, ".codex/skills"))
    {:ok, root} = SymphonyElixir.PathSafety.canonicalize(root)
    on_exit(fn -> File.rm_rf!(root) end)
    %{root: root}
  end

  test "declared local procedures resolve separately from global canonical procedures", %{root: root} do
    assert {:ok, %{}} = Skills.sources(root)
    local = Path.join(root, ".codex/skills/land")
    File.mkdir_p!(local)
    File.write!(Path.join(local, "SKILL.md"), "local procedure")
    manifest = Path.join(root, ".codex/symphony-skill-overrides.yml")
    File.write!(manifest, "overrides:\n  - name: symphony-land\n    source: legacy:.codex/skills/land\n    source_revision: abc\n")
    assert {:ok, %{"symphony-land" => ^local}} = Skills.sources(root)
    File.write!(manifest, "overrides:\n  - name: symphony-land\n    source: global\n")
    assert {:ok, %{}} = Skills.sources(root)
    File.write!(manifest, "overrides:\n  - name: symphony-land\n    source: legacy:../../escape\n")
    assert {:error, :invalid_skill_manifest} = Skills.sources(root)
  end

  test "missing, symlinked and malformed procedure overrides fail closed", %{root: root} do
    manifest = Path.join(root, ".codex/symphony-skill-overrides.yml")

    for source <- ["legacy:.codex/skills/symphony-land", "unsupported"] do
      File.write!(manifest, "overrides:\n  - name: symphony-land\n    source: #{source}\n")
      assert {:error, _} = Skills.sources(root)
    end

    File.write!(manifest, "overrides: invalid\n")
    assert {:error, :invalid_skill_manifest} = Skills.sources(root)
    File.rm!(manifest)
    File.mkdir!(manifest)
    assert {:error, :invalid_skill_manifest} = Skills.sources(root)
    File.rmdir!(manifest)
    local = Path.join(root, ".codex/skills/symphony-land")
    File.mkdir!(local)
    assert {:error, :unsafe_skill_override} = Skills.sources(root)
    File.rmdir!(local)
    File.ln_s!("/etc", local)
    assert {:error, :unsafe_skill_override} = Skills.sources(root)
    assert {:error, :unreadable_repository_instructions} = Instructions.context(Path.join(root, String.duplicate("x", 256)))
  end

  test "root instructions are bounded, combined, and never generated", %{root: root} do
    File.write!(Path.join(root, "AGENTS.md"), "preserve history")
    File.write!(Path.join(root, "CLAUDE.md"), "run tests")
    assert {:ok, text} = Instructions.context(root)
    assert text =~ "preserve history"
    assert text =~ "run tests"
    assert text =~ "one workpad"
    File.rm!(Path.join(root, "CLAUDE.md"))
    File.ln_s!("/etc/passwd", Path.join(root, "CLAUDE.md"))
    assert {:error, :unsafe_repository_instructions} = Instructions.context(root)
  end
end
