import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).parents[1]))
import recover_native_record as recovery


class RetainedArchiveTest(unittest.TestCase):
    def fixture(self, destination, wrong_tag=False, bad_receipt=False):
        config=b'{"architecture":"arm64","config":{"Labels":{"original":"source"}}}'
        config_hash=hashlib.sha256(config).hexdigest()
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
            values=[{'Config':config_hash+'.json','RepoTags':['localhost:5000/symphony-codex:'+('wrong' if wrong_tag else 'candidate')],'Layers':[]}]
            for name,data in ((config_hash+'.json',config),('manifest.json',json.dumps(values).encode())):
                item=tarfile.TarInfo(name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
        image=buffer.getvalue()
        digest=hashlib.sha256(image).hexdigest()
        payload=destination/'artifact.zip'
        with zipfile.ZipFile(payload,'w') as archive:
            archive.writestr('codex-arm64.docker.tar.gz',image)
            archive.writestr('codex-arm64.sha256',('f'*64 if bad_receipt else digest)+'  ./codex-arm64.docker.tar.gz\n')
            archive.writestr('../../foreign','unselected member must never be extracted')
        service={'digest':'sha256:'+hashlib.sha256(payload.read_bytes()).hexdigest()}
        return payload,service,digest,'sha256:'+config_hash

    def test_actual_opaque_archive_checksum_and_original_config_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            destination=Path(folder)
            payload,service,digest,config=self.fixture(destination)
            archive,actual=recovery.extract(service,payload,destination,'codex','arm64')
            self.assertEqual(actual,digest)
            self.assertEqual(recovery.archive_config(archive,'localhost:5000/symphony-codex:candidate'),config)
            self.assertEqual(sorted(path.name for path in destination.iterdir()),['artifact.zip','codex-arm64.docker.tar.gz'])

    def test_corrupt_service_or_archive_receipts_and_wrong_original_tag_refuse(self):
        for variant in ('service','receipt','tag'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                destination=Path(folder)
                payload,service,_,_=self.fixture(destination,wrong_tag=variant=='tag',bad_receipt=variant=='receipt')
                if variant=='service':
                    service['digest']='sha256:'+'0'*64
                with self.assertRaises(ValueError):
                    archive,_=recovery.extract(service,payload,destination,'codex','arm64')
                    recovery.archive_config(archive,'localhost:5000/symphony-codex:candidate')
