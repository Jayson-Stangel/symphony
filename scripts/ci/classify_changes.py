#!/usr/bin/env python3
"""Conservative event-wide documentation classification; no API or dependencies."""
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
    }
    prefixes = ('.github/actions/', 'scripts/release/runner-cache-', 'scripts/release/build-runner-')
    try:
        if any(PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or
               path in exact or path.startswith(prefixes) for path in paths):
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


def git(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE)


def changed_revisions(event, name):
    if name == 'pull_request':
        base = event['pull_request']['base']['sha']
        head = event['pull_request']['head']['sha']
        # Compare the complete PR, including older commits and stale bases.
        base = git('merge-base', base, head).decode().strip()
    elif name == 'push':
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
    except (KeyError, ValueError, UnicodeError, subprocess.SubprocessError):
        return False, 'diff discovery unavailable or incomplete; require code qualification', []


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    docs, reason, paths = classify(event, os.environ['GITHUB_EVENT_NAME'])
    value = 'false' if docs else 'true'
    browser = browser_required(paths, event, os.environ['GITHUB_EVENT_NAME'])
    if browser:
        value = 'true'
        if docs:
            reason = 'browser-consumed inputs or browser validation require code qualification'
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f'code={value}\n')
            f.write('browser=' + str(browser).lower() + '\n')
    summary = f'### Change classification\n\nCode qualification required: **{value}**. {reason}.\n\n'
    summary += 'Browser input qualification required: **' + str(browser).lower() + '**.\n\n'
    # Render arbitrary Git paths as escaped JSON inside a code block.
    summary += 'Changed paths: ' + str(len(paths)) + '\n\n' + '\n'.join('    ' + json.dumps(p) for p in paths) + '\n'
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(summary)


if __name__ == '__main__':
    main()
