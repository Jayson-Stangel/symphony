#!/usr/bin/env python3
"""Opt-in updater lifecycle proof on prepared production images; never authenticates.

Usage: python3 runner/updater-smoke.py CODEX_IMAGE CLAUDE_IMAGE

Runs actual app/entrypoints, Docker mounts, HTTP controls and updater metadata.
ONLY Codex native login-status and selection of prepared build images are peers;
no model requests or account credentials are used. Claude missing-auth is native.
Injected failures occur after delegated successful Docker side effects. Uses an
empty Memory project and unique disposable resources, retaining local evidence.
Does not qualify authenticated inference or successful mixed-provider migration.
"""
import json, os, pathlib, shutil, socket, subprocess, sys, tempfile, time, urllib.request, uuid
if len(sys.argv) != 3:
 raise SystemExit('Usage: python3 runner/updater-smoke.py CODEX_IMAGE CLAUDE_IMAGE')
checkout=pathlib.Path(__file__).resolve().parent.parent
codex_image, claude_image = sys.argv[1:]
root=pathlib.Path(tempfile.mkdtemp(prefix='symphony-updater-smoke-')); root.chmod(0o755)
namespace='updater-smoke-'+uuid.uuid4().hex[:12]; prefix='symphony-'+namespace
repo=root/'service'; repo.mkdir(); config=root/'config'; (config/'projects').mkdir(parents=True)
real=shutil.which('docker'); auth=prefix+'-codex-auth'
if real is None or shutil.which('curl') is None:
 raise SystemExit('Docker and curl are required')
env=dict(os.environ, SYMPHONY_HOME=str(checkout), SYMPHONY_RUNNER_NAMESPACE=namespace, SYMPHONY_CONFIG_HOME=str(config), SYMPHONY_RUNNER_HOME=str(config/'runners'), PROOF_ROOT=str(root), PROOF_REAL_DOCKER=real, SYMPHONY_CODEX_AUTH_VOLUME=auth, SYMPHONY_RUNNER_IMAGE='', GH_TOKEN='', GITHUB_TOKEN='')
def cmd(args, expected=0, **kw):
 p=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,**kw)
 print('$', ' '.join(map(str,args)), '\n',p.stdout,flush=True)
 if expected is not None: assert p.returncode==expected,(args,p.returncode,p.stdout)
 return p.stdout.strip()
def docker(*a): return cmd([real,*a])
cmd(['git','init','-q',str(repo)]); cmd(['git','-C',str(repo),'remote','add','origin','https://example.invalid/service.git'])
project_id=cmd(['bash','-c','source "$SYMPHONY_HOME/scripts/symphony" --help >/dev/null; project_id "$1"','identity',str(repo)],env=env)
name=prefix+'-'+project_id
owned_names=[name,name+'-before-update',prefix+'-service',prefix+'-service-before-update']
owned_storage={name}
(repo/'WORKFLOW.md').write_text('---\ntracker:\n  kind: memory\nworkspace:\n  root: /workspaces\npolling:\n  interval_ms: 1000\nobservability:\n  dashboard_enabled: false\n---\nEmpty isolated proof; no inference.\n')
(config/('projects/'+project_id+'.env')).write_text('GH_TOKEN=credential-free-placeholder\n')
wrapper=root/'docker-peer'
wrapper.write_text('''#!/usr/bin/env python3
import json,os,pathlib,subprocess,sys,time
r=pathlib.Path(os.environ['PROOF_ROOT']); a=sys.argv[1:]; real=os.environ['PROOF_REAL_DOCKER']
with (r/'docker-calls.jsonl').open('a') as f:f.write(json.dumps(a)+'\\n')
if a[0]=='build':
 image=(r/'claude-image').read_text().strip() if a[a.index('--target')+1]=='claude' else (r/'build-image').read_text().strip(); tag=a[a.index('-t')+1]
 sys.exit(subprocess.call([real,'tag',image,tag]))
if a[0]=='run' and '--entrypoint' in a and a[a.index('--entrypoint')+1]=='codex' and a[-2:]==['login','status']:
 print('STUB native authentication status; no credentials/inference');sys.exit(0)
p=subprocess.run([real,*a]); fault=r/'fault'
if p.returncode==0 and a[0]=='stop' and (r/'hold-stop').exists():
 (r/'stop-observed').touch(); deadline=time.monotonic()+60
 while (r/'hold-stop').exists():
  if time.monotonic()>deadline:sys.exit('timed out waiting for competing-command proof')
  time.sleep(.05)
if p.returncode==0 and fault.exists():
 mode=fault.read_text().strip()
 matches=(mode=='create' and a[0]=='run' and '--detach' in a) or (mode=='backup-remove' and a[0]=='rm' and a[-1].endswith('-before-update')) or (mode=='rollback-rename' and a[0]=='rename' and a[1].endswith('-before-update'))
 if matches:fault.unlink();print('FAULT AFTER actual Docker side effect: '+mode,file=sys.stderr);sys.exit(97)
sys.exit(p.returncode)
'''); wrapper.chmod(0o755);env['SYMPHONY_CONTAINER_RUNTIME']=str(wrapper)
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
curl=root/'curl'
curl.write_text("#!/usr/bin/env python3\nimport json,os,pathlib,subprocess,sys\np=subprocess.run(["+repr(shutil.which('curl'))+",*sys.argv[1:]],stdout=subprocess.PIPE,stderr=subprocess.PIPE)\nwith (pathlib.Path(os.environ['PROOF_ROOT'])/'http.jsonl').open('a') as f:f.write(json.dumps({'args':sys.argv[1:],'exit':p.returncode,'body':p.stdout.decode(errors='replace')})+'\\n')\nsys.stdout.buffer.write(p.stdout);sys.stderr.buffer.write(p.stderr);sys.exit(p.returncode)\n")
curl.chmod(0o755);env['PATH']=str(root)+':'+env['PATH']
def launch(*a, expected=0, cwd=repo):return cmd(['bash',str(checkout/'scripts/symphony'),*a],expected,cwd=cwd,env=env)
def inspect(n,fmt):return docker('inspect','--format',fmt,n)
def state(listener_port=port):
 for i in range(50):
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{listener_port}/api/v1/state',timeout=2) as r:v=json.load(r)
   if 'error' not in v:return v
  except OSError:pass
  time.sleep(.2)
 raise AssertionError('real app listener did not become healthy')
def image(i):
 tag=f'{prefix}-proof:{i}'
 d=root/f'build-{i}';d.mkdir();(d/'Dockerfile').write_text(f'FROM {base_tag}\nLABEL com.symphony.proof={i}\n')
 docker('build','--quiet','-t',tag,str(d));return docker('image','inspect','--format','{{.Id}}',tag)
def selected():return (config/('runners/'+project_id+'.env')).read_text()
def legacy_identity():
 # Recreate the previous launcher's names without changing runtime/storage data.
 legacy=prefix+'-service'
 for suffix in ['', '-before-update']:
  if subprocess.run([real,'inspect',name+suffix],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
   docker('rename',name+suffix,legacy+suffix)
 for base,suffix in [('projects','.env'),('runners','.env'),('runners','.transition'),('runners','.transition.complete')]:
  source=config/base/(project_id+suffix)
  if source.exists():source.rename(config/base/('service'+suffix))

def preserved():return docker('exec',name,'sh','-ceu','sha256sum /workspaces/lifecycle-proof/edited /workspaces/lifecycle-proof/untracked /var/lib/symphony/proof-state; git -C /workspaces/lifecycle-proof rev-parse HEAD; git -C /workspaces/lifecycle-proof status --porcelain')
def fail(mode): (root/'fault').write_text(mode)
def build(i):(root/'build-image').write_text(i)
try:
 base=docker('image','inspect','--format','{{.Id}}',codex_image)
 claude_image=docker('image','inspect','--format','{{.Id}}',claude_image)
 assert docker('image','inspect','--format','{{index .Config.Labels "com.symphony.agent"}}',base)=='codex'
 assert docker('image','inspect','--format','{{index .Config.Labels "com.symphony.agent"}}',claude_image)=='claude'
 assert docker('image','inspect','--format','{{.Config.User}}',claude_image)=='11001:11001'
 (root/'claude-image').write_text(claude_image)
 base_tag=prefix+'-source:codex';docker('tag',base,base_tag)
 v1=image(1);v2=image(2)
 docker('volume','create',auth);docker('tag',base,prefix+'-runner:local')
 launch('run','--port',str(port)); assert state()['counts']=={'running':0,'retrying':0,'blocked':0}
 # Same basename, distinct normalized remotes: two real listeners and separate
 # project credentials, runner metadata, containers and physical state volumes.
 other=root/'other'/'service';other.mkdir(parents=True)
 cmd(['git','init','-q',str(other)]);cmd(['git','-C',str(other),'remote','add','origin','https://example.invalid/other/service.git'])
 other_id=cmd(['bash','-c','source "$SYMPHONY_HOME/scripts/symphony" --help >/dev/null; project_id "$1"','identity',str(other)],env=env)
 assert other_id!=project_id
 shutil.copyfile(repo/'WORKFLOW.md',other/'WORKFLOW.md')
 (config/('projects/'+other_id+'.env')).write_text('GH_TOKEN=other-credential-free-placeholder\n')
 with socket.socket() as s:s.bind(('127.0.0.1',0));other_port=s.getsockname()[1]
 other_name=prefix+'-'+other_id
 owned_names.append(other_name);owned_storage.add(other_name)
 launch('run','--port',str(other_port),cwd=other)
 assert state(other_port)['counts']=={'running':0,'retrying':0,'blocked':0}
 assert inspect(other_name,'{{.State.Running}}')=='true' and inspect(name,'{{.State.Running}}')=='true'
 assert inspect(other_name,'{{index .Config.Labels "com.symphony.storage"}}')!=inspect(name,'{{index .Config.Labels "com.symphony.storage"}}')
 assert (config/('runners/'+other_id+'.env')).exists() and selected()
 assert (config/('projects/'+other_id+'.env')).read_text()!=(config/('projects/'+project_id+'.env')).read_text()
 launch('runner','stop',cwd=other)
 print('PASS real same-basename repositories retain independent containers, credentials, metadata and storage',flush=True)
 docker('exec',name,'sh','-ceu','mkdir /workspaces/lifecycle-proof; git -C /workspaces/lifecycle-proof init -q; git -C /workspaces/lifecycle-proof config user.name Fixture; git -C /workspaces/lifecycle-proof config user.email fixture@example.invalid; printf committed > /workspaces/lifecycle-proof/edited; git -C /workspaces/lifecycle-proof add edited; git -C /workspaces/lifecycle-proof commit -qm fixture; printf dirty >> /workspaces/lifecycle-proof/edited; printf untracked > /workspaces/lifecycle-proof/untracked; printf preserved > /var/lib/symphony/proof-state')
 assert docker('exec',name,'id','-u')=='0'
 physical_mounts=inspect(name,'{{json .Mounts}}');print('INITIAL PHYSICAL MOUNTS '+physical_mounts,flush=True)
 preservation=preserved()
 initial_id=inspect(name,'{{.Id}}');initial_metadata=selected()
 initial_credentials=(config/('projects/'+project_id+'.env')).read_text()
 legacy_identity();launch('update','--development-runner','--migrate-project-identity')
 assert inspect(name,'{{.Id}}')==initial_id;assert selected()==initial_metadata
 assert (config/('projects/'+project_id+'.env')).read_text()==initial_credentials
 assert inspect(name,'{{json .Mounts}}')==physical_mounts;assert preserved()==preservation
 print('PASS real running basename identity migration preserves container, credentials and physical mounts',flush=True)
 before=selected();assert base in before
 build(v1);fail('create');(root/'hold-stop').touch()
 with (root/'held-update.log').open('w') as output:
  update=subprocess.Popen(['bash',str(checkout/'scripts/symphony'),'update','--development-runner','--agent','codex'],cwd=repo,env=env,stdout=output,stderr=subprocess.STDOUT)
  try:
   deadline=time.monotonic()+60
   while not (root/'stop-observed').exists():
    assert update.poll() is None,'updater exited before stop barrier'
    assert time.monotonic()<deadline,'updater did not reach actual Docker stop'
    time.sleep(.05)
   refusal=launch('run','--port',str(port),expected=1)
   assert 'another updater is active' in refusal
  finally:
   (root/'hold-stop').unlink(missing_ok=True)
   try:result=update.wait(timeout=90)
   except subprocess.TimeoutExpired:update.kill();update.wait();raise
 print((root/'held-update.log').read_text(),flush=True);assert result==97
 print('PASS real stop-before-rename competing run refuses existing updater ownership',flush=True)
 assert selected()==before;assert inspect(name,'{{.Image}}')==v1;assert inspect(name+'-before-update','{{.Image}}')==base
 assert inspect(name+'-before-update','{{.State.ExitCode}}') in ['0','1']
 assert inspect(name+'-before-update','{{.State.OOMKilled}}')=='false'
 assert state()['agent']['draining'] is True
 previous_metadata=(config/('runners/'+project_id+'.transition/previous.env')).read_text()
 candidate_id=inspect(name,'{{.Id}}');backup_id=inspect(name+'-before-update','{{.Id}}')
 legacy_identity();launch('update','--development-runner','--migrate-project-identity')
 assert inspect(name,'{{.Id}}')==candidate_id;assert inspect(name+'-before-update','{{.Id}}')==backup_id
 assert (config/('runners/'+project_id+'.transition/previous.env')).read_text()==previous_metadata
 assert preserved()==preservation
 print('PASS real pending transition basename identity migration preserves candidate, backup and journal',flush=True)
 launch('runner','stop');assert inspect(name,'{{.State.Running}}')=='false'
 launch('update','--development-runner','--agent','codex');assert v1 in selected();assert state()['agent']['draining'] is False
 assert inspect(name,'{{.Id}}')==candidate_id
 print('PASS real stopped retained candidate restarts with the same container identity',flush=True)
 assert not (config/('runners/'+project_id+'.transition')).exists()
 launch('runner','stop');launch('run','--port',str(port));assert state()['agent']['provider']=='codex'
 assert preserved()==preservation
 print('PASS real create-before-cache interruption + retry + ordinary restart',flush=True)
 build(v2);fail('create');launch('update','--development-runner','--agent','codex',expected=97);state()
 previous=config/('runners/'+project_id+'.transition/previous.env');previous.write_text(previous.read_text().replace('SYMPHONY_RUNNER_AGENT=codex','SYMPHONY_RUNNER_AGENT=claude').replace('SYMPHONY_RUNNER_PROFILE=none','SYMPHONY_RUNNER_PROFILE=stale'))
 fail('rollback-rename');launch('update','--development-runner','--rollback-runner',expected=97)
 assert inspect(name,'{{.Image}}')==v1
 launch('update','--development-runner','--rollback-runner');assert v1 in selected();assert 'SYMPHONY_RUNNER_AGENT=codex\n' in selected();assert 'SYMPHONY_RUNNER_PROFILE=none\n' in selected();assert state()['agent']['draining'] is False
 assert preserved()==preservation
 print('PASS real rollback rename interruption + repeat + observed source metadata',flush=True)
 build(v2);fail('backup-remove');launch('update','--development-runner','--agent','codex',expected=97)
 assert v2 in selected();assert (config/('runners/'+project_id+'.transition')).is_dir();assert state()['agent']['draining'] is False
 launch('update','--development-runner','--agent','codex');assert v2 in selected();assert not (config/('runners/'+project_id+'.transition')).exists()
 assert preserved()==preservation
 print('PASS real backup removal before journal cleanup + retry',flush=True)
 # Real negative native auth: fresh project-scoped empty volume, no auth/check peer.
 storage=inspect(name,'{{index .Config.Labels "com.symphony.storage"}}');claude_auth=storage+'-claude-auth'
 docker('volume','create','--label','com.symphony.storage='+storage,claude_auth)
 docker('run','--rm','--network','none','--read-only','--cap-drop','ALL','--cap-add','CHOWN','--user','0:0','--entrypoint','sh','--volume',claude_auth+':/home/claude',claude_image,'-ceu','chown 11001:11001 /home/claude')
 prior_id=inspect(name,'{{.Id}}');before=selected()
 negative=launch('update','--development-runner','--agent','claude',expected=1)
 assert 'auth_required' in negative and 'model_requests' in negative
 assert selected()==before and inspect(name,'{{.Id}}')==prior_id and inspect(name,'{{.State.Running}}')=='true'
 assert not (config/('runners/'+project_id+'.transition')).exists();assert preserved()==preservation
 print('PASS actual Claude native missing-auth preflight refuses before source/journal mutation; zero model requests',flush=True)
 print(json.dumps({'namespace':namespace,'root':str(root),'port':port,'base':base,'v1':v1,'v2':v2,'metadata':selected(),'modelRequests':0,'nativeAuth':'STUB login status ONLY; no credential used','appState':state()},sort_keys=True),flush=True)
finally:
 try:print('FINAL ACTUAL HTTP SNAPSHOT '+json.dumps(state(),sort_keys=True),flush=True)
 except Exception as e:print('FINAL SNAPSHOT UNAVAILABLE '+str(e),flush=True)
 if (root/'http.jsonl').exists():print('ACTUAL CONTROL HTTP '+(root/'http.jsonl').read_text(),flush=True)
 for n in owned_names:
  subprocess.run([real,'logs',n],stdout=open(root/(n+'.log'),'w'),stderr=subprocess.STDOUT)
  owner=subprocess.run([real,'inspect','--format','{{index .Config.Labels \"com.symphony.storage\"}}',n],text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
  if owner.returncode==0 and owner.stdout.strip() in owned_storage:
   subprocess.run([real,'rm','-f',n],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 volumes=subprocess.check_output([real,'volume','ls','--format','{{.Name}}'],text=True).splitlines()
 for v in volumes:
  if v.startswith(prefix+'-'):subprocess.run([real,'volume','rm',v],stdout=subprocess.DEVNULL)
 tags=subprocess.check_output([real,'image','ls','--format','{{.Repository}}:{{.Tag}}'],text=True).splitlines()
 for tag in tags:
  if tag.startswith(prefix+'-'):subprocess.run([real,'image','rm',tag],stdout=subprocess.DEVNULL)
 print('Owned disposable containers/volumes/tags cleanup complete; evidence root '+str(root),flush=True)
