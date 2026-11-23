#!/usr/bin/env python3
"""Normalize a saved official template; no network or package installation."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def normalize(data, fmt, template_id, name=None):
    if fmt == 'github_form':
        if not isinstance(data, dict) or not isinstance(data.get('body'), list):
            raise ValueError('A GitHub form requires a body element list')
        fields, seen = [], set()
        for index, raw in enumerate(data['body']):
            kind = raw['type']
            source_id = raw.get('id')
            fid = source_id or f'element-{index+1}'
            if not isinstance(fid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', fid) or fid in seen:
                raise ValueError('Invalid/duplicate source or generated ID: ' + str(fid))
            seen.add(fid)
            attrs, rules = raw.get('attributes', {}), raw.get('validations', {})
            if kind != 'markdown' and not isinstance(attrs.get('label'), str):
                raise ValueError('A submitted control needs its original label')
            if kind == 'dropdown' and (not isinstance(attrs.get('options'), list) or not all(isinstance(o, str) for o in attrs['options'])):
                raise ValueError('Dropdown options must preserve their string values')
            if kind == 'checkboxes' and (not isinstance(attrs.get('options'), list) or
                    not all(isinstance(o, dict) and isinstance(o.get('label'), str) for o in attrs['options'])):
                raise ValueError('Checkbox options need their original label/required records')
            fields.append(dict(id=fid, source_id=source_id, source_index=index, type=kind,
                label=attrs.get('label', 'Template instructions'), description=attrs.get('description', ''),
                purpose='instruction' if kind == 'markdown' else 'unassigned',
                required=bool(rules.get('required', False)), validations=rules,
                options=attrs.get('options', []), multiple=bool(attrs.get('multiple', False)),
                default=attrs.get('value', ''), default_index=attrs.get('default'),
                placeholder=attrs.get('placeholder', ''), render=attrs.get('render'), raw=raw))
        return dict(id=template_id, name=name or data.get('name', template_id), format=fmt,
                    description=data.get('description', ''), title_default=data.get('title', ''), fields=fields)
    body = data
    meta = {}
    if body.startswith('---\n') or body.startswith('---\r\n'):
        match = re.match(r'\A---\r?\n(.*?)\r?\n---\s*\r?\n', body, re.S)
        if match:
            # Preserve raw frontmatter regardless of availability of a YAML parser.
            meta['raw_frontmatter'] = match[1]
            body = body[match.end():]
    return dict(id=template_id, name=name or template_id, format=fmt, metadata=meta,
                fields=[dict(id='body', source_id=None, type='textarea', label='Issue body',
                             description='Fill the original Markdown template, preserving its sections.',
                             purpose='body', required=True, validations={'required':True}, default=body)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--format', choices=['github_form', 'markdown', 'generic'], required=True)
    parser.add_argument('--id', required=True)
    parser.add_argument('--name')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--url', default='')
    parser.add_argument('--revision', default='')
    parser.add_argument('--retrieved-at', default='')
    parser.add_argument('--official', action='store_true')
    parser.add_argument('--purpose-map', type=Path)
    parser.add_argument('--artifact-root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite normalized template')
    text = args.input.read_text(encoding='utf-8')
    if args.format == 'github_form':
        if args.input.suffix.lower() == '.json':
            raw = json.loads(text)
        else:
            try:
                import yaml
            except ImportError:
                parser.error('Use an existing interpreter with PyYAML, or provide JSON. No dependency will be installed.')
            try:
                raw = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                parser.error('Invalid YAML template: ' + str(exc))
    else:
        raw = text
    result = normalize(raw, args.format, args.id, args.name)
    try:
        snapshot_path = str(args.input.resolve().relative_to(args.artifact_root.resolve()))
    except ValueError:
        parser.error('Freeze the snapshot under --artifact-root first')
    result['source'] = dict(path=snapshot_path, sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                            url=args.url, revision=args.revision, retrieved_at=args.retrieved_at,
                            status='official' if args.official else 'unverified',
                            limitation='' if args.official else 'Source has not been verified as the official applicable template.')
    if args.official and (not args.url or not args.retrieved_at or args.format == 'generic'):
        parser.error('Official snapshots need URL, retrieval time and an actual official template format')
    if args.purpose_map:
        purposes = json.loads(args.purpose_map.read_text())
        for field in result['fields']:
            if field['id'] in purposes:
                entry = purposes[field['id']]
                field.update(entry if isinstance(entry, dict) else {'purpose':entry})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
