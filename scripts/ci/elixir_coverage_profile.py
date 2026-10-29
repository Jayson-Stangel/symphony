"""One reviewed source family in a read-only content-addressed OCI world.

OS/tool bytes are the actual image/config/rootfs identity. Only declared mutable
source/cache/private state is hashed; no host-wide executable scan or metadata
normalization manufactures eligibility. Unknown state remains fresh/refused.
"""
import ast
import importlib.util
import json
import os
from pathlib import Path
import time

from validation_inputs import UnknownInput, digest, tree_identity, tracked_source, untracked_inputs
import validation_oci as oci

PROFILE_PATH = Path(__file__).with_name('elixir-coverage-profile.json')
GENERATED = ('elixir/deps', 'elixir/_build', 'elixir/bin/symphony', 'elixir/cover', 'runner/claude/node_modules')
EXPLICIT_SOURCE = {'.github/pull_request_template.md','scripts/codex-apparmor.profile','scripts/codex-seccomp.json'}
CONTROL_FILES = frozenset({
 '.github/workflows/make-all.yml','.github/workflows/change-classification.yml',
 'scripts/ci/validation_inputs.py','scripts/ci/validation_evidence.py','scripts/ci/validation_cache.py',
 'scripts/ci/validation_oci.py','scripts/ci/elixir_coverage_profile.py','scripts/ci/elixir-coverage-profile.json',
 'scripts/ci/elixir_coverage.py','scripts/ci/elixir_coverage_bootstrap.py',
 'scripts/ci/classify_changes.py','scripts/ci/release_tests.py','scripts/ci/presentation_only.rb',
 'scripts/ci/elixir-coverage.Dockerfile','scripts/ci/elixir-coverage.passwd','scripts/ci/elixir-coverage.group',
 'scripts/release/runner_inputs.py','runner/Dockerfile',
})

def classifier():
    spec = importlib.util.spec_from_file_location('coverage_consumer_selection', Path(__file__).with_name('classify_changes.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_exclusion():
    policy = classifier()
    def excluded(path):
        # Exact existing presentation assets have no fixed-suite reader. New
        # media remains conservative; do not use a directory/extension shortcut.
        if path in {'.github/media/symphony-demo.mp4', '.github/media/elixir-screenshot.png'}:
            return True
        if path in EXPLICIT_SOURCE:
            return False
        # These are bound separately, including the entire actual workflow.
        if path.startswith(('scripts/ci/', '.github/workflows/', '.github/actions/')):
            return True
        return 'application' not in policy.consumer_selection(path)[0]
    return excluded


def source_identity(root, revision):
    untracked_inputs(root, GENERATED)
    identity, rows = tracked_source(root, revision, source_exclusion())
    if any(row[1] == '120000' for row in rows):
        raise UnknownInput('source symlinks are outside this audited full-coverage profile')
    return identity, rows


def audited_code(rows):
    # The reviewed code pin is independent of a particular VM's UID/mode state;
    # actual permissions/ownership remain part of the execution fingerprint.
    return digest([row[:3] for row in rows])


def control_path(path):
    return path in CONTROL_FILES


def controls(root, revision):
    value, rows = tracked_source(root, revision, lambda path: not control_path(path))
    if set(row[0] for row in rows) != CONTROL_FILES:
        raise UnknownInput('complete finite coverage controller/import inventory unavailable')
    return dict(identity=value, files={row[0]: row[2] for row in rows})


def public_environment(environment, parent=None):
    if environment != oci.environment():
        raise UnknownInput('execution environment is outside the finite public OCI profile')
    return environment


def execution_identity(root, revision, state):
    source, rows = source_identity(root, revision)
    description = json.loads(PROFILE_PATH.read_text())
    if description.get('version') != 2 or audited_code(rows) != description['audited_source_sha256']:
        raise UnknownInput('changed or unaudited full-coverage source profile')
    runtime = json.loads((state / 'runtime.json').read_text())
    # Compute the compile protocol from actual source, never an artifact-supplied
    # cache-key/version assertion. Only validation-only functions may differ.
    tree = ast.parse((Path(root)/'scripts/ci/elixir_coverage.py').read_text())
    compile_functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name in ('prepare','run')]
    if len(compile_functions) != 2:
        raise UnknownInput('finite build protocol unavailable')
    tools,_=tree_identity(state/'mix',allowed_roots=[state],deadline=time.monotonic()+15)
    return dict(profile='full-exunit-oci-v2', source=source, runtime=runtime, public_tools=tools,
                compile_protocol=digest([ast.dump(node,include_attributes=False) for node in compile_functions]))


def fingerprint(root, revision, environment, parent, *, budget_seconds=30):
    started = time.monotonic(); public_environment(environment)
    root=Path(root);state=Path(parent['RUNNER_TEMP'])/'symphony-coverage-v2'
    execution=execution_identity(root,revision,state)
    roots=[root/'elixir/deps',root/'elixir/_build/test',root/'runner/claude/node_modules',
           state/'mix',state/'hex',state/'home']
    identities=[]
    for item in roots:
        value,_=tree_identity(item,allowed_roots=[root,state],deadline=started+budget_seconds)
        identities.append(value)
    value=dict(schema_version=2,family='full-exunit-coverage',profile_version=2,
               execution=execution,controllers=controls(root,revision),environment=environment,
               public_inputs=identities,command=['mix','test','--cover','--seed','0','--max-failures','1'])
    return dict(identity=value,sha256=digest(value),elapsed_seconds=round(time.monotonic()-started,3),
                files=sum(item['files'] for item in identities),bytes=sum(item['bytes'] for item in identities))
