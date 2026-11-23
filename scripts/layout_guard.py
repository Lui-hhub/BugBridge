#!/usr/bin/env python3
"""Detect accidental changes to the approved view; never regenerates its baseline."""
import argparse
import hashlib
import json
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
VIEW_FILES = ('assets/review-app.html', 'assets/review-app.css', 'assets/review-app.js',
              'assets/markdown.js', 'assets/review.html')
MANIFEST = 'assets/view-manifest.json'


def inspect_layout(root=SKILL_ROOT, baseline=None):
    root = Path(root).resolve()
    path = Path(baseline) if baseline else root/MANIFEST
    errors, actual = [], {}
    try:
        expected = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(expected,dict) or expected.get('schema_version') != '1.0' or not isinstance(expected.get('files'),dict) or set(expected['files']) != set(VIEW_FILES):
            raise ValueError('View baseline has an invalid file set')
        for name in VIEW_FILES:
            target = (root/name).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                errors.append('Missing or escaping view file: '+name)
                continue
            actual[name] = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual[name] != expected['files'][name]:
                errors.append('Approved view file changed: '+name)
    except (OSError, ValueError, TypeError) as exc:
        errors.append('Cannot verify view baseline: '+str(exc))
    return dict(ok=not errors, errors=errors, files=actual)


def ensure_layout(root=SKILL_ROOT, baseline=None):
    report = inspect_layout(root, baseline)
    if not report['ok']:
        raise ValueError('\n'.join(report['errors']))
    return report


def freeze_layout(output, root=SKILL_ROOT):
    ensure_layout(root)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write((Path(root)/MANIFEST).read_bytes())
    return inspect_layout(root, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    freeze = sub.add_parser('freeze')
    freeze.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = freeze_layout(args.output) if args.command=='freeze' else ensure_layout()
        print(json.dumps(report, ensure_ascii=False))
    except (OSError, ValueError) as exc:
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__':
    main()
