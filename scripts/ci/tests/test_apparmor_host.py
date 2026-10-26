"""Execute host-preparation shell with synthetic daemon/kernel/systemd peers.

No sudo, host /etc writes, real policy load, container or reboot occurs. Source
paths and executable search are rewritten only in this disposable test copy.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
PROFILE = 'symphony-codex-candidate-b6261d4c613296dd'
FAKE = r'''#!/usr/bin/env python3
import hashlib,json,os,pathlib,sys
r=pathlib.Path(os.environ['FIXTURE']); o=json.loads(os.environ['OPTIONS'])
n=pathlib.Path(sys.argv[0]).name; a=sys.argv[1:]
with (r/'calls').open('a') as f:f.write(json.dumps([n,*a])+'\n')
if n=='uname':print('Linux')
elif n=='hostname':print('host')
elif n=='id':print(o.get('uid',0))
elif n=='stat':
 p=pathlib.Path(a[-1]); print('0 '+(oct(p.stat().st_mode & 0o777)[2:] if p.is_relative_to(r) else '755'))
elif n=='cat':
 if o.get('deny_inventory') and a and a[-1].endswith('/sys/kernel/security/apparmor/profiles'):sys.exit(1)
 if a:sys.stdout.write(pathlib.Path(a[-1]).read_text())
 else:sys.stdout.write(sys.stdin.read())
elif n=='sha256sum':print(hashlib.sha256(pathlib.Path(a[-1]).read_bytes()).hexdigest()+'  '+a[-1])
elif n=='docker':
 if a[0]=='context': print(o.get('endpoint','unix:///var/run/docker.sock'))
 elif a[-1]=='{{.ID}}':print('daemon-id')
 elif a[-1]=='{{.Name}}':print(o.get('host','host'))
 else:print('name=seccomp,profile=builtin'+('' if o.get('no_apparmor') else '\nname=apparmor'))
elif n=='systemctl':
 if a[0]=='show':
  if '--property=Requires' in a or '--property=After' in a:print('' if o.get('override_guard') else 'apparmor.service')
  elif '--property=ExecStartPre' in a:
   parser=str(r/'bin/apparmor_parser'); destination=str(r/'etc/apparmor.d/symphony-codex-candidate-b6261d4c613296dd')
   path=parser; argv=parser+' --replace --skip-cache '+destination; ignore='no'
   if o.get('ignored_guard'):ignore='yes'
   if o.get('echoed_guard'):path='/usr/bin/echo'; argv=path+' '+argv
   if o.get('wrong_destination'):argv+='.other'
   if o.get('spoofed_metadata'):
    argv+=' ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'; ignore='yes'
   metadata=' ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'
   record='{ path='+path+' ; argv[]='+argv+' ; ignore_errors='+ignore+metadata
   if o.get('unrelated_precommand'):
    print('{ path=/sbin/modprobe ; argv[]=/sbin/modprobe -va overlay ; ignore_errors=yes'+metadata)
   print(record)
   if o.get('duplicate_guard'):print(record)
  else:print('not-found' if o.get('missing_service') else 'loaded')
 elif o.get('systemctl_failure'):sys.exit(9)
elif n=='flock':pass
elif n=='apparmor_parser':
 if '--names' in a:print('wrong-name' if o.get('wrong_name') else 'symphony-codex-candidate-b6261d4c613296dd')
 elif '--skip-kernel-load' in a:
  if o.get('compile_failure'):sys.exit(9)
 elif '--replace' in a:
  if o.get('load_failure'):sys.exit(9)
  (r/'sys/kernel/security/apparmor/profiles').write_text('unrelated (enforce)\nsymphony-codex-candidate-b6261d4c613296dd ('+('complain' if o.get('complain') else 'enforce')+')\n')
 else:sys.exit(8)
else:sys.exit(8)
'''


class HostPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.options = {}
        self.bin = self.root/'bin'; self.bin.mkdir()
        for name in ('bash','python3','cut','grep','cmp','cp','install','mkdir','mktemp','rm','dirname'):
            (self.bin/name).symlink_to(sys.executable if name=='python3' else shutil.which(name))
        for name in ('uname','hostname','id','cat','stat','sha256sum','docker','systemctl','flock','apparmor_parser'):
            p=self.bin/name; p.write_text(FAKE); p.chmod(0o755)
        for path, value in {
            'sys/module/apparmor/parameters/enabled':'Y\n',
            'sys/kernel/security/lsm':'lockdown,apparmor\n',
            'sys/kernel/security/apparmor/profiles':'unrelated (enforce)\n',
            'proc/thread-self/attr/apparmor/exec':'',
            'etc/apparmor.d/abi/3.0':'abi',
            'etc/apparmor.d/tunables/global':'tunables',
            'etc/apparmor.d/abstractions/base':'base',
            'etc/systemd/system/sentinel':'user-owned',
        }.items():
            p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value)
        self.bundle=self.root/'source free package'; (self.bundle/'scripts').mkdir(parents=True)
        shutil.copyfile(ROOT/'scripts/codex-apparmor.profile', self.bundle/'scripts/codex-apparmor.profile')
        module=(ROOT/'scripts/symphony-apparmor.sh').read_text()
        for prefix in ('/etc/','/sys/','/proc/'):
            module=module.replace(prefix,str(self.root)+prefix)
        module=module.replace('[ "$EUID" = 0 ]', '[ "$(id -u)" = 0 ]')
        module=module.replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH', ': # fixture executable peers')
        (self.bundle/'scripts/symphony-apparmor.sh').write_text(module)
        self.driver=self.bundle/'scripts/symphony'
        digest=hashlib.sha256((ROOT/'scripts/codex-apparmor.profile').read_bytes()).hexdigest()
        self.driver.write_text('''#!/usr/bin/env bash
set -euo pipefail
symphony_home=$(cd "$(dirname "$0")/.." && pwd)
. "$symphony_home/scripts/symphony-apparmor.sh"
container_runtime() { echo docker; }
codex_apparmor_id() { echo DIGEST; }
codex_apparmor_profile() { if [ "${NO_APPARMOR:-}" = yes ]; then echo none; else echo PROFILE; fi; }
codex_verify_profile() { [ ! -L "$1" ] && [ "$(sha256sum "$1" | cut -d ' ' -f1)" = "$2" ] || { apparmor_fail integrity 'Policy digest mismatch'; return 1; }; }
if [ "${1:-}" = doctor ]; then apparmor_doctor docker; else runner_apparmor "$@"; fi
'''.replace('DIGEST',digest).replace('PROFILE',PROFILE))
        self.driver.chmod(0o755)

    def run_command(self, *args, success=True):
        env=dict(os.environ,PATH=str(self.bin),FIXTURE=str(self.root),OPTIONS=json.dumps(self.options))
        env.pop('DOCKER_CONTEXT',None);env.pop('DOCKER_HOST',None)
        if self.options.get('no_apparmor'):env['NO_APPARMOR']='yes'
        result=subprocess.run([str(self.driver),*args],env=env,text=True,capture_output=True)
        self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
        self.assertEqual((self.root/'etc/systemd/system/sentinel').read_text(),'user-owned')
        self.assertIn('unrelated (enforce)',(self.root/'sys/kernel/security/apparmor/profiles').read_text())
        return result.stdout+result.stderr

    def apply(self, **kwargs):return self.run_command('--apply','--daemon-id','daemon-id',**kwargs)
    def calls(self):return [json.loads(v) for v in (self.root/'calls').read_text().splitlines()]
    def destination(self):return self.root/'etc/apparmor.d'/PROFILE

    def test_snapshot_selection_diagnoses_absent_policy_before_export_or_store(self):
        launcher = (ROOT / 'scripts/symphony').read_text()
        selection = 'codex_config_select() {' + launcher.split('codex_config_select() {', 1)[1].split('\ncodex_config_require() {', 1)[0]
        doctor = 'runner_doctor() {' + launcher.split('runner_doctor() {', 1)[1].split('\nrunner() {', 1)[0]
        # Admission now holds the actual project and compatibility leases.
        # Only identity/path discovery is redirected to this disposable host.
        locks = ('updater_file_lock() {' + launcher.split('updater_file_lock() {', 1)[1].split('\nsource_update_lock() {', 1)[0] +
                 'runner_project_lock() {' + launcher.split('runner_project_lock() {', 1)[1].split('\n# Keep the original lock pathname', 1)[0] +
                 'runner_transition_lock() {' + launcher.split('runner_transition_lock() {', 1)[1].split('\nrunner_resume_when_ready() {', 1)[0])
        original = self.driver.read_text()
        for name in ('native-auth', 'native-settings', 'runner-state'):
            (self.root / name).write_text('preserved-' + name)
        for invocation in ('codex_config_select /unused docker fixture-image',
                           'if codex_config_select /unused docker fixture-image; then exit 88; else exit 1; fi',
                           'codex_config_select /unused docker fixture-image | cat',
                           'runner_doctor /unused codex true',
                           'if runner_doctor /unused codex true; then exit 88; else exit 1; fi'):
            with self.subTest(invocation=invocation):
                driver = original.replace('if [ "${1:-}" = doctor ]; then apparmor_doctor docker; else runner_apparmor "$@"; fi',
                    selection + '\n' + doctor + '\n' + locks + '\ncodex_auth_volume=fixture-auth\npackaged_install=false\n'
                    'runner_home="$FIXTURE/runners"\n'
                    'project_id() { printf fixture-project; }\nrunner_id() { printf host-preparation-fixture; }\n'
                    'runner_resource_lock_path() { printf \"%s/resource.lock\" \"$FIXTURE\"; }\n'
                    'container_engine_ready() { return 0; }\nvalidate_agent() { return 0; }\n'
                    'runner_image() { printf fixture-image; }\n'
                    'runner_codex_sandbox_ready() { printf called > \"$FIXTURE/unexpected-protected\"; return 91; }\n'
                    'codex_config_store_root() { printf called > "$FIXTURE/unexpected-store"; printf "%s" "$FIXTURE/store"; }\n'
                    'runner_codex_run() { printf called > "$FIXTURE/unexpected-export"; return 89; }\n'
                    'codex_config_store() { cat >/dev/null; return 90; }\n'
                    + invocation)
                self.driver.write_text(driver)
                output = self.run_command(success=False)
                self.assertIn('AppArmor [absent]', output)
                self.assertIn('Run symphony runner apparmor', output)
                if 'runner_doctor' in invocation:
                    self.assertTrue((self.root / 'resource.lock').is_file())
                    self.assertTrue((self.root / 'runners/fixture-project.transition.lock').is_file())
                self.assertFalse((self.root / 'unexpected-store').exists())
                self.assertFalse((self.root / 'unexpected-export').exists())
                self.assertFalse((self.root / 'unexpected-protected').exists())
                for name in ('native-auth', 'native-settings', 'runner-state'):
                    self.assertEqual((self.root / name).read_text(), 'preserved-' + name)

    def test_plan_is_source_free_read_only_and_exact(self):
        output=self.run_command()
        self.assertIn('--apply --daemon-id daemon-id',output)
        self.assertIn(str(self.root/'etc/apparmor.d'/PROFILE),output)
        self.assertFalse(self.destination().exists())
        self.assertFalse(any(v[0] in ('systemctl','apparmor_parser') for v in self.calls()))

    def test_prepare_repeat_enforce_and_boot_guard(self):
        self.assertIn('exact owned policy loaded',self.apply())
        self.assertEqual(self.destination().read_bytes(),(ROOT/'scripts/codex-apparmor.profile').read_bytes())
        guard=next((self.root/'etc/systemd/system/docker.service.d').glob('*.conf')).read_text()
        self.assertIn('Requires=apparmor.service\nAfter=apparmor.service',guard)
        self.assertIn('--replace --skip-cache '+str(self.destination()),guard)
        self.assertIn('exact owned policy loaded',self.apply())
        self.assertIn('following protected command',self.run_command('doctor'))
        self.assertFalse(any('restart' in v or '--remove' in v for v in self.calls()))

    def test_no_apparmor_no_mutation(self):
        self.options['no_apparmor']=True
        self.assertIn('no host changes',self.apply())
        self.assertFalse(self.destination().exists())

    def test_remote_and_wrong_host_refuse_without_secret_endpoint(self):
        for options in ({'endpoint':'ssh://user:synthetic-secret@remote'},{'host':'other'}):
            self.options=options
            output=self.apply(success=False)
            self.assertIn('[daemon-host]',output);self.assertNotIn('synthetic-secret',output)
            self.assertFalse(self.destination().exists())

    def test_explicit_authorization_and_privilege(self):
        self.run_command('--apply',success=False)
        self.run_command('--apply','--daemon-id','other',success=False)
        self.options['uid']=1000
        self.assertIn('[privilege]',self.apply(success=False))
        self.assertFalse(self.destination().exists())

    def test_unknown_loaded_name_refused(self):
        (self.root/'sys/kernel/security/apparmor/profiles').write_text('unrelated (enforce)\n'+PROFILE+' (enforce)\n')
        self.assertIn('[conflict]',self.apply(success=False))
        self.assertFalse(self.destination().exists())

    def test_unknown_file_and_symlink_refused(self):
        self.destination().write_text('unknown')
        self.assertIn('[conflict]',self.apply(success=False))
        self.assertEqual(self.destination().read_text(),'unknown')
        self.destination().unlink(); self.destination().symlink_to(self.root/'etc/systemd/system/sentinel')
        self.assertIn('[conflict]',self.apply(success=False))

    def test_corrupt_policy_refused(self):
        (self.bundle/'scripts/codex-apparmor.profile').write_text('corrupt')
        self.assertIn('[integrity]',self.apply(success=False))
        self.assertFalse(self.destination().exists())

    def test_kernel_and_exec_interfaces_refuse_before_mutation(self):
        enabled=self.root/'sys/module/apparmor/parameters/enabled'
        enabled.write_text('N\n')
        self.assertIn('[kernel]',self.apply(success=False))
        enabled.write_text('Y\n')
        (self.root/'proc/thread-self/attr/apparmor/exec').unlink()
        self.assertIn('[kernel]',self.apply(success=False))
        self.assertFalse(self.destination().exists())

    def test_missing_import_and_unsafe_parent(self):
        p=self.root/'etc/apparmor.d/abstractions/base';p.unlink()
        self.assertIn('[imports]',self.apply(success=False))
        p.write_text('base');p.parent.chmod(0o777)
        self.assertIn('[imports]',self.apply(success=False))
        self.assertFalse(self.destination().exists())

    def test_parser_tool_and_compile_name_failures(self):
        for name in ('compile_failure','wrong_name','missing_service'):
            self.options={name:True};self.apply(success=False)
            self.assertFalse(self.destination().exists())
        (self.bin/'apparmor_parser').unlink()
        self.assertIn('[tools]',self.apply(success=False))

    def test_partial_service_failure_can_retry(self):
        self.options['systemctl_failure']=True
        self.assertIn('[persistence]',self.apply(success=False))
        self.options={};self.apply()

    def test_ordinary_doctor_inventory_denial_defers_to_protected_probe(self):
        self.options['deny_inventory']=True
        self.assertIn('continuing to actual protected execution',self.run_command('doctor'))
        self.assertFalse(self.destination().exists())
        self.assertFalse(any('--replace' in v for v in self.calls()))

    def test_overridden_effective_boot_guard_refused(self):
        self.options['override_guard']=True
        self.assertIn('[persistence]',self.apply(success=False))

    def test_effective_guard_requires_exact_execution_and_fatal_failure(self):
        for option in ('ignored_guard','echoed_guard','wrong_destination','spoofed_metadata','duplicate_guard'):
            with self.subTest(option=option):
                self.options={option:True}
                self.assertIn('[persistence]',self.apply(success=False))
        self.assertFalse(any(v[0]=='apparmor_parser' and '--replace' in v for v in self.calls()))

    def test_unrelated_daemon_precommands_preserve_exact_boot_guard(self):
        self.options['unrelated_precommand']=True
        self.assertIn('exact owned policy loaded',self.apply())
        self.assertIn('following protected command',self.run_command('doctor'))

    def test_non_enforcing_load_and_absent_doctor_are_distinct(self):
        self.assertIn('[absent]',self.run_command('doctor',success=False))
        self.options['complain']=True
        self.assertIn('[enforcement]',self.apply(success=False))
        self.options={};self.apply()

    def test_owned_changes_and_boot_guard_conflict_refused(self):
        self.apply()
        guard=next((self.root/'etc/systemd/system/docker.service.d').glob('*.conf'))
        guard.write_text('foreign')
        self.assertIn('[conflict]',self.apply(success=False))
        self.assertEqual(guard.read_text(),'foreign')


if __name__=='__main__':unittest.main()
