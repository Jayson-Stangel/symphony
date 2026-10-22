#!/usr/bin/env python3
"""Validate the repository PR template using only the Python standard library."""
import argparse
import json
from pathlib import Path
import re


def lint(template, body):
    headings = re.findall(r'^#{4,6}\s+.+$', template, re.M)
    errors = []
    if not headings:
        return ['No markdown headings found in template']
    positions = [body.find(h) for h in headings]
    errors += ['Missing required heading: ' + h for h, p in zip(headings, positions) if p < 0]
    observed = [p for p in positions if p >= 0]
    if observed != sorted(observed):
        errors.append('Required headings are out of order.')
    if '<!--' in body:
        errors.append('PR description still contains template placeholder comments.')

    def section(doc, heading):
        start = doc.find(heading)
        if start < 0:
            return ''
        start += len(heading)
        ends = [doc.find('\n' + h, start) for h in headings if h != heading]
        ends = [end for end in ends if end >= 0]
        return doc[start:min(ends) if ends else len(doc)]

    for h, p in zip(headings, positions):
        if p < 0:
            continue
        content = section(body, h)
        if not content.strip():
            errors.append('Section cannot be empty: ' + h)
        if re.search(r'^- ', section(template, h), re.M) and not re.search(r'^- ', content, re.M):
            errors.append('Section must include at least one bullet item: ' + h)
        if re.search(r'^- \[ \] ', section(template, h), re.M) and not re.search(r'^- \[[ xX]\] ', content, re.M):
            errors.append('Section must include at least one checkbox item: ' + h)
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--event')
    parser.add_argument('--file')
    parser.add_argument('--template', default='.github/pull_request_template.md')
    args = parser.parse_args()
    body = (json.loads(Path(args.event).read_text())['pull_request'].get('body') or '') if args.event else Path(args.file).read_text()
    errors = lint(Path(args.template).read_text(), body)
    for error in errors:
        print('ERROR: ' + error)
    if errors:
        raise SystemExit(1)
    print('PR body format OK')


if __name__ == '__main__':
    main()
