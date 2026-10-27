"""Bridge/workflow/temporary fixture glue peers; no live API or native execution."""
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
import fixture_handoff_proof as fixture_proof
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

    def test_fixed_fixture_metadata_refuses_before_any_binary_get(self):
        peer=local_fixtures.TransportProbeTest();fetch,release,asset,calls=peer.fixture()
        asset['name']='candidate-private.zip'
        with patch.object(fixture_proof,'staging_api',side_effect=fetch):
            with self.assertRaises(ValueError):fixture_proof.fixture_download('70','71',local.PROBE_DIGEST,'a'*40)
        self.assertFalse(any(binary for _,binary in calls))

    def test_temporary_wrapper_subprocess_failures_emit_only_closed_stage(self):
        args=['fixture_handoff_proof.py','produce','70','71',local.PROBE_DIGEST,'unused-output',
              '--approved-revision','c'*40,'--fixture-revision','c'*40,'--diagnostic-only','true',
              '--transport-probe-only','false']
        for failing in ('source','stage'):
            error=subprocess.TimeoutExpired('PRIVATE TOKEN https://private.invalid',20)
            stderr=io.StringIO()
            with self.subTest(failing=failing),patch.object(sys,'argv',args), \
                 patch.dict(os.environ,GITHUB_SHA='c'*40,GITHUB_REF=fixture_proof.CONTRACT.ref), \
                 patch.object(fixture_proof,'source_guard',side_effect=error if failing=='source' else None), \
                 patch.object(fixture_proof,'stage_core',side_effect=error),redirect_stderr(stderr):
                with self.assertRaises(SystemExit):fixture_proof.main()
            self.assertNotIn('PRIVATE',stderr.getvalue());self.assertNotIn('https://',stderr.getvalue())
            self.assertIn('source-binding' if failing=='source' else 'opaque-stage',stderr.getvalue())

    def test_temporary_wrapper_invalid_modes_cannot_call_transport_core(self):
        args=['fixture_handoff_proof.py','produce','70','71',local.PROBE_DIGEST,'unused-output',
              '--approved-revision','c'*40,'--fixture-revision','c'*40,'--diagnostic-only','false',
              '--transport-probe-only','false']
        with patch.object(sys,'argv',args),patch.object(fixture_proof,'source_guard') as guard, \
             patch.object(fixture_proof,'stage_core') as stage,redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):fixture_proof.main()
        guard.assert_not_called();stage.assert_not_called()

    def test_workflow_write_boundary_and_bare_action_digest_normalization_both_roles(self):
        text=(Path(__file__).parents[3]/'.github/workflows/macos-local-signing-import.yml').read_text()
        for key in ('staging-ingress','fixture-ingress'):
            producer=job(text,key)
            self.assertIn('contents: write\n      actions: read',producer)
            self.assertIn('artifact_digest: "sha256:${{ steps.opaque.outputs.artifact-digest }}"',producer)
            bare='d'*64
            normalized='sha256:${{ steps.opaque.outputs.artifact-digest }}'.replace('${{ steps.opaque.outputs.artifact-digest }}',bare)
            self.assertRegex(normalized,r'^sha256:[a-f0-9]{64}$')
        native=job(text,'verify');reader=job(text,'fixture-read-consumer')
        for consumer in (native,reader):
            self.assertIn('contents: read\n      actions: read',consumer)
            self.assertNotIn('contents: write',consumer)
        self.assertIn('needs: [mac-runner-local-import, staging-ingress]',native)
        main=job(text,'staging-ingress')
        self.assertIn("github.ref == 'refs/heads/main'",main)
        self.assertIn('environment: macos-production-signing',main)
        for key in ('fixture-ingress','fixture-read-consumer'):
            proof=job(text,key)
            self.assertNotIn('environment:',proof);self.assertNotIn('secrets.',proof)
            self.assertNotIn('production-local-signed-',proof)
        for key in ('verify','staging-ingress','mac-runner-local-import','transport-probe'):
            block=job(text,key)
            self.assertIn('!inputs.diagnostic_only',block)
            self.assertIn('!inputs.diagnostic_handoff_proof',block)
        for name in ('staging_release','staging_asset','staging_digest'):
            self.assertIn('required: true',text.split('      '+name+':\n',1)[1].split('        type:',1)[0])


if __name__=='__main__':unittest.main()
