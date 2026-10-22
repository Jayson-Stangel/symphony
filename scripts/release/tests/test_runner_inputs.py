import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("runner_inputs", Path(__file__).parents[1] / "runner_inputs.py")
INPUTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INPUTS)
BASE = "base@sha256:" + "a" * 64


class RunnerInputsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "--quiet")
        self.write(".dockerignore", ".git\n**/node_modules\nelixir/_build\nelixir/deps\n**/.env\n")
        self.write("runner/Dockerfile", f"""FROM {BASE} AS browser
COPY runner/browser/package-lock.json /browser/
RUN echo compile-browser
FROM browser AS common
COPY elixir /app
COPY .codex/skills /skills
FROM {BASE} AS claude-deps
COPY runner/claude/*.mjs /claude/
RUN echo compile-claude
FROM common AS claude
COPY --from=claude-deps /claude /claude
LABEL com.symphony.agent=claude com.symphony.protocol=1
FROM common AS codex
ARG CODEX_VERSION=1.0
RUN echo install-$CODEX_VERSION
LABEL com.symphony.agent=codex com.symphony.protocol=1
""")
        for name in ("runner/browser/package-lock.json", "runner/claude/runtime.mjs", "elixir/mix.lock",
                     "elixir/lib/engine.ex", "elixir/WORKFLOW.md", "elixir/AGENTS.md",
                     ".codex/skills/linear/SKILL.md", "scripts/package/host.swift", "docs/readme.md"):
            self.write(name, "original\n")
        self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], env=self.env, stderr=subprocess.PIPE)

    def write(self, path, contents):
        filename = self.root / path
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(contents)

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--quiet", "-m", "actual source")

    def identities(self):
        return [INPUTS.identity(self.root, provider, "arm64")["input_identity"] for provider in ("codex", "claude")]

    def test_browser_projection_uses_same_recipe_and_ignores_only_nonbrowser_inputs(self):
        recipe = self.root / "runner/Dockerfile"
        recipe.write_text(recipe.read_text().replace("AS browser\n", "AS browser-tools\n").replace("FROM browser AS common", "FROM browser-tools AS common"))
        self.commit()
        before = INPUTS.identity(self.root, "browser-tools", "arm64")["input_identity"]
        self.write("elixir/lib/engine.ex", "actual engine change")
        self.commit()
        self.assertEqual(before, INPUTS.identity(self.root, "browser-tools", "arm64")["input_identity"])
        self.write("runner/browser/package-lock.json", "actual browser dependency change")
        self.commit()
        self.assertNotEqual(before, INPUTS.identity(self.root, "browser-tools", "arm64")["input_identity"])

    def test_manager_revision_and_unconsumed_docs_do_not_change_runtime_identity(self):
        before = self.identities()
        self.write("scripts/package/host.swift", "actual manager/helper change\n")
        self.write("docs/readme.md", "actual unconsumed docs change\n")
        self.write("scripts/release/manager-version", "1.2.4\n")
        self.commit()
        self.assertEqual(before, self.identities())
        self.write("elixir/_build/generated", "ignored build output\n")
        self.write("elixir/deps/generated", "ignored dependency output\n")
        self.assertEqual(before, self.identities())

    def test_provider_only_source_and_recipe_affect_only_consuming_provider(self):
        before = self.identities()
        self.write("runner/claude/runtime.mjs", "actual Claude-only runtime change\n")
        self.commit()
        after = self.identities()
        self.assertEqual(before[0], after[0])
        self.assertNotEqual(before[1], after[1])
        recipe = self.root / "runner/Dockerfile"
        recipe.write_text(recipe.read_text().replace("RUN echo compile-claude", "RUN echo different-claude-recipe"))
        self.commit()
        changed = self.identities()
        self.assertEqual(after[0], changed[0])
        self.assertNotEqual(after[1], changed[1])

    def test_whole_elixir_copy_skills_locks_and_operational_markdown_are_real_inputs(self):
        for path in ("elixir/lib/engine.ex", "elixir/mix.lock", "elixir/WORKFLOW.md", "elixir/AGENTS.md",
                     ".codex/skills/linear/SKILL.md", "runner/browser/package-lock.json"):
            before = self.identities()
            self.write(path, "changed " + path)
            self.commit()
            for old, new in zip(before, self.identities()):
                self.assertNotEqual(old, new, path)

    def test_add_rename_delete_and_ignore_changes_invalidate_actual_closure(self):
        before = self.identities()
        self.write("elixir/added.ex", "actual added runtime input")
        self.commit()
        self.assertNotEqual(before, self.identities())
        before = self.identities()
        (self.root / "elixir/added.ex").rename(self.root / "elixir/renamed.ex")
        self.commit()
        self.assertNotEqual(before, self.identities())
        before = self.identities()
        (self.root / "elixir/renamed.ex").unlink()
        self.commit()
        self.assertNotEqual(before, self.identities())
        before = self.identities()
        self.write(".dockerignore", (self.root / ".dockerignore").read_text() + "elixir/AGENTS.md\n")
        self.commit()
        after_ignore = self.identities()
        self.assertNotEqual(before, after_ignore)
        self.write("elixir/AGENTS.md", "now actually ignored by Docker")
        self.assertEqual(after_ignore, self.identities())

    def test_platform_and_effective_build_options_separate_identities(self):
        first = INPUTS.identity(self.root, "codex", "arm64")["input_identity"]
        self.assertNotEqual(first, INPUTS.identity(self.root, "codex", "x86_64")["input_identity"])
        self.assertNotEqual(first, INPUTS.identity(self.root, "codex", "arm64", {"CODEX_VERSION": "2.0"})["input_identity"])
        for options in ({"UNKNOWN": "1"}, {"SYMPHONY_REVISION": "a" * 40}):
            with self.assertRaises(ValueError):
                INPUTS.identity(self.root, "codex", "arm64", options)

    def test_dirty_untracked_symlink_and_missing_consumed_inputs_refuse_reuse(self):
        path = self.root / "elixir/lib/engine.ex"
        path.write_text("dirty source")
        with self.assertRaisesRegex(ValueError, "modified consumed"):
            self.identities()
        path.write_text("original\n")
        self.write("elixir/untracked.ex", "untracked source")
        with self.assertRaisesRegex(ValueError, "uncommitted consumed"):
            self.identities()
        (self.root / "elixir/untracked.ex").unlink()
        (self.root / "elixir/unknown-empty").mkdir()
        with self.assertRaisesRegex(ValueError, "empty directory"):
            self.identities()
        (self.root / "elixir/unknown-empty").rmdir()
        path.unlink()
        path.symlink_to("../../docs/readme.md")
        with self.assertRaisesRegex(ValueError, "nonregular"):
            self.identities()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.identities()

    def test_deleted_or_dangling_ignore_metadata_refuses_uncommitted_fallback(self):
        ignore = self.root / '.dockerignore'
        data = ignore.read_text()
        ignore.unlink()
        with self.assertRaisesRegex(ValueError, 'ignore file is missing'):
            self.identities()
        ignore.write_text(data)
        specific = self.root / 'runner/Dockerfile.dockerignore'
        specific.write_text(data)
        self.commit()
        specific.unlink()
        with self.assertRaisesRegex(ValueError, 'ignore file is missing'):
            self.identities()
        specific.symlink_to('missing')
        with self.assertRaisesRegex(ValueError, 'nonregular'):
            self.identities()

    def test_explicit_automatic_platform_override_cannot_alias_native_identity(self):
        recipe = self.root / 'runner/Dockerfile'
        recipe.write_text(recipe.read_text() + 'ARG TARGETARCH\nLABEL actual-arch=$TARGETARCH\n')
        self.commit()
        INPUTS.identity(self.root, 'codex', 'arm64')
        for architecture in ('amd64', 'arm64'):
            with self.assertRaisesRegex(ValueError, 'platform override'):
                INPUTS.identity(self.root, 'codex', 'arm64', {'TARGETARCH': architecture})

    def test_metadata_arguments_must_be_final_labels_and_cannot_flow_into_child_runtime(self):
        recipe = self.root / 'runner/Dockerfile'
        original = recipe.read_text()
        recipe.write_text(original + 'ARG SYMPHONY_REVISION=unknown\nLABEL source=$SYMPHONY_REVISION\n')
        self.commit()
        self.identities()
        for instruction in ('RUN env', 'ENV CAPTURED=$SYMPHONY_REVISION', 'COPY elixir /captured'):
            recipe.write_text(original + 'ARG SYMPHONY_REVISION=unknown\n' + instruction + '\n')
            self.commit()
            with self.subTest(instruction=instruction), self.assertRaisesRegex(ValueError, 'metadata'):
                self.identities()
        recipe.write_text(original.replace('COPY .codex/skills /skills', 'COPY .codex/skills /skills\nARG SYMPHONY_VERSION=unknown').replace('FROM common AS claude', 'FROM common AS claude\nRUN env'))
        self.commit()
        with self.assertRaisesRegex(ValueError, 'metadata'):
            self.identities()

    def test_unknown_syntax_unpinned_bases_and_dirty_recipe_refuse_reuse(self):
        recipe = self.root / "runner/Dockerfile"
        original = recipe.read_text()
        recipe.write_text(original + "# actual dirty recipe\n")
        with self.assertRaisesRegex(ValueError, "modified consumed"):
            self.identities()
        for changed in (original.replace(BASE, "base:latest"),
                        original.replace("RUN echo compile-browser", "RUN --mount=type=secret echo bad"),
                        original.replace("COPY elixir /app", "COPY --exclude=secret elixir /app")):
            recipe.write_text(changed)
            with self.assertRaises(ValueError):
                self.identities()
        recipe.write_text(original)
        self.write(".dockerignore", "!elixir/secrets\n")
        with self.assertRaisesRegex(ValueError, "unsupported Docker ignore"):
            self.identities()


if __name__ == "__main__":
    unittest.main()
