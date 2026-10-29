import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1]))
import artifacts
import component_provenance as provenance


def record(provider="codex", architecture="arm64"):
    source = "b" * 40
    value = {"provider": provider, "architecture": architecture,
             "platform": artifacts.ARCHITECTURES[architecture], "source_revision": source,
             "image": f"ghcr.io/test/{provider}@sha256:" + "c" * 64,
             "input_identity": "d" * 64, "protocol": 1, "image_config": "sha256:"+"9"*64,
             "tool_versions": {tool: "1.0" for tool in (provider, "git", "node", "playwright")},
             "dependencies": {"resolution_policy": "retain-original-qualified-resolution",
                              "base_images": ["base@sha256:" + "a" * 64],
                              "os_packages": {"git": "1.0"}, "build_options": {"TARGETARCH": "arm64" if architecture == "arm64" else "amd64"}}}
    value['tool_versions']['elixir'] = 'Elixir fixture / OTP fixture'
    value["qualification"] = {"repository": provenance.REPOSITORY, "run_id": 123, "run_attempt": 1,
                              "controller_revision": "a" * 40, "job_name": f"runner-{provider}-{architecture}",
                              "artifact_id": 987, "artifact_name": f"runner-candidate-{provider}-{architecture}",
                              "artifact_digest": "sha256:" + "e" * 64,
                              "archive_filename": f"{provider}-{architecture}.docker.tar.gz", "archive_sha256": "f" * 64,
                              **{key: value[key] for key in ("source_revision", "image", "input_identity")}}
    return value


class MetadataReadRecovery(unittest.TestCase):
    @staticmethod
    def failure(status):
        return subprocess.CalledProcessError(1,['gh','api'],stderr=(
            'gh: private-provider-message TOKEN_NEVER_EXPOSE (HTTP '+str(status)+')\n').encode())

    def test_transient_http_errors_recover_with_finite_get_only_backoff(self):
        for status in (502,503,504):
            with self.subTest(status=status),patch.object(provenance.subprocess,'check_output',side_effect=[self.failure(status),b'{"id": 123}']) as request,patch.object(provenance.time,'sleep') as pause:
                self.assertEqual(provenance.api('actions/runs/123'),dict(id=123))
                self.assertEqual(request.call_count,2);pause.assert_called_once_with(1)
                for call in request.call_args_list:
                    self.assertEqual(call.args[0][:4],['gh','api','--method','GET'])
                    self.assertLessEqual(call.kwargs['timeout'],60)

    def test_persistent_502_is_bounded_and_diagnostics_do_not_expose_stderr(self):
        with patch.object(provenance.subprocess,'check_output',side_effect=self.failure(502)) as request,patch.object(provenance.time,'sleep') as pause,self.assertRaisesRegex(ValueError,'http-502') as error:
            provenance.api('actions/runs/123/attempts/1/jobs?per_page=100')
        self.assertEqual(request.call_count,3);self.assertEqual([x.args[0] for x in pause.call_args_list],[1,2])
        self.assertNotIn('TOKEN_NEVER_EXPOSE',str(error.exception));self.assertIsNone(error.exception.__cause__)
        self.assertTrue(error.exception.__suppress_context__)

    def test_auth_permanent_unknown_and_ambiguous_errors_never_retry(self):
        errors=[self.failure(n) for n in (400,401,403,404,429,500)]
        errors += [subprocess.CalledProcessError(1,['gh'],stderr=b'TOKEN_NEVER_EXPOSE'),
                   subprocess.CalledProcessError(1,['gh'],stderr=b'gh: denied (HTTP 403)\ngh: gateway (HTTP 502)\n'),
                   subprocess.CalledProcessError(1,['gh'],stderr=b'gh: gateway (HTTP 502)\n'+b'x'*65536),
                   OSError('TOKEN_NEVER_EXPOSE')]
        for failure in errors:
            with self.subTest(failure=type(failure).__name__),patch.object(provenance.subprocess,'check_output',side_effect=failure) as request,patch.object(provenance.time,'sleep') as pause,self.assertRaises(ValueError) as error:
                provenance.api('actions/runs/123')
            self.assertEqual(request.call_count,1);pause.assert_not_called()
            self.assertNotIn('TOKEN_NEVER_EXPOSE',str(error.exception))

    def test_malformed_json_never_retries_even_after_a_transient_read(self):
        for responses in ([b'TOKEN_NEVER_EXPOSE invalid JSON'],[self.failure(502),b'TOKEN_NEVER_EXPOSE invalid JSON']):
            with self.subTest(reads=len(responses)),patch.object(provenance.subprocess,'check_output',side_effect=responses) as request,patch.object(provenance.time,'sleep'),self.assertRaisesRegex(ValueError,'malformed JSON') as error:
                provenance.api('actions/runs/123')
            self.assertEqual(request.call_count,len(responses));self.assertNotIn('TOKEN_NEVER_EXPOSE',str(error.exception))

    def test_timeout_retry_obeys_one_absolute_120_second_budget(self):
        clock=SimpleNamespace(now=0)
        def timeout(args,**kwargs):
            clock.now+=kwargs['timeout']
            raise subprocess.TimeoutExpired(args,kwargs['timeout'],stderr=b'TOKEN_NEVER_EXPOSE')
        def pause(seconds):clock.now+=seconds
        with patch.object(provenance.time,'monotonic',side_effect=lambda:clock.now),patch.object(provenance.time,'sleep',side_effect=pause),patch.object(provenance.subprocess,'check_output',side_effect=timeout) as request,self.assertRaisesRegex(ValueError,'deadline'):
            provenance.api('actions/runs/123')
        self.assertEqual(clock.now,120);self.assertEqual([c.kwargs['timeout'] for c in request.call_args_list],[60,59])

    def test_short_timeout_can_recover_but_late_success_does_not_authorize(self):
        with patch.object(provenance.subprocess,'check_output',side_effect=[subprocess.TimeoutExpired(['gh'],60),b'{}']) as request,patch.object(provenance.time,'sleep'):
            self.assertEqual(provenance.api('actions/runs/123'),{});self.assertEqual(request.call_count,2)
        clock=SimpleNamespace(now=0)
        def late(*args,**kwargs):clock.now=121;return b'{}'
        with patch.object(provenance.time,'monotonic',side_effect=lambda:clock.now),patch.object(provenance.subprocess,'check_output',side_effect=late),self.assertRaisesRegex(ValueError,'deadline'):
            provenance.api('actions/runs/123')

    def test_binary_archive_reads_keep_one_attempt_without_retry(self):
        for failure in (self.failure(502),subprocess.TimeoutExpired(['gh'],60)):
            with self.subTest(failure=type(failure).__name__),patch.object(provenance,'_BINARY_FLAGS',[]),patch.object(provenance.subprocess,'check_output',side_effect=failure) as request,patch.object(provenance.time,'sleep') as pause,self.assertRaises(ValueError):
                provenance.api('actions/artifacts/123/zip',binary=True)
            self.assertEqual(request.call_count,1);pause.assert_not_called()


class ComponentProofTest(unittest.TestCase):
    def setUp(self):
        self.record = record()
        self.now = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.payload = {"id": 987, "name": "runner-candidate-codex-arm64", "digest": "sha256:" + "e" * 64,
                        "expired": False, "expires_at": "2026-11-01T00:00:00Z", "size_in_bytes": 1234,
                        "workflow_run": {"id": 123, "head_sha": "a" * 40}}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("codex-arm64.json", json.dumps(self.record))
        self.zip = buffer.getvalue()
        self.metadata = dict(self.payload, id=654, name="runner-records-codex-arm64", size_in_bytes=len(self.zip),
                             digest="sha256:" + hashlib.sha256(self.zip).hexdigest())
        self.run = {"id": 123, "head_sha": "a" * 40, "event": "workflow_dispatch",
                    "repository": {"full_name": provenance.REPOSITORY},
                    "head_repository": {"full_name": provenance.REPOSITORY},
                    "path": ".github/workflows/runner-release.yml", "conclusion": "failure"}
        self.jobs = {"total_count": 1, "jobs": [{"name": "runner-codex-arm64", "status": "completed",
                                                "conclusion": "success", "labels": ["ubuntu-24.04-arm"]}]}

    def fetch(self, path, binary=False):
        if path.startswith("compare/"):
            return {"status": "ahead", "merge_base_commit": {"sha": path.split("/")[1].split("...")[0]}}
        return {"actions/runs/123": self.run, "actions/runs/123/attempts/1/jobs?per_page=100": self.jobs,
                "actions/artifacts/987": self.payload,
                "actions/runs/123/artifacts?per_page=100": {"total_count": 1, "artifacts": [self.metadata]},
                "actions/artifacts/654/zip": self.zip}[path]

    def verify(self):
        return provenance.verify_origin(self.record, fetch=self.fetch, now=self.now)

    def test_successful_native_component_survives_unrelated_downstream_failure(self):
        result = self.verify()
        self.assertEqual(result["artifact_id"], 987)
        self.assertEqual(result["record_artifact_id"], 654)

    def test_expired_absent_corrupt_and_foreign_payloads_refuse(self):
        for field, value in (("expired", True), ("size_in_bytes", 0), ("digest", "sha256:" + "0" * 64),
                             ("expires_at", "2026-01-01T00:00:00Z"),
                             ("workflow_run", {"id": 456, "head_sha": "a" * 40})):
            previous = self.payload[field]
            self.payload[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify()
            self.payload[field] = previous

    def test_receipt_does_not_replace_original_available_record(self):
        self.record["tool_versions"]["codex"] = "forged-new-qualification"
        with self.assertRaisesRegex(ValueError, "differs from the original"):
            self.verify()
        self.record = record()
        self.zip += b"corruption"
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.verify()
        self.zip = b"not a ZIP"
        self.metadata["digest"] = "sha256:" + hashlib.sha256(self.zip).hexdigest()
        with self.assertRaisesRegex(ValueError, 'corrupt or unsupported'):
            self.verify()

    def test_wrong_platform_failed_cancelled_or_ambiguous_jobs_refuse(self):
        for value in ("failure", "cancelled", "skipped"):
            self.jobs["jobs"][0]["conclusion"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.verify()
        self.jobs["jobs"][0]["conclusion"] = "success"
        self.jobs["jobs"][0]["labels"] = ["ubuntu-24.04"]
        with self.assertRaisesRegex(ValueError, "native platform"):
            self.verify()
        self.jobs["jobs"][0]["labels"] = ["ubuntu-24.04-arm"]
        self.jobs["jobs"].append(copy.deepcopy(self.jobs["jobs"][0]))
        with self.assertRaises(ValueError):
            self.verify()


class CurrentRunProofTest(unittest.TestCase):
    setUp = ComponentProofTest.setUp
    fetch = ComponentProofTest.fetch
    def test_current_pr_merge_is_ephemeral_and_not_a_future_baseline(self):
        self.run.update(event='pull_request', path='.github/workflows/runner-integration.yml',head_sha='c'*40,
                        head_repository={'full_name':'contributor/untrusted-fork'})
        self.record['source_revision']='a'*40
        self.record['qualification']['source_revision']='a'*40
        self.payload['workflow_run']['head_sha']='c'*40
        self.metadata['workflow_run']={'id':123,'head_sha':'c'*40}
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr('codex-arm64.json',json.dumps(self.record))
        self.zip=buffer.getvalue()
        self.metadata.update(digest='sha256:'+hashlib.sha256(self.zip).hexdigest(),size_in_bytes=len(self.zip))
        def fetch(path,binary=False):
            if path=='commits/'+'a'*40:
                return {'sha':'a'*40,'parents':[{'sha':'c'*40},{'sha':'b'*40}]}
            return self.fetch(path,binary)
        context={'run_id':123,'controller_revision':'a'*40,'event':'pull_request'}
        value=provenance.verify_origin(self.record,fetch=fetch,now=self.now,current_context=context)
        self.assertEqual(value['artifact_id'],987)
        with self.assertRaises(ValueError):
            provenance.verify_origin(self.record,fetch=fetch,now=self.now)
        for changed in (dict(context,run_id=456),dict(context,controller_revision='f'*40),dict(context,event='push')):
            with self.assertRaises(ValueError):
                provenance.verify_origin(self.record,fetch=fetch,now=self.now,current_context=changed)


class NativeSealTest(unittest.TestCase):
    def test_upload_action_bare_digest_normalizes_to_service_digest_strictly(self):
        # Actual upload-artifact output format from failed baseline run39867029678.
        bare = "604dd89e912f8b9213606cbf04aefd7f3ae0d839d67912d83ba32d8f6ebeb0ec"
        self.assertEqual(provenance.artifact_digest(bare), 'sha256:' + bare)
        self.assertEqual(provenance.artifact_digest('sha256:' + bare), 'sha256:' + bare)
        for invalid in ('', bare+'0', bare.upper(), 'sha256:'+bare+'\n', 'sha512:'+bare):
            with self.assertRaises(ValueError):
                provenance.artifact_digest(invalid)

    def test_seal_preserves_actual_reusable_job_and_shared_payload_record_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            native = record()
            (root/'record.json').write_text(json.dumps(native))
            (root/'payload').write_bytes(b'actual archive bytes fixture')
            args = SimpleNamespace(record=str(root/'record.json'), archive=str(root/'payload'),
                                   archive_name='codex-arm64.docker.tar.gz', record_name='previous/codex-arm64.json',
                                   artifact_id=987, artifact_digest='sha256:'+'e'*64, job_name='runner-codex-arm64')
            env = {'GITHUB_REPOSITORY': provenance.REPOSITORY, 'GITHUB_RUN_ID': '123',
                   'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_SHA': 'a'*40}
            inventory = {'total_count': 1, 'jobs': [{'name': 'runners / runner-codex-arm64', 'labels': ['ubuntu-24.04-arm']}]}
            with patch.dict(os.environ, env), patch.object(provenance, 'api', return_value=inventory):
                provenance.seal(args)
            sealed = json.loads((root/'record.json').read_text())
            self.assertEqual(sealed['qualification']['job_name'], 'runners / runner-codex-arm64')
            self.assertEqual(sealed['qualification']['record_filename'], 'previous/codex-arm64.json')
            self.assertEqual(sealed['qualification']['archive_sha256'], hashlib.sha256((root/'payload').read_bytes()).hexdigest())
            for name in artifacts.runner_job_names('codex', 'arm64'):
                inventory['jobs'][0]['name'] = name
                with patch.dict(os.environ, env), patch.object(provenance, 'api', return_value=inventory):
                    provenance.seal(args)
                current = json.loads((root/'record.json').read_text())
                self.assertEqual(current['qualification']['job_name'], name)
                artifacts.validate_component(current, 'codex', 'arm64')
            inventory['jobs'].append(copy.deepcopy(inventory['jobs'][0]))
            with patch.dict(os.environ, env), patch.object(provenance, 'api', return_value=inventory), self.assertRaises(ValueError):
                provenance.seal(args)


class ComponentManifestTest(unittest.TestCase):
    def manifest(self):
        return {"schema_version": 2, "source_revision": "a" * 40, "version": "1.2.4", "channel": "candidate",
                "host_architectures": ["arm64", "x86_64"], "compatibility": {"runner_protocol": 1},
                "bundle": {"filename": "symphony-1.2.4-macos.tar.gz", "sha256": "f" * 64},
                "runners": {provider: {architecture: {k: v for k, v in record(provider, architecture).items()
                                                       if k not in ("provider", "architecture")}
                                       for architecture in artifacts.ARCHITECTURES} for provider in artifacts.PROVIDERS}}

    def test_distinct_manager_runner_sources_and_legacy_strictness(self):
        manifest = self.manifest()
        artifacts.validate(manifest)
        manifest["schema_version"] = 1
        with self.assertRaisesRegex(ValueError, "runner revision differs"):
            artifacts.validate(manifest)

    def test_typed_proof_and_compatibility_cannot_be_weakened(self):
        for mutate in (lambda m: m["compatibility"].update(runner_protocol=True),
                       lambda m: m["compatibility"].update(runner_protocol=2),
                       lambda m: m["runners"]["codex"]["arm64"].update(protocol=True),
                       lambda m: m["runners"]["codex"]["arm64"].update(input_identity="unknown"),
                       lambda m: m["runners"]["codex"]["arm64"]["qualification"].update(run_id=True),
                       lambda m: m["runners"]["codex"]["arm64"]["qualification"].update(run_attempt=1.0),
                       lambda m: m["runners"]["codex"]["arm64"]["qualification"].update(repository="foreign/repo"),
                       lambda m: m["runners"]["codex"]["arm64"]["qualification"].update(source_revision="0" * 40)):
            value = self.manifest()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                artifacts.validate(value)


if __name__ == "__main__":
    unittest.main()
