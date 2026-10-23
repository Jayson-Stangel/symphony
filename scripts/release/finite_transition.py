"""One pinned, exhaustive physical-input transition; never a general path exception."""
import hashlib
from pathlib import Path
import subprocess

from manager_contract import canonical,decode,require
from runner_inputs import identity

AUTHORITY_SHA256='13d8a16cc765fb4844f4a1269fb0192210d794d2f47802f7a705c86602300f36'
SOURCE='68ae23bb195965b96c53b3532d5fab01d7244453'


def policy():
    raw=Path(__file__).with_name('manager-transition-policy.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest()==AUTHORITY_SHA256,'finite transition authority changed; explicit review required')
    value=decode(raw);require(value['source_revision']==SOURCE,'finite transition source differs')
    return value


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE,timeout=30)


def tree(root,revision):
    output={}
    for item in git(root,'ls-tree','-rz',revision).split(b'\0'):
        if not item:continue
        metadata,path=item.split(b'\t',1);mode,kind,blob=metadata.decode().split()
        output[path.decode()]={'mode':mode,'kind':kind,'blob':blob}
    return output


def validate_evidence(evidence,manager):
    p=policy()
    expected={'schema_version':1,'authority_sha256':AUTHORITY_SHA256,'source_revision':SOURCE,
              'manager_source_revision':manager,'source_input_identities':p['source_input_identities'],
              'manager_input_identities':p['manager_input_identities'],'changes':p['changes']}
    require(type(evidence.get('schema_version')) is int and evidence==expected,'input transition is outside the exact reviewed authority')
    return expected


def evaluate(root,records):
    """Return a derived receipt only after the actual committed consumed delta matches."""
    root=Path(root).resolve();p=policy();manager=git(root,'rev-parse','HEAD').decode().strip()
    require(git(root,'rev-parse',SOURCE+'^{tree}').decode().strip()==p['source_tree_oid'],'original source tree unavailable or changed')
    before,after=tree(root,SOURCE),tree(root,'HEAD');paths=set()
    for provider in ('codex','claude'):
        for architecture in ('arm64','x86_64'):
            record=records[provider][architecture]
            require(record['source_revision']==SOURCE and record['input_identity']==p['source_input_identities'][provider][architecture],
                    'selected component does not have the original reviewed actual source/input identity')
            desired=identity(root,provider,architecture)
            require(desired['input_identity']==p['manager_input_identities'][provider][architecture],
                    'runtime, recipe, options, mode or unknown input differs; compatible-original selection refused without building')
            require(sorted(desired['inputs']['files'])==p['source_consumed_paths'][provider][architecture],
                    'consumed input addition/deletion/rename is outside the finite authority')
            paths.update(desired['inputs']['files'])
    actual=[]
    classified={change['path']:change for change in p['changes']}
    for path in sorted(paths):
        require(path in before and path in after,'consumed source inventory missing')
        if before[path]==after[path]:continue
        require(path in classified,'unclassified consumed input change')
        item={'path':path,'classification':classified[path]['classification']}
        for name,entries in (('before',before),('after',after)):
            value=entries[path];require(value['kind']=='blob','consumed input is not an ordinary blob')
            raw=git(root,'cat-file','blob',value['blob'])
            item[name]={'blob':value['blob'],'mode':value['mode'],'sha256':hashlib.sha256(raw).hexdigest()}
        actual.append(item)
    require(actual==p['changes'],'actual changed-input inventory differs from reviewed exact blob pairs')
    evidence={'schema_version':1,'authority_sha256':AUTHORITY_SHA256,'source_revision':SOURCE,
              'manager_source_revision':manager,'source_input_identities':p['source_input_identities'],
              'manager_input_identities':p['manager_input_identities'],'changes':actual}
    return validate_evidence(evidence,manager)
