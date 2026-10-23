"""Stage-specific recovery controller fixtures; actual OCI replay is separate native evidence."""
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1]))
import component_provenance as proof
import test_component_provenance as fixtures


class NativeRecoveryTest(fixtures.ComponentProofTest):
    def setUp(self):
        super().setUp()
        self.record['source_revision'] = 'a'*40
        self.record['qualification']['source_revision'] = 'a'*40
        self.original = self.jobs['jobs'][0]
        self.original.update(id=100, conclusion='failure', steps=[
            {'name': proof.BUILD_STEP, 'number': 5, 'status': 'completed', 'conclusion': 'success'},
            {'name': proof.NATIVE_STEP, 'number': 6, 'status': 'completed', 'conclusion': 'success'},
            {'name': 'Run actions/upload-artifact@pinned', 'number': 7, 'status': 'completed', 'conclusion': 'success', 'started_at':'2026-10-22T21:59:00Z','completed_at':'2026-10-22T22:00:00Z'},
            {'name': proof.SEAL_STEP, 'number': 8, 'status': 'completed', 'conclusion': 'failure',
             'started_at': '2026-10-22T22:00:00Z', 'completed_at': '2026-10-22T22:00:01Z'},
            {'name': proof.CLEANUP_STEP, 'number': 10, 'status': 'completed', 'conclusion': 'success'}])
        self.payload['created_at']='2026-10-22T21:59:30Z'
        self.log = b'2026-10-22T22:00:00.500Z component provenance: missing immutable artifact digest; original evidence could not be verified\n'
        self.record['qualification'].update(native_step_recovery=proof.recovery_eligibility(self.original, self.log),
            payload_origin={'run_id':123,'controller_revision':'a'*40},
            record_origin={'run_id':456,'run_attempt':1,'controller_revision':'c'*40,'job_name':'recover-codex-arm64'})
        self.recovery_run = dict(self.run, id=456, head_sha='c'*40, conclusion='success')
        self.recovery_jobs = {'total_count':1,'jobs':[{'name':'recover-codex-arm64','status':'completed','conclusion':'success','labels':['ubuntu-24.04-arm']}]}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr('codex-arm64.json',json.dumps(self.record))
        self.zip = buffer.getvalue()
        self.metadata.update(digest='sha256:'+hashlib.sha256(self.zip).hexdigest(),size_in_bytes=len(self.zip),
                             workflow_run={'id':456,'head_sha':'c'*40})

    def fetch(self,path,binary=False):
        extra={'actions/runs/456':self.recovery_run,'actions/runs/456/attempts/1/jobs?per_page=100':self.recovery_jobs,
               'actions/runs/456/artifacts?per_page=100':{'total_count':1,'artifacts':[self.metadata]},
               'actions/jobs/100/logs':self.log}
        return extra[path] if path in extra else super().fetch(path,binary)

    # Override inherited normal-job cases: these are deliberately different eligibility rules.
    def test_successful_native_component_survives_unrelated_downstream_failure(self):
        value=proof.verify_origin(self.record,fetch=self.fetch,now=self.now,candidate_run=456)
        self.assertEqual(value['original_job_conclusion'],'failure')
        self.assertEqual(value['native_qualification_scope'],'successful-native-step-before-seal-failure')
        self.assertEqual(value['run_id'],123)
        self.assertEqual(value['record_run_id'],456)
        self.assertEqual(value['artifact_id'],987)

    def test_wrong_platform_failed_cancelled_or_ambiguous_jobs_refuse(self):
        for step,conclusion in ((0,'failure'),(1,'cancelled'),(2,'failure'),(4,'failure')):
            before=copy.deepcopy(self.original['steps'])
            self.original['steps'][step]['conclusion']=conclusion
            with self.subTest(step=step,conclusion=conclusion),self.assertRaises(ValueError):
                proof.verify_origin(self.record,fetch=self.fetch,now=self.now,candidate_run=456)
            self.original['steps']=before
        self.recovery_jobs['jobs'][0]['conclusion']='failure'
        with self.assertRaisesRegex(ValueError,'recovery job did not pass'):
            proof.verify_origin(self.record,fetch=self.fetch,now=self.now,candidate_run=456)

    def test_receipt_does_not_replace_original_available_record(self):
        self.record['tool_versions']['codex']='forged'
        with self.assertRaisesRegex(ValueError,'differs from the original'):
            proof.verify_origin(self.record,fetch=self.fetch,now=self.now,candidate_run=456)

    def test_only_known_error_in_actual_failed_step_can_recover(self):
        for log in (b'2026-10-22T21:59:00Z '+proof.SEAL_FORMAT_ERROR.encode(), b'2026-10-22T22:00:00Z unrelated failure'):
            with self.assertRaises(ValueError):
                proof.recovery_eligibility(self.original,log)
        self.original['steps'][3]['name']='Unknown failure'
        with self.assertRaises(ValueError):
            proof.recovery_eligibility(self.original,self.log)
