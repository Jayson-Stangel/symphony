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
import traceback

import elixir_coverage_profile as profile
from validation_inputs import UnknownInput, command, controlled_environment, digest, file_identity
from validation_evidence import (Fresh, Service, REPOSITORY, WORKFLOW, SHA, MAX_ARTIFACT,
                                 current_context, coverage_summary, immutable_tree,
                                 latest_proof, merged_pr_proof, repository, strict_json, tree_controls, validate_input_profile, difference_facets)


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
    _, temporary, source, _, selected = paths()
    selection = read(selected)
    supported = selection.get('supported') is True
    # Environment selects the capability probes, never reuse authorization.
    # Actual API caller metadata still supplies every eligibility decision.
    required = supported and (
        (os.environ.get('GITHUB_EVENT_NAME') == 'push'
         and os.environ.get('GITHUB_REF') in ('refs/heads/dev', 'refs/heads/main'))
        or (os.environ.get('GITHUB_EVENT_NAME') == 'pull_request'
            and os.environ.get('COVERAGE_HEAD_REPOSITORY') == REPOSITORY))
    stage = 'client'
    try:
        service = client(temporary)
        stage = 'caller_context'
        value = context(service, source)
        if required:
            stage = 'caller_eligibility'
            require(value['eligible_consumer'] or value['eligible_producer'],
                    'supported caller is not actually eligible')
            revision = value['trusted_dev_revision']
            require(type(revision) is str and SHA.fullmatch(revision),
                    'authenticated protected dev revision unavailable')
            stage = 'protected_self_compare'
            comparison = service.read('compare/' + revision + '...' + revision)
            require(type(comparison) is dict and comparison.get('status') == 'identical'
                    and type(comparison.get('merge_base_commit')) is dict
                    and comparison['merge_base_commit'].get('sha') == revision,
                    'protected dev self comparison differs')
        stage = 'artifact_metadata'
        artifacts = service.read('actions/artifacts/0')  # Temporary hosted unavailable-evidence experiment
        require(type(artifacts) is dict and type(artifacts.get('artifacts')) is list,
                'artifact read returned malformed metadata')
        store(temporary / 'symphony-evidence-capability.json', dict(available=True, source=source, context=value,
              protected_compare=required))
        output(available=True, supported=supported)
        summary('Evidence capability: actual workflow-token run/job/source and artifact metadata GETs succeeded; protected self comparison=' + str(required).lower() + '; this is not reuse authorization.')
    except (Fresh, ValueError, KeyError, TypeError, OSError) as error:
        # Diagnose the exact local guard without printing provider payloads,
        # credentials, environment values or arbitrary exception text.
        sites = [frame for frame in traceback.extract_tb(error.__traceback__)
                 if Path(frame.filename).name in ('elixir_coverage.py', 'validation_evidence.py')
                 and frame.name != 'require']
        site = sites[-1] if sites else None
        diagnostic = dict(stage=stage, error=type(error).__name__,
                          site=(Path(site.filename).name + ':' + site.name + ':' + str(site.lineno)) if site else 'unavailable')
        store(temporary / 'symphony-evidence-capability.json', dict(available=False, source=source,
              reason='fresh_fallback', diagnostic=diagnostic))
        # Select the existing ordinary setup and make elixir-ci path before any
        # OCI preparation, controller loading or protected cache restoration.
        selection.update(supported=False, reason='evidence service unavailable or caller ineligible; ordinary fresh qualification')
        store(selected, selection)
        output(available=False, supported=False)
        summary('Evidence capability unavailable; stage=' + diagnostic['stage'] + '; error=' + diagnostic['error']
                + '; site=' + diagnostic['site'] + '. Ordinary full fresh qualification remains required; reuse is disabled.')


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
        decision=dict(mode='RESTORED',consumer_eligible=caller['eligible_consumer'],artifact_id=result['artifact_id'],artifact_digest=result['artifact_digest'],
                      original=result['original'],original_run_url=result['original_run_url'])
    except (Fresh,UnknownInput) as error:
        decision['reason']=str(error)
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        decision['reason']='unavailable or malformed public evidence'
    store(state/'warm.json',decision,new=True)
    summary('Validation build input cache: '+decision['mode']+'; '+decision.get('reason','authenticated original public checkpoints')+'. This is not reused qualification.')


def install_bundle(root,state,result):
    # No phase container/Mix is running here. Validate and install authenticated
    # deps first so the finite raw dev/test links resolve against their actual
    # authenticated targets. All four validate before any cached code executes.
    targets={'deps':root/'elixir/deps','hex':state/'hex','dev':root/'elixir/_build/dev','test':root/'elixir/_build/test'}
    staged={};backups={};installed=[]
    def stage(name):
        target=targets[name];path=target.with_name('.'+target.name+'-evidence-v2')
        require(not path.exists() and not path.is_symlink(),'cache staging already exists')
        staged[name]=path
        cache.stage(result['sidecar_bytes'][name],result['sidecar'][name],path,root,**(dict(private_root=state) if name=='hex' else {}))
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
        stage('hex');stage('dev');stage('test')
        install('hex');install('dev');install('test')
    except (Fresh,UnknownInput,OSError,ValueError,KeyError,TypeError):
        for name in reversed(installed):
            targets[name].rename(staged[name]);backups[name].rename(targets[name])
        raise
    finally:
        for path in staged.values():
            if path.exists():shutil.rmtree(path)
    for path in backups.values():shutil.rmtree(path)


def compile_test_inputs(root,state):
    online=phase_run(root,state,'test-dependencies-online',['mix','compile'],network=True)
    oci.clean_native_fetch(state)
    compiled=phase_run(root,state,'test-compile',['mix','compile'])
    phase_run(root,state,'native-manifest',['mix','run','--no-start','-e',
        'ExUnit.FailuresManifest.write!(ExUnit.FailuresManifest.new(), Path.join(Mix.Project.manifest_path(), ".mix_test_failures"))'])
    summary(f'EXECUTED TEST preparation: online compile {online}s; offline guard {compiled}s; native empty-manifest initialization. No compiler/source bytes or timestamps normalized.')


def prepare():
    root,_,source,state,selected=paths();selection=read(selected)
    require(selection['source']==source,'source selection changed')
    if selection['supported'] is not True:
        state.mkdir(mode=0o700);store(state/'prepared.json',dict(source=source,supported=False,ordinary=True));return
    if not (state/'warm.json').exists():store(state/'warm.json',dict(mode='CLEAN',reason='immutable seed verifier unavailable'),new=True)
    environment=oci.environment();profile.public_environment(environment)
    development=phase_run(root,state,'prepare',['make','setup','build','fmt-check','lint','claude-setup'],mix='dev',network=True)
    phase_run(root,state,'offline-resolution',['mix','deps.get'],mix='dev')
    warm=read(state/'warm.json')
    pending=warm.get('mode')=='RESTORED' and warm.get('consumer_eligible') is True
    if not pending:compile_test_inputs(root,state)
    store(state/'environment.json',environment,new=True)
    store(state/'prepared.json',dict(source=source,supported=True,ordinary=False,test_inputs='PENDING' if pending else 'FRESH'),new=True)
    summary(f'EXECUTED fresh DEV setup/build/format/lint/npm {development}s and offline dependency validation. TEST inputs: '+('authenticated retained cache pending full comparison; no TEST compile/init yet.' if pending else 'freshly compiled; full suite required.'))


CHECKPOINT_FILES=('inputs.json','sidecar.json','hex-sidecar.json','test-cache.tar.gz','hex-cache.tar.gz')


def checkpoint_start(root,source,state,*,replace=False):
    # Fallback may replace only these five exact owned host records, outside
    # container binds. Any incomplete write refuses the immediate execution gate.
    if replace:
        for name in CHECKPOINT_FILES:
            value=file_identity(state/name)
            require(value['uid']==os.getuid() and value['gid']==os.getgid() and value['mode']==0o600,
                    'provisional checkpoint replacement is not exact owned state')
    oci.require_empty_native_fetch(state)
    inventory=cache.precheck([root/'elixir/deps',root/'elixir/_build/dev',root/'elixir/_build/test',state/'hex'],root,private_root=state)
    summary(f"Early four-checkpoint bound: {inventory['entries']} entries/{inventory['bytes']} raw bytes; final post-PLT bounds remain required.")
    started=time.monotonic();measured=profile.fingerprint(root,source,read(state/'environment.json'),os.environ)
    for name,target,record in (('test',root/'elixir/_build/test','sidecar.json'),('hex',state/'hex','hex-sidecar.json')):
        raw,metadata=cache.create(target,root,**(dict(private_root=state) if name=='hex' else {}))
        row=next(row for row in measured['identity']['public_inputs'] if row['path']==str(target))
        require(metadata['cache']==row,name+' cache changed after complete measurement')
        fd=os.open(state/(name+'-cache.tar.gz'),os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|(os.O_TRUNC if replace else os.O_EXCL),0o600)
        with os.fdopen(fd,'wb') as output:output.write(raw)
        store(state/record,metadata,new=not replace)
    store(state/'inputs.json',measured,new=not replace)
    summary(f"Complete OCI/public mutable input identity: {measured['files']} entries/{measured['bytes']} bytes; fingerprint+gzip1 checkpoint {time.monotonic()-started:.3f}s. No coverage reuse inferred.")
    return measured


def measure():
    root,_,source,state,_=paths();prepared=read(state/'prepared.json')
    require(prepared['source']==source,'prepared source changed')
    if prepared['ordinary']:output(available=False);return
    require(prepared.get('test_inputs') in ('FRESH','PENDING'),'unknown TEST preparation state')
    measured=checkpoint_start(root,source,state)
    output(available=True,input_sha256=measured['sha256'])


def decide():
    root,temporary,source,state,_=paths();started=time.monotonic();prepared=read(state/'prepared.json')
    decision=dict(mode='EXECUTED',reason='full fresh qualification required')
    try:
        require(prepared['source']==source,'prepared source changed')
        if prepared['ordinary'] or prepared.get('test_inputs')!='PENDING':raise Fresh('ordinary or freshly compiled TEST inputs require execution')
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
    except (Fresh,UnknownInput) as error:
        decision=dict(mode='EXECUTED',reason=str(error))
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        decision=dict(mode='EXECUTED',reason='trusted complete original evidence unavailable or malformed')
    decision['verification_seconds']=round(time.monotonic()-started,3)
    if not prepared['ordinary'] and prepared.get('test_inputs')=='PENDING':
        if decision['mode']=='REUSED':
            prepared['test_inputs']='RETAINED_VERIFIED'
            summary('REUSED: complete original PRE world verified before any TEST compilation or failures-manifest initialization; DEV checks remain fresh.')
        else:
            fallback=time.monotonic()
            compile_test_inputs(root,state)
            prepared['test_inputs']='FRESH'
            store(state/'prepared.json',prepared)
            checkpoint_start(root,source,state,replace=True)
            decision['fallback_prepare_seconds']=round(time.monotonic()-fallback,3)
            summary('EXECUTED fallback: freshly compiled TEST inputs replaced the provisional retained checkpoint before execution.')
        store(state/'prepared.json',prepared)
    store(state/'decision.json',decision,new=True);output(mode=decision['mode'],origin=decision.get('original_run_url',''))
    summary('Coverage decision: '+decision['mode']+f"; verification {decision['verification_seconds']}s; original {decision.get('original_run_url','none')}; reason {decision.get('reason','verified exact original inputs')}.")


def protected_precheckpoints(root,state,inputs):
    validate_input_profile(inputs,strict_json(profile.PROFILE_PATH.read_bytes()))
    metadata={};raw={}
    for name,target,record in (('test',root/'elixir/_build/test','sidecar.json'),('hex',state/'hex','hex-sidecar.json')):
        value=read(state/record)
        rows=[row for row in inputs['identity']['public_inputs'] if type(row) is dict and row.get('path')==str(target)]
        require(type(value) is dict and set(value)=={'sha256','size','cache'}
                and len(rows)==1 and value['cache']==rows[0], 'protected '+name+' checkpoint is not bound to recorded start inputs')
        path=state/(name+'-cache.tar.gz');identity=file_identity(path)
        require(identity['uid']==os.getuid() and identity['gid']==os.getgid() and identity['mode']==0o600
                and 0<identity['size']<=cache.MAX_COMPRESSED,'protected '+name+' archive is unsafe or oversized')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:payload=stream.read(cache.MAX_COMPRESSED+1)
        require(len(payload)==identity['size']==value['size'] and hashlib.sha256(payload).hexdigest()==identity['sha256']==value['sha256'],
                'protected '+name+' original archive bytes changed')
        metadata[name]=value;raw[name]=payload
    return metadata,raw


def execute():
    root,_,source,state,_=paths();prepared=read(state/'prepared.json');execution={};started=False
    require(prepared['source']==source,'prepared source changed')
    if prepared['ordinary']:
        environment=dict(os.environ);environment.pop('GH_TOKEN',None);environment.pop('GITHUB_TOKEN',None)
        seconds=run(['make','elixir-ci'],root,environment,log=state/'coverage.log')
    else:
        require(prepared.get('test_inputs')=='FRESH','executed original requires fresh TEST preparation')
        recorded=read(state/'inputs.json')
        current=profile.fingerprint(root,source,read(state/'environment.json'),os.environ)
        require(current['sha256']==recorded['sha256']==digest(recorded['identity']),
                'full input drift before execution: '+difference_facets(recorded['identity'],current['identity']))
        checkpoints,_=protected_precheckpoints(root,state,recorded)
        execution=dict(mode='EXECUTING',start_inputs_sha256=recorded['sha256'],precoverage_checkpoints=checkpoints)
        # This host record and the protected PRE archives are outside all writable
        # container binds. Persist before starting so failures retain the world.
        store(state/'execution.json',execution,new=True);started=True
        seconds=phase_run(root,state,'coverage',['mix','test','--cover','--seed','0','--max-failures','1'],log=state/'coverage.log')
    execution.update(mode='EXECUTED',exit_code=0,seconds=seconds,
        coverage_log_sha256=hashlib.sha256((state/'coverage.log').read_bytes()).hexdigest())
    store(state/'execution.json',execution,new=not started)
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
    root,temporary,source,state,_=paths();available=False;stage='prepared_record';reason='qualified original receipt created'
    try:
        decision=state/'decision.json'
        if decision.exists() and read(decision).get('mode')=='REUSED':
            store(state/'seal-status.json',dict(schema_version=1,kind='seal-status-not-qualification',available=False,
                stage='reused_original',reason='REUSED retains its original identity; no new baseline is minted'))
            summary('REUSED: original proof retained; no new original receipt or healthy-consumer diagnostics.')
            output(available=False);return
        require(read(state/'prepared.json')==dict(source=source,supported=True,ordinary=False,test_inputs='FRESH'),'unsupported original or nonfresh TEST preparation')
        stage='dialyzer_record'
        require(read(state/'dialyzer.json')==dict(source=source,success=True),'fresh Dialyzer missing')
        stage='execution_record'
        execution=read(state/'execution.json');require(execution['mode']=='EXECUTED' and execution['exit_code']==0,'not original executed qualification')
        stage='inputs_record'
        inputs=read(state/'inputs.json');require(inputs['sha256']==digest(inputs['identity']),'producer identity invalid')
        stage='coverage_summary'
        execution['summary']=coverage_summary((state/'coverage.log').read_bytes(),strict_json(profile.PROFILE_PATH.read_bytes()))
        stage='producer_context'
        caller=context(client(temporary),source);require(caller['eligible_producer'],'ineligible original producer')
        stage='protected_start_inputs'
        sides,raw=protected_precheckpoints(root,state,inputs)
        require(execution.get('start_inputs_sha256')==inputs['sha256'] and execution.get('precoverage_checkpoints')==sides,
                'successful execution lacks its protected start inputs/checkpoint binding')
        for name,path in (('deps',root/'elixir/deps'),('dev',root/'elixir/_build/dev')):
            stage=name+'_archive';raw[name],sides[name]=cache.create(path,root)
        stage='aggregate_archive_bounds'
        require(sum(len(value) for value in raw.values())<=cache.MAX_COMPRESSED
                and sum(value['cache']['bytes'] for value in sides.values())<=cache.MAX_EXPANDED
                and sum(value['cache']['files'] for value in sides.values())<=cache.MAX_ENTRIES,'combined checkpoint exceeds bounds')
        stage='receipt_write'
        proof=state/'proof';proof.mkdir(mode=0o700)
        store(proof/'receipt.json',dict(schema_version=4,family='full-exunit-coverage',inputs_sha256=inputs['sha256'],
            sidecar=sides,checkpoints=dict(test='PRE_COVERAGE',hex='PRE_COVERAGE',deps='POST_DIALYZER',dev='POST_DIALYZER'),
            cache_origin=read(state/'warm.json'),producer=dict(repository=REPOSITORY,repository_id=caller['repository_id'],
            run_id=caller['run_id'],attempt=caller['attempt'],job_id=caller['job_id'],source=source,workflow=WORKFLOW),execution=execution),new=True)
        store(proof/'inputs.json',inputs,new=True);(proof/'coverage.log').write_bytes((state/'coverage.log').read_bytes())
        for name,value in raw.items():(proof/(name+'-cache.tar.gz')).write_bytes(value)
        available=True
    except (Fresh,UnknownInput,ValueError) as error:
        reason=str(error) if type(error) in (Fresh,UnknownInput,ValueError) else 'malformed public record'
        summary('No reusable original receipt; stage='+stage+'; reason='+reason+'. Qualification execution status is unchanged.')
    except (OSError,KeyError,TypeError,AttributeError) as error:
        reason=type(error).__name__
        summary('No reusable original receipt; stage='+stage+'; reason='+reason+'. Qualification execution status is unchanged.')
    store(state/'seal-status.json',dict(schema_version=1,kind='seal-status-not-qualification',available=available,
        stage='complete' if available else stage,reason=reason))
    output(available=available)


DIAGNOSTIC_RECORDS=('inputs.json','execution.json','sidecar.json','hex-sidecar.json',
                    'prepared.json','dialyzer.json','warm.json','seal-status.json')
MAX_DIAGNOSTICS=1024*1024


def diagnostics():
    _,_,source,state,_=paths();available=False
    try:
        require(not state.is_symlink(),'diagnostic state is symlinked')
        state.mkdir(mode=0o700,exist_ok=True)
        require(state.stat().st_uid==os.getuid() and not state.stat().st_mode & 0o077,'diagnostic state is unsafe')
        records={};rejected=[]
        for name in DIAGNOSTIC_RECORDS:
            path=state/name
            if not path.exists():continue
            try:
                read(path)  # Validate a known private public JSON record first.
                identity=file_identity(path)
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as stream:raw=stream.read(MAX_DIAGNOSTICS+1)
                require(len(raw)==identity['size'] and hashlib.sha256(raw).hexdigest()==identity['sha256'],
                        'public diagnostic record changed')
                records[name]=raw
            except (Fresh,UnknownInput,OSError,ValueError,KeyError,TypeError,AttributeError):
                rejected.append(name)
        status=dict(schema_version=1,kind='unqualified-full-coverage-diagnostics',qualified=False,source=source,
                    run_id=int(os.environ['GITHUB_RUN_ID']),attempt=int(os.environ['GITHUB_RUN_ATTEMPT']),
                    present=sorted(records),missing=sorted(set(DIAGNOSTIC_RECORDS)-records.keys()),rejected=rejected)
        records['diagnostic-status.json']=(json.dumps(status,sort_keys=True,indent=2)+'\n').encode()
        require(sum(map(len,records.values()))<=MAX_DIAGNOSTICS,'public diagnostic payload exceeds1MiB')
        directory=state/'diagnostics';directory.mkdir(mode=0o700)
        for name,raw in records.items():
            fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as target:target.write(raw)
        available=True
        summary('Unqualified public input diagnostics prepared; bounded1MiB, no cache payloads or environment dump; never qualification evidence.')
    except (Fresh,UnknownInput,OSError,ValueError,KeyError,TypeError,AttributeError):
        summary('Unqualified public input diagnostics unavailable or outside1MiB bound; no unknown/private payload uploaded.')
    output(available=available)


def cleanup():
    _,_,_,state,_=paths()
    oci.cleanup(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('select','capability','oci_setup','warm','prepare','measure','decide','execute','reused','dialyzer','seal','diagnostics','cleanup'))
    phase = parser.parse_args().phase
    globals()[dict(select='select_profile').get(phase, phase)]()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        print('Full Elixir qualification controller refused an incomplete/failed phase.', file=sys.stderr)
        raise SystemExit(1)
