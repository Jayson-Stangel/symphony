#!/usr/bin/env python3
"""Native macOS/Linux package and helper regressions; no account/model requests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
import stat
import unittest

from first_mac_install_test import FirstMacInstallMixin
from shell_setup_test import ShellSetupTest
from runner_import_test import RunnerImportTest
from command_activation_test import CommandActivationTest, PackageCommandActivationMixin
from runner_provision_test import RunnerProvisionTest

ROOT = Path(__file__).resolve().parents[2]
HELPER = Path(os.environ.get("SYMPHONY_TEST_HOST_HELPER", "/tmp/symphony-package-test-host"))


def call(args, **kwargs):
    return subprocess.run([str(v) for v in args], text=True, capture_output=True, **kwargs)


def manifest(version="1.2.3", revision=None):
    revision = revision or call(["git", "-C", ROOT, "rev-parse", "HEAD"]).stdout.strip()
    runners = {}
    for provider in ("codex", "claude"):
        runners[provider] = {}
        for arch, platform in (("arm64", "linux/arm64"), ("x86_64", "linux/amd64")):
            runners[provider][arch] = dict(platform=platform, image=f"ghcr.io/test/{provider}@sha256:" + "b" * 64,
                source_revision=revision, tool_versions={tool: "1.0" for tool in (provider, "git", "node", "playwright")})
    bundle = dict(filename=f"symphony-{version}-macos.tar.gz", architectures=["arm64"])
    return dict(schema_version=1, version=version, source_revision=revision, channel="candidate",
        host_architectures=["arm64", "x86_64"], supported_hosts={"macos": ["arm64"], "linux_x86_64": ["x86_64"]}, runners=runners, bundle=bundle,
        bundles=dict(macos=bundle, linux_x86_64=dict(filename=f"symphony-{version}-linux_x86_64.tar.gz", architectures=["x86_64"])))


class InstallerLaunchTest(unittest.TestCase):
    """Execute the shipped installer body with explicit helper process peers.

    These fixtures distinguish shell stages; they do not model Gatekeeper or
    replace the native archive/inventory/install tests below.
    """
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="symphony-installer-launch-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "download with spaces"
        (self.bundle / "libexec").mkdir(parents=True)
        (self.bundle / "scripts").mkdir()
        source = (ROOT / "scripts/release/package.sh").read_text()
        installer = source.split("<<'INSTALL'\n", 1)[1].split("\nINSTALL\n", 1)[0]
        self.install = self.bundle / "install.sh"
        self.install.write_text(installer + "\n"); self.install.chmod(0o755)
        (self.bundle / "host-platform").write_text("macos\n" if sys.platform == "darwin" else "linux_x86_64\n")
        self.activation = self.root / "activated"
        manager = self.bundle / "scripts/symphony"
        manager.write_text('#!/bin/sh\nprintf activated > "$TEST_ACTIVATION"\n')
        manager.chmod(0o755)
        self.helper = self.bundle / "libexec/symphony-host"
        self.env = dict(os.environ, TEST_ACTIVATION=str(self.activation))
        self.existing = self.root / "existing-command"
        self.existing.write_text("user-owned-command")

    def peer(self, body):
        self.helper.write_text("#!/bin/sh\n" + body + "\n")
        self.helper.chmod(0o755)

    def assertPreserved(self, result, status):
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        self.assertFalse(self.activation.exists())
        self.assertEqual(self.existing.read_text(), "user-owned-command")

    def test_helper_killed_at_start_refuses_before_inventory_and_can_retry(self):
        self.peer('kill -KILL $$')
        failed = call([self.install], env=self.env)
        self.assertPreserved(failed, 137)
        self.assertIn("native helper startup failed (exit status 137)", failed.stderr)
        self.assertIn("not proof of a checksum mismatch", failed.stderr)
        if sys.platform == "darwin":
            self.assertIn("Privacy & Security", failed.stderr)
            self.assertIn("trusted candidate/release checksum", failed.stderr)
        # Retry after restoring an executable peer; this represents only the
        # retry control flow, not actual macOS approval.
        self.peer('case "$1" in host-platform|verify-files) exit 0 ;; *) exit 99 ;; esac')
        retried = call([self.install], env=self.env)
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertTrue(self.activation.exists())

    def test_inventory_refusal_is_not_reported_as_security_approval(self):
        self.peer('case "$1" in host-platform) exit 0 ;; verify-files) echo "bundle checksum mismatch: ./payload" >&2; exit 1 ;; *) exit 99 ;; esac')
        failed = call([self.install], env=self.env)
        self.assertPreserved(failed, 1)
        self.assertIn("bundle checksum mismatch", failed.stderr)
        self.assertIn("bundle inventory verification failed", failed.stderr)
        self.assertNotIn("Open Anyway", failed.stderr)

    def test_verification_interruption_is_not_reported_as_checksum_refusal(self):
        self.peer('case "$1" in host-platform) exit 0 ;; verify-files) kill -KILL $$ ;; *) exit 99 ;; esac')
        failed = call([self.install], env=self.env)
        self.assertPreserved(failed, 137)
        self.assertIn("native helper inventory-verification failed (exit status 137)", failed.stderr)
        self.assertNotIn("bundle inventory verification failed", failed.stderr)

    def test_unlaunchable_helper_refuses_before_activation(self):
        self.peer('exit 0')
        self.helper.chmod(0o644)
        failed = call([self.install], env=self.env)
        self.assertIn(failed.returncode, (1, 126), failed.stderr)
        self.assertPreserved(failed, failed.returncode)
        self.assertIn("native helper startup failed", failed.stderr)


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "native helper requires macOS or Linux")
class PackageTest(FirstMacInstallMixin, PackageCommandActivationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HELPER.is_file():
            result = call([ROOT / "scripts/release/build-host-helper.sh", HELPER, "--native"])
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="symphony-package-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.identity_home = self.root / "identity-home"; self.identity_home.mkdir()
        self.git_env = dict(os.environ, HOME=str(self.identity_home), XDG_CONFIG_HOME=str(self.identity_home / "xdg"),
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.identity_home / ".gitconfig"))
        for name in list(self.git_env):
            if (name.startswith("GIT_CONFIG_") and name not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM")) or name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_IMPLICIT_WORK_TREE", "GIT_CONFIG"):
                self.git_env.pop(name)
        self.file = self.root / "release.json"
        self.file.write_text(json.dumps(manifest()))

    def helper(self, *args, **kwargs):
        kwargs.setdefault("env", self.git_env)
        return call([HELPER, *args], **kwargs)

    def assertOK(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def bundle(self):
        output = self.root / "output"
        self.assertOK(call([ROOT / "scripts/release/package.sh", self.file, output, HELPER]))
        archive = output / manifest()["bundles"]["macos" if sys.platform == "darwin" else "linux_x86_64"]["filename"]
        extracted = self.root / "download"
        extracted.mkdir()
        self.assertOK(call(["tar", "-xzf", archive, "-C", extracted]))
        return extracted

    def test_payload_inventory_is_identical_under_private_umask(self):
        def inventory(path):
            with tarfile.open(path, 'r:gz') as archive:
                return {entry.name: (entry.type, entry.mode, archive.extractfile(entry).read() if entry.isfile() else None)
                        for entry in archive}
        archives = []
        for mask in (0o022, 0o077):
            output = self.root / ('mask-' + oct(mask))
            result = call([ROOT / 'scripts/release/package.sh', self.file, output, HELPER], umask=mask)
            self.assertOK(result)
            archive = output / manifest()['bundles']['macos' if sys.platform == 'darwin' else 'linux_x86_64']['filename']
            archives.append(archive)
            if mask == 0o077:
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        first, second = [inventory(path) for path in archives]
        self.assertEqual(first, second)
        self.assertGreater(len(first), 30)
        self.assertTrue(all(mode == 0o755 for kind, mode, _ in second.values() if kind == tarfile.DIRTYPE))
        self.assertTrue(all(mode in (0o644, 0o755) for kind, mode, _ in second.values() if kind == tarfile.REGTYPE))

    def environment(self):
        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        # Poison optional host runtimes; installed code must never execute these.
        for tool in ("git", "python3", "python", "node", "mise", "elixir", "erl", "swift", "swiftc", "shasum", "codex", "claude"):
            (tools / tool).write_text(f"#!/bin/sh\necho 'unexpected host dependency: {tool}' >&2\nexit 98\n")
            (tools / tool).chmod(0o755)
        docker = tools / "docker"
        docker.write_text("#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$TEST_DOCKER_CALLS\"\ncase \"$1\" in info) exit 0 ;; image) exit 1 ;; pull) exit 29 ;; volume) exit 1 ;; *) exit 97 ;; esac\n")
        docker.chmod(0o755)
        return dict(os.environ, HOME=str(self.root / "home"), PATH=f"{tools}:/usr/bin:/bin",
            SYMPHONY_HOME="", SYMPHONY_APP_HOME=str(self.root / "app"),
            SYMPHONY_CONFIG_HOME=str(self.root / "config"), SYMPHONY_BIN_DIR=str(self.root / "bin"),
            SYMPHONY_SKILLS_DIR=str(self.root / "skills"), SYMPHONY_RUNNER_NAMESPACE="package-fixture",
            SYMPHONY_CONTAINER_RUNTIME=str(docker), TEST_DOCKER_CALLS=str(self.root / "docker.calls"))

    def test_packaged_apparmor_plan_is_source_free_and_remote_safe(self):
        bundle = self.bundle()
        env = self.environment()
        for name in ('DOCKER_CONTEXT', 'DOCKER_HOST'):
            env.pop(name, None)
        docker = Path(env['SYMPHONY_CONTAINER_RUNTIME'])
        docker.write_text("""#!/bin/sh
case "$1" in
  context) echo 'ssh://user:synthetic-secret@remote' ;;
  info)
    case "$3" in
      '{{.ID}}') echo daemon-id ;;
      '{{.Name}}') echo remote-daemon ;;
      *) printf 'name=seccomp,profile=builtin\\n'; [ "${TEST_APPARMOR:-}" != yes ] || echo name=apparmor ;;
    esac ;;
  *) exit 97 ;;
esac
""")
        command = [bundle / 'scripts/symphony', 'runner', 'apparmor']
        plain = call(command, cwd=self.root, env=env)
        self.assertIn('no host changes required', self.assertOK(plain))
        env['TEST_APPARMOR'] = 'yes'
        remote = call(command, cwd=self.root, env=env)
        self.assertEqual(remote.returncode, 1, remote.stdout + remote.stderr)
        self.assertIn('[daemon-host]', remote.stderr)
        self.assertIn('remote-daemon (ID daemon-id)', remote.stdout)
        self.assertNotIn('synthetic-secret', remote.stdout + remote.stderr)
        self.assertIn('2eddb013e3503591aaac8e583e6dca093290287713c8aa0869b2d4458fa594aa', remote.stdout)
        (bundle / 'scripts/codex-apparmor.profile').write_text('corrupt')
        corrupt = call(command, cwd=self.root, env=env)
        self.assertEqual(corrupt.returncode, 1)
        self.assertIn('integrity check failed', corrupt.stderr)
        self.assertFalse((bundle / '.git').exists())

    def test_manifest_validation_and_typed_json(self):
        self.assertOK(self.helper("validate-manifest", self.file))
        self.assertEqual(self.assertOK(self.helper("host-architecture")), os.uname().machine)
        for change in [lambda m: m.update(version="1.2.3\n"), lambda m: m.update(schema_version=True), lambda m: m.update(schema_version=1.0),
                lambda m: m["runners"]["codex"]["arm64"].update(image="mutable:latest"),
                lambda m: m["runners"]["claude"]["x86_64"].pop("tool_versions"),
                lambda m: m["bundle"].update(filename="other.tar.gz"),
                lambda m: m["runners"].update(other={}),
                lambda m: m["supported_hosts"]["macos"].append("x86_64"),
                lambda m: m["supported_hosts"].update(linux_arm64=["arm64"]),
                lambda m: m["bundle"].update(architectures=["arm64", "x86_64"]),
                lambda m: m["bundles"]["linux_x86_64"].update(architectures=["arm64"])]:
            value = copy.deepcopy(manifest()); change(value); self.file.write_text(json.dumps(value))
            self.assertNotEqual(self.helper("validate-manifest", self.file).returncode, 0)
        self.assertNotEqual(self.helper("get").returncode, 0)
        self.assertEqual(self.helper("json", "idle", input='{"agent":{"draining":true},"counts":{"running":false}}').returncode, 1)
        self.assertEqual(self.helper("json", "idle", input='{"agent":{"draining":true},"counts":{"running":0}}').returncode, 0)
        for count in ("0.0", "false", "true", "1.5", '"0"'):
            self.assertEqual(self.helper("json", "idle", input='{"agent":{"draining":true},"counts":{"running":' + count + '}}').returncode, 1)
        self.assertEqual(self.helper("json", "admission", input='{"draining":false,"admission":{"ready":false,"reason":"quota_exhausted","retry_at":3}}').stdout.strip(), "quota_paused")

    def test_component_manifest_keeps_original_sources_and_strict_compatibility(self):
        value = manifest()
        value.update(schema_version=2, compatibility={'runner_protocol': 1})
        for provider, runners in value['runners'].items():
            for arch, runner in runners.items():
                runner.update(source_revision='a' * 40, input_identity='c' * 64, protocol=1, image_config='sha256:'+'9'*64,
                              dependencies={'resolution_policy': 'retain-original-qualified-resolution',
                                            'base_images': ['base@sha256:' + 'a' * 64],
                                            'os_packages': {'git': '1.0'}, 'build_options': {'TARGETARCH': 'arm64' if arch == 'arm64' else 'amd64'}})
                runner['tool_versions']['elixir'] = 'Elixir fixture / OTP fixture'
                runner['qualification'] = {'repository': 'Jayson-Stangel/symphony', 'run_id': 123, 'run_attempt': 1,
                    'controller_revision': 'b' * 40, 'job_name': f'runner-{provider}-{arch}', 'artifact_id': 456,
                    'artifact_name': f'runner-candidate-{provider}-{arch}', 'artifact_digest': 'sha256:' + 'd' * 64,
                    'archive_filename': f'{provider}-{arch}.docker.tar.gz', 'archive_sha256': 'e' * 64,
                    **{key: runner[key] for key in ('source_revision', 'input_identity', 'image')}}
        value['distribution'] = {provider: {arch: 'ghcr.io/immutable/'+provider+'@'+runner['image'].split('@')[1]
            for arch,runner in group.items()} for provider,group in value['runners'].items()}
        self.file.write_text(json.dumps(value))
        self.assertOK(self.helper('validate-manifest', self.file))
        # Display migration must agree between Python and the compiled helper.
        sys.path.insert(0, str(ROOT / 'scripts/release'))
        import artifacts
        for index in range(4):
            named = copy.deepcopy(value)
            for provider, group in named['runners'].items():
                for arch, runner in group.items():
                    runner['qualification']['job_name'] = artifacts.runner_job_names(provider, arch)[index]
            artifacts.validate(named, require_bundle=False)
            self.file.write_text(json.dumps(named))
            self.assertOK(self.helper('validate-manifest', self.file))
        changes = [lambda m: m['distribution']['codex'].update(arm64='ghcr.io/changed@sha256:'+'f'*64),
                   lambda m: m['distribution']['codex'].pop('arm64'),
                   lambda m: m['runners']['codex']['arm64'].pop('image_config'),
                   lambda m: m.update(schema_version=1),
                   lambda m: m['compatibility'].update(runner_protocol=True),
                   lambda m: m['compatibility'].update(runner_protocol=2),
                   lambda m: m['runners']['codex']['arm64'].update(protocol=1.0),
                   lambda m: m['runners']['codex']['arm64']['qualification'].update(run_id=True),
                   lambda m: m['runners']['codex']['arm64']['qualification'].update(repository='foreign/repo'),
                   lambda m: m['runners']['codex']['arm64']['qualification'].update(job_name='Build runner (claude, arm64)'),
                   lambda m: m['runners']['codex']['arm64']['qualification'].update(source_revision='0' * 40),
                   lambda m: m['runners']['codex']['arm64']['dependencies'].update(os_packages={})]
        for change in changes:
            invalid = copy.deepcopy(value)
            change(invalid)
            self.file.write_text(json.dumps(invalid))
            self.assertNotEqual(self.helper('validate-manifest', self.file).returncode, 0)

    def test_schema1_bridge_original_components_preserve_strict_native_proof(self):
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location('bridge_fixture', ROOT / 'scripts/release/tests/test_component_provenance.py')
        fixture = module_from_spec(spec); spec.loader.exec_module(fixture)
        value=manifest();originals={}
        for provider in ('codex','claude'):
            originals[provider]={}
            for arch in ('arm64','x86_64'):
                original=fixture.record(provider,arch)
                original['source_revision']=value['source_revision']
                original['qualification']['source_revision']=value['source_revision']
                originals[provider][arch]=original
                selected=copy.deepcopy(original)
                selected['image']='ghcr.io/distribution/'+provider+'@'+original['image'].split('@')[1]
                selected.pop('qualification')
                value['runners'][provider][arch]=selected
        value['original_components']=originals
        self.file.write_text(json.dumps(value));self.assertOK(self.helper('validate-manifest',self.file))
        for mutate in (lambda m:m['runners']['codex']['arm64'].update(image='ghcr.io/changed@sha256:'+'0'*64),
                       lambda m:m['original_components']['claude']['x86_64']['qualification'].update(run_id=True)):
            broken=copy.deepcopy(value);mutate(broken);self.file.write_text(json.dumps(broken))
            self.assertNotEqual(self.helper('validate-manifest',self.file).returncode,0)

    def test_recovered_component_records_keep_failed_job_and_separate_origins(self):
        sys.path.insert(0, str(ROOT / 'scripts/release'))
        import artifacts
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location('recovery_fixture', ROOT / 'scripts/release/tests/test_component_provenance.py')
        fixture = module_from_spec(spec); spec.loader.exec_module(fixture)
        value = manifest()
        value.update(schema_version=2, compatibility={'runner_protocol': 1})
        for provider in ('codex', 'claude'):
            for arch in ('arm64', 'x86_64'):
                record = fixture.record(provider, arch)
                value['runners'][provider][arch] = {key: field for key, field in record.items() if key not in ('provider','architecture')}
        proof = value['runners']['codex']['arm64']['qualification']
        proof.update(record_origin={'run_id':789,'run_attempt':1,'controller_revision':'c'*40,'job_name':'recover-codex-arm64'},
                     payload_origin={'run_id':123,'controller_revision':'a'*40},
                     native_step_recovery={'job_id':1234,'original_job_conclusion':'failure','build_step_number':5,'qualification_step_number':6,
                         'payload_step_number':7,'failure_step_number':8,'cleanup_step_number':10,
                         'failure_reason':'bare-upload-artifact-sha256','log_sha256':'e'*64})
        artifacts.validate(value, require_bundle=False)
        self.file.write_text(json.dumps(value)); self.assertOK(self.helper('validate-manifest',self.file))
        for name in artifacts.recovery_job_names('codex', 'arm64'):
            proof['record_origin']['job_name'] = name
            artifacts.validate(value, require_bundle=False)
            self.file.write_text(json.dumps(value)); self.assertOK(self.helper('validate-manifest',self.file))
        for field, invalid_value in (('job_id',True),('qualification_step_number',1.0),('original_job_conclusion','success'),('failure_reason','runtime-failure')):
            changed = copy.deepcopy(value)
            changed['runners']['codex']['arm64']['qualification']['native_step_recovery'][field] = invalid_value
            self.file.write_text(json.dumps(changed))
            self.assertNotEqual(self.helper('validate-manifest',self.file).returncode,0)

    def test_additive_platform_manifest_and_legacy_mac_contract(self):
        value = manifest()
        self.assertOK(self.helper("validate-manifest", self.file))
        legacy = copy.deepcopy(value); legacy.pop("bundles")
        self.file.write_text(json.dumps(legacy))
        self.assertOK(self.helper("validate-manifest", self.file))
        for mutate in (lambda v: v["bundles"].update(linux_arm64={}),
                       lambda v: v["bundles"]["linux_x86_64"].update(filename="bad.tar.gz"),
                       lambda v: v["bundles"]["linux_x86_64"].update(sha256=True),
                       lambda v: v.update(package_platform="linux_arm64")):
            changed = copy.deepcopy(value); mutate(changed)
            self.file.write_text(json.dumps(changed))
            self.assertNotEqual(self.helper("validate-manifest", self.file).returncode, 0)
        self.assertEqual(self.assertOK(self.helper("host-platform")),
            "macos" if sys.platform == "darwin" else "linux_x86_64")
        self.assertEqual(self.assertOK(self.helper("sha256", self.file)), hashlib.sha256(self.file.read_bytes()).hexdigest())

    def test_git_identity_and_linked_worktree_match_git(self):
        repo = self.root / "project with space"; repo.mkdir()
        self.assertOK(call(["git", "init", "-q", repo], env=self.git_env))
        remote = 'https://github.com/owner/example.git'
        self.assertOK(call(["git", "-C", repo, "remote", "add", "origin", remote], env=self.git_env))
        self.assertEqual(self.assertOK(self.helper("repository-root", repo)), str(repo))
        self.assertEqual(self.assertOK(self.helper("repository-remote", repo)), remote)
        self.assertEqual(self.assertOK(self.helper("hash-blob", remote)), self.assertOK(call(["git", "hash-object", "--stdin"], input=remote, env=self.git_env)))
        self.assertOK(call(["git", "-C", repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "Fixture"], env=self.git_env))
        worktree = self.root / "linked"
        self.assertOK(call(["git", "-C", repo, "worktree", "add", "-qb", "fixture", worktree], env=self.git_env))
        self.assertEqual(self.assertOK(self.helper("repository-remote", worktree)), remote)
        with (repo / ".git/config").open("a") as config:
            config.write('\n[remote "origin"]\nurl = "https://github.com/owner/encoded%20path.git" # comment\n')
        self.assertEqual(self.assertOK(self.helper("repository-remote", repo)), self.assertOK(call(["git", "-C", repo, "remote", "get-url", "origin"], env=self.git_env)))
        original = (repo / ".git/config").read_text()
        for extra in ('\n[extensions]\nworktreeConfig = true\n', '\n[url "ssh://git@example.invalid/"]\ninsteadOf = https://github.com/\n', '\n[url "ssh://git@example.invalid/"] insteadOf = https://github.com/\n'):
            (repo / ".git/config").write_text(original + extra)
            self.assertNotEqual(self.helper("repository-remote", repo).returncode, 0)
        (repo / ".git/config").write_text(original)
        # A symlinked Git directory still resolves the same explicit origin.
        moved = repo / "git-metadata"; (repo / ".git").rename(moved); (repo / ".git").symlink_to(moved)
        self.assertEqual(self.assertOK(self.helper("repository-remote", repo)), self.assertOK(call(["git", "-C", repo, "remote", "get-url", "origin"], env=self.git_env)))
        with (repo / ".git/config").open("a") as config:
            config.write('\n[include]\npath = ../other\n')
        self.assertNotEqual(self.helper("repository-remote", repo).returncode, 0)

    def test_external_git_identity_indirection_fails_without_exposing_values(self):
        repo = self.root / "external-config-project"; repo.mkdir()
        env = dict(self.git_env); env.pop("GIT_CONFIG_GLOBAL")
        self.assertOK(call(["git", "init", "-q", repo], env=env))
        raw = "https://mirror.example/owner/repository.git"
        self.assertOK(call(["git", "-C", repo, "remote", "add", "origin", raw], env=env))
        harmless = '[user]\n name = Fixture\n email = fixture@example.invalid\n[credential]\n helper = !echo PRIVATE-HELPER-MUST-NOT-RUN\n'
        global_file = self.identity_home / ".gitconfig"; global_file.write_text(harmless)
        self.assertEqual(self.assertOK(self.helper("repository-remote", repo, env=env)), raw)
        rewrite = '[url "https://github.com/PRIVATE-REWRITE/"]\n insteadOf = https://mirror.example/\n'
        xdg = Path(env["XDG_CONFIG_HOME"]) / "git/config"; xdg.parent.mkdir(parents=True)
        system = self.root / "system.gitconfig"
        custom = self.root / "custom.gitconfig"
        for path, selected in ((global_file, env), (xdg, env),
                (system, dict(env, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM=str(system), GIT_CONFIG_NOSYSTEM="0")),
                (custom, dict(env, GIT_CONFIG_GLOBAL=str(custom)))):
            with self.subTest(source=path.name):
                global_file.write_text(harmless); xdg.write_text("")
                path.write_text(rewrite)
                effective = self.assertOK(call(["git", "-C", repo, "remote", "get-url", "origin"], env=selected))
                self.assertNotEqual(effective, raw)
                result = self.helper("repository-remote", repo, env=selected)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("external Git configuration contains identity indirection", result.stderr)
                self.assertNotIn("PRIVATE-REWRITE", result.stderr + result.stdout)
        # Canonical /dev/null selections and ordinary empty files are supported.
        selected_null = dict(env, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_CONFIG_NOSYSTEM="0")
        self.assertEqual(self.assertOK(self.helper("repository-remote", repo, env=selected_null)), raw)
        custom.write_text(""); system.write_text("")
        selected_empty = dict(env, GIT_CONFIG_GLOBAL=str(custom), GIT_CONFIG_SYSTEM=str(system), GIT_CONFIG_NOSYSTEM="0")
        self.assertEqual(self.assertOK(self.helper("repository-remote", repo, env=selected_empty)), raw)
        # Explicit global selection suppresses both standard HOME/XDG files.
        global_file.write_text(rewrite); xdg.write_text(rewrite); custom.write_text(harmless)
        self.assertEqual(self.assertOK(self.helper("repository-remote", repo, env=dict(env, GIT_CONFIG_GLOBAL=str(custom)))), raw)
        included = self.root / "included.gitconfig"; included.write_text(rewrite)
        for value in ('[include]\n path = ' + str(included) + '\n',
                '[includeIf "gitdir:' + str(repo) + '/"]\n path = ' + str(included) + '\n',
                '[remote "origin"]\n url = https://example.invalid/PRIVATE-ORIGIN.git\n',
                '[remote.origin]\n url = https://example.invalid/PRIVATE-ORIGIN.git\n'):
            custom.write_text(value)
            selected = dict(env, GIT_CONFIG_GLOBAL=str(custom))
            result = self.helper("repository-remote", repo, env=selected)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("identity indirection", result.stderr)
            self.assertNotIn("PRIVATE-", result.stderr + result.stdout)
        # NOSYSTEM follows Git boolean semantics, including false spelled '0'.
        custom.write_text(harmless); system.write_text(rewrite)
        for value, disabled in (("0", False), ("false", False), ("", False), ("1", True), ("true", True), ("2", True)):
            selected = dict(env, GIT_CONFIG_GLOBAL=str(custom), GIT_CONFIG_SYSTEM=str(system), GIT_CONFIG_NOSYSTEM=value)
            effective = self.assertOK(call(["git", "-C", repo, "remote", "get-url", "origin"], env=selected))
            result = self.helper("repository-remote", repo, env=selected)
            self.assertEqual(effective == raw, disabled)
            self.assertEqual(result.returncode == 0, disabled)
            if disabled: self.assertEqual(result.stdout.strip(), raw)

    def test_git_discovery_and_config_injection_environment_is_rejected(self):
        repo = self.root / "environment-project"; repo.mkdir()
        self.assertOK(call(["git", "init", "-q", repo], env=self.git_env))
        self.assertOK(call(["git", "-C", repo, "remote", "add", "origin", "https://example.invalid/local.git"], env=self.git_env))
        overrides = {"GIT_DIR": str(repo / ".git"), "GIT_COMMON_DIR": str(repo / ".git"), "GIT_WORK_TREE": str(self.root),
            "GIT_CEILING_DIRECTORIES": str(self.root), "GIT_DISCOVERY_ACROSS_FILESYSTEM": "0",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "remote.origin.url", "GIT_CONFIG_VALUE_0": "PRIVATE-INJECTION",
            "GIT_CONFIG_PARAMETERS": "PRIVATE-INJECTION", "GIT_CONFIG": str(self.file)}
        for key, value in overrides.items():
            result = self.helper("repository-remote", repo, env=dict(self.git_env, **{key: value}))
            self.assertNotEqual(result.returncode, 0, key)
            self.assertIn(key, result.stderr)
            self.assertNotIn("PRIVATE-INJECTION", result.stderr + result.stdout)
        injected = dict(self.git_env, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="url.https://injected.invalid/.insteadOf", GIT_CONFIG_VALUE_0="https://example.invalid/")
        effective = self.assertOK(call(["git", "-C", repo, "remote", "get-url", "origin"], env=injected))
        self.assertEqual(effective, "https://injected.invalid/local.git")
        self.assertNotEqual(self.helper("repository-root", repo, env=injected).returncode, 0)

    def test_inventory_hashing_unsafe_paths_and_links(self):
        directory = self.root / "inventory"; directory.mkdir()
        content = directory / "payload"; content.write_bytes(b"actual helper inventory")
        digest = hashlib.sha256(content.read_bytes()).hexdigest()
        inventory = directory / "files.sha256"
        inventory.write_text(f"{digest}  ./payload\n")
        self.assertOK(self.helper("verify-files", directory))
        content.write_bytes(b"tampered")
        self.assertNotEqual(self.helper("verify-files", directory).returncode, 0)
        content.write_bytes(b"actual helper inventory")
        for name in ("../inventory/payload", str(content)):
            inventory.write_text(f"{digest}  {name}\n")
            self.assertNotEqual(self.helper("verify-files", directory).returncode, 0)
        link = directory / "link"; link.symlink_to(content)
        inventory.write_text(f"{digest}  ./link\n")
        self.assertNotEqual(self.helper("verify-files", directory).returncode, 0)
        outside = self.root / "outside"; outside.mkdir()
        (outside / "payload").write_bytes(content.read_bytes())
        (directory / "parent").symlink_to(outside, target_is_directory=True)
        inventory.write_text(f"{digest}  ./parent/payload\n")
        self.assertNotEqual(self.helper("verify-files", directory).returncode, 0)
        os.link(content, directory / "hardlink")
        inventory.write_text(f"{digest}  ./hardlink\n")
        self.assertNotEqual(self.helper("verify-files", directory).returncode, 0)
        target = directory / "activation"; target.write_text("unrelated")
        self.assertNotEqual(self.helper("ensure-link", "payload", target).returncode, 0)
        self.assertEqual(target.read_text(), "unrelated")
        staged = directory / "staged"; staged.symlink_to("payload")
        self.assertNotEqual(self.helper("replace-link", staged, target).returncode, 0)
        target.unlink()
        self.assertOK(self.helper("replace-link", staged, target))
        self.assertEqual(os.readlink(target), "payload")

    def test_lock_survives_helper_exit_and_releases_after_last_inheritor(self):
        lock = self.root / "inherited.lock"
        with lock.open("a") as owner:
            fd = owner.fileno()
            self.assertOK(self.helper("lock", lock, fd, "acquire", pass_fds=(fd,)))
            self.assertOK(self.helper("lock", lock, fd, "inherited", pass_fds=(fd,)))
            # A different open description must remain excluded after the helper exits.
            with lock.open("a") as competitor:
                self.assertNotEqual(self.helper("lock", lock, competitor.fileno(), "acquire",
                    pass_fds=(competitor.fileno(),)).returncode, 0)
            child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                stdin=subprocess.PIPE, pass_fds=(fd,))
            self.addCleanup(lambda: child.poll() is None and child.kill())
        # The original parent has closed its descriptor; only the child retains it.
        with lock.open("a") as competitor:
            self.assertNotEqual(self.helper("lock", lock, competitor.fileno(), "acquire",
                pass_fds=(competitor.fileno(),)).returncode, 0)
            child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0)
            self.assertOK(self.helper("lock", lock, competitor.fileno(), "acquire",
                pass_fds=(competitor.fileno(),)))

    def test_flock_matches_python_owner_and_exclusion(self):
        lock = self.root / "lock"
        script = '''exec 8>>"$1"; "$2" lock "$1" 8 acquire; "$2" lock "$1" 8 inherited; python3 - "$1" <<'PY'
import fcntl,sys
try:
 fcntl.flock(open(sys.argv[1],'a'), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
 sys.exit(0)
sys.exit(1)
PY
'''
        self.assertOK(call(["bash", "-e", "-c", script, "fixture", lock, HELPER]))
        self.assertNotEqual(call(["bash", "-c", 'exec 8>>"$1"; "$2" lock "$1" 8 inherited', "fixture", lock, HELPER]).returncode, 0)

    def legacy_macos_upgrade_fixture(self):
        # The test controller builds actual old/current source-bound managers.
        # Runner records are metadata fixtures: this test makes no lifecycle or
        # Linux-artifact claim and never pulls an image or authenticates.
        legacy_revision = "6794fc7a385a52429d80ca77cb6564408ed872c6"
        legacy = self.root / "legacy-source"
        self.assertOK(call(["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", ROOT, legacy]))
        self.assertOK(call(["git", "-C", legacy, "checkout", "--quiet", "--detach", legacy_revision]))
        old_helper = self.root / "old-helper"
        self.assertOK(call([legacy / "scripts/release/build-host-helper.sh", old_helper, "--native"]))
        old_manifest = manifest(version="1.2.2", revision=legacy_revision)
        old_manifest.pop("bundles")
        old_manifest.pop("supported_hosts")
        old_manifest["bundle"].pop("architectures")
        old_internal = self.root / "old-internal.json"; old_internal.write_text(json.dumps(old_manifest))
        old_output = self.root / "old-output"
        self.assertOK(call([legacy / "scripts/release/package.sh", old_internal, old_output, old_helper]))
        old_archive = old_output / old_manifest["bundle"]["filename"]
        old_download = self.root / "old-download"; old_download.mkdir()
        self.assertOK(call(["tar", "-xzf", old_archive, "-C", old_download]))
        current_download = self.bundle()
        current_archive = self.root / "output" / manifest()["bundle"]["filename"]
        current_manifest = manifest()
        current_manifest["bundle"]["sha256"] = hashlib.sha256(current_archive.read_bytes()).hexdigest()
        current_manifest["bundles"]["linux_x86_64"]["sha256"] = "e" * 64
        current_external = self.root / "current-external.json"; current_external.write_text(json.dumps(current_manifest))
        old_manifest["bundle"]["sha256"] = hashlib.sha256(old_archive.read_bytes()).hexdigest()
        old_external = self.root / "old-external.json"; old_external.write_text(json.dumps(old_manifest))
        env = self.environment()
        # The already-installed old updater legitimately uses the standard
        # macOS shasum utility until activation of the new helper-owned hash.
        (self.root / "tools/shasum").unlink()
        self.assertOK(call([old_download / "install.sh"], env=env))
        command = self.root / "bin/symphony"
        self.assertEqual(json.loads(self.assertOK(call([command, "--version"], env=env)))["source_revision"], legacy_revision)
        return legacy_revision, old_external, old_archive, current_external, current_archive, env, command

    @unittest.skipUnless(sys.platform == "darwin", "old macOS updater must execute on macOS")
    def test_actual_legacy_macos_updater_accepts_new_manifest_and_roundtrip(self):
        legacy_revision, old_external, old_archive, current_external, current_archive, env, command = self.legacy_macos_upgrade_fixture()
        self.assertOK(call([command, "update", "--candidate-manifest", current_external, "--candidate-bundle", current_archive], env=env))
        self.assertEqual(json.loads(self.assertOK(call([command, "--version"], env=env)))["source_revision"], manifest()["source_revision"])
        # New updater must also understand prior manifest shape, and retain
        # updater-controlled rollback into the actual old installation.
        self.assertOK(call([command, "update", "--rollback-release"], env=env))
        self.assertEqual(json.loads(self.assertOK(call([command, "--version"], env=env)))["source_revision"], legacy_revision)
        self.assertOK(call([command, "update", "--candidate-manifest", current_external, "--candidate-bundle", current_archive], env=env))
        self.assertOK(call([command, "update", "--candidate-manifest", old_external, "--candidate-bundle", old_archive], env=env))
        self.assertEqual(json.loads(self.assertOK(call([command, "--version"], env=env)))["source_revision"], legacy_revision)

    @unittest.skipUnless(sys.platform == "darwin", "legacy Mac policy replay requires native macOS")
    def test_actual_legacy_updater_intel_policy_replay_stages_but_cannot_activate_arm_bundle(self):
        # Real old installed updater/helper, actual new ARM bundle. uname replay
        # tests Intel selection semantics on ARM, not native Intel qualification.
        legacy_revision, _, _, current_external, current_archive, env, command = self.legacy_macos_upgrade_fixture()
        current = self.root / 'app/current'
        command_target, current_target = command.readlink(), current.readlink()
        canary = self.root / 'config/project-data'
        canary.parent.mkdir(exist_ok=True)
        canary.write_bytes(b'preserved project data\x00')
        uname = self.root / 'tools/uname'
        uname.write_text('#!/bin/sh\ncase "$1" in -s) echo Darwin ;; -m) echo x86_64 ;; *) exec /usr/bin/uname "$@" ;; esac\n')
        uname.chmod(0o755)
        failed = call([command, 'update', '--candidate-manifest', current_external, '--candidate-bundle', current_archive], env=env)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('packaged installs support only macOS ARM64 and Linux x86-64', failed.stderr)
        # Known compatibility limit: the unchanged legacy selector can copy the
        # ARM archive before the new launcher refuses; never claim no selection.
        staged = self.root / 'app/releases' / ('1.2.3-' + manifest()['source_revision'])
        self.assertTrue(staged.is_dir())
        self.assertEqual(json.loads((staged/'release.json').read_text())['bundle']['architectures'], ['arm64'])
        self.assertEqual(command.readlink(), command_target)
        self.assertEqual(current.readlink(), current_target)
        self.assertFalse((self.root/'app/previous').exists())
        self.assertFalse((self.root/'app/pending-transition').exists())
        self.assertEqual(canary.read_bytes(), b'preserved project data\x00')
        self.assertFalse((self.root/'docker.calls').exists())
        uname.unlink()
        self.assertEqual(json.loads(self.assertOK(call([command, '--version'], env=env)))['source_revision'], legacy_revision)

    @unittest.skipUnless(sys.platform == "darwin", "Mach-O slice validation requires native macOS")
    def test_supplied_universal_helper_refused_before_packaging(self):
        # Exercise the architecture refusal without creating any Intel payload.
        # The fake inspection result represents a supplied universal helper;
        # the sentinel proves refusal happens before helper execution.
        tools = self.root/'inspection-tools'; tools.mkdir()
        inspected = self.root/'inspection.calls'
        lipo = tools/'lipo'
        lipo.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_INSPECTION_CALLS"\n'
                        '[ "$1" = -archs ] || exit 99\nprintf "arm64 x86_64\\n"\n')
        lipo.chmod(0o755)
        sentinel = self.root/'helper-executed'
        helper = self.root/'helper-sentinel'
        helper.write_text('#!/bin/sh\ntouch "$TEST_HELPER_EXECUTED"\nexit 99\n')
        helper.chmod(0o755)
        env = dict(os.environ, PATH=f'{tools}:' + os.environ['PATH'],
                   TEST_INSPECTION_CALLS=str(inspected), TEST_HELPER_EXECUTED=str(sentinel))
        output = self.root/'universal-output'
        refused = call([ROOT/'scripts/release/package.sh', self.file, output, helper], env=env)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('macOS helper must contain only arm64', refused.stderr)
        self.assertEqual(len(inspected.read_text().splitlines()), 1)
        self.assertTrue(inspected.read_text().startswith('-archs '))
        self.assertFalse(sentinel.exists())
        self.assertFalse(list(output.glob('*.tar.gz')))
        self.assertFalse((self.root/'app').exists())

    @unittest.skipUnless(sys.platform == "darwin", "signature integrity guard requires macOS packaging")
    def test_invalid_helper_signature_refused_before_execution(self):
        tools = self.root / "signature-tools"; tools.mkdir()
        for name, body in (("lipo", "printf 'arm64\\n'"), ("codesign", "exit 1")):
            tool = tools / name
            tool.write_text("#!/bin/sh\n" + body + "\n"); tool.chmod(0o755)
        sentinel = self.root / "helper-executed"
        helper = self.root / "helper-sentinel"
        helper.write_text('#!/bin/sh\ntouch "$TEST_HELPER_EXECUTED"\nexit 99\n'); helper.chmod(0o755)
        env = dict(os.environ, PATH=f"{tools}:" + os.environ["PATH"], TEST_HELPER_EXECUTED=str(sentinel))
        output = self.root / "invalid-signature-output"
        refused = call([ROOT / "scripts/release/package.sh", self.file, output, helper], env=env)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("macOS helper signature integrity check failed", refused.stderr)
        self.assertFalse(sentinel.exists())
        self.assertFalse(list(output.glob("*.tar.gz")))

    def test_unsupported_installer_refuses_before_helper_or_activation(self):
        bundle = self.bundle()
        env = self.environment()
        canary = self.root/'project-canary'; canary.write_bytes(b'keep data\x00')
        uname = self.root/'tools/uname'
        for system, architecture in [('Darwin', 'x86_64'), ('Linux', 'aarch64')]:
            with self.subTest(system=system, architecture=architecture):
                uname.write_text(f'#!/bin/sh\ncase "$1" in -s) echo {system} ;; -m) echo {architecture} ;; esac\n')
                uname.chmod(0o755)
                refused = call([bundle/'install.sh'], env=env)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn('packaged installs support macOS ARM64 and Linux x86-64', refused.stderr)
                self.assertFalse((self.root/'app').exists())
                self.assertFalse((self.root/'bin/symphony').exists())
                self.assertEqual(canary.read_bytes(), b'keep data\x00')

    def test_source_free_init_provider_choice_and_retained_transition_guidance(self):
        bundle = self.bundle(); env = self.environment()
        self.assertOK(call([bundle / "install.sh"], env=env))
        # The installer may inspect Docker context for activation diagnostics.
        # Measure init's inspect-only contract from the end of installation.
        (self.root / "docker.calls").write_text("")
        command = self.root / "bin/symphony"
        repo = self.root / "provider-project"; repo.mkdir(); (repo / ".git").mkdir()
        (repo / ".git/config").write_text('[remote "origin"]\nurl = https://github.com/fixture/provider.git\n')
        for provider in ("codex", "claude"):
            output = self.assertOK(call([command, "init", "--linear-project", "fixture", "--agent", provider], cwd=repo, env=env))
            self.assertIn("auth " + provider + " login", output)
            self.assertIn("run --agent " + provider, output)
        credentials = next((self.root / "config/projects").glob("*.env"))
        workflow = repo / "WORKFLOW.md"
        before = (credentials.read_bytes(), workflow.read_bytes())
        directory = self.root / "config/runners/environments"; directory.mkdir(parents=True, mode=0o700)
        selection = directory / (credentials.stem + ".json")
        docker = self.root / "tools/docker"
        docker.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_DOCKER_CALLS"\n'
            'case "$*" in *State.Status*) echo running ;; *com.symphony.agent*) echo "$ACTUAL" ;; *) exit 97 ;; esac\n')
        for old, new in (("claude", "codex"), ("codex", "claude")):
            selection.write_text(json.dumps(dict(schema_version=1, provider=old,
                image_id="sha256:" + "a" * 64, reference="team:retained",
                runtime_revision="b" * 40, platform="linux/amd64", protocol=1)))
            selection.chmod(0o600)
            retained = selection.read_bytes()
            output = self.assertOK(call([command, "init", "--linear-project", "fixture", "--agent", new],
                cwd=repo, env=dict(env, ACTUAL=old)))
            self.assertIn("Stored desired provider: " + old, output)
            self.assertIn("Actual runner provider: " + old, output)
            self.assertIn("environment select --agent " + new + " IMAGE", output)
            self.assertIn("symphony update --agent " + new, output)
            self.assertLess(output.index("auth " + new + " check"), output.index("update --pause-runner"))
            self.assertLess(output.index("update --pause-runner"), output.index("auth " + new + " login"))
            self.assertEqual(selection.read_bytes(), retained)
            self.assertEqual((credentials.read_bytes(), workflow.read_bytes()), before)
            refused = call([command, "init", "--linear-project", "fixture"], cwd=repo, env=dict(env, ACTUAL=old))
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("unavailable or incompatible", refused.stdout)
            self.assertNotIn("auth " + old + " login", refused.stdout)
        operations = (self.root / "docker.calls").read_text().splitlines()
        self.assertTrue(all(line.startswith("inspect ") or line.startswith("image inspect ") for line in operations))

    def test_install_twice_source_free_init_doctor_and_failed_pull(self):
        bundle = self.bundle(); env = self.environment()
        self.assertOK(call([bundle / "install.sh"], env=env))
        command = self.root / "bin/symphony"
        self.assertOK(call([bundle / "install.sh"], env=env))
        shutil.rmtree(bundle)
        self.assertIn('"installation":"packaged"', self.assertOK(call([command, "--version"], env=env)))
        self.assertIn('"ready":true', self.assertOK(call([command, "doctor", "--json"], env=env)))
        repo = self.root / "project"; repo.mkdir(); (repo / ".git").mkdir()
        (repo / ".git/config").write_text('[remote "origin"]\nurl = https://github.com/fixture/project.git\n')
        self.assertIn("Project: initialized", self.assertOK(call([command, "init", "--linear-project", "disposable-fixture"], cwd=repo, env=env)))
        original = (repo / "WORKFLOW.md").read_text()
        self.assertOK(call([command, "init", "--linear-project", "preserve"], cwd=repo, env=env))
        self.assertEqual((repo / "WORKFLOW.md").read_text(), original)
        failed = call([command, "auth", "codex", "login"], cwd=repo, env=env)
        self.assertNotEqual(failed.returncode, 0); self.assertIn("cannot pull released codex image", failed.stderr)
        self.assertNotIn("build", (self.root / "docker.calls").read_text())
        self.assertOK(call([command, "--version"], env=env))

    if sys.platform == 'darwin':
        def test_unsigned_native_dmg_mount_inventory_install_retry_and_eject(self):
            """Real native construction/installation; no signing or Apple trust claim."""
            import plistlib
            bundle = self.bundle()
            dmg = self.root / 'unsigned.dmg'
            self.assertIn('UNSIGNED DMG', self.assertOK(call([
                sys.executable, ROOT / 'scripts/release/macos_distribution.py',
                'unsigned-dmg', bundle, dmg])))
            self.assertOK(call(['hdiutil', 'verify', dmg]))
            mounted = plistlib.loads(self.assertOK(call([
                'hdiutil', 'attach', '-readonly', '-nobrowse', '-plist', dmg])).encode())
            volumes = [entry for entry in mounted['system-entities'] if 'mount-point' in entry]
            self.assertEqual(len(volumes), 1)
            mount = Path(volumes[0]['mount-point'])
            try:
                self.assertOK(self.helper('verify-files', mount / 'Symphony'))
                env = self.environment()
                for attempt in range(2):
                    self.assertOK(call(['/bin/bash', mount / 'Symphony/install.sh'], env=env))
                self.assertIn('unsigned candidate', (mount / 'Symphony/distribution-status.txt').read_text())
            finally:
                self.assertOK(call(['hdiutil', 'detach', mount]))
            command = self.root / 'bin/symphony'
            self.assertIn('"installation":"packaged"', self.assertOK(call([command, '--version'], env=env)))
            self.assertIn('"ready":true', self.assertOK(call([command, 'doctor', '--json'], env=env)))

    def test_conflict_and_corrupt_install_do_not_activate(self):
        bundle = self.bundle(); env = self.environment()
        command = self.root / "bin/symphony"; command.parent.mkdir(); command.write_text("owned")
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0); self.assertEqual(command.read_text(), "owned")
        self.assertFalse((self.root / "app").exists())
        command.unlink()
        self.assertOK(call([bundle / "install.sh"], env=env))
        canary = self.root / "payload-ran"
        (bundle / "scripts/symphony-package.sh").write_text(f"touch {canary}\n")
        # Installer verifies shell payload before sourcing the manager library.
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "app/current").exists())
        self.assertOK(call([command, "--version"], env=env))
        self.assertFalse(canary.exists())
        self.assertIn("bundle checksum mismatch", result.stderr)

    def test_release_storage_conflicts_do_not_write_through_links(self):
        bundle = self.bundle(); env = self.environment()
        app = self.root / "app"; app.mkdir(mode=0o700)
        foreign = self.root / "foreign"; foreign.mkdir()
        canary = foreign / "keep"; canary.write_text("untouched")
        releases = app / "releases"
        for target in (foreign, self.root / "missing-target"):
            with self.subTest(target=target.name):
                releases.symlink_to(target)
                result = call([bundle / "install.sh"], env=env)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("unsafe or foreign metadata", result.stderr)
                self.assertEqual(list(foreign.iterdir()), [canary])
                self.assertEqual(canary.read_text(), "untouched")
                self.assertFalse((self.root / "missing-target").exists())
                self.assertFalse((app / "current").exists())
                releases.unlink()
        releases.write_text("owned file")
        result = call([bundle / "install.sh", "--stage-only"], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(releases.read_text(), "owned file")
        releases.unlink(); releases.mkdir(mode=0o755)
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list(releases.iterdir()))
        self.assertFalse((app / "current").exists())
        # Canonical parent aliases may be intentional; only managed storage
        # components themselves must be real owned directories.
        releases.chmod(0o700)
        version_path = releases / (manifest()["version"] + "-" + manifest()["source_revision"])
        version_path.symlink_to(self.root / "missing-release")
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(version_path.is_symlink())
        self.assertFalse((self.root / "missing-release").exists())
        version_path.unlink()
        alias = self.root / "parent-alias"; alias.symlink_to(self.root, target_is_directory=True)
        self.assertOK(call([bundle / "install.sh", "--app-dir", alias / "app"], env=env))
        self.assertIn('"ready":true', self.assertOK(call([self.root / "bin/symphony", "doctor", "--json"], env=env)))

    def test_links_created_during_copy_are_preserved_before_activation(self):
        bundle = self.bundle(); env = self.environment()
        # Retain a separate already-installed manager as a usability canary.
        self.assertOK(call([bundle / "install.sh"], env=env))
        existing = self.root / "bin/symphony"
        before = self.assertOK(call([existing, "--version"], env=env))
        cp = self.root / "tools/cp"
        cp.write_text('#!/bin/bash\n/bin/cp "$@" || exit $?\n/bin/mkdir -p "$(dirname "$TEST_CONFLICT_TARGET")"\n/bin/ln -s /a/foreign/command "$TEST_CONFLICT_TARGET"\n')
        cp.chmod(0o755)
        for index, kind in enumerate(("command", "skill")):
            with self.subTest(kind=kind):
                base = self.root / f"competing-{index}"
                candidate_env = dict(env, SYMPHONY_APP_HOME=str(base / "app"),
                    SYMPHONY_BIN_DIR=str(base / "bin"), SYMPHONY_SKILLS_DIR=str(base / "skills"))
                target = base / ("bin/symphony" if kind == "command" else "skills/symphony-commit")
                candidate_env["TEST_CONFLICT_TARGET"] = str(target)
                result = call([bundle / "install.sh"], env=candidate_env)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("refusing to replace", result.stderr)
                self.assertEqual(os.readlink(target), "/a/foreign/command")
                self.assertFalse((base / "app/current").is_symlink())
                self.assertEqual(self.assertOK(call([existing, "--version"], env=env)), before)
        # Final writes themselves must verify exact targets, not accept any link.
        wrong = self.root / "wrong-link"; wrong.symlink_to("/a/foreign/command")
        self.assertNotEqual(self.helper("ensure-link", "/expected/command", wrong).returncode, 0)
        self.assertEqual(os.readlink(wrong), "/a/foreign/command")

    def test_failed_stage_directory_creation_never_attempts_copy(self):
        bundle = self.bundle(); env = self.environment()
        tools = self.root / "tools"
        (tools / "mktemp").write_text("#!/bin/sh\nexit 73\n"); (tools / "mktemp").chmod(0o755)
        copied = self.root / "copy-called"
        (tools / "cp").write_text(f"#!/bin/sh\ntouch {copied}\nexit 97\n"); (tools / "cp").chmod(0o755)
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(copied.exists())
        self.assertFalse((self.root / "app/current").is_symlink())
        self.assertFalse((self.root / "bin/symphony").is_symlink())
        self.assertFalse(list((self.root / "app/releases").iterdir()))

    def test_failed_stage_rename_never_activates_and_preserves_existing_install(self):
        bundle = self.bundle(); env = self.environment()
        mv = self.root / "tools/mv"; mv.write_text("#!/bin/sh\nexit 73\n"); mv.chmod(0o755)
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Installed Symphony", result.stdout)
        self.assertFalse((self.root / "app/current").is_symlink())
        self.assertFalse((self.root / "bin/symphony").is_symlink())
        self.assertFalse(list((self.root / "app/releases").iterdir()))
        mv.unlink()
        self.assertOK(call([bundle / "install.sh"], env=env))
        command = self.root / "bin/symphony"
        before = self.assertOK(call([command, "--version"], env=env))
        # A second fresh staging destination isolates the rename failure while
        # retaining the existing active manager and command.
        script = 'source "$1" --help >/dev/null; package_app_home="$2"; package_stage_bundle "$3"'
        other = self.root / "other-app"; other.mkdir(mode=0o700)
        mv.write_text("#!/bin/sh\nexit 73\n"); mv.chmod(0o755)
        result = call(["bash", "-e", "-c", script, "fixture", command, other, bundle], env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list((other / "releases").iterdir()))
        self.assertEqual(self.assertOK(call([command, "--version"], env=env)), before)

    def test_host_layouts_missing_engine_and_source_bound_archive(self):
        mismatch = manifest(revision="c" * 40)
        self.file.write_text(json.dumps(mismatch))
        result = call([ROOT / "scripts/release/package.sh", self.file, self.root / "mismatch-output", HELPER])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manager input selection refused: manager manifest differs from actual checkout", result.stderr)
        self.assertFalse(list((self.root / "mismatch-output").glob("*.tar.gz")))
        self.assertFalse((self.root / "app/current").exists())
        self.file.write_text(json.dumps(manifest()))
        bundle = self.bundle(); env = self.environment()
        self.assertOK(call([bundle / "install.sh"], env=env))
        command = self.root / "bin/symphony"
        uname = self.root / "tools/uname"
        for architecture, platform in ((("arm64", "linux/arm64"),) if sys.platform == "darwin" else (("x86_64", "linux/amd64"),)):
            uname.write_text(f'#!/bin/sh\ncase "$1" in -s) echo {"Darwin" if sys.platform == "darwin" else "Linux"} ;; *) echo {architecture} ;; esac\n'); uname.chmod(0o755)
            output = self.assertOK(call([command, "doctor", "--json"], env=env))
            self.assertEqual(json.loads(output)["architecture"], architecture)
            result = call([command, "auth", "codex", "login"], env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"pull --platform {platform}", (self.root / "docker.calls").read_text())
        result = call([command, "doctor", "--json"], env=dict(env, SYMPHONY_CONTAINER_RUNTIME="/unavailable/engine"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["ready"])

    def test_unsupported_host_and_wrong_os_refuse_before_native_helper(self):
        bundle = self.bundle(); env = self.environment()
        canary = self.root / "wrong-platform-helper-executed"
        (bundle / "libexec/symphony-host").write_text(f"#!/bin/sh\ntouch '{canary}'\nexit 99\n")
        uname = self.root / "tools/uname"
        other_os = "Linux" if sys.platform == "darwin" else "Darwin"
        other_architecture = "x86_64" if other_os == "Linux" else "arm64"
        policy = "macOS ARM64 and Linux x86-64"
        for system, architecture, reason in (("Linux", "aarch64", policy),
                                            ("Darwin", "x86_64", policy),
                                            ("Windows_NT", "x86_64", policy),
                                            (other_os, other_architecture, "bundle platform")):
            uname.write_text(f'#!/bin/sh\ncase "$1" in -s) echo {system} ;; *) echo {architecture} ;; esac\n')
            uname.chmod(0o755)
            for command in ([bundle / "install.sh"], [bundle / "scripts/symphony", "--version"]):
                refused = call(command, env=env)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn(reason, refused.stderr)
                self.assertFalse(canary.exists(), "unsupported platform executed native helper")

    def test_mutable_or_foreign_image_overrides_are_rejected(self):
        bundle = self.bundle(); env = self.environment()
        self.assertOK(call([bundle / "install.sh"], env=env))
        command = self.root / "bin/symphony"
        # Installation may inspect the CLI's current context. A rejected image
        # override must still fail before any additional Docker operation.
        calls_file = self.root / "docker.calls"
        docker_calls = calls_file.read_text() if calls_file.exists() else ""
        for image in ("mutable:latest", "sha256:" + "c" * 64, "ghcr.io/foreign/runner@sha256:" + "d" * 64):
            result = call([command, "auth", "codex", "login"], env=dict(env, SYMPHONY_RUNNER_IMAGE=image))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("remove SYMPHONY_RUNNER_IMAGE", result.stderr)
        self.assertEqual(calls_file.read_text() if calls_file.exists() else "", docker_calls)

    def test_custom_app_dir_does_not_require_environment_override(self):
        bundle = self.bundle(); env = self.environment()
        env.pop("SYMPHONY_APP_HOME")
        app = self.root / "custom-app"
        self.assertOK(call([bundle / "install.sh", "--app-dir", app], env=env))
        self.assertOK(call([bundle / "install.sh", "--app-dir", app], env=env))
        shutil.rmtree(bundle)
        command = self.root / "bin/symphony"
        self.assertIn('"ready":true', self.assertOK(call([command, "doctor", "--json"], env=env)))
        self.assertOK(call([command, "--version"], env=env))

    def test_stage_only_does_not_activate_and_arch_failure_is_clear(self):
        bundle = self.bundle(); env = self.environment()
        path = self.assertOK(call([bundle / "install.sh", "--stage-only"], env=env))
        self.assertTrue(Path(path).is_dir()); self.assertFalse((self.root / "app/current").exists())
        uname = self.root / "tools/uname"; uname.write_text('#!/bin/sh\ncase "$1" in -s) echo Darwin ;; *) echo unsupported ;; esac\n'); uname.chmod(0o755)
        result = call([bundle / "install.sh"], env=env)
        self.assertNotEqual(result.returncode, 0); self.assertIn("unsupported host architecture", result.stderr)


if __name__ == "__main__":
    unittest.main()
