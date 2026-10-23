"""Execute actual retained-image and preactivation checks with observable Docker receipts."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = (ROOT/'scripts/symphony-package.sh').read_text()
CHECKS = SOURCE[SOURCE.index('package_retained_image_compatible() {'):SOURCE.index('package_build_image() {')]
UPDATER = (ROOT/'scripts/symphony-package-update.sh').read_text()
COMPLETE = UPDATER[UPDATER.index('complete_package_update() {'):]
BUILD = SOURCE[SOURCE.index('package_build_image() {'):SOURCE.index('# A shared manager update')]


class RetainedCompatibility(unittest.TestCase):
    def execute(self, variant, activation=False, migration=None):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            docker=path/'docker'
            docker.write_text('''#!/bin/sh
[ "$3" = --format ] || exit 0
case "$4" in
'{{.Id}}') printf '%s\\n' "$IMAGE" ;;
'{{.Os}}/{{.Architecture}}') echo "${PLATFORM:-linux/amd64}" ;;
*com.symphony.agent*) echo "${PROVIDER:-codex}" ;;
*com.symphony.protocol*) echo "${PROTOCOL:-1}" ;;
*org.opencontainers.image.revision*) echo "$REVISION" ;;
*RepoDigests*) echo "$DIGEST" ;;
*) exit 90 ;;
esac
''');docker.chmod(0o755)
            previous=path/'previous';previous.mkdir()
            runners=path/'runners';runners.mkdir()
            image='sha256:'+'c'*64
            (runners/'actual.env').write_text('SYMPHONY_RUNNER_IMAGE_ID='+image+'\nSYMPHONY_RUNNER_AGENT=codex\n')
            env=dict(os.environ,PATH=str(path)+':'+os.environ['PATH'],IMAGE=image,REVISION='a'*40,
                     DIGEST='registry.invalid/codex@sha256:'+'d'*64,PREVIOUS=str(previous),
                     MARKER=str(path/'activated'),SCHEMA='1')
            env.update(variant)
            shell='''set -euo pipefail
die() { echo "$*" >&2; exit 42; }
package_arch=x86_64
package_verified_home() { test -d "$1" && test ! -L "$1" || die 'missing original release evidence'; printf '%s\\n' "$1"; }
package_helper() {
 case "$1/${3:-}" in
 get/schema_version) echo "$SCHEMA" ;;
 get/runners.codex.x86_64.image) echo "${EXPECTED_DIGEST:-$DIGEST}" ;;
 get/runners.codex.x86_64.source_revision) echo "${EXPECTED_REVISION:-$REVISION}" ;;
 owned-file/) test -f "$2" && test ! -L "$2" ;;
 owned-directory/) test -d "$2" && test ! -L "$2" ;;
 *) exit 91 ;;
 esac
}
container_runtime() { echo docker; }
package_update_lock() { :; }
package_activate_home() { touch "$MARKER"; }
runner_transition() { touch "$MARKER"; }
'''+CHECKS+'\nrunner_home='+str(runners)+'\n'
            if migration:
                app=path/'application';app.mkdir()
                (app/'previous').symlink_to(previous,target_is_directory=True)
                shell+=BUILD+'\npackage_app_home='+str(app)+'\nrunner_image() { echo "$IMAGE"; }\npackage_build_image codex\n'
                if migration=='missing':
                    shell+='rmdir "$PREVIOUS"\n'
                else:
                    shell+='rm "$package_app_home/previous"\nmkdir "$package_app_home/new-schema2"\nln -s "$package_app_home/new-schema2" "$package_app_home/previous"\n'
                shell+='package_build_image codex\n'
            elif activation:
                shell+=COMPLETE+'\nrunner_home='+str(runners)+'\nsymphony_home="$PREVIOUS"\ncomplete_package_update "$PREVIOUS" ""\n'
            else:
                shell+='\npackage_retained_image_compatible docker "$IMAGE" codex "$PREVIOUS"\npackage_retained_image_compatible docker "$IMAGE" codex \"\"\n'
            result=subprocess.run(['bash','-c',shell],env=env,text=True,capture_output=True)
            return result,(path/'activated').exists()

    def test_protocol_and_exact_legacy_migration_receipts(self):
        for variant,accepted in (({},True),({'PROTOCOL':'2'},False),({'PLATFORM':'linux/arm64'},False),
                ({'PROVIDER':'claude'},False),({'PROTOCOL':'<no value>'},True),
                ({'PROTOCOL':'<no value>','SCHEMA':'2'},False),
                ({'PROTOCOL':'<no value>','EXPECTED_REVISION':'b'*40},False),
                ({'PROTOCOL':'<no value>','EXPECTED_DIGEST':'registry.invalid/foreign@sha256:'+'e'*64},False)):
            with self.subTest(variant=variant):
                result,_=self.execute(variant)
                self.assertEqual(result.returncode==0,accepted,result.stderr)

    def test_incompatible_retained_project_refuses_before_actual_activation(self):
        for protocol,accepted in (('1',True),('2',False)):
            with self.subTest(protocol=protocol):
                result,activated=self.execute({'PROTOCOL':protocol},activation=True)
                self.assertEqual(result.returncode==0,accepted,result.stderr)
                self.assertEqual(activated,accepted)

    def test_actual_previous_symlink_and_durable_migration_basis_survive_manager_changes(self):
        result,_=self.execute({'PROTOCOL':'<no value>'},migration='survive')
        self.assertEqual(result.returncode,0,result.stderr)
        result,_=self.execute({'PROTOCOL':'<no value>'},migration='missing')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('missing original release evidence',result.stderr)
