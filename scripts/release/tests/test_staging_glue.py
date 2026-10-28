"""Bridge/workflow glue peers; no live API or native execution."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parents[1]))
import local_distribution as local
import staging_ingress as ingress
import test_staging_ingress as ingress_fixtures
import test_local_distribution as local_fixtures


def job(text,key):
    return re.split(r'\n  [a-z][a-z0-9-]*:',text.split('  '+key+':\n',1)[1])[0]


class StagingGlueTest(unittest.TestCase):
    def test_actual_stage_receive_and_original_q_native_admission_keep_signed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            peer=local_fixtures.LocalTransportTest()
            _,signed,fetch,_,_,_,_,_,_=peer.fixture(root)
            raw=local_fixtures.zip_bytes({p.relative_to(signed).as_posix():p.read_bytes() for p in signed.rglob('*') if p.is_file()})
            before={p.relative_to(signed).as_posix():p.read_bytes() for p in signed.rglob('*') if p.is_file()}
            core=ingress_fixtures.StagingIngressTest();core.setUp();self.addCleanup(core.doCleanups)
            core.payload=raw;core.staging_digest='sha256:'+hashlib.sha256(raw).hexdigest()
            core.origin.update(release_id=70,asset_id=71,digest=core.staging_digest,size=len(raw))
            staged=ingress.stage('70','71',core.staging_digest,core.revision,root/'opaque',download=core.download,
                fetch=core.fetch,context=core.context('staging-ingress'),invoke=core.git)
            core.manifest=json.loads(staged['manifest'].read_text())
            core.artifact={'id':81,'name':staged['artifact_name'],'digest':'','size_in_bytes':1,'expired':False,
                'expires_at':'2099-01-01T00:00:00Z','created_at':'2026-01-01T01:01:00Z',
                'workflow_run':{'id':71,'head_sha':core.revision}}
            core.package()
            def combined(path,binary=False):
                if path.startswith('actions/runs/71') or path.startswith('actions/artifacts/81'):
                    return core.fetch(path,binary=binary)
                return fetch(path,binary=binary)
            receive=ingress.receive
            def receive_peer(*args,**kwargs):
                return receive(*args,**kwargs,context=core.context('verify'),invoke=core.git)
            with patch.object(ingress,'receive',side_effect=receive_peer):
                local.import_ingress('81',core.artifact['name'],core.artifact['digest'],'70','71',core.staging_digest,
                    core.revision,root/'admitted','A'*40,'FAKETEAM01',combined,peer.native_peer)
            for name,value in before.items():self.assertEqual((root/'admitted/signed'/name).read_bytes(),value)
            origin=json.loads((root/'admitted/local-origin.json').read_text())
            self.assertEqual(origin['ingress_artifact']['id'],81)
            self.assertEqual(origin['ingress_payload_sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(origin['ingress_payload_size'],len(raw))
            self.assertEqual(core.run['status'],'in_progress')
            self.assertEqual(core.run['updated_at'],'2020-01-01T00:00:00Z')

    def test_workflow_only_protected_main_ingress_can_write_and_mac_is_read_only(self):
        text=(Path(__file__).parents[3]/'.github/workflows/macos-local-signing-import.yml').read_text()
        self.assertEqual(set(re.findall(r'^  ([a-z][a-z0-9-]*):$',text,re.M)),
                         {'verify','mac-runner-local-import','staging-ingress',
                          'transport-source','transport-reader','transport-registry'})
        producer=job(text,'staging-ingress');native=job(text,'verify')
        self.assertIn('contents: write\n      actions: read',producer)
        self.assertIn('artifact_digest: "sha256:${{ steps.opaque.outputs.artifact-digest }}"',producer)
        bare='d'*64
        normalized='sha256:${{ steps.opaque.outputs.artifact-digest }}'.replace('${{ steps.opaque.outputs.artifact-digest }}',bare)
        self.assertRegex(normalized,r'^sha256:[a-f0-9]{64}$')
        self.assertIn('contents: read\n      actions: read',native)
        self.assertNotIn('contents: write',native)
        self.assertIn('needs: [mac-runner-local-import, staging-ingress]',native)
        for key in ('verify','staging-ingress','mac-runner-local-import'):
            block=job(text,key)
            self.assertIn("github.ref == 'refs/heads/main'",block)
            self.assertIn('!inputs.transport_fixture_only',block)
            self.assertIn('inputs.approved_revision == github.sha',block)
        self.assertIn('environment: macos-production-signing',producer)
        self.assertIn('environment: macos-production-signing',native)
        self.assertEqual(text.count('contents: write'),2)
        self.assertNotIn('secrets.',text)
        self.assertIn('default: false', text.split('      transport_fixture_only:',1)[1].split('      approved_revision:',1)[0])
        for key in ('transport-source','transport-reader','transport-registry'):
            fixture=job(text,key)
            self.assertIn("github.ref == 'refs/heads/dev'",fixture)
            self.assertIn('github.run_attempt == 1',fixture)
            self.assertIn("github.actor == 'Jayson-Stangel'",fixture)
            self.assertIn('inputs.transport_fixture_only',fixture)
            self.assertIn('runs-on: ubuntu-24.04',fixture)
            self.assertNotIn('environment:',fixture)
            self.assertNotIn('local_distribution.py import',fixture)
        for retired in ('transport_probe_only','diagnostic_only','diagnostic_fixture_revision','diagnostic_handoff_proof',
                        'http-diagnostic','fixture-ingress','fixture-read-consumer'):
            self.assertNotIn(retired,text)
        for name in ('staging_release','staging_asset','staging_digest'):
            self.assertIn('required: true',text.split('      '+name+':\n',1)[1].split('        type:',1)[0])

    def test_retired_probe_and_diagnostic_cli_modes_refuse_before_any_source_or_api_call(self):
        for mode in ('probe','diagnostic'):
            with self.subTest(mode=mode),patch.object(sys,'argv',['local_distribution.py',mode]), \
                 patch.object(local,'exact_source') as source,redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):local.main()
                source.assert_not_called()


if __name__=='__main__':unittest.main()
