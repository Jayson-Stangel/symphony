#!/usr/bin/env python3
"""Conservative event-wide code and browser classification; no API or dependencies."""
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess


def documentation(path):
    p = PurePosixPath(path)
    # These Markdown trees are consumed by the runtime or copied into images.
    if p.is_absolute() or '..' in p.parts or path.startswith(('.codex/', '.agents/')):
        return False
    if p.name.upper() == 'WORKFLOW.MD':
        return False
    if path in ('README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'CODE_OF_CONDUCT.md', 'SECURITY.md', 'SPEC.md'):
        return True
    # elixir is copied wholesale into the production runner: only known prose.
    if path in ('elixir/README.md', 'elixir/AGENTS.md'):
        return True
    return (path.startswith(('docs/', 'elixir/docs/')) and
            p.suffix.lower() in ('.md', '.txt', '.rst'))


def browser_recipe(contents):
    """Use the existing closed Docker parser for the actual browser target graph."""
    source = Path(__file__).resolve().parents[1] / 'release/runner_inputs.py'
    spec = importlib.util.spec_from_file_location('browser_runner_inputs', source)
    inputs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inputs)
    stages = inputs.stage_graph(contents)
    selected = set()
    def visit(name):
        if name not in selected:
            selected.add(name)
            for dependency in stages[name]['dependencies']:
                visit(dependency)
    visit('browser-tools')
    return {name: stages[name] for name in selected}, inputs.pattern_expression


def browser_required(paths, event=None, name=None):
    """Diff only consumed browser inputs; unknown syntax/history requires qualification."""
    if not paths:
        return True
    exact = {
        '.dockerignore', 'runner/Dockerfile.dockerignore', 'runner/node-smoke.sh',
        '.github/workflows/browser-smoke.yml', '.github/workflows/change-classification.yml',
        'scripts/ci/classify_changes.py', 'scripts/release/runner_inputs.py',
        'scripts/release/runner-cache-policy.sh',
    }
    prefixes = ('scripts/ci/', '.github/actions/build-runner/')
    nonbrowser_actions = ('.github/actions/configure-runner-cache/',
                          '.github/actions/runner-cache-runtime/')
    try:
        if any(PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or
               path in exact or
               (path.startswith(prefixes) and path != 'scripts/ci/check_pr_body.py') or
               (path.startswith('.github/actions/') and not path.startswith(nonbrowser_actions))
               for path in paths):
            return True
        base, head = changed_revisions(event, name)
        old, pattern_expression = browser_recipe(git('show', base + ':runner/Dockerfile'))
        current, _ = browser_recipe(git('show', head + ':runner/Dockerfile'))
        if old != current:
            return True
        sources = {source for stage in current.values() for source in stage['sources']}
        for source in sources:
            source_path = PurePosixPath(source)
            if source_path.is_absolute() or '..' in source_path.parts:
                return True
            # Docker accepts leading/repeated ./ and directory COPY sources.
            # Normalize those before matching; unsupported patterns still refuse.
            expression = pattern_expression(str(source_path))
            if any(expression.fullmatch(path) or any(expression.fullmatch(str(parent)) for parent in PurePosixPath(path).parents)
                   for path in paths):
                return True
        return False
    except (KeyError, ValueError, TypeError, UnicodeError, OSError, subprocess.SubprocessError):
        return True


# Ordinary consumers. Full manual/tag/candidate calls never use a diff omission.
COMPONENTS = ('application', 'portable', 'package_macos', 'package_linux', 'fixtures')
ALL = set(COMPONENTS)
PACKAGES = {'portable', 'package_macos', 'package_linux'}
# These controllers/tests have no native-helper or native process consumer.
# Unknown release inputs retain every native consumer until audited.
PORTABLE_RELEASE = {
    'benchmark-transfer.py',
    'build-candidate-runners.sh',
    'build-manager-package.sh',
    'build-runner-cached.sh',
    'component_discovery.py',
    'component_publication.py',
    'component_restore.py',
    'export-image.py',
    'finalize-manager-packages.py',
    'graph_publication.py',
    'package-lifecycle.py',
    'prepare-candidate-source.sh',
    'prepare-manager-inputs.py',
    'previous-package-subject.py',
    'probe-registry-closure.py',
    'record.py',
    'recover_native_record.py',
    'registry_closure.py',
    'runner-cache-policy.sh',
    'runner-cache-probe.py',
    'runner-smoke.sh',
    'runner-startup.sh',
    'runner_reuse.py',
    'test_registry.py',
    'verify-prior-source.py',
}


def consumers(path):
    p = PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts:
        return ALL
    if documentation(path):
        return set()
    if path in ('.github/pull_request_template.md', '.github/workflows/pr-description-lint.yml',
                'scripts/ci/check_pr_body.py'):
        return set()  # checked by always-on metadata/policy validation
    if path.startswith('scripts/ci/') or path == '.github/workflows/change-classification.yml':
        return ALL
    if path == '.github/workflows/make-all.yml':
        return {'application'}
    if path.startswith('.github/workflows/'):
        # Native package/release controls can affect portable and native evidence;
        # manual-only image controllers are validated by portable contract tests.
        if p.stem in ('runner-release', 'runner-integration', 'component-plan',
                       'transfer-timing', 'current-manager-package'):
            return {'portable'}
        if p.stem in ('package-checks', 'release-native', 'release-preflight',
                       'host-policy', 'native-manager-helpers', 'burrito-release'):
            return PACKAGES | {'fixtures'}
        if p.stem == 'browser-smoke':
            return {'portable'}
        return ALL
    if path.startswith(('.codex/', '.agents/')) or p.name.upper() == 'WORKFLOW.MD':
        return PACKAGES | {'application'}
    if path == 'elixir/mix.exs':
        return {'application', 'portable'}
    if path.startswith('elixir/'):
        return {'application'}
    if path in ('scripts/symphony', 'scripts/symphony-package.sh', 'scripts/symphony-package-update.sh'):
        return PACKAGES | {'application', 'fixtures'}
    if path == 'scripts/symphony-environment.sh':
        return PACKAGES | {'application'}
    if path.startswith('scripts/package/linux/') or path == 'scripts/release/build-linux-host-helper.sh':
        return {'portable', 'package_linux'}
    if path.startswith('scripts/package/'):
        return PACKAGES
    if path in ('scripts/release/engine-smoke.py', 'scripts/release/tests/test_engine_smoke.py',
                'scripts/release/tests/test_package_update.py'):
        return {'portable', 'fixtures'}
    if path in ('scripts/release/tests/test_component_provenance.py',
                'scripts/release/tests/fixtures/manager-original-68.json'):
        return PACKAGES
    if path.startswith('scripts/release/tests/'):
        # Existing portable suite inventory is explicit; new tests remain native.
        spec = importlib.util.spec_from_file_location('ci_release_suites', Path(__file__).with_name('release_tests.py'))
        suites = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(suites)
        return {'portable'} if p.name in suites.PORTABLE else ALL
    if path.startswith('scripts/release/'):
        relative = str(p.relative_to('scripts/release'))
        if relative in PORTABLE_RELEASE:
            return {'portable'}
        if relative in {
            'artifacts.py', 'build-host-helper.sh', 'package.sh', 'component_provenance.py',
            'finite_transition.py', 'host-targets.json', 'manager-transition-policy.json',
            'manager_contract.py', 'manager_envelope.py', 'manager_inputs.py', 'manager_version.py',
            'manager-version', 'release-schema', 'runner_inputs.py', 'select-hosts.py',
            'component_join.py', 'component_selection.py', 'qualified_graph.py', 'reuse_payload.py',
        }:
            return PACKAGES
        return ALL
    if path.startswith('.github/actions/'):
        if path.split('/')[2] in ('build-runner', 'configure-runner-cache', 'runner-cache-runtime'):
            return {'portable'}
        return ALL
    if path.startswith('runner/templates/'):
        return PACKAGES | {'application'}
    if path.startswith('runner/claude/qualification/'):
        return {'portable'}  # explicit manual reference_node_smoke consumes the image
    if path.startswith('runner/claude/') or path in ('runner/entrypoint.sh', 'runner/updater-smoke.py'):
        return {'application'}
    if path.startswith('runner/browser/') or path in ('runner/Dockerfile', '.dockerignore',
            'runner/Dockerfile.dockerignore', 'runner/node-smoke.sh'):
        return {'portable'}  # browser graph independently routes actual browser inputs
    return ALL


def decisions(paths):
    selected = set().union(*(consumers(path) for path in paths)) if paths else ALL
    values = {key: key in selected for key in COMPONENTS}
    macos = values['package_macos'] or values['fixtures']
    linux = values['package_linux'] or values['fixtures']
    values['native'] = macos or linux
    values['native_platform'] = 'all' if macos == linux else ('macos' if macos else 'linux_x86_64')
    return values


def git(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE)


def changed_revisions(event, name):
    if name == 'pull_request':
        base = event['pull_request']['base']['sha']
        head = event['pull_request']['head']['sha']
        # Compare the complete PR, including older commits and stale bases.
        base = git('merge-base', base, head).decode().strip()
    elif name == 'push':
        # A moved tag can have a valid docs-only diff. It still selects complete
        # release artifacts, so never apply ordinary branch omission policy.
        if str(event.get('ref', '')).startswith('refs/tags/'):
            raise ValueError('tag events require full artifact qualification')
        base, head = event['before'], event['after']
    else:
        raise ValueError('event requires full code qualification')
    if not all(isinstance(s, str) and re.fullmatch('[0-9a-f]{40}', s) and s != '0' * 40 for s in (base, head)):
        raise ValueError('missing or invalid event revisions')
    return base, head


def changed_paths(event, name):
    base, head = changed_revisions(event, name)
    # No rename detection: both old and new paths matter, including docs->code.
    raw = git('diff', '--name-only', '--no-renames', '-z', base, head, '--')
    paths = raw.decode('utf-8', errors='strict').split('\0')
    if paths[-1] != '':
        raise ValueError('incomplete NUL-delimited diff')
    paths = paths[:-1]
    if not paths:
        raise ValueError('empty diff cannot prove documentation-only')
    return paths


def classify(event, name):
    try:
        paths = changed_paths(event, name)
        code = [p for p in paths if not documentation(p)]
        return not code, ('all changed paths are human-facing documentation' if not code
                          else 'behavioral or unknown paths require code qualification'), paths
    except (KeyError, TypeError, ValueError, UnicodeError, OSError, subprocess.SubprocessError):
        return False, 'full-event policy or unavailable/incomplete diff requires code qualification', []


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    docs, reason, paths = classify(event, os.environ['GITHUB_EVENT_NAME'])
    value = 'false' if docs else 'true'
    browser = browser_required(paths, event, os.environ['GITHUB_EVENT_NAME'])
    if browser:
        value = 'true'
        if docs:
            reason = 'browser-consumed inputs or browser validation require code qualification'
    routing = decisions(paths)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f'code={value}\n')
            f.write('browser=' + str(browser).lower() + '\n')
            for key, required in routing.items():
                f.write(key + '=' + str(required).lower() + '\n')
    summary = f'### Change classification\n\nCode qualification required: **{value}**. {reason}.\n\n'
    summary += 'Browser qualification required: **' + str(browser).lower() + '**. ' + (
        'Browser inputs, validation controls or full-event policy require qualification.\n\n'
        if browser else 'No browser build/qualification inputs changed.\n\n')
    for key, required in routing.items():
        summary += f'{key}: **{str(required).lower()}**.\n\n'
    # Render arbitrary Git paths as escaped JSON inside a code block.
    summary += 'Changed paths: ' + str(len(paths)) + '\n\n' + '\n'.join('    ' + json.dumps(p) for p in paths) + '\n'
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(summary)


if __name__ == '__main__':
    main()
