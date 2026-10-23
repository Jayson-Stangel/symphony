#!/usr/bin/env python3
"""Conservative event-wide code and browser classification; no API or dependencies."""
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


# Dependency closure of browser-smoke -> build-runner -> browser-tools and
# node-smoke. Keep aligned with the build/consumer map in docs/development.md.
BROWSER_FILES = {
    'runner/Dockerfile', '.dockerignore', 'runner/Dockerfile.dockerignore',
    'runner/node-smoke.sh', 'scripts/release/runner-cache-policy.sh',
    '.github/workflows/browser-smoke.yml',
    '.github/workflows/change-classification.yml',
}
BROWSER_TREES = ('runner/browser/', '.github/actions/build-runner/', 'scripts/ci/')


def browser_required(paths):
    # Discovery failures (including empty diffs) never prove safe omission.
    return not paths or any(p in BROWSER_FILES or p.startswith(BROWSER_TREES) for p in paths)


def git(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE)


def changed_paths(event, name):
    if name == 'pull_request':
        base = event['pull_request']['base']['sha']
        head = event['pull_request']['head']['sha']
        # Compare the complete PR, including older commits and stale bases.
        base = git('merge-base', base, head).decode().strip()
    elif name == 'push':
        base, head = event['before'], event['after']
    else:
        raise ValueError('event requires full code qualification')
    if not all(re.fullmatch('[0-9a-f]{40}', s) and s != '0' * 40 for s in (base, head)):
        raise ValueError('missing or invalid event revisions')
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
    browser = 'true' if browser_required(paths) else 'false'
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f'code={value}\nbrowser={browser}\n')
    summary = f'### Change classification\n\nCode qualification required: **{value}**. {reason}.\n\n'
    summary += f'Browser qualification required: **{browser}**. ' + (
        'Browser build/qualification inputs changed or diff discovery is unavailable.\n\n'
        if browser == 'true' else 'No browser build/qualification inputs changed.\n\n')
    # Render arbitrary Git paths as escaped JSON inside a code block.
    summary += 'Changed paths: ' + str(len(paths)) + '\n\n' + '\n'.join('    ' + json.dumps(p) for p in paths) + '\n'
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(summary)


if __name__ == '__main__':
    main()
