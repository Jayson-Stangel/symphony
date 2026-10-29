#!/usr/bin/env python3
"""Existing Elixir job phases with one conservative full-coverage reuse seam."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil

import validation_cache as cache
import validation_oci as oci
import stat
import subprocess
import sys
import time

import elixir_coverage_profile as profile
from validation_inputs import UnknownInput, command, controlled_environment, digest
from validation_evidence import (Fresh, Service, REPOSITORY, WORKFLOW, SHA, MAX_ARTIFACT,
                                 current_context, coverage_summary, immutable_tree,
                                 latest_proof, merged_pr_proof, repository, strict_json, tree_controls)


def require(value, message):
    if not value:
        raise ValueError(message)


def locations():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_OS') == 'Linux'
            and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY,
            'coverage controller is limited to the actual repository Linux CI job')
    root = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    require(str(root) == '/home/runner/work/symphony/symphony'
            and temporary == Path('/home/runner/work/_temp'), 'unknown hosted job/source paths')
    source = os.environ['GITHUB_SHA']
    require(SHA.fullmatch(source) and command(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip() == source,
            'checkout is not the actual platform validation source')
    return root, temporary, source


def store(path, value, *, new=False):
    if path.exists():
        require(not path.is_symlink() and path.stat().st_uid == os.getuid()
                and not path.stat().st_mode & 0o077, 'private validation record is unsafe')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_EXCL if new else os.O_TRUNC), 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(value, output, sort_keys=True, indent=2)
        output.write('\n')


def read(path):
    require(not path.is_symlink() and path.is_file() and path.stat().st_uid == os.getuid()
            and not path.stat().st_mode & 0o077 and path.stat().st_size <= 2 * 1024 * 1024,
            'private validation record is unavailable or unsafe')
    return strict_json(path.read_bytes())


def output(**values):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as target:
        for key, value in values.items():
            text = str(value).lower() if type(value) is bool else str(value)
            require('\n' not in text and '\r' not in text, 'unsafe workflow output')
            target.write(key + '=' + text + '\n')


def summary(text):
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as target:
        target.write(text + '\n')


def paths():
    root, temporary, source = locations()
    return root, temporary, source, temporary / 'symphony-coverage-v2', temporary / 'symphony-coverage-selection.json'


def select_profile():
    root, temporary, source, state, selected = paths()
    supported, reason = False, 'changed or unknown validation source; existing ordinary fresh path'
    try:
        _, rows = profile.source_identity(root, source)
        description = strict_json(profile.PROFILE_PATH.read_bytes())
        supported = profile.audited_code(rows) == description['audited_source_sha256']
        if supported:
            reason = 'reviewed full-coverage source profile; fresh unless complete upstream evidence verifies'
    except (UnknownInput, Fresh, OSError, ValueError, KeyError):
        pass
    store(selected, dict(source=source, supported=supported, reason=reason), new=True)
    output(supported=supported, cache_family='exunit-private-v1' if supported else 'mix')
    summary('Coverage preparation: ' + reason + '.')


def client(temporary):
    home = temporary / 'symphony-evidence-api-v1'
    home.mkdir(mode=0o700, exist_ok=True)
    require(not home.is_symlink() and home.stat().st_uid == os.getuid() and not home.stat().st_mode & 0o077,
            'private evidence client directory is unsafe')
    return Service(os.environ.get('GH_TOKEN', ''), home)


def context(service, source):
    number = os.environ.get('COVERAGE_PR_NUMBER', '')
    return current_context(service, run_id=int(os.environ['GITHUB_RUN_ID']),
                           attempt=int(os.environ['GITHUB_RUN_ATTEMPT']), source=source,
                           event=os.environ['GITHUB_EVENT_NAME'], ref=os.environ['GITHUB_REF'],
                           pr_number=int(number) if number else None)


def capability():
    _, temporary, source, _, _ = paths()
    try:
        service = client(temporary)
        value = context(service, source)
        artifacts = service.read('actions/artifacts?per_page=1')
        require(type(artifacts) is dict and type(artifacts.get('artifacts')) is list,
                'artifact read returned malformed metadata')
        store(temporary / 'symphony-evidence-capability.json', dict(available=True, source=source, context=value))
        output(available=True)
        summary('Evidence capability: actual workflow-token run/job/source and artifact metadata GETs succeeded; this is not reuse authorization.')
    except (Fresh, ValueError, KeyError, TypeError, OSError):
        store(temporary / 'symphony-evidence-capability.json', dict(available=False, source=source))
        output(available=False)
        summary('Evidence service unavailable or caller ineligible; full fresh qualification remains required.')


def run(arguments, root, environment, *, log=None, seconds=1200):
    started = time.monotonic()
    if log is None:
        result = subprocess.run(arguments, cwd=root / 'elixir', env=environment, timeout=seconds)
        require(result.returncode == 0, 'fresh qualification command failed')
    else:
        with log.open('xb') as target, subprocess.Popen(arguments, cwd=root / 'elixir', env=environment,
                                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
            received = 0
            try:
                with selectors.DefaultSelector() as ready:
                    ready.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = seconds - (time.monotonic() - started)
                        require(remaining > 0 and ready.select(remaining), 'fresh coverage command timed out')
                        block = os.read(process.stdout.fileno(), 64 * 1024)
                        if not block:
                            break
                        received += len(block)
                        require(received <= 8 * 1024 * 1024, 'fresh coverage output exceeded bound')
                        target.write(block)
                        sys.stdout.buffer.write(block); sys.stdout.buffer.flush()
                require(process.wait(timeout=max(0.1, seconds - (time.monotonic() - started))) == 0,
                        'fresh coverage command failed')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
    return round(time.monotonic() - started, 3)


def oci_setup():
    root, _, _, state, selected = paths()
    if read(selected)['supported']:
        started=time.monotonic();oci.initialize(root,state);oci.preflight(root,state)
        # Bare public tool installation, not project/dependency execution. This
        # precedes warm-cache authentication so actual Hex/Rebar bytes are bound.
        oci.run(root,state,'public-tools',['bash','-euc',
            'cd /home/ci; mix local.hex --force; mix local.rebar --force'],network=True)
        oci.clean_tmp(state)
        summary(f'OCI tool/isolation/tmp/process/loopback preflight EXECUTED before compilation; {time.monotonic()-started:.3f}s. No full-suite proof inferred.')


def phase_run(root,state,phase,args,*,mix='test',network=False,log=None):
    started=time.monotonic()
    raw=oci.run(root,state,phase,args,mix=mix,network=network,capture=log is not None)
    if log is not None:
        require(type(raw) is bytes and len(raw)<=8*1024*1024,'bounded actual phase output unavailable')
        log.write_bytes(raw);os.chmod(log,0o600)
        sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
    oci.clean_tmp(state)
    return round(time.monotonic()-started,3)


def warm():
    root,temporary,source,state,_=paths()
    decision=dict(mode='CLEAN',reason='no authenticated compatible protected-origin cache')
    try:
        envelope=read(temporary/'symphony-trusted-coverage-v1'/'trusted-source.json')
        service=client(temporary);caller=context(service,source)
        require(caller.get('eligible_consumer') or caller.get('eligible_producer'), 'foreign/untrusted caller')
        require(caller['trusted_dev_revision']==envelope['trusted_dev_revision'],'protected warm controller changed')
        _,tested_tree=immutable_tree(service,source)
        require(tree_controls(tested_tree)==envelope['controls'],'warm actual tested controllers differ')
        # Re-observe actual immutable image + public runtime through protected
        # code before installing any cache bytes supplied by another run.
        oci.preflight(root,state)
        execution=profile.execution_identity(root,source,state)
        candidate=dict(identity=dict(execution=execution));candidate['sha256']=digest(candidate['identity'])
        if os.environ['GITHUB_EVENT_NAME']=='push' and os.environ['GITHUB_REF']=='refs/heads/dev':
            result=merged_pr_proof(service,source,candidate,strict_json(profile.PROFILE_PATH.read_bytes()),
                envelope['controls'],caller['repository_id'],warm=True)
        else:
            result=latest_proof(service,candidate,strict_json(profile.PROFILE_PATH.read_bytes()),envelope['controls'],
                consumer_repository_id=caller['repository_id'],trusted_dev_revision=caller.get('proof_revision',caller['trusted_dev_revision']),warm=True)
        install_bundle(root,state,result)
        (state/'seed.zip').write_bytes(result.pop('bundle_bytes'));os.chmod(state/'seed.zip',0o600)
        decision=dict(mode='RESTORED',artifact_id=result['artifact_id'],artifact_digest=result['artifact_digest'],
                      original=result['original'],original_run_url=result['original_run_url'])
    except (Fresh,UnknownInput,OSError,ValueError,KeyError,TypeError,AttributeError):
        pass
    store(state/'warm.json',decision,new=True)
    summary('Validation build input cache: '+decision['mode']+'. This is not reused qualification.')


def install_bundle(root,state,result):
    # No phase container/Mix is running here. Validate and install authenticated
    # deps first so the finite raw dev/test links resolve against their actual
    # authenticated targets. All three validate before any cached code executes.
    targets={'deps':root/'elixir/deps','dev':root/'elixir/_build/dev','test':root/'elixir/_build/test'}
    staged={};backups={};installed=[]
    def stage(name):
        target=targets[name];path=target.with_name('.'+target.name+'-evidence-v2')
        require(not path.exists() and not path.is_symlink(),'cache staging already exists')
        staged[name]=path
        cache.stage(result['sidecar_bytes'][name],result['sidecar'][name],path,root)
    def install(name):
        target=targets[name];backup=target.with_name('.'+target.name+'-fresh-v2')
        require(target.is_dir() and not target.is_symlink() and not backup.exists()
                and not backup.is_symlink(),'cache swap is not exclusively owned')
        target.rename(backup);backups[name]=backup
        try:staged[name].rename(target)
        except OSError:backup.rename(target);backups.pop(name);raise
        installed.append(name)
    try:
        stage('deps');install('deps')
        stage('dev');stage('test')
        install('dev');install('test')
    except (Fresh,UnknownInput,OSError,ValueError,KeyError,TypeError):
        for name in reversed(installed):
            targets[name].rename(staged[name]);backups[name].rename(targets[name])
        raise
    finally:
        for path in staged.values():
            if path.exists():shutil.rmtree(path)
    for path in backups.values():shutil.rmtree(path)


def prepare():
    root,_,source,state,selected=paths();selection=read(selected)
    require(selection['source']==source,'source selection changed')
    if selection['supported'] is not True:
        state.mkdir(mode=0o700);store(state/'prepared.json',dict(source=source,supported=False,ordinary=True));return
    if not (state/'warm.json').exists():store(state/'warm.json',dict(mode='CLEAN',reason='immutable seed verifier unavailable'),new=True)
    environment=oci.environment();profile.public_environment(environment)
    development=phase_run(root,state,'prepare',['make','setup','build','fmt-check','lint','claude-setup'],mix='dev',network=True)
    oci.clean_hex(state)
    compiled=phase_run(root,state,'test-compile',['mix','compile'])
    phase_run(root,state,'native-manifest',['mix','run','--no-start','-e',
        'ExUnit.FailuresManifest.write!(ExUnit.FailuresManifest.new(), Path.join(Mix.Project.manifest_path(), ".mix_test_failures"))'])
    store(state/'environment.json',environment,new=True)
    store(state/'prepared.json',dict(source=source,supported=True,ordinary=False),new=True)
    summary(f'EXECUTED: fresh setup/build/format/lint/npm {development}s; test compile {compiled}s; pinned ExUnit native empty-manifest initialization. Hosted scheduler defaults retained.')


def measure():
    root,_,source,state,_=paths();prepared=read(state/'prepared.json')
    require(prepared['source']==source,'prepared source changed')
    if prepared['ordinary']:output(available=False);return
    # A supported OCI profile that cannot describe its actual inputs refuses
    # here before spending another full suite/PLT run on an unprovable profile.
    started=time.monotonic();measured=profile.fingerprint(root,source,read(state/'environment.json'),os.environ)
    raw,metadata=cache.create(root/'elixir/_build/test',root)
    cache_row=next(row for row in measured['identity']['public_inputs'] if row['path']==str(root/'elixir/_build/test'))
    require(metadata['cache']==cache_row,'test cache changed after complete measurement')
    (state/'test-cache.tar.gz').write_bytes(raw);os.chmod(state/'test-cache.tar.gz',0o600)
    store(state/'sidecar.json',metadata,new=True);store(state/'inputs.json',measured,new=True)
    output(available=True,input_sha256=measured['sha256'])
    summary(f"Complete OCI/public mutable input identity: {measured['files']} entries/{measured['bytes']} bytes; fingerprint+gzip1 checkpoint {time.monotonic()-started:.3f}s. No coverage reuse inferred.")


def decide():
    root,temporary,source,state,_=paths();started=time.monotonic()
    decision=dict(mode='EXECUTED',reason='full fresh qualification required')
    try:
        envelope=read(temporary/'symphony-trusted-coverage-v1'/'trusted-source.json')
        service=client(temporary);caller=context(service,source)
        if caller['eligible_consumer'] and (state/'inputs.json').exists():
            require(caller['trusted_dev_revision']==envelope['trusted_dev_revision'],'protected controller changed')
            _,tree=immutable_tree(service,source)
            require(tree_controls(tree)==envelope['controls'],'actual validation controllers differ')
            if (state/'seed.zip').is_file():
                seed=read(state/'warm.json')
                service.binary_cache={'actions/artifacts/'+str(seed['artifact_id'])+'/zip':(state/'seed.zip').read_bytes()}
            inputs=profile.fingerprint(root,source,read(state/'environment.json'),os.environ)
            if os.environ['GITHUB_EVENT_NAME']=='push' and os.environ['GITHUB_REF']=='refs/heads/dev':
                decision=merged_pr_proof(service,source,inputs,strict_json(profile.PROFILE_PATH.read_bytes()),envelope['controls'],caller['repository_id'])
            else:
                decision=latest_proof(service,inputs,strict_json(profile.PROFILE_PATH.read_bytes()),envelope['controls'],
                    consumer_repository_id=caller['repository_id'],trusted_dev_revision=caller.get('proof_revision',caller['trusted_dev_revision']))
            for key in ('sidecar_bytes','sidecar','original_inputs','bundle_bytes'):decision.pop(key,None)
    except (Fresh,UnknownInput,OSError,ValueError,KeyError,TypeError,AttributeError):
        decision=dict(mode='EXECUTED',reason='trusted complete original evidence unavailable or incompatible')
    decision['verification_seconds']=round(time.monotonic()-started,3)
    store(state/'decision.json',decision,new=True);output(mode=decision['mode'],origin=decision.get('original_run_url',''))
    summary('Coverage decision: '+decision['mode']+f"; verification {decision['verification_seconds']}s; original {decision.get('original_run_url','none')}.")


def execute():
    root,_,source,state,_=paths();prepared=read(state/'prepared.json')
    require(prepared['source']==source,'prepared source changed')
    if prepared['ordinary']:
        environment=dict(os.environ);environment.pop('GH_TOKEN',None);environment.pop('GITHUB_TOKEN',None)
        seconds=run(['make','elixir-ci'],root,environment,log=state/'coverage.log')
    else:
        seconds=phase_run(root,state,'coverage',['mix','test','--cover','--seed','0','--max-failures','1'],log=state/'coverage.log')
    store(state/'execution.json',dict(mode='EXECUTED',exit_code=0,seconds=seconds,
        coverage_log_sha256=hashlib.sha256((state/'coverage.log').read_bytes()).hexdigest()),new=True)
    output(mode='EXECUTED',origin='');summary(f'EXECUTED: actual full qualification {seconds}s; other stages remain required.')


def reused():
    _,_,_,state,_=paths();decision=read(state/'decision.json');require(decision['mode']=='REUSED','verified decision missing')
    output(mode='REUSED',origin=decision['original_run_url'])
    summary(f"REUSED original {decision['original_run_url']} job{decision['original']['job_id']} artifact{decision['artifact_id']} {decision['artifact_digest']}; no current test execution claimed. Fresh quality and Dialyzer remain required.")


def dialyzer():
    root,_,source,state,_=paths();prepared=read(state/'prepared.json')
    if not prepared['ordinary']:
        seconds=phase_run(root,state,'dialyzer',['make','dialyzer'],mix='dev')
        summary(f'EXECUTED fresh ordinary Dialyzer {seconds}s, including any PLT rebuild; no --no-check.')
    store(state/'dialyzer.json',dict(source=source,success=True),new=True)


def seal():
    root,temporary,source,state,_=paths();available=False
    try:
        require(read(state/'prepared.json')==dict(source=source,supported=True,ordinary=False),'unsupported original')
        require(read(state/'dialyzer.json')==dict(source=source,success=True),'fresh Dialyzer missing')
        execution=read(state/'execution.json');require(execution['mode']=='EXECUTED' and execution['exit_code']==0,'not original executed qualification')
        inputs=read(state/'inputs.json');require(inputs['sha256']==digest(inputs['identity']),'producer identity invalid')
        execution['summary']=coverage_summary((state/'coverage.log').read_bytes(),strict_json(profile.PROFILE_PATH.read_bytes()))
        caller=context(client(temporary),source);require(caller['eligible_producer'],'ineligible original producer')
        sides={'test':read(state/'sidecar.json')}
        require(cache.identity(root/'elixir/_build/test',root)==sides['test']['cache'],'precoverage test cache changed')
        raw={'test':(state/'test-cache.tar.gz').read_bytes()}
        for name,path in (('deps',root/'elixir/deps'),('dev',root/'elixir/_build/dev')):raw[name],sides[name]=cache.create(path,root)
        require(sum(len(value) for value in raw.values())<=cache.MAX_COMPRESSED
                and sum(value['cache']['bytes'] for value in sides.values())<=cache.MAX_EXPANDED
                and sum(value['cache']['files'] for value in sides.values())<=cache.MAX_ENTRIES,'combined checkpoint exceeds bounds')
        proof=state/'proof';proof.mkdir(mode=0o700)
        store(proof/'receipt.json',dict(schema_version=3,family='full-exunit-coverage',inputs_sha256=inputs['sha256'],
            sidecar=sides,checkpoints=dict(test='PRE_COVERAGE',deps='POST_DIALYZER',dev='POST_DIALYZER'),
            cache_origin=read(state/'warm.json'),producer=dict(repository=REPOSITORY,repository_id=caller['repository_id'],
            run_id=caller['run_id'],attempt=caller['attempt'],job_id=caller['job_id'],source=source,workflow=WORKFLOW),execution=execution),new=True)
        store(proof/'inputs.json',inputs,new=True);(proof/'coverage.log').write_bytes((state/'coverage.log').read_bytes())
        for name,value in raw.items():(proof/(name+'-cache.tar.gz')).write_bytes(value)
        available=True
    except (Fresh,UnknownInput,OSError,ValueError,KeyError,TypeError,AttributeError):
        summary('No reusable original receipt: incomplete/changed input or checkpoint; qualification execution status is unchanged.')
    output(available=available)


def cleanup():
    _,_,_,state,_=paths()
    oci.cleanup(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('select','capability','oci_setup','warm','prepare','measure','decide','execute','reused','dialyzer','seal','cleanup'))
    phase = parser.parse_args().phase
    globals()[dict(select='select_profile').get(phase, phase)]()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        print('Full Elixir qualification controller refused an incomplete/failed phase.', file=sys.stderr)
        raise SystemExit(1)
