"""Portable shell-updater fixtures; these do not qualify macOS or containers."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest


REPOSITORY = Path(__file__).resolve().parents[3]
UPDATER = REPOSITORY / "scripts/symphony-package-update.sh"
HELPER = r'''#!/usr/bin/env python3
import fcntl, hashlib, json, os, pathlib, stat, sys
op, *args = sys.argv[1:]
if op == 'get':
    value = json.loads(pathlib.Path(args[0]).read_text())
    for key in args[1].split('.'): value = value[key]
    print(json.dumps(value) if isinstance(value, (bool, dict, list)) else value)
elif op == 'validate-manifest':
    value = json.loads(pathlib.Path(args[0]).read_text())
    assert value['schema_version'] == 1 and len(value['source_revision']) == 40
elif op == 'verify-files':
    root = pathlib.Path(args[0])
    for line in (root/'files.sha256').read_text().splitlines():
        digest, path = line.split('  ', 1)
        assert hashlib.sha256((root/path).read_bytes()).hexdigest() == digest, path
elif op == 'lock':
    path, fd, mode = args; fd=int(fd)
    st=os.fstat(fd); current=os.lstat(path)
    assert stat.S_ISREG(current.st_mode) and (st.st_dev,st.st_ino)==(current.st_dev,current.st_ino)
    if mode == 'inherited':
        probe=os.open(path,os.O_WRONLY)
        try:
            try: fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: pass
            else: sys.exit('fixture: inherited ownership missing')
        finally: os.close(probe)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
elif op == 'owned-directory':
    st=os.lstat(args[0]); assert stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid() and stat.S_IMODE(st.st_mode)==0o700
elif op == 'replace-link':
    if os.path.lexists(args[1]): assert stat.S_ISLNK(os.lstat(args[1]).st_mode)
    os.replace(args[0],args[1])
else: raise SystemExit('unknown fixture operation: '+op)
'''

LAUNCHER = r'''#!/bin/bash
set -euo pipefail
die() { printf 'symphony: %s\n' "$*" >&2; exit 1; }
script=${BASH_SOURCE[0]}
while [ -L "$script" ]; do
  link=$(readlink "$script")
  case "$link" in /*) script=$link ;; *) script="$(dirname "$script")/$link" ;; esac
done
symphony_home=${SYMPHONY_HOME:-$(cd "$(dirname "$script")/.." && pwd -P)}
package_app_home=${SYMPHONY_APP_HOME:?}
config_home=${SYMPHONY_CONFIG_HOME:?}
runner_home=${SYMPHONY_RUNNER_HOME:-$config_home/runners}
runner_namespace=${SYMPHONY_RUNNER_NAMESPACE:-}
bin_dir=$package_app_home/bin
skills_dir=$package_app_home/skills
package_helper() { "$symphony_home/libexec/symphony-host" "$@"; }
source_revision() { package_helper get "$symphony_home/release.json" source_revision; }
resolve_runner_home() { mkdir -p "$runner_home"; }
validate_agent() { case "$1" in codex|claude) ;; *) die 'bad fixture agent' ;; esac; }
repo_root() { pwd; }
project_id() { basename "$1"; }
# Installer boundary is a fixture here; native installer has its own tests.
package_stage_bundle() {
  local from=$1 version revision destination
  package_helper verify-files "$from"
  version=$(package_helper get "$from/release.json" version)
  revision=$(package_helper get "$from/release.json" source_revision)
  destination="$package_app_home/releases/$version-$revision"
  if [ ! -d "$destination" ]; then cp -R "$from" "$destination"; fi
  package_helper verify-files "$destination"
  printf '%s\n' "$destination"
}
. "$symphony_home/scripts/symphony-package-update.sh"
runner_transition() {
  local root=$1 agent=$2 journal="$runner_home/$(project_id "$1").transition" temporary
  if [ ! -d "$journal" ]; then
    temporary=$(mktemp -d "$runner_home/.transition.XXXXXX")
    printf '%s\n' "$agent" >"$temporary/target"
    package_write_transition "$temporary"
    if [ -f "$package_app_home/fail-publication" ]; then rm "$package_app_home/fail-publication"; exit 90; fi
    mv "$temporary" "$journal"
  fi
  printf '%s\n' "$(source_revision)" >"$runner_home/$(project_id "$root").image"
  if [ -f "$package_app_home/fail-transition" ]; then rm "$package_app_home/fail-transition"; exit 91; fi
  if [ -f "$package_app_home/hold-transition" ]; then
    touch "$package_app_home/transition-observed"
    while [ -f "$package_app_home/hold-transition" ]; do sleep .05; done
  fi
  package_finish_transition "$journal"
  if [ -f "$package_app_home/fail-after-activation" ]; then rm "$package_app_home/fail-after-activation"; exit 92; fi
  mv "$journal" "$journal.complete"
  if [ -f "$package_app_home/fail-journal-cleanup" ]; then rm "$package_app_home/fail-journal-cleanup"; exit 93; fi
  package_remove_transition_fields "$journal.complete"
  rm "$journal.complete/target"; rmdir "$journal.complete"
}
runner_rollback() {
  local journal="$runner_home/$(project_id "$1").transition"
  [ -d "$journal" ] || die 'no retained transition'
  package_finish_transition "$journal"
  package_remove_transition_fields "$journal"
  rm "$journal/target"; rmdir "$journal"
}
case "${1:-}" in
  update) shift; package_update "$@" ;;
  _complete-package-update) shift; complete_package_update "$@" ;;
  version) source_revision ;;
esac
'''


def manifest(version, revision):
    return {"schema_version": 1, "version": version, "source_revision": revision,
            "channel": "candidate", "host_architectures": ["arm64", "x86_64"],
            "bundle": {"filename": f"symphony-{version}-macos.tar.gz"},
            "runners": {provider: {arch: {"image": f"localhost:5000/{provider}@sha256:" + revision[:1] * 64,
                "source_revision": revision, "platform": platform,
                "tool_versions": {tool: "fixture" for tool in (provider, "node", "git", "playwright")}}
                for arch, platform in (("arm64", "linux/arm64"), ("x86_64", "linux/amd64"))}
                for provider in ("codex", "claude")}}


class PackageUpdateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="symphony-package-update-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.app = self.root / "app"
        (self.app / "releases").mkdir(parents=True)
        self.app.chmod(0o700)
        self.config = self.root / "config"
        self.config.mkdir()
        self.projects = [self.root / name for name in ("project-one", "project-two")]
        for project in self.projects: project.mkdir()
        self.env = dict(os.environ, SYMPHONY_APP_HOME=str(self.app), SYMPHONY_CONFIG_HOME=str(self.config),
                        SYMPHONY_RUNNER_NAMESPACE="package-fixture")
        self.env.pop("SYMPHONY_HOME", None)
        self.env.pop("SYMPHONY_RUNNER_HOME", None)
        self.first = self.bundle("0.0.1", "a" * 40)
        self.second = self.bundle("0.0.2", "b" * 40)
        self.initial = self.app / "releases" / self.first[2].name
        shutil.copytree(self.first[2], self.initial)
        (self.app / "current").symlink_to(self.initial)
        self.command = self.app / "current/scripts/symphony"

    def bundle(self, version, revision, channel="candidate"):
        directory = self.root / f"{version}-{revision}"
        (directory / "scripts").mkdir(parents=True)
        (directory / "libexec").mkdir()
        (directory / "scripts/symphony").write_text(LAUNCHER)
        (directory / "scripts/symphony").chmod(0o755)
        shutil.copyfile(UPDATER, directory / "scripts/symphony-package-update.sh")
        (directory / "libexec/symphony-host").write_text(HELPER)
        (directory / "libexec/symphony-host").chmod(0o755)
        value = manifest(version, revision)
        value["channel"] = channel
        (directory / "release.json").write_text(json.dumps(value))
        (directory / "files.sha256").write_text("".join(
            hashlib.sha256(path.read_bytes()).hexdigest() + "  " + str(path.relative_to(directory)) + "\n"
            for path in sorted(directory.rglob("*")) if path.is_file()))
        archive = self.root / value["bundle"]["filename"]
        with tarfile.open(archive, "w:gz") as tar:
            for path in sorted(directory.iterdir()): tar.add(path, arcname=path.name)
        value["bundle"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        external = self.root / f"{version}-release.json"
        external.write_text(json.dumps(value))
        return external, archive, directory

    def run_update(self, *args, project=0, env=None):
        return subprocess.run([str(self.command), "update", *args], cwd=self.projects[project],
                              env=self.env | (env or {}), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def candidate(self, *extra, **kwargs):
        return self.run_update("--candidate-manifest", str(self.second[0]),
                               "--candidate-bundle", str(self.second[1]), *extra, **kwargs)

    def current(self):
        return (self.app / "current").resolve()

    def test_manager_update_and_completed_release_rollback(self):
        result = self.candidate()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.current().name, self.second[2].name)
        result = self.run_update("--rollback-release", "--agent", "codex")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.current(), self.initial)
        self.assertEqual((self.config / "runners/project-one.image").read_text().strip(), "a" * 40)

    def test_failed_verification_keeps_current_and_existing_project_canaries(self):
        evidence = self.projects[1] / "untracked-evidence"
        evidence.write_text("keep me")
        self.second[1].write_bytes(b"truncated download")
        result = self.candidate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stdout)
        self.assertEqual(self.current(), self.initial)
        self.assertEqual(evidence.read_text(), "keep me")

    def test_pending_transition_pins_retry_and_excludes_other_configuration(self):
        (self.app / "fail-transition").touch()
        result = self.candidate("--agent", "codex")
        self.assertEqual(result.returncode, 91, result.stdout)
        self.assertEqual(self.current(), self.initial)
        pointer = self.app / "pending-transition"
        self.assertTrue(pointer.is_symlink())
        other = self.root / "alternate-config"
        other.mkdir()
        result = self.run_update("--rollback-release", project=1, env={"SYMPHONY_CONFIG_HOME": str(other)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unfinished packaged update", result.stdout)
        # No network or original download is required once the journal pins a release.
        self.second[0].unlink()
        self.second[1].unlink()
        result = self.run_update("--agent", "codex")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.current().name, self.second[2].name)
        self.assertFalse(pointer.exists())

    def test_rollback_restores_manager_after_interrupted_activation(self):
        (self.app / "fail-after-activation").touch()
        result = self.candidate("--agent", "codex")
        self.assertEqual(result.returncode, 92, result.stdout)
        self.assertEqual(self.current().name, self.second[2].name)
        result = self.run_update("--rollback-runner")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.current(), self.initial)

    def test_locator_recovers_publication_and_completed_cleanup_interruptions(self):
        for fault, code in (("fail-publication", 90), ("fail-journal-cleanup", 93)):
            with self.subTest(fault=fault):
                (self.app / fault).touch()
                result = self.candidate("--agent", "codex")
                self.assertEqual(result.returncode, code, result.stdout)
                result = self.run_update("--agent", "codex") if code == 93 else self.candidate("--agent", "codex")
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertFalse((self.app / "pending-transition").exists())

    def test_archive_links_and_traversal_are_rejected_before_extraction(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("evil", tarfile.SYMTYPE)):
            with self.subTest(name=name):
                archive = self.root / "unsafe.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    item = tarfile.TarInfo(name)
                    item.type = kind
                    if kind == tarfile.SYMTYPE: item.linkname = "/tmp"
                    tar.addfile(item, io.BytesIO())
                script = 'set -e; die() { echo "$*" >&2; exit 1; }; source "$1"; package_extract_bundle "$2" "$3"'
                result = subprocess.run(["bash", "-c", script, "fixture", str(UPDATER), str(archive), str(self.root / "extracted")],
                                        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse((self.root / "extracted").exists())

    def test_production_download_pins_one_completed_release(self):
        release = self.bundle("0.0.3", "c" * 40, channel="stable")
        selection = self.root / "selection.json"
        selection.write_text(json.dumps({"tag_name": "v0.0.3", "draft": False, "prerelease": False}))
        prefix = "https://github.com/Jayson-Stangel/symphony/releases/download/v0.0.3/"
        latest = "https://api.github.com/repos/Jayson-Stangel/symphony/releases/latest"
        mapping = self.root / "urls.json"
        mapping.write_text(json.dumps({latest: str(selection), prefix + "release.json": str(release[0]),
                                      prefix + release[1].name: str(release[1])}))
        tools = self.root / "tools"
        tools.mkdir()
        curl = tools / "curl"
        curl.write_text('''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
args=sys.argv[1:]; url=args[-1]
with open(os.environ['FIXTURE_URL_LOG'],'a') as log: log.write(url+'\\n')
mapping=json.loads(pathlib.Path(os.environ['FIXTURE_URLS']).read_text())
if url not in mapping: sys.exit(22)
shutil.copyfile(mapping[url], args[args.index('--output')+1])
''')
        curl.chmod(0o755)
        log = self.root / "urls.log"
        env = {"PATH": str(tools) + ":" + self.env["PATH"], "FIXTURE_URLS": str(mapping), "FIXTURE_URL_LOG": str(log)}
        result = self.run_update(env=env)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(log.read_text().splitlines(), [latest, prefix + "release.json", prefix + release[1].name])
        self.assertEqual(self.current().name, release[2].name)
        selection.write_text(json.dumps({"tag_name": "v0.0.3", "draft": True, "prerelease": False}))
        result = self.run_update(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("completed production releases", result.stdout)
        self.assertEqual(self.current().name, release[2].name)
        mapping.write_text("{}")
        result = self.run_update(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("download failed", result.stdout)
        self.assertEqual(self.current().name, release[2].name)

    def test_candidates_need_namespace_and_continuations_need_ownership(self):
        result = self.candidate(env={"SYMPHONY_RUNNER_NAMESPACE": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("isolated", result.stdout)
        result = subprocess.run([str(self.command), "_complete-package-update", str(self.initial), ""],
                                cwd=self.projects[0], env=self.env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.current(), self.initial)

    def test_shared_manager_lock_excludes_competing_update(self):
        held = self.app / "hold-transition"
        observed = self.app / "transition-observed"
        held.touch()
        process = subprocess.Popen([str(self.command), "update", "--candidate-manifest", str(self.second[0]),
                                    "--candidate-bundle", str(self.second[1]), "--agent", "codex"],
                                   cwd=self.projects[0], env=self.env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 15
            while not observed.exists():
                self.assertIsNone(process.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.05)
            result = self.candidate(project=1)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.current(), self.initial)
        finally:
            held.unlink(missing_ok=True)
            try: output, _ = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill(); process.communicate(); raise
        self.assertEqual(process.returncode, 0, output)


if __name__ == "__main__":
    unittest.main()
