"""Narrow manual bootstrap of one original prior; fresh proof never rewrites history."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import zipfile

from artifacts import require

ORIGINAL = dict(run_id=40588897389, run_attempt=3,
                source_revision='ec2be6e1c21c7dc7f7d7d8536bcac1ce06f3202b',
                package_artifact_id=12774063336,
                package_artifact_digest='sha256:6347428eaa08c972eb43090af41460c0d73c9d84776351f2b91c80d407bdc446',
                lifecycle_job_id=122501814533)
OWNER = 'Jayson-Stangel'
PENDING = 'UNQUALIFIED_PENDING'


def descriptor(raw, env=None):
    if not raw:
        return None
    env = os.environ if env is None else env
    require(isinstance(raw, str) and len(raw) <= 2048, 'bootstrap descriptor invalid or oversized')
    value = json.loads(raw)
    require(isinstance(value, dict) and set(value) == set(ORIGINAL) | {'candidate_source_revision'},
            'bootstrap descriptor fields differ')
    require(all(type(value[k]) is type(v) and value[k] == v for k, v in ORIGINAL.items()),
            'bootstrap original subject differs')
    require(re.fullmatch('[a-f0-9]{40}', value['candidate_source_revision'] or '') is not None
            and value['candidate_source_revision'] == env.get('GITHUB_SHA'), 'bootstrap candidate source differs')
    require(env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and env.get('GITHUB_REF') == 'refs/heads/dev'
            and env.get('GITHUB_REF_TYPE') == 'branch' and env.get('GITHUB_REPOSITORY') == OWNER+'/symphony'
            and env.get('GITHUB_ACTOR') == OWNER and env.get('GITHUB_TRIGGERING_ACTOR') == OWNER,
            'bootstrap requires an exact owner manual dev candidate')
    require(env.get('PREPARE_RELEASE', 'false') == 'false' and env.get('COMPOSE_PACKAGES', 'true') == 'true'
            and env.get('MANAGER_ONLY_UPDATE', 'false') == 'false'
            and not env.get('PREVIOUS_REVISION') and not env.get('LEGACY_ARTIFACT_RUN')
            and env.get('PREVIOUS_ARTIFACT_RUN') == str(ORIGINAL['run_id']),
            'bootstrap is only a nonproduction two-source candidate with the pinned prior')
    require(all(re.fullmatch('[1-9][0-9]*',env.get(k,'') or '') for k in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')),
            'bootstrap current run identity missing')
    require(env.get('GITHUB_WORKFLOW_REF') == OWNER+'/symphony/.github/workflows/burrito-release.yml@refs/heads/dev',
            'bootstrap requires the combined dev candidate controller')
    return value


def successful(stage, name):
    found = [s for s in stage.get('steps', []) if s.get('name') == name]
    require(len(found) == 1 and found[0].get('status') == 'completed' and found[0].get('conclusion') == 'success',
            'native provenance required step did not succeed: '+name)
    return found[0]


NATIVE_GUARD = '''test "$(uname -s)/$(uname -m)" = Darwin/arm64
python3 -c 'import sys; assert sys.version_info >= (3, 11), "native Mac checks require Python 3.11+"'
if [ "$BACKUP_MAC_SELECTED" = true ]; then
  case "$(sw_vers -productVersion)" in 14.*) ;; *) echo 'Backup guest must be macOS 14' >&2; exit 1 ;; esac
fi'''


def owned_mac_job(stage, source, root, helper=False):
    """Positive executed native guard at its exact trusted source, never label inference."""
    require(stage.get('status') == 'completed' and stage.get('conclusion') == 'success', 'native job failed')
    workflow = 'package-checks' if helper else 'runner-release'
    text = subprocess.check_output(['git', '-C', str(root), 'show', source+':.github/workflows/'+workflow+'.yml'], text=True)
    key = 'native-host-package' if helper else 'packaged-candidate'
    jobs = text.split('  '+key+':\n')
    require(len(jobs)==2,'native source job missing or ambiguous')
    body = re.split(r'\n  [a-z][a-z0-9-]*:',jobs[1],maxsplit=1)[0]
    blocks = body.split('      - name: Require actual native Mac prerequisites\n')
    require(len(blocks) == 2, 'native source guard missing or ambiguous')
    block = blocks[1].split('\n      - name:', 1)[0]
    require("if: matrix.platform == 'macos'" in block and '        run: |\n' in block, 'native guard scope differs')
    script = '\n'.join(line[10:] for line in block.split('        run: |\n', 1)[1].splitlines() if line.strip()).strip()
    require(script == NATIVE_GUARD, 'native source guard is not the audited OS/CPU/version assertion')
    guard = successful(stage, 'Require actual native Mac prerequisites')
    build = successful(stage, 'Build actual native macOS ARM64 helper' if helper else 'Build source-bound native candidate archives')
    verify = successful(stage, 'Native source-independent shared helper contracts' if helper else 'Verify qualified helper source, platform and checksum')
    require(guard['number'] < build['number'] and (build['number'] < verify['number'] if helper else verify['number'] < build['number']),
            'native source/helper execution order differs')


def mac_helper_receipt(raw, item, source):
    require(isinstance(raw, bytes) and len(raw) <= 2*1024*1024
            and 'sha256:'+hashlib.sha256(raw).hexdigest() == item['digest'], 'native helper service digest differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(sorted(archive.namelist()) == ['symphony-host', 'symphony-host.sha256']
                and sum(i.file_size for i in archive.infolist()) <= 4*1024*1024, 'native helper receipt inventory differs')
        binary = archive.read('symphony-host'); receipt = archive.read('symphony-host.sha256').decode()
    digest = hashlib.sha256(binary).hexdigest()
    require(re.fullmatch(digest+r'  (?:\./)?symphony-host\s*', receipt) is not None, 'native helper inner checksum differs')
    require(len(binary) >= 32 and binary[:4] == bytes.fromhex('cffaedfe')
            and struct.unpack('<I', binary[4:8])[0] == 0x100000c, 'native helper is not Mach-O ARM64')
    require(source.encode() in binary, 'native helper lacks its exact compiled source identity')
    return dict(artifact_id=item['id'], artifact_digest=item['digest'], helper_sha256=digest,
                source_revision=source, platform='macos-arm64', validation='bytes only; original successful source/helper steps bind execution')


def initial_proof(subject, candidate, installed_source, doctor, projects):
    """Called only after actual initial install/doctor/listener/image/canary checks."""
    require(subject.get('baseline_status') == PENDING, 'bootstrap subject is not pending')
    require(installed_source == subject['source_revision'], 'initial baseline installed source differs')
    require(doctor.get('ready') is True, 'initial baseline doctor failed')
    require(len(projects) == 2 and all(p.get('listener') is True and p.get('image_bound') is True
                                     and p.get('canaries') is True for p in projects), 'initial baseline listener/image/canary proof missing')
    actual = dict(run_id=subject['run_id'],run_attempt=subject['run_attempt'],source_revision=subject['source_revision'],
                  package_artifact_id=subject['package_artifact']['id'],package_artifact_digest=subject['package_artifact']['digest'],
                  lifecycle_job_id=subject['lifecycle_job'])
    require(actual == ORIGINAL and subject.get('bootstrap_descriptor',{}).get('candidate_source_revision') == candidate,
            'initial baseline original descriptor differs')
    return dict(original=actual, candidate_source_revision=candidate,
                current_run_id=os.environ.get('GITHUB_RUN_ID'), current_run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),
                baseline_status=PENDING, initial_source_revision=installed_source,
                initial_install_repeat=True, doctor_ready=True, projects=projects)


def completed_proof(result, subject, candidate):
    actual = dict(run_id=subject.get('run_id'),run_attempt=subject.get('run_attempt'),source_revision=subject.get('source_revision'),
                  lifecycle_job_id=subject.get('lifecycle_job'),package_artifact_id=subject.get('package_artifact',{}).get('id'),
                  package_artifact_digest=subject.get('package_artifact',{}).get('digest'))
    require(actual == ORIGINAL, 'fresh baseline original artifact identity differs')
    proof = result.get('bootstrap_initial')
    require(isinstance(proof, dict) and proof.get('original') == ORIGINAL
            and proof.get('candidate_source_revision') == candidate and result.get('source_revision') == candidate
            and proof.get('current_run_id') == os.environ.get('GITHUB_RUN_ID')
            and proof.get('current_run_attempt') == os.environ.get('GITHUB_RUN_ATTEMPT'), 'fresh baseline identity/run proof missing')
    require(result.get('passed') is True and result.get('mode') == 'two-revision-lifecycle'
            and result.get('cleanup', {}).get('errors') == [] and result.get('cleanup', {}).get('verified_absent'),
            'fresh baseline full lifecycle or cleanup failed')
    require(result.get('bootstrap_transition_complete') is True and proof.get('initial_install_repeat') is True
            and proof.get('doctor_ready') is True and proof.get('initial_source_revision') == ORIGINAL['source_revision']
            and len(proof.get('projects', [])) == 2
            and all(p.get('listener') is True and p.get('image_bound') is True and p.get('canaries') is True for p in proof['projects']),
            'fresh baseline initial/transition proof incomplete')
    require(subject.get('baseline_status') == PENDING and subject.get('bootstrap_descriptor', {}).get('candidate_source_revision') == candidate,
            'pending original baseline descriptor missing')
    return dict(proof, status='FRESH_BASELINE_QUALIFIED_BY_CURRENT_LIFECYCLE', historical_lifecycle_conclusion='skipped')


def package_inventory(archive, source, platform="linux_x86_64", expected_manifest=None):

    """Validate inner installed bytes before executing even an original installer."""
    from signing_payload import bundle_files
    files = bundle_files(archive)
    require('files.sha256' in files and 'release.json' in files and 'host-platform' in files and 'libexec/symphony-host' in files,
            'bootstrap package source/inventory missing')
    expected = {name:hashlib.sha256(value[0]).hexdigest() for name,value in files.items() if name != 'files.sha256'}
    observed = {}
    for line in files['files.sha256'][0].decode().splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  \./(.+)',line)
        require(match is not None and match[2] not in observed, 'bootstrap package inventory malformed')
        observed[match[2]] = match[1]
    require(expected == observed, 'bootstrap package inner checksum differs')
    from manager_envelope import normalize_document
    info = json.loads(files['release.json'][0])
    if 'manager_envelope' in info:
        require('manager-core.json' in files, 'bootstrap manager core missing')
        info = normalize_document(info,files['manager-core.json'][0])
    require(info.get('source_revision') == source and files['host-platform'][0].strip() == platform.encode(),
            'bootstrap package source/platform differs')
    if expected_manifest is not None:
        require(all(info.get(k)==expected_manifest.get(k) for k in ('source_revision','version','channel','runners')),
                'bootstrap inner manager/component manifest differs')
    helper = files['libexec/symphony-host'][0]
    require(source.encode() in helper, 'bootstrap helper compiled source differs')
    if platform == 'linux_x86_64':
        require(len(helper)>=20 and helper[:4]==b'\x7fELF' and helper[4:6]==b'\x02\x01'
                and struct.unpack('<H',helper[18:20])[0]==62, 'bootstrap helper is not Linux ELF x86-64')
    return files


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['validate']:
        descriptor(os.environ.get('BOOTSTRAP_PREVIOUS',''))
    elif len(sys.argv) == 3 and sys.argv[1] == 'verify-result':
        value = descriptor(os.environ.get('BOOTSTRAP_PREVIOUS',''))
        require(value is not None, 'bootstrap descriptor missing')
        root = Path(sys.argv[2])
        subject = json.loads((root/'previous-package-subject.json').read_text())
        result = json.loads((root/'result.json').read_text())
        output = completed_proof(result,subject,os.environ['GITHUB_SHA'])
        (root/'fresh-baseline-qualification.json').write_text(json.dumps(output,indent=2)+'\n')
    else:
        raise SystemExit('expected validate or verify-result EVIDENCE')
