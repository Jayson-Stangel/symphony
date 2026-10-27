#!/usr/bin/env python3
"""Failure-injection tests, distinct from real native sandbox qualification."""
import importlib.util
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

PROBE = Path(__file__).resolve().parents[1] / "elixir/priv/codex_readiness.py"
spec = importlib.util.spec_from_file_location("probe", PROBE)
probe = importlib.util.module_from_spec(spec)
exec(compile(PROBE.with_name("codex_config_boundary.py").read_text(), str(PROBE.with_name("codex_config_boundary.py")), "exec"), probe.__dict__)
spec.loader.exec_module(probe)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jay704-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "workspaces"
        self.workspace.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.binary = self.root / "codex"
        self.mode = self.root / "mode"
        self.binary.write_text(f'''#!{sys.executable}
import errno,os,pathlib,signal,sys,time
mode=pathlib.Path({str(self.mode)!r}).read_text()
if mode in ('namespace','proc'):
    print("bwrap: No permissions to create a new namespace" if mode=='namespace' else "bwrap: Can't mount proc on /proc: Operation not permitted", file=sys.stderr)
    sys.exit(1)
if mode=='signal':os.kill(os.getpid(),signal.SIGKILL)
if mode=='zero':sys.exit(0)
if mode=='git-failure':sys.exit(72)
if mode=='timeout':
    import subprocess
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    pathlib.Path({str(self.root / 'descendant.pid')!r}).write_text(str(child.pid))
    time.sleep(60)
args=sys.argv[sys.argv.index('--')+1:]
if mode=='permissive':os.execv(args[0],args)
if mode=='denial-fixture':
    if args[1:2]==['-I']:args=[args[0],*args[2:]]
    source=args[2]
    sys.argv=['-c',*args[3:]]
    original=pathlib.Path.write_text
    denied=[pathlib.Path(p) for p in sys.argv[2:4]]
    def write(p,*a,**kw):
        if any(root in p.parents for root in denied):raise PermissionError(errno.EACCES,'fixture denial')
        return original(p,*a,**kw)
    pathlib.Path.write_text=write
    exec(source)
''')
        self.binary.chmod(0o755)
        self.value = dict(command=f"{self.binary} app-server", workspace=str(self.workspace), remote=False,
                          policy=dict(type="workspaceWrite", networkAccess=False,
                                      excludeTmpdirEnvVar=True, excludeSlashTmp=True))
        self.env = patch.dict(os.environ, {"HOME": str(self.home), "CODEX_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_mode(self, mode):
        self.mode.write_text(mode)
        try:
            return probe.check(self.value)
        finally:
            self.assertEqual(list(self.workspace.iterdir()), [], "owned workspace canaries leaked")

    def test_success_fixture_requires_both_effects_and_denials(self):
        self.assertTrue(self.run_mode("denial-fixture")["ready"])

    def test_owned_git_grant_qualifies_real_git_workflow_and_preserves_other_denials(self):
        self.value['containerized'] = True
        with self.assertRaisesRegex(probe.Refusal, 'sandbox_git_unavailable'):
            self.run_mode('denial-fixture')
        self.value['policy']['writableRoots'] = [str(self.workspace / '.git')]
        self.assertTrue(self.run_mode('denial-fixture')['ready'])
        with self.assertRaisesRegex(probe.Refusal, 'sandbox_git_unavailable'):
            self.run_mode('git-failure')
        with self.assertRaisesRegex(probe.Refusal, 'sandbox_policy_enforcement_failed'):
            self.run_mode('permissive')

    def test_container_wrapper_with_existing_project_skills_runs_git_in_disposable_copy(self):
        self.make_wrapper('exec "$@"\n')
        skills = self.project / '.codex' / 'skills'
        skills.mkdir(parents=True)
        (skills / 'fixture.txt').write_text('preserve source')
        self.value['containerized'] = True
        self.value['policy']['writableRoots'] = [str(self.workspace / '.git')]
        self.assertTrue(self.run_mode('denial-fixture')['ready'])
        self.assertEqual((skills / 'fixture.txt').read_text(), 'preserve source')
        self.assertFalse((self.project / '.git').exists())

    def test_zero_exit_without_effects_is_not_ready(self):
        with self.assertRaisesRegex(probe.Refusal, "sandbox_required_effect_missing"):
            self.run_mode("zero")

    def test_plain_shell_cannot_pass(self):
        with self.assertRaisesRegex(probe.Refusal, "sandbox_policy_enforcement_failed"):
            self.run_mode("permissive")

    def test_stage_diagnostics(self):
        for mode, code in (("namespace", "sandbox_namespace_denied"), ("proc", "sandbox_proc_setup_failed"),
                           ("signal", "sandbox_helper_terminated")):
            with self.subTest(mode=mode), self.assertRaisesRegex(probe.Refusal, code):
                self.run_mode(mode)

    def test_timeout_kills_helper_and_cleans_canaries(self):
        original = subprocess.Popen.wait

        def short_wait(child, timeout=None):
            if timeout:
                deadline = time.monotonic() + 2
                while not (self.root / "descendant.pid").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
            return original(child, timeout=0.1 if timeout else timeout)

        with patch.object(subprocess.Popen, "wait", short_wait), self.assertRaisesRegex(probe.Refusal, "sandbox_timeout"):
            self.run_mode("timeout")
        pid = int((self.root / "descendant.pid").read_text())
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], text=True, capture_output=True).stdout.strip()
        self.assertTrue(not state or state.startswith("Z"), "sandbox descendant remained running")

    def test_missing_helper(self):
        self.value["command"] = "/definitely-missing/codex app-server"
        with self.assertRaisesRegex(probe.Refusal, "sandbox_helper_missing"):
            probe.check(self.value)

    def test_unsupported_inputs_fail_closed(self):
        for field, value in (("command", "codex app-server; true"), ("remote", True),
                             ("policy", {"type": "dangerFullAccess"}),
                             ("policy", {"type": "workspaceWrite", "unknown": True})):
            with self.subTest(field=field), self.assertRaises(probe.Refusal):
                probe.check({**self.value, field: value})

    def test_unknown_native_config_is_not_silently_ignored(self):
        (self.home / "config.toml").write_text("[features]\nuse_legacy_landlock=true\n")
        with self.assertRaisesRegex(probe.Refusal, "sandbox_unqualified_config"):
            probe.check(self.value)

    def make_wrapper(self, body):
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        wrapper = self.project / "with node.sh"
        wrapper.write_text("#!/bin/bash\nset -eu\n" + body)
        self.value["launch_root"] = str(self.project)
        self.value["command"] = f'bash "with node.sh" "{self.binary}" --config shell_environment_policy.inherit=all app-server'
        return wrapper

    def test_wrapper_preserves_environment_and_uses_disposable_project(self):
        self.make_wrapper('export WRAPPER_READY=yes\nprintf setup > setup-result\nexec "$@"\n')
        self.binary.write_text(self.binary.read_text().replace("mode=pathlib", "assert os.environ.get('WRAPPER_READY')=='yes'\nassert pathlib.Path('setup-result').read_text()=='setup'\nmode=pathlib"))
        self.assertTrue(self.run_mode("denial-fixture")["ready"])
        self.assertFalse((self.project / "setup-result").exists())

    def test_project_local_node_receipt_relocates_only_canonical_task_relative_path(self):
        actual = self.root / "actual-task"
        actual.mkdir()
        copied = actual / ".readiness-owned-copy"
        relative = Path("var/toolchain/node-v24.14.0-linux-x64/bin/node")
        for root in (copied, actual):
            (root / relative).parent.mkdir(parents=True)
            (root / relative).write_bytes(b"qualified Node bytes")
        chain = {"node": {"path": str(copied / relative), "sha256": "a" * 64},
                 "frontend": {"path": "/official/frontend", "sha256": "b" * 64}}
        result = probe.rebase_project_node(chain, copied, actual)
        self.assertEqual(result["node"], {"path": str(actual / relative), "sha256": "a" * 64})
        self.assertEqual(result["frontend"], chain["frontend"])
        self.assertEqual(chain["node"]["path"], str(copied / relative))

    def test_global_node_receipt_is_not_relocated(self):
        copied, actual = self.root / "copy", self.root / "actual"
        copied.mkdir(); actual.mkdir()
        global_node = self.root / "global-node"
        global_node.write_bytes(b"node")
        chain = {"node": {"path": str(global_node), "sha256": "a" * 64}}
        self.assertIs(probe.rebase_project_node(chain, copied, actual), chain)

    def test_project_node_relocation_refuses_symlink_and_ambiguous_roots(self):
        copied, actual = self.root / "copy", self.root / "actual"
        copied.mkdir(); actual.mkdir()
        (copied / "node").write_bytes(b"node")
        chain = {"node": {"path": str(copied / "node"), "sha256": "a" * 64}}
        with self.assertRaises(probe.Refusal):
            probe.rebase_project_node(chain, copied, copied)
        foreign = self.root / "foreign"; foreign.mkdir()
        (actual / "node").symlink_to(foreign / "node")
        with self.assertRaises(probe.Refusal):
            probe.rebase_project_node(chain, copied, actual)
        (copied / "alias").symlink_to(copied / "node")
        chain["node"]["path"] = str(copied / "alias")
        with self.assertRaises(probe.Refusal):
            probe.rebase_project_node(chain, copied, actual)

    def test_copy_preserves_private_helper_root_without_changing_source_permissions(self):
        source = self.root / "ordinary source"
        source.mkdir(mode=0o775)
        source.chmod(0o775)
        (source / "tracked").write_text("preserved source")
        target = self.root / "owned helper root"
        target.mkdir(mode=0o700)
        before = target.stat()
        probe.copy_project(source, target)
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        self.assertEqual(target.stat().st_ino, before.st_ino)
        self.assertEqual(source.stat().st_mode & 0o777, 0o775)
        self.assertEqual((source / "tracked").read_text(), "preserved source")
        self.assertEqual((target / "tracked").read_text(), "preserved source")

    def test_copy_failure_restores_only_the_owned_private_destination(self):
        source = self.root / "source"
        source.mkdir(mode=0o775)
        source.chmod(0o775)
        target = self.root / "owned temporary root"
        target.mkdir(mode=0o700)

        def failed_copy(*args, **kwargs):
            target.chmod(0o775)
            raise OSError("ordinary copy failure")

        with patch.object(probe.shutil, "copytree", side_effect=failed_copy), self.assertRaisesRegex(OSError, "ordinary copy failure"):
            probe.copy_project(source, target)
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        self.assertEqual(source.stat().st_mode & 0o777, 0o775)

    def test_copy_never_chmods_a_replacement_or_linked_directory(self):
        source = self.root / "source"
        source.mkdir()
        target = self.root / "owned root"
        target.mkdir(mode=0o700)
        moved = self.root / "moved owned root"
        other = self.root / "surviving directory"
        other.mkdir(mode=0o775)
        other.chmod(0o775)

        def replaced_copy(*args, **kwargs):
            target.chmod(0o775)
            target.rename(moved)
            target.symlink_to(other)

        with patch.object(probe.shutil, "copytree", side_effect=replaced_copy), self.assertRaisesRegex(probe.Refusal, "sandbox_launch_unavailable"):
            probe.copy_project(source, target)
        self.assertEqual(moved.stat().st_mode & 0o777, 0o700)
        self.assertEqual(other.stat().st_mode & 0o777, 0o775)
        self.assertTrue(target.is_symlink())

    def test_copy_refuses_existing_task_roots_and_symlink_destinations(self):
        source = self.root / "source"
        source.mkdir()
        task = self.root / "existing task"
        task.mkdir(mode=0o700)
        (task / "canary").write_text("keep")
        with self.assertRaisesRegex(probe.Refusal, "sandbox_launch_unavailable"):
            probe.copy_project(source, task)
        self.assertEqual((task / "canary").read_text(), "keep")
        task.chmod(0o775)
        with self.assertRaisesRegex(probe.Refusal, "sandbox_launch_unavailable"):
            probe.copy_project(source, task)
        self.assertEqual(task.stat().st_mode & 0o777, 0o775)
        link = self.root / "linked target"
        link.symlink_to(task)
        with self.assertRaises(OSError):
            probe.copy_project(source, link)
        self.assertEqual(task.stat().st_mode & 0o777, 0o775)

    def test_snapshot_preserves_internal_links_and_refuses_foreign_links(self):
        self.make_wrapper('cat internal-link >/dev/null\nexec "$@"\n')
        (self.project / "data").write_text("project data")
        link = self.project / "internal-link"
        link.symlink_to(self.project / "data")
        self.assertTrue(self.run_mode("denial-fixture")["ready"])
        link.unlink()
        link.symlink_to(self.home)
        with self.assertRaisesRegex(probe.Refusal, "sandbox_launch_unavailable"):
            self.run_mode("denial-fixture")

    def test_snapshot_excludes_its_own_directory_when_workspace_is_project(self):
        self.make_wrapper('exec "$@"\n')
        self.workspace = self.project
        self.value["workspace"] = str(self.project)
        self.mode.write_text("denial-fixture")
        self.assertTrue(probe.check(self.value)["ready"])
        self.assertEqual(sorted(p.name for p in self.project.iterdir()), ["with node.sh"])

    def test_direct_executable_path_with_spaces(self):
        directory = self.root / "bin with spaces"
        directory.mkdir()
        binary = directory / "codex"
        binary.write_bytes(self.binary.read_bytes())
        binary.chmod(0o755)
        self.value["command"] = f'"{binary}" app-server'
        self.assertTrue(self.run_mode("denial-fixture")["ready"])

    def test_wrapper_setup_failure_is_distinct_from_sandbox_failure(self):
        wrapper = self.make_wrapper('exit 3\n')
        with self.assertRaisesRegex(probe.Refusal, "sandbox_launch_failed"):
            self.run_mode("denial-fixture")
        wrapper.write_text('exec "$@"\n')
        with self.assertRaisesRegex(probe.Refusal, "sandbox_namespace_denied"):
            self.run_mode("namespace")

    def snapshot_fixture(self, program):
        # Outer mount/image attestation is fixture input. The real child process,
        # wrapper, private inherited FD, receipt parser and cleanup still run.
        self.value.update(snapshot_profile="trust-ro-v2", launch_program=program)
        context = patch.object(probe, "attest_context", return_value={"config_bytes": b"", "executable": str(self.binary)}, create=True)
        image = patch.object(probe, "image_chain", return_value={"profile": "fixture"}, create=True)
        context.start(); image.start()
        self.addCleanup(context.stop); self.addCleanup(image.stop)

    def test_retained_wrapper_failure_without_guard_receipt_has_bootstrap_stage(self):
        wrapper = self.make_wrapper('printf "secret-child-output" >&2\nexit 78\n')
        self.snapshot_fixture("raise RuntimeError('guard must not run')")
        with self.assertRaisesRegex(probe.Refusal, "^sandbox_wrapper_failed$"):
            self.run_mode("denial-fixture")
        wrapper.write_text('exit 0\n')
        with self.assertRaisesRegex(probe.Refusal, "^sandbox_wrapper_failed$"):
            self.run_mode("denial-fixture")

    def test_retained_success_receipt_keeps_existing_chain_and_effect_checks(self):
        self.make_wrapper('exec "$@"\n')
        program = ("import json,os,sys; value=json.loads(sys.argv[1]); "
                   "chain={**value['expected_image_chain'],'node':{'path':os.path.realpath(sys.executable),'sha256':'a'*64}}; "
                   "os.write(value['receipt_fd'],json.dumps(chain).encode()); os.close(value['receipt_fd']); "
                   "os.execv(sys.argv[2],sys.argv[2:])")
        self.snapshot_fixture(program)
        result = self.run_mode("denial-fixture")
        self.assertTrue(result["ready"])
        self.assertEqual(result["launch_chain"]["profile"], "fixture")
        self.assertEqual(result["launch_chain"]["node"]["sha256"], "a" * 64)
        with self.assertRaisesRegex(probe.Refusal, "^sandbox_policy_enforcement_failed$"):
            self.run_mode("permissive")

    def managed_browser_program(self):
        # The native sandbox effects remain a fixture, but disposition and
        # environment initialization execute the real shipped probe helper.
        return PROBE.with_name('codex_config_boundary.py').read_text() + """
value=json.loads(sys.argv[1])
try:
    os.environ.update(probe_browser_environment(value, os.environ))
except Refusal as error:
    os.write(value['receipt_fd'],json.dumps({'error':str(error)}).encode())
    os.close(value['receipt_fd'])
    sys.exit(78)
selected=Path(os.environ.get('CHROME_CONFIG_HOME',os.environ.get('XDG_CONFIG_HOME')))
assert Path(value['workspace']) in selected.parents
chain={**value['expected_image_chain'],'node':{'path':os.path.realpath(sys.executable),'sha256':'a'*64}}
os.write(value['receipt_fd'],json.dumps(chain).encode())
os.close(value['receipt_fd'])
os.execv(sys.argv[2],sys.argv[2:])
"""

    def retained_browser_state_case(self, explicit):
        state_name = 'team-browser-state' if explicit else '.symphony-browser-config'
        self.make_wrapper(f'test ! -e {state_name}/chromium/SingletonSocket\nexec "$@"\n')
        self.value['workspace'] = str(self.project)
        state = self.project / state_name
        (state / 'chromium').mkdir(parents=True)
        socket = state / 'chromium' / 'SingletonSocket'
        socket.symlink_to('/tmp/.org.chromium.fixture/SingletonSocket')
        self.snapshot_fixture(self.managed_browser_program())
        if explicit:
            browser_env = patch.dict(os.environ, CHROME_CONFIG_HOME=str(state))
            browser_env.start()
            self.addCleanup(browser_env.stop)
        self.assertTrue(self.run_mode('denial-fixture')['ready'])
        self.assertTrue(socket.is_symlink())
        self.assertEqual(os.readlink(socket), '/tmp/.org.chromium.fixture/SingletonSocket')
        # Exclusion is narrow: an unrelated project escape still refuses.
        (self.project / 'unrelated-link').symlink_to(self.home, target_is_directory=True)
        with self.assertRaisesRegex(probe.Refusal, '^sandbox_browser_config_unsafe$'):
            self.run_mode('denial-fixture')

    def test_default_browser_state_runtime_symlinks_do_not_break_restart(self):
        self.retained_browser_state_case(explicit=False)

    def test_explicit_browser_state_runtime_symlinks_do_not_break_restart(self):
        self.retained_browser_state_case(explicit=True)

    def wrapper_selected_browser_case(self, absolute=False, unsafe=False):
        wrapper = self.make_wrapper('exec "$@"\n')
        self.value['workspace'] = str(self.project)
        state = self.project / 'var' / 'chrome'
        state.parent.mkdir()
        if unsafe:
            state.symlink_to(self.home, target_is_directory=True)
        else:
            (state / 'chromium').mkdir(parents=True)
            (state / 'chromium' / 'SingletonSocket').symlink_to('/tmp/.org.chromium.fixture/socket')
            (state / 'retained').write_text('must-stay-unchanged')
        selected = str(state) if absolute else '$PWD/var/chrome'
        wrapper.write_text('#!/bin/bash\nset -eu\nexport CHROME_CONFIG_HOME="' + selected + '"\nexec "$@"\n')
        self.snapshot_fixture(self.managed_browser_program())
        if unsafe:
            with self.assertRaisesRegex(probe.Refusal, '^sandbox_browser_config_unsafe$'):
                self.run_mode('denial-fixture')
            self.assertTrue(state.is_symlink())
            self.assertEqual(list(self.home.iterdir()), [])
        else:
            self.assertTrue(self.run_mode('denial-fixture')['ready'])
            self.assertEqual(sorted(path.name for path in state.iterdir()), ['chromium', 'retained'])
            self.assertEqual((state / 'retained').read_text(), 'must-stay-unchanged')
            self.assertEqual(os.readlink(state / 'chromium' / 'SingletonSocket'), '/tmp/.org.chromium.fixture/socket')
            (self.project / 'unrelated').symlink_to(self.home, target_is_directory=True)
            with self.assertRaisesRegex(probe.Refusal, '^sandbox_browser_config_unsafe$'):
                self.run_mode('denial-fixture')

    def test_wrapper_relative_browser_state_restart_omits_only_its_runtime_links(self):
        self.wrapper_selected_browser_case()

    def test_wrapper_absolute_browser_state_restart_does_not_initialize_real_source(self):
        self.wrapper_selected_browser_case(absolute=True)

    def test_wrapper_selected_browser_root_link_remains_unsafe(self):
        self.wrapper_selected_browser_case(unsafe=True)

    def test_selected_browser_root_symlink_refuses_before_native_launch(self):
        self.make_wrapper('exec "$@"\n')
        self.value['workspace'] = str(self.project)
        state = self.project / '.symphony-browser-config'
        state.symlink_to(self.home, target_is_directory=True)
        self.snapshot_fixture(self.managed_browser_program())
        with self.assertRaisesRegex(probe.Refusal, '^sandbox_browser_config_unsafe$'):
            self.run_mode('denial-fixture')
        self.assertTrue(state.is_symlink())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_retained_browser_configuration_is_validated_then_rebased_without_real_writes(self):
        self.make_wrapper('exec "$@"\n')
        self.snapshot_fixture(self.managed_browser_program())
        chrome = self.workspace / 'team-chrome' / 'config'
        with patch.dict(os.environ, CHROME_CONFIG_HOME=str(chrome), XDG_CONFIG_HOME='/ignored-invalid-lower-priority'):
            self.assertTrue(self.run_mode('denial-fixture')['ready'])
        self.assertFalse(chrome.parent.exists())
        for selected in ('relative', str(self.home / 'config')):
            with patch.dict(os.environ, CHROME_CONFIG_HOME=selected), \
                    self.assertRaisesRegex(probe.Refusal, '^sandbox_browser_config_unsafe$'):
                self.run_mode('denial-fixture')

    def test_wrapper_derived_inherited_browser_state_keeps_setup_and_init_disposable(self):
        self.make_wrapper('mkdir -p "$CHROME_CONFIG_HOME/setup-only"\nexport CHROME_CONFIG_HOME="$CHROME_CONFIG_HOME/subdir"\nexec "$@"\n')
        self.value['workspace'] = str(self.project)
        self.snapshot_fixture(self.managed_browser_program())
        original = self.project / 'team-state'
        with patch.dict(os.environ, CHROME_CONFIG_HOME=str(original)):
            self.assertTrue(self.run_mode('denial-fixture')['ready'])
        self.assertFalse(original.exists(), 'setup or initialization wrote real source')
        original.mkdir()
        (original / 'subdir').symlink_to(self.home, target_is_directory=True)
        with patch.dict(os.environ, CHROME_CONFIG_HOME=str(original)), \
                self.assertRaisesRegex(probe.Refusal, '^sandbox_browser_config_unsafe$'):
            self.run_mode('denial-fixture')
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual([path.name for path in original.iterdir()], ['subdir'])

    def test_startup_default_and_wrapper_explicit_source_obey_original_policy(self):
        wrapper = self.make_wrapper('exec "$@"\n')
        self.snapshot_fixture(self.managed_browser_program())
        self.assertNotEqual(self.project, self.workspace)
        self.assertTrue(self.run_mode('denial-fixture')['ready'])
        wrapper.write_text('#!/bin/bash\nexport CHROME_CONFIG_HOME="$PWD/var/chrome"\nexec "$@"\n')
        with self.assertRaisesRegex(probe.Refusal, '^sandbox_browser_config_unsafe$'):
            self.run_mode('denial-fixture')
        self.assertFalse((self.project / 'var').exists())
        self.value['policy']['writableRoots'] = [str(self.project)]
        self.assertTrue(self.run_mode('denial-fixture')['ready'])
        self.assertFalse((self.project / 'var').exists())

    def unsafe_inherited_browser_case(self, key, replacement, unrelated_link=False):
        setup = 'mkdir -p "${CHROME_CONFIG_HOME:-$XDG_CONFIG_HOME}/setup-only"\n'
        if replacement:
            setup += f'export {key}="$PWD/owned-browser"\n'
        self.make_wrapper(setup + 'exec "$@"\n')
        self.value['workspace'] = str(self.project)
        self.snapshot_fixture(self.managed_browser_program())
        auth = self.home / 'auth.json'
        auth.write_text('{"synthetic_auth_canary":true}')
        inherited = self.home / 'browser-config'
        if unrelated_link:
            (self.project / 'unrelated-link').symlink_to(self.home, target_is_directory=True)
        source_before = sorted(path.name for path in self.project.iterdir())
        with patch.dict(os.environ):
            os.environ.pop('CHROME_CONFIG_HOME', None)
            os.environ.pop('XDG_CONFIG_HOME', None)
            os.environ[key] = str(inherited)
            if replacement and not unrelated_link:
                self.assertTrue(self.run_mode('denial-fixture')['ready'])
            else:
                with self.assertRaisesRegex(probe.Refusal, '^sandbox_browser_config_unsafe$'):
                    self.run_mode('denial-fixture')
        self.assertEqual(sorted(path.name for path in self.project.iterdir()), source_before)
        self.assertFalse((self.project / 'owned-browser').exists())
        self.assertFalse(inherited.exists(), 'diagnostic setup or init wrote real inherited destination')
        self.assertEqual(auth.read_text(), '{"synthetic_auth_canary":true}')
        self.assertEqual(list(self.home.iterdir()), [auth])
        if unrelated_link:
            self.assertTrue((self.project / 'unrelated-link').is_symlink())

    def test_trusted_wrapper_can_replace_unsafe_inherited_chrome_without_source_writes(self):
        self.unsafe_inherited_browser_case('CHROME_CONFIG_HOME', replacement=True)

    def test_trusted_wrapper_can_replace_unsafe_inherited_xdg_without_source_writes(self):
        self.unsafe_inherited_browser_case('XDG_CONFIG_HOME', replacement=True)

    def test_unchanged_unsafe_inherited_chrome_refuses_at_final_guard(self):
        self.unsafe_inherited_browser_case('CHROME_CONFIG_HOME', replacement=False)

    def test_unchanged_unsafe_inherited_xdg_refuses_at_final_guard(self):
        self.unsafe_inherited_browser_case('XDG_CONFIG_HOME', replacement=False)

    def test_wrapper_replacement_does_not_authorize_unrelated_source_escape_links(self):
        self.unsafe_inherited_browser_case('CHROME_CONFIG_HOME', replacement=True, unrelated_link=True)

    def test_direct_launch_still_refuses_unsafe_inherited_browser_destination_before_launch(self):
        self.snapshot_fixture('raise AssertionError("unsafe direct launch reached guard")')
        for key in ('CHROME_CONFIG_HOME', 'XDG_CONFIG_HOME'):
            with self.subTest(key=key), patch.dict(os.environ):
                os.environ.pop('CHROME_CONFIG_HOME', None)
                os.environ.pop('XDG_CONFIG_HOME', None)
                os.environ[key] = str(self.home / 'browser-config')
                with self.assertRaisesRegex(probe.Refusal, '^sandbox_browser_config_unsafe$'):
                    self.run_mode('denial-fixture')
        self.assertEqual(list(self.home.iterdir()), [])

    def test_retained_guard_error_receipt_distinguishes_config_from_toolchain(self):
        self.make_wrapper('exec "$@"\n')
        self.snapshot_fixture("")
        for code in ("sandbox_unqualified_config", "sandbox_toolchain_unqualified", "sandbox_launch_failed",
                     "sandbox_browser_config_unsafe", "sandbox_browser_config_unwritable"):
            self.value["launch_program"] = ("import json,os,sys; value=json.loads(sys.argv[1]); "
                "os.write(value['receipt_fd'], " + repr(json.dumps({"error": code}).encode()) + "); "
                "print('secret-child-output',file=sys.stderr); sys.exit(78)")
            with self.subTest(code=code), self.assertRaisesRegex(probe.Refusal, "^" + code + "$"):
                self.run_mode("denial-fixture")

    def test_retained_invalid_receipts_never_become_config_refusals_or_readiness(self):
        self.snapshot_fixture("")
        for receipt in (b"{", b"[]", b"{}", b"x" * 16385,
                        b'{"error":"secret-child-output"}',
                        b'{"error":"sandbox_unqualified_config","extra":"secret"}'):
            # Bound writes to avoid filling the pipe; oversized input is fed at
            # its reader boundary separately below.
            if len(receipt) > 16384:
                with self.assertRaisesRegex(probe.Refusal, "^sandbox_launch_receipt_invalid$"):
                    probe.launch_receipt(receipt, {"profile": "fixture"}, False)
            else:
                self.value["launch_program"] = ("import json,os,sys; "
                    "os.write(json.loads(sys.argv[1])['receipt_fd'], " + repr(receipt) + ")")
                with self.subTest(receipt=receipt), self.assertRaisesRegex(probe.Refusal, "^sandbox_launch_receipt_invalid$"):
                    self.run_mode("denial-fixture")

    def test_missing_direct_receipt_and_process_spawn_failure_identify_launch_stage(self):
        self.snapshot_fixture("")
        with self.assertRaisesRegex(probe.Refusal, "^sandbox_launch_failed$"):
            self.run_mode("denial-fixture")
        with patch.object(probe.subprocess, "Popen", side_effect=OSError("secret-child-output")):
            with self.assertRaisesRegex(probe.Refusal, "^sandbox_launch_failed$"):
                self.run_mode("denial-fixture")

    def test_wrapper_missing_and_wrong_provider_refuse(self):
        wrapper = self.make_wrapper('exec "$@"\n')
        wrapper.unlink()
        with self.assertRaisesRegex(probe.Refusal, "sandbox_launch_unavailable"):
            self.run_mode("denial-fixture")
        self.value["command"] = "claude app-server"
        with self.assertRaisesRegex(probe.Refusal, "sandbox_unsupported_command"):
            probe.check(self.value)

    def test_wrapper_cannot_inject_unqualified_native_configuration(self):
        self.make_wrapper('export NODE_OPTIONS="--require bad.js"\nexec "$@"\n')
        with self.assertRaisesRegex(probe.Refusal, "sandbox_unqualified_config"):
            self.run_mode("denial-fixture")

    def test_shell_payloads_are_not_executed(self):
        payload = self.root / "unrelated-payload"
        for command in (f"bash -c 'touch {payload}'", f"codex app-server; touch {payload}",
                        f"bash wrapper.sh codex app-server $(touch {payload})",
                        "bash ../outside.sh codex app-server", "bash wrapper.sh claude app-server"):
            with self.subTest(command=command), self.assertRaises(probe.Refusal):
                probe.check({**self.value, "command": command})
        self.assertFalse(payload.exists())

    def test_generated_command_is_supported(self):
        self.value["command"] = f"{self.binary} --config shell_environment_policy.inherit=all app-server"
        self.assertTrue(self.run_mode("denial-fixture")["ready"])


class NativeSmokeFixtureTests(unittest.TestCase):
    def test_generated_wrapper_preserves_path_and_argument_boundaries(self):
        source = (PROBE.parents[2] / "runner/codex-sandbox-smoke.sh").read_text()
        start = source.index('  project="/workspaces/launch project"')
        stop = source.index('  SYMPHONY_LAUNCH_ROOT=', start)
        with tempfile.TemporaryDirectory(prefix="wrapper generator ") as directory:
            project = Path(directory) / "project with spaces"
            binaries = Path(directory) / "bin"
            binaries.mkdir()
            binary = binaries / "codex"
            binary.write_text(f"#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
            binary.chmod(0o755)
            code = source[start:stop].replace('/workspaces/launch project', str(project))
            code = code.replace('/tmp/wrapper-WORKFLOW.md', shlex.quote(str(Path(directory) / "WORKFLOW.md")))
            env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"]}
            subprocess.run(["sh", "-ceu", code], env=env, check=True, capture_output=True)
            expected = ["--config", "shell_environment_policy.inherit=all", "sandbox", "argument with spaces"]
            result = subprocess.run(["bash", str(project / "scripts/with node.sh"), "codex", *expected],
                                    cwd=project, env=env, text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout), expected)
            self.assertIn('command: bash "scripts/with node.sh" codex',
                          (Path(directory) / "WORKFLOW.md").read_text())


class GitHarnessTests(unittest.TestCase):
    def test_interleaved_notifications_and_buffered_response_are_consumed(self):
        harness_spec = importlib.util.spec_from_file_location("git_harness", PROBE.parents[2] / "runner/codex-git-workflow.py")
        harness = importlib.util.module_from_spec(harness_spec)
        harness_spec.loader.exec_module(harness)
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b'{"method":"notification"}\n{"id":1,"result":{"exitCode":0}}\n{"id":2,"result":{}}\n')
            with os.fdopen(read_fd, 'rb', buffering=0) as stream:
                buffer = bytearray()
                self.assertEqual(harness.read_response(stream, buffer, 1, timeout=1), {'exitCode': 0})
                # The fd has no new data; the second response is already buffered.
                self.assertEqual(harness.read_response(stream, buffer, 2, timeout=1), {})
        finally:
            os.close(write_fd)


class BoundaryHarnessTests(unittest.TestCase):
    def test_failed_or_empty_helper_cannot_qualify_process_and_network_effects(self):
        boundary_spec = importlib.util.spec_from_file_location("boundaries", PROBE.parents[2] / "runner/codex-boundaries.py")
        boundaries = importlib.util.module_from_spec(boundary_spec)
        boundary_spec.loader.exec_module(boundaries)
        for status, stdout in [(1, ""), (0, ""), (0, "unrelated success")]:
            with self.subTest(status=status, stdout=stdout):
                result = subprocess.CompletedProcess([], status, stdout, "")
                with patch.object(boundaries.subprocess, "run", return_value=result), self.assertRaises(RuntimeError):
                    boundaries.protected_checks(12345, 34567)


if __name__ == "__main__":
    unittest.main()
