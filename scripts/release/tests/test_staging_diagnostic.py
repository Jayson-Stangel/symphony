"""Closed source/HTTP peers only; never live API, token, package or native effects."""
import copy
from datetime import datetime, timezone
import io
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1]))
import staging_diagnostic as diagnostic
import local_distribution as local
import production_release as production

C, M = 'c'*40, 'a'*40


class DiagnosticTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root/'receipt.json'
        ref = 'refs/heads/codex/reviewed-diagnostic'
        self.env = {'GITHUB_SHA':C,'GITHUB_REPOSITORY':diagnostic.REPOSITORY,'GITHUB_EVENT_NAME':'workflow_dispatch',
                    'GITHUB_REF':ref,'GITHUB_WORKFLOW_REF':diagnostic.REPOSITORY+'/'+diagnostic.WORKFLOW+'@'+ref,
                    'GITHUB_WORKFLOW_SHA':C}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_STORED) as archive:
            entry=zipfile.ZipInfo('permission-probe.txt',date_time=(1980,1,1,0,0,0));entry.external_attr=0o100644<<16
            archive.writestr(entry,b'Symphony local signing staging permission probe. Public fixture. No credentials or candidate package.\n')
        self.raw = buffer.getvalue()
        self.asset = {'id':71,'name':diagnostic.FIXTURE_NAME,'size':240,'digest':diagnostic.FIXTURE_DIGEST,
                      'state':'uploaded','created_at':datetime.now(timezone.utc).isoformat()}
        self.release = {'id':70,'draft':True,'target_commitish':M,'tag_name':'local-signing-'+M+'-'+'b'*32,
                        'assets':[self.asset]}
        self.calls = []

    def git(self, args, **kwargs):
        body = C if 'rev-parse' in args else ''
        return subprocess.CompletedProcess(args,0,body,'')

    def invoke(self, args, **kwargs):
        self.calls.append(args)
        body = self.raw if 'Accept: application/octet-stream' in args else json.dumps(
            self.asset if 'releases/assets/71' in args else self.release).encode()
        return subprocess.CompletedProcess(args,0,b'HTTP/2.0 200 OK\r\nSecret: FAKE-SECRET\r\n\r\n'+body,b'')

    def run_diagnostic(self, **kwargs):
        return diagnostic.diagnose(self.root,self.output,C,M,'70','71',diagnostic.FIXTURE_DIGEST,self.env,
            local.download_staging,invoke=kwargs.pop('invoke',self.invoke),git=kwargs.pop('git',self.git),
            command=lambda path,binary=False:['gh','api',path],**kwargs)

    def receipt(self):
        return json.loads(self.output.read_text())

    def test_fixed_fixture_success_never_parses_or_qualifies_and_keeps_only_closed_data(self):
        with patch.object(zipfile,'ZipFile',side_effect=AssertionError('must not parse fixture')), \
             patch.object(production,'qualification',side_effect=AssertionError('must not qualify')), \
             patch.object(local,'verify_native',side_effect=AssertionError('must not run native')):
            proof=self.run_diagnostic()
        self.assertEqual(proof['status'],'success')
        self.assertEqual([item['stage'] for item in proof['observations']],
                         ['source-binding','release-metadata','asset-metadata','octet-download','fixture-binding'])
        self.assertEqual([cmd[2] for cmd in self.calls],['releases/70','releases/assets/71','releases/assets/71'])
        self.assertIn('Accept: application/octet-stream',self.calls[-1])
        self.assertNotIn('FAKE-SECRET',self.output.read_text())
        self.assertEqual(proof,self.receipt())
        self.assertEqual(self.output.stat().st_mode & 0o777,0o600)

    def test_http_status_is_observed_without_raw_errors_headers_accounts_or_urls(self):
        for code,reason in [(401,'http-unauthorized'),(403,'http-forbidden'),(404,'http-not-found'),
                            (429,'http-rate-limited'),(503,'http-server-error')]:
            with self.subTest(code=code):
                self.output.unlink(missing_ok=True);self.calls.clear()
                def failed(args,**kwargs):
                    self.calls.append(args)
                    return subprocess.CompletedProcess(args,1,f'HTTP/1.1 {code} Refused\r\nAuthorization: FAKE-TOKEN\r\n\r\n'.encode()+b'PRIVATE BODY',
                        b'private@example.invalid https://private.invalid FAKE-SECRET')
                with self.assertRaises(diagnostic.Refusal):self.run_diagnostic(invoke=failed)
                result=self.receipt()['observations'][-1]
                self.assertEqual((result['http_status'],result['reason'],result['exit_status']),(code,reason,1))
                self.assertEqual(len(self.calls),1)
                for text in ('FAKE','private@','https://','PRIVATE','Authorization'):
                    self.assertNotIn(text,self.output.read_text())

    def test_missing_status_malformed_json_timeouts_and_start_failure_have_closed_reasons(self):
        cases=[(subprocess.CompletedProcess([],1,b'private response',b'(HTTP 404) FAKE-SECRET'),'unknown-http-result'),
               (subprocess.CompletedProcess([],1,b'\r\n\r\nPRIVATE',b''),'unknown-http-result'),
               (subprocess.CompletedProcess([],1,b'',b'gh: private@example.invalid FAKE-TOKEN (HTTP 403)'),'http-forbidden'),
               (subprocess.CompletedProcess([],1,b'',b'gh: secret (HTTP 403)\ngh: other (HTTP 404)'),'unknown-http-result'),
               (subprocess.CompletedProcess([],0,b'HTTP/2.0 200 OK\n\nnot-json FAKE-SECRET',b''),'release-binding-mismatch'),
               (subprocess.TimeoutExpired('private command',20),'request-timeout'),(OSError('FAKE-SECRET'),'request-unavailable')]
        for value,reason in cases:
            with self.subTest(reason=reason):
                self.output.unlink(missing_ok=True)
                def invoke(*args,**kwargs):
                    if isinstance(value,Exception):raise value
                    return value
                with self.assertRaises(diagnostic.Refusal):self.run_diagnostic(invoke=invoke)
                self.assertEqual(self.receipt()['observations'][-1]['reason'],reason)
                self.assertNotIn('FAKE',self.output.read_text())

    def test_each_service_failure_has_its_actual_closed_stage_and_stops(self):
        for index,stage in enumerate(('release-metadata','asset-metadata','octet-download'),1):
            with self.subTest(stage=stage):
                self.output.unlink(missing_ok=True);self.calls.clear()
                def invoke(args,**kwargs):
                    if len(self.calls)+1==index:
                        self.calls.append(args)
                        return subprocess.CompletedProcess(args,1,b'HTTP/2.0 403 Forbidden\n\nPRIVATE',b'FAKE-SECRET')
                    return self.invoke(args,**kwargs)
                with self.assertRaises(diagnostic.Refusal):self.run_diagnostic(invoke=invoke)
                result=self.receipt()['observations'][-1]
                self.assertEqual((result['stage'],result['http_status'],result['reason']),(stage,403,'http-forbidden'))
                self.assertEqual(len(self.calls),index)
                self.assertNotIn('FAKE',self.output.read_text())

    def test_wrong_fixture_metadata_refuses_before_octet_download(self):
        changes=[lambda:self.release.update(id=99),lambda:self.release.update(draft=False),
                 lambda:self.release.update(target_commitish='d'*40),lambda:self.release.update(tag_name='wrong'),
                 lambda:self.asset.update(id=True),lambda:self.asset.update(name='private.zip'),
                 lambda:self.asset.update(size=239),lambda:self.asset.update(size=True),
                 lambda:self.asset.update(digest='sha256:'+'0'*64),lambda:self.asset.update(created_at='2000-01-01T00:00:00Z')]
        for change in changes:
            release,asset=copy.deepcopy(self.release),copy.deepcopy(self.asset)
            with self.subTest(change=change):
                self.output.unlink(missing_ok=True);self.calls.clear();change()
                with self.assertRaises(diagnostic.Refusal):self.run_diagnostic()
                self.assertFalse(any('Accept: application/octet-stream' in c for c in self.calls))
            self.asset=asset;self.release=release;self.release['assets']=[self.asset]

    def test_wrong_binary_bytes_refuse_without_parsing_or_native_calls(self):
        self.raw=b'X'*240
        with self.assertRaises(diagnostic.Refusal):self.run_diagnostic()
        self.assertEqual(self.receipt()['observations'][-1]['reason'],'fixture-bytes-mismatch')
        self.assertEqual(len(self.calls),3)

    def test_source_and_conflicting_mode_guards_fail_closed_before_http_with_retained_receipt(self):
        changes={'GITHUB_REPOSITORY':'fork/repo','GITHUB_EVENT_NAME':'push','GITHUB_SHA':'d'*40,
                 'GITHUB_REF':'refs/heads/main','GITHUB_WORKFLOW_SHA':'d'*40,'GITHUB_WORKFLOW_REF':'wrong'}
        for key,value in changes.items():
            with self.subTest(key=key):
                self.output.unlink(missing_ok=True);original=self.env[key];self.env[key]=value
                with self.assertRaises(diagnostic.Refusal):self.run_diagnostic()
                self.assertEqual(self.calls,[])
                self.assertEqual(self.receipt()['observations'][-1]['reason'],'source-binding-mismatch')
                self.env[key]=original
        self.output.unlink()
        with self.assertRaises(diagnostic.Refusal):self.run_diagnostic(conflicting='true')
        self.assertEqual(self.receipt()['observations'][-1]['reason'],'invalid-mode')
        self.assertEqual(self.calls,[])
        for args in ('status','merge-base'):
            self.output.unlink()
            def bad_git(command,**kwargs):
                result=self.git(command,**kwargs)
                if args in command:
                    result.returncode=1
                return result
            with self.assertRaises(diagnostic.Refusal):self.run_diagnostic(git=bad_git)
            self.assertEqual(self.calls,[])

    def test_diagnostic_cli_uses_own_guard_without_production_source_check(self):
        args=['local_distribution.py','diagnostic','70','71',diagnostic.FIXTURE_DIGEST,M,str(self.output),'--approved-revision',C]
        with patch.object(sys,'argv',args),patch.object(diagnostic,'diagnose') as run, \
             patch.object(local,'exact_source',side_effect=AssertionError('must not fake production source')):
            local.main()
        run.assert_called_once()

    def test_green_feature_diagnostic_refuses_production_before_artifact_selection(self):
        run={'head_sha':C,'head_branch':'codex/reviewed-diagnostic','event':'workflow_dispatch','status':'completed',
             'conclusion':'success','run_attempt':1,'path':diagnostic.WORKFLOW,
             'head_repository':{'full_name':diagnostic.REPOSITORY}}
        calls=[]
        def fetch(path,**kwargs):
            calls.append(path)
            if path=='actions/runs/77':return run
            self.fail('Diagnostic must refuse before artifact selection: '+path)
        with self.assertRaises(ValueError):local.retained_local('77','1',C,self.root/'native',fetch)
        self.assertEqual(calls,['actions/runs/77'])

    def test_green_main_diagnostic_with_skipped_native_refuses_before_artifact_selection(self):
        run={'head_sha':C,'head_branch':'main','event':'workflow_dispatch','status':'completed',
             'conclusion':'success','run_attempt':1,'path':diagnostic.WORKFLOW,
             'head_repository':{'full_name':diagnostic.REPOSITORY}}
        calls=[]
        def fetch(path,**kwargs):
            calls.append(path)
            if path in ('actions/runs/77','actions/runs/77/attempts/1'):return run
            if path=='actions/runs/77/attempts/1/jobs?per_page=100':
                return {'jobs':[{'name':'Verify locally signed distribution','conclusion':'skipped'},
                                {'name':'Diagnose private fixture HTTP stages','conclusion':'success'}]}
            self.fail('Diagnostic must refuse before artifact selection: '+path)
        with self.assertRaises(ValueError):local.retained_local('77','1',C,self.root/'native',fetch)
        self.assertFalse(any('artifacts' in path for path in calls))

    def test_workflow_scopes_diagnostic_write_and_keeps_production_jobs_separate(self):
        text=(Path(__file__).parents[3]/diagnostic.WORKFLOW).read_text()
        diag=re.split(r'\n  [a-z][a-z0-9-]*:',text.split('  http-diagnostic:\n',1)[1])[0]
        original=text.split('  http-diagnostic:\n',1)[0]
        self.assertIn('diagnostic_only:',text);self.assertIn('diagnostic_fixture_revision:',text)
        self.assertIn('default: false\n        type: boolean',text)
        self.assertNotIn('environment:',diag);self.assertNotIn('secrets.',diag);self.assertNotIn('MACOS_',diag)
        self.assertIn('runs-on: ubuntu-24.04',diag);self.assertIn('if: always()',diag)
        self.assertIn('local-signing-http-diagnostic-',diag);self.assertNotIn('production-local-signed-',diag)
        self.assertIn('contents: read\n  actions: read',text)
        self.assertIn('permissions:\n      contents: write\n      actions: read',diag)
        self.assertNotIn('contents: write',original)
        self.assertEqual(original.count('!inputs.diagnostic_only'),3)
        self.assertIn('--transport-probe-only "$TRANSPORT_PROBE_ONLY"',diag)


if __name__=='__main__':unittest.main()
