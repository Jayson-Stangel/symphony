"""Distribution aliases preserve the complete original qualified component."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]))
import artifacts
from test_artifacts import manifest
from test_component_provenance import record


class DistributionTest(unittest.TestCase):
    def fixture(self):
        value=manifest();value.update(schema_version=2,compatibility={'runner_protocol':1})
        value['runners']={provider:{arch:record(provider,arch) for arch in artifacts.ARCHITECTURES} for provider in artifacts.PROVIDERS}
        value['distribution']={provider:{arch:'ghcr.io/original/'+provider+'@'+item['image'].split('@')[1] for arch,item in group.items()} for provider,group in value['runners'].items()}
        return value

    def test_separate_mapping_preserves_original_full_records_and_exact_digests(self):
        value=self.fixture();original=copy.deepcopy(value['runners'])
        artifacts.validate(value)
        self.assertEqual(value['runners'],original)
        for mutate in (lambda v:v['distribution']['codex'].update(arm64='ghcr.io/changed@sha256:'+'a'*64),
                       lambda v:v['distribution']['codex'].pop('arm64'),
                       lambda v:v['distribution'].update(foreign={}),
                       lambda v:v['distribution']['claude'].update(x86_64='ghcr.io/tag:latest'),
                       lambda v:v['runners']['codex']['arm64'].pop('image_config')):
            invalid=copy.deepcopy(value);mutate(invalid)
            with self.assertRaises(ValueError):artifacts.validate(invalid)
