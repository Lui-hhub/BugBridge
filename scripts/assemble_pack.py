#!/usr/bin/env python3
"""Mechanical pack assembly only: no execution, network, installs or judgments."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from review_pack import read_json, safe_id, sha


class Builder:
    def __init__(self, pack_path):
        self.path = Path(pack_path).resolve()
        self.root = self.path.parent
        self.pack = read_json(self.path)
        if self.pack.get('schema_version') != '2.0':
            raise ValueError('Assembly requires a fresh 2.0 pack; do not rewrite historical packs')

    def reference(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError('Expected a file inside the pack root: ' + str(name))
        ref = dict(path=path.relative_to(self.root).as_posix(), sha256=sha(path))
        old = next((r for r in self.pack['sources'] if r['path'] == ref['path']), None)
        if old and old != ref:
            raise ValueError('A frozen source changed: ' + ref['path'])
        if not old:
            self.pack['sources'].append(ref)
        return ref

    def freeze(self, source, destination):
        content = Path(source).read_bytes()
        target = (self.root / destination).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError('Destination escapes pack root')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
        return self.reference(destination)

    def issue(self, iid):
        return next(i for i in self.pack['issues'] if i['id'] == iid)

    def add_material(self, mid, refs, description):
        if not safe_id(mid) or any(m['id'] == mid for m in self.pack['material_items']):
            raise ValueError('Material ID must be safe and unique')
        paths = [self.reference(name)['path'] for name in refs]
        self.pack['material_items'].append(dict(id=mid, refs=paths, description=description))

    def add_issue(self, iid, title, kind, action, reason, template_id=None):
        if not safe_id(iid) or any(i['id'] == iid for i in self.pack['issues']):
            raise ValueError('Issue ID must be safe and unique')
        if action == 'new_issue' and template_id not in {t['id'] for t in self.pack['templates']}:
            raise ValueError('Select a normalized template before preparing a new issue')
        record = dict(id=iid, title=title, kind=kind, material_ids=[], material_refs=[],
                      finding=dict(status='needs_review', reason=reason),
                      action=dict(type=action, reason=reason), responses={}, submission_todos=[],
                      publication=dict(quality_review=dict(status='pending', checks=[])),
                      reproduction=dict(status='insufficient_material', reason='Execution has not been prepared; inspect material and existing environments.',
                                        environment_selection=dict(status='not_checked', reason='Not yet inspected.'), runs=[], selected_run_ids=[]),
                      upstream=dict(relation='unknown', coverage='not_searched', reason='No upstream inspection performed.'))
        if template_id:
            record['template_id'] = template_id
        self.pack['issues'].append(record)

    def assign(self, mid, iid, decision, reason, merged_into=None):
        material = next(m for m in self.pack['material_items'] if m['id'] == mid)
        issue = self.issue(iid)
        if any(r['material_id'] == mid for r in self.pack['dispositions']):
            raise ValueError('Material already has a disposition; review explicit edits instead of silently reassigning')
        row = dict(material_id=mid, issue_id=iid, decision=decision, reason=reason)
        if merged_into:
            row['merged_into'] = merged_into
        self.pack['dispositions'].append(row)
        issue['material_ids'].append(mid)
        issue['material_refs'] = sorted(set(issue['material_refs']) | set(material['refs']))

    def register_run(self, iid, rid, result_name, assessment_name, select=False):
        issue = self.issue(iid)
        repro = issue['reproduction']
        if not safe_id(rid) or any(r['id'] == rid for r in repro.get('runs', [])):
            raise ValueError('Run ID must be safe and unique')
        result_ref, assessment_ref = self.reference(result_name), self.reference(assessment_name)
        result = read_json(self.root / result_ref['path'])
        assessment = read_json(self.root / assessment_ref['path'])
        for name in ['source', 'stdout', 'stderr']:
            path = (Path(result_ref['path']).parent / result[name]['path']).as_posix()
            if self.reference(path)['sha256'] != result[name]['sha256']:
                raise ValueError('Run artifact hash mismatch: ' + name)
        if assessment.get('run_id') != rid or assessment.get('result_sha256') != result_ref['sha256'] or assessment.get('source_sha256') != result['source']['sha256']:
            raise ValueError('Assessment must explicitly bind this result and source')
        criterion = assessment['criterion_ref']
        if self.reference(criterion['path']) != criterion:
            raise ValueError('Assessment criterion hash mismatch')
        definition = read_json(self.root / criterion['path'])
        for ref in definition.get('basis', []) + assessment.get('evidence', []):
            if self.reference(ref['path']) != {k:ref[k] for k in ['path', 'sha256']}:
                raise ValueError('Criterion/assessment evidence hash mismatch')
        repro.setdefault('runs', []).append(dict(id=rid, result=result_ref, assessment=assessment_ref))
        if select:
            if repro.get('criterion_ref') and repro['criterion_ref'] != criterion:
                raise ValueError('Selected runs must use the same frozen criterion')
            repro['criterion_ref'] = criterion
            repro.setdefault('selected_run_ids', []).append(rid)
        return dict(result=result_ref, assessment=assessment_ref)

    def set_response(self, iid, fid, text_path):
        issue = self.issue(iid)
        value = Path(text_path).read_text(encoding='utf-8')
        if issue['action']['type'] == 'existing_issue_comment' and fid == 'comment':
            issue['action']['comment'] = dict(value=value)
        else:
            template = next(t for t in self.pack['templates'] if t['id'] == issue.get('template_id'))
            field = next(f for f in template['fields'] if f['id'] == fid)
            if field['type'] not in {'input', 'textarea'}:
                raise ValueError('Use structured responses for selections, attestations and attachments')
            issue['responses'][fid] = dict(value=value)

    def save(self):
        fd, temporary = tempfile.mkstemp(prefix='.pack-', suffix='.json', dir=self.root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(self.pack, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
            os.replace(temporary, self.path)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    for key in ['run-id', 'materials-path', 'library', 'repository', 'resolution-reason']:
        init.add_argument('--' + key, required=True)
    init.add_argument('--template', action='append', type=Path, default=[])
    freeze = commands.add_parser('freeze')
    freeze.add_argument('--source', required=True, type=Path)
    freeze.add_argument('--as', dest='destination', required=True)
    material = commands.add_parser('material')
    material.add_argument('--id', required=True)
    material.add_argument('--source', required=True, action='append')
    material.add_argument('--description', required=True)
    issue = commands.add_parser('issue')
    for key in ['id', 'title', 'kind', 'reason']:
        issue.add_argument('--' + key, required=True)
    issue.add_argument('--action', choices=['new_issue', 'existing_issue_comment', 'needs_evidence', 'no_submission'], required=True)
    issue.add_argument('--template-id')
    assign = commands.add_parser('assign')
    assign.add_argument('--material-id', required=True)
    assign.add_argument('--issue-id', required=True)
    assign.add_argument('--decision', choices=['retained', 'merged', 'excluded', 'needs_evidence'], required=True)
    assign.add_argument('--reason', required=True)
    assign.add_argument('--merged-into')
    run = commands.add_parser('run')
    for key in ['issue-id', 'id', 'result', 'assessment']:
        run.add_argument('--' + key, required=True)
    run.add_argument('--select', action='store_true')
    response = commands.add_parser('response')
    for key in ['issue-id', 'field-id', 'file']:
        response.add_argument('--' + key, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'init':
            if not safe_id(args.run_id):
                raise ValueError('Unsafe run ID')
            args.pack.parent.mkdir(parents=True, exist_ok=True)
            pack = dict(schema_version='2.0', run_id=args.run_id, workflow_status='in_progress',
                        materials_path=args.materials_path,
                        library=dict(name=args.library, repository=args.repository, resolution_reason=args.resolution_reason),
                        sources=[], material_items=[], dispositions=[], templates=[read_json(p) for p in args.template],
                        issues=[], requests=[], review_limitations=[])
            with args.pack.open('x', encoding='utf-8') as stream:
                json.dump(pack, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
            return 0
        builder = Builder(args.pack)
        result = None
        if args.command == 'freeze':
            result = builder.freeze(args.source, args.destination)
        elif args.command == 'material':
            builder.add_material(args.id, args.source, args.description)
        elif args.command == 'issue':
            builder.add_issue(args.id, args.title, args.kind, args.action, args.reason, args.template_id)
        elif args.command == 'assign':
            builder.assign(args.material_id, args.issue_id, args.decision, args.reason, args.merged_into)
        elif args.command == 'run':
            result = builder.register_run(args.issue_id, args.id, args.result, args.assessment, args.select)
        elif args.command == 'response':
            builder.set_response(args.issue_id, args.field_id, args.file)
        builder.save()
        print(json.dumps(result or dict(updated=str(args.pack)), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
