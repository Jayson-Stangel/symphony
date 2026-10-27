#!/usr/bin/env python3
"""Conservative event-wide code and browser classification; no API or dependencies."""
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
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
    graph = {name: dict(stages[name]) for name in selected}
    browser_stages = set(selected)
    for provider in ('codex', 'claude'):
        if provider in stages:
            visit(provider)
    inputs.validate_metadata_scope(stages, selected)
    def metadata_label(item):
        if item[0] != 'LABEL':
            return False
        fields = shlex.split(item[1])
        expected = {'org.opencontainers.image.version': 'SYMPHONY_VERSION',
                    'org.opencontainers.image.revision': 'SYMPHONY_REVISION',
                    'com.symphony.inputs.sha256': 'SYMPHONY_INPUT_IDENTITY',
                    'com.symphony.architecture': 'TARGETARCH'}
        return bool(fields) and all('=' in field and field.split('=', 1)[0] in expected and
            field.split('=', 1)[1] in ('$' + expected[field.split('=', 1)[0]],
                                     '${' + expected[field.split('=', 1)[0]] + '}') for field in fields)
    for name in selected - browser_stages:
        stage = stages[name]
        instructions = [item for item in stage['instructions']
                        if not metadata_label(item)
                        and not (item[0] == 'ARG' and
                            item[1].split('=', 1)[0] in inputs.METADATA_ARGS)]
        graph[name] = dict(stage, instructions=instructions, sources=[])
    return graph, inputs.pattern_expression


def browser_selection(paths, event=None, name=None):
    """Diff only consumed browser inputs; unknown syntax/history requires qualification."""
    if not paths:
        return True, [], ['Full-event policy or unavailable browser diff requires qualification']
    exact = {
        '.dockerignore', 'runner/Dockerfile.dockerignore', 'runner/node-smoke.sh',
        '.github/workflows/browser-smoke.yml',
        '.github/workflows/change-classification.yml', 'runner/browser/smoke-image.sh',
        'scripts/ci/classify_changes.py', 'scripts/release/runner_inputs.py',
        'scripts/release/runner-cache-policy.sh', 'scripts/release/runner-smoke.sh',
        'scripts/release/package-lifecycle.py',
    }
    prefixes = ('.github/actions/build-runner/',)
    nonbrowser_actions = ('.github/actions/configure-runner-cache/',
                          '.github/actions/runner-cache-runtime/')
    triggers = []
    for path in paths:
        if PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
            return True, [], ['Unsafe browser path: ' + path]
        if 'conservative full qualification' in consumer_selection(path)[1]:
            triggers.append({'path': path, 'rule': 'unknown consumer: conservative browser qualification'})
        if (path in exact or (path.startswith(prefixes) and path != 'scripts/ci/check_pr_body.py') or
                (path.startswith('scripts/ci/') and not path.startswith('scripts/ci/tests/')
                 and path != 'scripts/ci/check_pr_body.py') or
                (path.startswith('.github/actions/') and not path.startswith(nonbrowser_actions))):
            triggers.append({'path': path, 'rule': 'browser build, runtime smoke or shared CI validation control'})
    try:
        base, head = changed_revisions(event, name)
        for path in paths:
            if ((path.startswith('.github/workflows/') and path.endswith(('.yml', '.yaml'))) or
                    (path.startswith('.github/actions/') and PurePosixPath(path).name in ('action.yml', 'action.yaml'))):
                if not workflow_comparison(path, base, head):
                    triggers.append({'path': path, 'rule': 'unsupported or unavailable workflow comparison: conservative browser qualification'})
        old, pattern_expression = browser_recipe(git('show', base + ':runner/Dockerfile'))
        current, _ = browser_recipe(git('show', head + ':runner/Dockerfile'))
        if '.github/workflows/runner-integration.yml' in paths:
            comparison = workflow_comparison('.github/workflows/runner-integration.yml', base, head)
            if comparison.get('browser_control_changed') is not False:
                triggers.append({'path': '.github/workflows/runner-integration.yml',
                                 'rule': 'ordinary browser/global workflow controls changed or comparison refused'})
        if old != current:
            triggers.append({'path': 'runner/Dockerfile', 'rule': 'browser dependency stage or final provider runtime recipe changed'})
        sources = {source for graph in (old, current) for stage in graph.values() for source in stage['sources']}
        for source in sorted(sources):
            source_path = PurePosixPath(source)
            if source_path.is_absolute() or '..' in source_path.parts:
                return True, triggers, ['Unsafe browser COPY source: ' + source]
            expression = pattern_expression(str(source_path))
            for path in paths:
                if expression.fullmatch(path) or any(expression.fullmatch(str(parent)) for parent in PurePosixPath(path).parents):
                    triggers.append({'path': path, 'rule': 'consumed browser-stage COPY input: ' + source})
        return bool(triggers), triggers, []
    except (KeyError, ValueError, TypeError, UnicodeError, OSError, subprocess.SubprocessError):
        return True, triggers, ['Unavailable browser history or unsupported Docker syntax requires qualification']


def browser_required(paths, event=None, name=None):
    return browser_selection(paths, event, name)[0]


# Ordinary consumers. Full manual/tag/candidate calls never use a diff omission.
COMPONENTS = ('application', 'portable', 'package_macos', 'package_linux', 'fixtures',
              'npm', 'readiness', 'codex_config', 'contracts')
ALL = set(COMPONENTS)
PACKAGES = {'portable', 'package_macos', 'package_linux'}
CI_POLICY_TESTS = {
    'test_apparmor_ci.py', 'test_apparmor_host.py', 'test_browser_image.py',
    'test_browser_routing.py', 'test_ci.py', 'test_components.py',
    'test_macos_signing_entrypoint.py', 'test_manager_routing.py',
    'test_presentation.py', 'test_pr_policy.py', 'test_release_tests.py', 'test_workflow_names.py',
}
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


def consumer_selection(path):
    p = PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts:
        return ALL, 'unsafe or unrecognized path: conservative full qualification'
    if documentation(path):
        return set(), 'human-facing documentation allowlist'
    if path in ('.github/pull_request_template.md', '.github/workflows/pr-description-lint.yml',
                'scripts/ci/check_pr_body.py'):
        return set(), 'always-on PR metadata validation'
    if path.startswith('scripts/ci/tests/'):
        if p.name in CI_POLICY_TESTS and len(p.parts) == 4:
            return set(), 'cheap CI policy regression; executed by the always-on policy suite'
        return ALL, 'unknown CI regression input: conservative full qualification'
    if path.startswith('scripts/ci/') or path == '.github/workflows/change-classification.yml':
        return ALL, 'shared CI classifier, comparison or regression control'
    if path == '.github/workflows/make-all.yml':
        return {'application', 'npm', 'readiness', 'codex_config', 'contracts'}, 'application and fast-family workflow control'
    if path.startswith('.github/workflows/'):
        # Native package/release controls can affect portable and native evidence;
        # manual-only image controllers are validated by portable contract tests.
        if p.stem in ('runner-release', 'runner-integration', 'component-plan',
                       'transfer-timing', 'current-manager-package'):
            return {'portable'}, 'runner/component planning or composition control validated by portable tests'
        if p.stem in ('package-checks', 'release-native', 'release-preflight',
                       'host-policy', 'native-manager-helpers', 'burrito-release'):
            return PACKAGES | {'fixtures'}, 'portable/native package qualification workflow control'
        if p.stem == 'browser-smoke':
            return {'portable'}, 'browser workflow migration control'
        if p.stem in ('macos-signing', 'macos-production-signing', 'production-publish'):
            return {'portable'}, 'explicit signing workflow policy; no ordinary Apple operation'
        return ALL, 'unknown workflow: conservative full qualification'
    if path.startswith(('.codex/', '.agents/')) or p.name.upper() == 'WORKFLOW.MD':
        return PACKAGES | {'application'}, 'packaged behavioral skills or runtime workflow'
    if path == 'elixir/mix.exs':
        return {'application', 'portable'}, 'application and release-version input'
    if path == 'elixir/priv/codex_readiness.py':
        return {'application', 'readiness', 'codex_config'}, 'packaged Codex readiness implementation and its fast contracts'
    if path in ('elixir/priv/codex_config_boundary.py', 'elixir/priv/codex_config_snapshot.py',
                'elixir/priv/codex_launch.py'):
        return {'application', 'codex_config'}, 'packaged Codex configuration implementation and its fast contracts'
    if path.startswith('elixir/'):
        if p.name in ('codex_config_boundary_test.py', 'codex_config_snapshot_test.py'):
            return {'codex_config'}, 'cheap Codex file and mount-policy contracts'
        return {'application'}, 'application source, tests, dependency or toolchain input'
    if path in ('scripts/symphony', 'scripts/symphony-package.sh', 'scripts/symphony-package-update.sh'):
        return PACKAGES | {'application', 'fixtures', 'contracts', 'codex_config'}, 'installed manager launch/update and native process input'
    if path in ('scripts/symphony-environment.sh', 'scripts/symphony-apparmor.sh', 'scripts/symphony-runner-import.sh', 'scripts/symphony-runner-provision.sh'):
        return PACKAGES | {'application', 'contracts'}, 'installed environment input'
    if path.startswith('scripts/package/linux/') or path == 'scripts/release/build-linux-host-helper.sh':
        return {'portable', 'package_linux'}, 'Linux helper source, SDK or build input'
    if path.startswith('scripts/package/'):
        return PACKAGES, 'shared native helper/package input'
    if path in ('scripts/release/tests/test_codex_doctor_lock.py',
                'scripts/release/tests/test_project_credentials.py',
                'scripts/release/tests/test_readiness_stages.py',
                'scripts/release/tests/codex_config_fixture.py'):
        return {'contracts'}, 'cheap cross-platform launcher credential, readiness and lock contracts'
    if path in ('scripts/release/engine-smoke.py', 'scripts/release/tests/test_engine_smoke.py',
                'scripts/release/tests/test_package_update.py'):
        return {'portable', 'fixtures'}, 'native process or installed updater fixture'
    if path in ('scripts/release/tests/test_component_provenance.py',
                'scripts/release/tests/fixtures/manager-original-68.json'):
        return PACKAGES, 'packaged provenance validator fixture dependency'
    if path.startswith('scripts/release/tests/'):
        # Existing portable suite inventory is explicit; new tests remain native.
        spec = importlib.util.spec_from_file_location('ci_release_suites', Path(__file__).with_name('release_tests.py'))
        suites = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(suites)
        return ({'portable'}, 'audited portable release suite') if p.name in suites.PORTABLE else (ALL, 'unknown release suite: conservative full qualification')
    if path.startswith('scripts/release/'):
        relative = str(p.relative_to('scripts/release'))
        if relative in PORTABLE_RELEASE:
            return {'portable'}, 'release controller with audited portable tests'
        if relative in {'macos_signing_inputs.py', 'production_release.py'}:
            return {'portable'}, 'portable signing or package diagnostic policy; Apple work remains explicit'
        if relative in {'macos_distribution.py', 'signing_payload.py', 'package_diagnostics.py', 'package-diagnostics.sh'}:
            return PACKAGES, 'native manager assembly and diagnostic dependency; no application or process fixtures'
        if relative in {
            'artifacts.py', 'build-host-helper.sh', 'package.sh', 'component_provenance.py',
            'finite_transition.py', 'host-targets.json', 'manager-transition-policy.json',
            'manager_contract.py', 'manager_envelope.py', 'manager_inputs.py', 'manager_version.py',
            'manager-version', 'release-schema', 'runner_inputs.py', 'select-hosts.py',
            'component_join.py', 'component_selection.py', 'qualified_graph.py', 'reuse_payload.py',
        }:
            return PACKAGES, 'packaged release validator and transitive metadata dependency'
        return ALL, 'unknown release controller: conservative full qualification'
    if path.startswith('.github/actions/'):
        if path.split('/')[2] in ('build-runner', 'configure-runner-cache', 'runner-cache-runtime'):
            return {'portable'}, 'audited local build/cache action control'
        return ALL, 'unknown local action: conservative full qualification'
    if path.startswith('runner/templates/'):
        return PACKAGES | {'application'}, 'packaged team environment template'
    if path.startswith('runner/claude/qualification/'):
        return {'portable'}, 'explicit reference harness input'
    if path.startswith('runner/claude/') or path in ('runner/entrypoint.sh', 'runner/updater-smoke.py'):
        return {'application'}, 'Claude runtime or runner entrypoint input'
    if path in ('runner/test_npm_cache.py', 'runner/test_npm_cache_lifecycle.py'):
        return {'npm'}, 'npm configuration semantics and cross-platform native process ownership tests'
    if path == 'runner/npm-cache.npmrc':
        return {'portable', 'npm'}, 'npm configuration semantics and consumed runner build input'
    if path == 'runner/test_codex_readiness.py':
        return {'readiness'}, 'cheap Codex executable identity and readiness contracts'
    if path.startswith('runner/browser/') or path in ('runner/Dockerfile', '.dockerignore',
            'runner/Dockerfile.dockerignore', 'runner/node-smoke.sh'):
        return {'portable'}, 'browser build input; separate Docker graph qualification'
    return ALL, 'unrecognized path: conservative full qualification'

def consumers(path):
    return consumer_selection(path)[0]


def decisions(paths):
    selected = set().union(*(consumers(path) for path in paths)) if paths else ALL
    return component_decisions(selected)


def component_decisions(selected):
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


def workflow_comparison(path, base, head):
    if not (path.startswith('.github/workflows/') and path.endswith(('.yml', '.yaml')) or
            path.startswith('.github/actions/') and PurePosixPath(path).name in ('action.yml', 'action.yaml')):
        return {}
    try:
        payload = {'path': path, 'before': git('show', base + ':' + path).decode(),
                   'after': git('show', head + ':' + path).decode()}
        process = subprocess.run(['ruby', str(Path(__file__).with_name('presentation_only.rb'))],
                                 input=json.dumps(payload), text=True, capture_output=True, timeout=10)
        if process.returncode:
            return {}
        result = json.loads(process.stdout)
        if type(result.get('presentation_only')) is not bool:
            raise ValueError('invalid comparison result')
        return result
    except (ValueError, UnicodeError, OSError, subprocess.SubprocessError):
        return {}


def presentation_only(path, base, head):
    if not (path.startswith('.github/workflows/') and path.endswith(('.yml', '.yaml')) or
            path.startswith('.github/actions/') and PurePosixPath(path).name in ('action.yml', 'action.yaml')):
        return False, None
    result = workflow_comparison(path, base, head)
    return result.get('presentation_only', False), (None if result else
        'YAML comparison unavailable or refused; ordinary conservative path policy applies')


def event_routing(paths, event, name):
    triggers = {key: [] for key in COMPONENTS}
    presentation = []
    fallbacks = []
    active = []
    selected_consumers = set()
    if not paths:
        fallbacks.append('Full-event policy or unavailable/empty event diff requires all consumers')
        return decisions([]), {key: [] for key in COMPONENTS}, [], [], fallbacks
    try:
        base, head = changed_revisions(event, name)
    except (KeyError, TypeError, ValueError, UnicodeError, OSError, subprocess.SubprocessError):
        base = head = None
    for path in paths:
        only_names, fallback = presentation_only(path, base, head) if base else (False, None)
        if only_names:
            presentation.append(path)
            continue
        active.append(path)
        if fallback:
            fallbacks.append(path + ': ' + fallback)
        selected, rule = consumer_selection(path)
        if fallback:
            selected, rule = ALL, 'unsupported or unavailable workflow comparison: conservative full qualification'
        if path == '.github/workflows/make-all.yml' and base:
            families = workflow_comparison(path, base, head).get('fast_workflow_families')
            if families and set(families) <= {'npm', 'readiness', 'codex_config'}:
                selected, rule = set(families), 'only audited cheap test commands changed; other workflow controls identical'
        selected_consumers.update(selected)
        for key in selected:
            triggers[key].append({'path': path, 'rule': rule})
        if 'conservative' in rule:
            fallbacks.append(path + ': ' + rule)
    # An empty set after proven presentation omission is not missing history.
    route = component_decisions(selected_consumers) if active else dict.fromkeys(COMPONENTS, False)
    if not active:
        route.update(native=False, native_platform='all')
    return route, triggers, active, presentation, fallbacks


def classify(event, name):
    try:
        paths = changed_paths(event, name)
        code = [p for p in paths if not documentation(p)]
        return not code, ('all changed paths are human-facing documentation' if not code
                          else 'selected consumers and exact triggering rules are listed below'), paths
    except (KeyError, TypeError, ValueError, UnicodeError, OSError, subprocess.SubprocessError):
        return False, 'full-event policy or unavailable/incomplete diff requires code qualification', []


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    ready = True
    if os.environ['GITHUB_EVENT_NAME'] == 'pull_request':
        draft = event['pull_request']['draft']
        if type(draft) is not bool:
            raise ValueError('PR draft readiness must be a boolean')
        ready = not draft
    docs, reason, paths = classify(event, os.environ['GITHUB_EVENT_NAME'])
    routing, triggers, active, presentation, fallbacks = event_routing(paths, event, os.environ['GITHUB_EVENT_NAME'])
    if paths and not active:
        browser, browser_triggers, browser_fallbacks = False, [], []
        value = 'false'
        reason = 'only verified presentation fields changed; policy, graph and protection contracts still validated'
    else:
        browser, browser_triggers, browser_fallbacks = browser_selection(active, event, os.environ['GITHUB_EVENT_NAME'])
        value = 'true' if browser or not docs else 'false'
        if browser and docs:
            reason = 'browser-consumed inputs or browser validation require code qualification'
    fallbacks.extend(browser_fallbacks)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f'code={value}\n')
            f.write('policy=' + str(not docs or browser).lower() + '\n')
            f.write('browser=' + str(browser).lower() + '\n')
            f.write('ready=' + str(ready).lower() + '\n')
            for key, required in routing.items():
                f.write(key + '=' + str(required).lower() + '\n')
    summary = f'### Change classification\n\nCode qualification required: **{value}**. {reason}.\n\n'
    summary += 'Expensive qualification enabled: **' + str(ready).lower() + '**. Draft-deferred required consumers remain non-passing until ready qualification succeeds.\n\n'
    summary += 'CI policy/graph validation required: **' + str(not docs or browser).lower() + '**.\n\n'
    summary += 'Browser qualification required: **' + str(browser).lower() + '**. ' + (
        'Browser inputs, validation controls or full-event policy require qualification.\n\n'
        if browser else 'No browser build/qualification inputs changed.\n\n')
    for key, required in routing.items():
        summary += f'{key}: **{str(required).lower()}**.\n\n'
    for key in COMPONENTS:
        summary += key + ' triggering inputs:\n'
        for trigger in triggers[key]:
            summary += '    ' + json.dumps(trigger, sort_keys=True) + '\n'
        if not triggers[key]:
            summary += ('    conservative full-event fallback; no trustworthy path attribution\n' if not paths
                        else '    no relevant changes\n')
        summary += '\n'
    summary += 'browser triggering inputs:\n' + '\n'.join('    ' + json.dumps(item, sort_keys=True) for item in browser_triggers) + '\n'
    if not browser_triggers:
        summary += '    ' + ('conservative browser fallback' if browser else 'no relevant changes') + '\n'
    summary += '\nVerified presentation-only files (always-on policy/graph/protection metadata checks remain):\n'
    summary += '\n'.join('    ' + json.dumps(path) for path in presentation) + '\n\n'
    summary += 'Conservative fallback reasons:\n' + '\n'.join('    ' + json.dumps(item) for item in fallbacks) + '\n\n'
    # Render arbitrary Git paths as escaped JSON inside a code block.
    summary += 'Changed paths: ' + str(len(paths)) + '\n\n' + '\n'.join('    ' + json.dumps(p) for p in paths) + '\n'
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(summary)


if __name__ == '__main__':
    main()
