#!/usr/bin/env python3
"""Validate and render a template-driven review pack. Never runs issue code."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlparse
from pack_v2 import enrich_cards, legacy_upstream, validate_v2

KINDS = {'input', 'textarea', 'dropdown', 'checkboxes', 'upload', 'markdown'}
PURPOSES = {'problem', 'reproducer', 'expected', 'observed', 'environment', 'documentation_fix',
            'attestation', 'attachments', 'body', 'other', 'instruction'}
REPRO = {'reproduced', 'not_reproduced', 'inconclusive', 'awaiting_environment',
         'insufficient_material', 'not_applicable', 'skipped_by_operator'}
EXECUTION = {'returned', 'nonzero_exit', 'process_signal', 'timeout', 'memory_limit', 'setup_error'}
UPSTREAM = {'not_searched', 'same_issue', 'same_mechanism', 'related', 'no_match_in_scope', 'incomplete', 'not_applicable'}
SLUG = re.compile(r'^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}$')
DOC_DUMP = re.compile(r'(?m)^\s*(?:Collecting environment information|PyTorch version:|Traceback \(most recent call last\)|RESULT=|ENV=|#{1,6} (?:Full environment reports|Related reports|Complete .* (?:stdout|stderr)))')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_id(value):
    return isinstance(value, str) and bool(SLUG.fullmatch(value)) and value not in {'.', '..'}


def within_repository(url, repositories):
    target = urlparse(url)
    return any(target.scheme in {'http', 'https'} and target.netloc.lower() == urlparse(repo).netloc.lower()
               and (target.path.rstrip('/') == urlparse(repo).path.rstrip('/') or
                    target.path.startswith(urlparse(repo).path.rstrip('/') + '/')) for repo in repositories if repo)


def artifact(base, ref):
    """Only read declared, hashed artifacts under the pack/attempt root."""
    name = ref['path']
    if not isinstance(name, str) or Path(name).is_absolute():
        raise ValueError('Artifact paths must be relative: ' + str(name))
    path = (base / name).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError('Artifact escapes its root or is missing: ' + name)
    if sha(path) != ref['sha256']:
        raise ValueError('Artifact hash mismatch: ' + name)
    return path


def field_text(field, response):
    value = response.get('value')
    if value is None:
        return ''
    if field['type'] == 'checkboxes':
        return '\n'.join(('- [x] ' if item['label'] in value else '- [ ] ') + item['label']
                         for item in field.get('options', []))
    if isinstance(value, list):
        return '\n'.join(value)
    return str(value)


def body_text(template, fields):
    if template['format'] in {'markdown', 'generic'}:
        return next((f['text'] for f in fields if f['type'] != 'markdown'), '')
    sections = []
    for field in fields:
        if field['type'] == 'markdown' or not field['text']:
            continue
        value = field['text']
        if field.get('render') and field['type'] == 'textarea':
            marker = '`' * max(3, max((len(x) + 1 for x in re.findall(r'`+', value)), default=3))
            value = marker + field['render'] + '\n' + value.rstrip('\n') + '\n' + marker
        sections.append('### ' + field['label'] + '\n\n' + value)
    return '\n\n'.join(sections)


def validate(pack, base, allow_pending=False, collect_artifacts=False):
    errors, warnings, blockers, cards = [], [], {}, []
    inventory = {}

    def check(ok, message):
        if not ok:
            errors.append(message)

    def verify(ref, root=base):
        try:
            path = artifact(root, ref)
            if collect_artifacts:
                name = Path(os.path.abspath(root/ref['path'])).relative_to(base.resolve()).as_posix()
                inventory[name] = dict(path=name,sha256=ref['sha256'])
            return path
        except (ValueError, KeyError, OSError, TypeError) as exc:
            errors.append(str(exc))
            return None

    modern = pack.get('schema_version') == '2.0'
    check(pack.get('schema_version') in {'1.0', '2.0'}, 'schema_version must be 1.0 or 2.0')
    check(pack.get('workflow_status') in {'in_progress', 'awaiting_environment', 'ready_for_review'}, 'workflow_status missing/invalid')
    check(safe_id(pack.get('run_id')), 'run_id must be a safe unique identifier')
    check(bool(pack.get('library', {}).get('name')), 'library.name is required')
    check(bool(pack.get('materials_path')), 'materials_path is required')
    check(bool(pack.get('library', {}).get('resolution_reason')), 'record official-tracker resolution evidence/reason')
    check(bool(pack.get('sources')), 'declare at least one frozen material source')
    source_paths = {ref.get('path') for ref in pack.get('sources', [])}
    for ref in pack.get('sources', []):
        verify(ref)
    requests = pack.get('requests', [])
    pending = [r for r in requests if r.get('state') == 'pending']
    for request in requests:
        check(bool(request.get('id')) and bool(request.get('question')), 'requests need id and actual question')
        check(bool(request.get('asked_at')), 'record when the operator was actually asked')
        if request.get('type') == 'environment':
            check(bool(request.get('requirements')) and bool(request.get('discovery_notes')), 'environment questions need requirements and local discovery/mismatch notes')
        if request.get('state') in {'answered', 'skipped_by_operator'}:
            check(bool(request.get('operator_response')), 'resolved requests need the operator response')
    if pending:
        check(pack.get('workflow_status') != 'ready_for_review', 'pending operator questions cannot be marked completed')
        check(allow_pending, 'operator response pending; use --allow-pending only for a visibly incomplete draft')

    templates, template_blockers = {}, {}
    for template in pack.get('templates', []):
        tid = template.get('id')
        check(safe_id(tid) and tid not in templates, 'template IDs must be safe and unique')
        templates[tid] = template
        check(template.get('format') in {'github_form', 'markdown', 'generic'}, 'unknown template format')
        source = template.get('source', {})
        template_blockers[tid] = []
        if source.get('path'):
            snapshot = verify(source)
            if snapshot and template.get('format') in {'github_form', 'markdown'}:
                try:
                    from normalize_template import normalize
                    raw = snapshot.read_text(encoding='utf-8')
                    if template['format'] == 'github_form':
                        if snapshot.suffix.lower() == '.json':
                            raw = json.loads(raw)
                        else:
                            import yaml
                            try:
                                raw = yaml.safe_load(raw)
                            except yaml.YAMLError as exc:
                                raise ValueError(str(exc)) from exc
                    original_fields = normalize(raw, template['format'], tid)['fields']
                    keys = ['id', 'source_id', 'source_index', 'type', 'label', 'description', 'required',
                            'validations', 'options', 'multiple', 'default', 'default_index', 'placeholder', 'render', 'raw']
                    check([{k:f.get(k) for k in keys} for f in template.get('fields', [])] ==
                          [{k:f.get(k) for k in keys} for f in original_fields],
                          str(tid) + ': field model differs from the preserved official snapshot')
                except ImportError:
                    warnings.append(str(tid) + ': existing YAML parser unavailable; template structure needs manual verification')
                    template_blockers[tid].append('模板结构尚未独立核对；请使用已有 YAML 解析环境')
                except (ValueError, KeyError, TypeError) as exc:
                    errors.append(str(tid) + ': cannot verify source template: ' + str(exc))
        if source.get('status') == 'official':
            check(bool(source.get('path') and source.get('url') and source.get('retrieved_at')), 'official template needs snapshot, URL and retrieval date')
            check(urlparse(source.get('url', '')).scheme in {'http', 'https'}, 'official source must have an HTTP(S) provenance URL')
        else:
            check(bool(source.get('limitation')), 'non-official/missing template needs an explicit limitation')
        seen = set()
        for field in template.get('fields', []):
            fid = field.get('id')
            check(safe_id(fid) and fid not in seen, 'field IDs must be safe and unique within template')
            seen.add(fid)
            check(isinstance(field.get('label'), str), 'field label must be preserved')
            if field.get('type') != 'markdown':
                check(field.get('purpose') in PURPOSES, 'assign a field purpose from its actual description: ' + str(fid))
        check(bool(seen), 'template has no fields')

    ids = [i.get('id') for i in pack.get('issues', [])]
    check(bool(ids) and len(set(ids)) == len(ids) and all(safe_id(i) for i in ids), 'issue IDs must be safe, nonempty and unique')
    for request in requests:
        check(bool(request.get('issue_ids')) and set(request.get('issue_ids', [])) <= set(ids), 'requests must reference existing issue IDs')
    for issue in pack.get('issues', []):
        iid = issue['id']
        problems = list(template_blockers.get(issue.get('template_id'), []))
        template = templates.get(issue.get('template_id'))
        form_required = not modern or issue.get('action', {}).get('type') == 'new_issue'
        if not form_required:
            template = dict(id='', name='', format='generic', fields=[])
            problems = []
            check(not issue.get('responses'), iid + ': comments/archives use no official form responses')
        if template is None:
            errors.append(iid + ': selected template does not exist')
            continue
        check(bool(issue.get('title')), iid + ': title missing')
        check(bool(issue.get('material_refs')) and set(issue.get('material_refs', [])) <= source_paths, iid + ': cite frozen material sources')
        finding = issue.get('finding', {})
        check(finding.get('status') in {'needs_review', 'supported', 'excluded', 'merged', 'deferred'}, iid + ': finding status missing/invalid')
        check(bool(finding.get('reason')), iid + ': finding reason missing')
        if finding.get('status') == 'merged':
            check(finding.get('merged_into') in ids and finding.get('merged_into') != iid, iid + ': invalid merge destination')
        reproduction = issue.get('reproduction', {})
        status = reproduction.get('status')
        check(status in REPRO and bool(reproduction.get('reason')), iid + ': reproduction status/reason missing')
        selection = reproduction.get('environment_selection', {})
        check(selection.get('status') in {'matched', 'missing', 'not_needed', 'not_checked', 'operator_skipped'} and bool(selection.get('reason')), iid + ': record local environment selection')
        if selection.get('status') == 'missing':
            check(status == 'awaiting_environment', iid + ': missing environment must actively await an operator response')
        if status in {'reproduced', 'not_reproduced'}:
            check(selection.get('status') == 'matched', iid + ': execution judgment needs a matching local environment')
        if status == 'awaiting_environment':
            matching = [r for r in pending if r.get('type') == 'environment' and iid in r.get('issue_ids', [])]
            check(bool(matching), iid + ': missing environment requires an active, actually asked operator question')
            check(not reproduction.get('runs'), iid + ': do not run while waiting for its environment')
            problems.append('等待操作者提供匹配环境')
        if status == 'skipped_by_operator':
            check(bool(reproduction.get('operator_decision')), iid + ': skipping requires explicit operator instruction')
        runs = []
        for run in reproduction.get('runs', []):
            result_path = verify(run.get('result', {}))
            if not result_path:
                continue
            result = read_json(result_path)
            check(result.get('execution_status') in EXECUTION, iid + ': invalid execution status')
            check(result.get('execution_performed') is True, iid + ': run does not record actual execution')
            check(isinstance(result.get('command'), list) and bool(result['command']), iid + ': command array missing')
            env = result.get('environment', {})
            check(env.get('verified') is True and bool(env.get('executable')) and bool(env.get('selection_reason')),
                  iid + ': execution requires a verified existing environment with selection reason')
            check(bool(env.get('library', {}).get('version')), iid + ': actual tested library version missing')
            view = dict(result)
            if modern:
                assessment_path = verify(run.get('assessment', {}))
                if assessment_path:
                    view['assessment'] = read_json(assessment_path)
            for key in ['source', 'stdout', 'stderr']:
                path = verify(result.get(key, {}), result_path.parent)
                view[key + '_text'] = path.read_text(encoding='utf-8', errors='replace') if path else ''
            runs.append(view)
        if status in {'reproduced', 'not_reproduced'}:
            check(bool(runs) and bool(reproduction.get('criterion') or (modern and reproduction.get('criterion_ref'))), iid + ': symptom judgment needs runs and a prior criterion')
        upstream = legacy_upstream(issue.get('upstream', {})) if modern else issue.get('upstream', {})
        us = upstream.get('status')
        check(us in UPSTREAM and bool(upstream.get('reason')), iid + ': upstream status/reason missing')
        if us in {'same_issue', 'same_mechanism', 'related'}:
            check(bool(upstream.get('matches')), iid + ': upstream relationship needs inspected matches')
        if us not in {'not_searched', 'not_applicable'}:
            check(bool(upstream.get('checked_at')), iid + ': record the upstream inspection date')
        if us == 'no_match_in_scope':
            check(bool(upstream.get('queries')) and bool(upstream.get('checked_at')), iid + ': no-match claim needs dated query evidence')
            check(all(q.get('complete') is True and not q.get('gaps') for q in upstream.get('queries', [])), iid + ': query gaps must not be reported as a complete negative search')
        for match in upstream.get('matches', []):
            check(urlparse(match.get('url', '')).scheme in {'http', 'https'}, iid + ': invalid upstream URL')
            repositories = [pack.get('library', {}).get('repository'), *pack.get('library', {}).get('official_repositories', [])]
            check(within_repository(match.get('url', ''), repositories), iid + ': comparison sources must belong to the verified official trackers')
            check(bool(match.get('reason')) and bool(match.get('trigger_comparison')), iid + ': compare actual triggers, not just titles')
            if match.get('kind') == 'pull_request':
                check(bool(match.get('fix_scope')), iid + ': PR needs explicit fix-scope comparison')
        responses = issue.get('responses', {})
        check(not (set(responses) - {f['id'] for f in template['fields']}), iid + ': response uses fields from another template')
        fields = []
        for field in template['fields']:
            fid, kind = field['id'], field.get('type')
            response = responses.get(fid, {})
            value = response.get('value')
            if kind == 'markdown':
                check(fid not in responses, iid + ': instruction markdown is not a submitted field')
                fields.append(dict(field, text=field.get('default', ''), has_value=False))
                continue
            if kind not in KINDS:
                problems.append('Unsupported control: ' + str(kind))
            extra_attributes = set(field.get('raw', {}).get('attributes', {})) - {'label', 'description', 'value', 'placeholder', 'render', 'options', 'default', 'multiple'}
            if extra_attributes:
                problems.append('Unreviewed template attributes: ' + ', '.join(sorted(extra_attributes)))
            validations = field.get('validations', {})
            unknown_rules = set(validations) - {'required', 'min_length'}
            if unknown_rules:
                problems.append('Unreviewed constraints: ' + ', '.join(sorted(unknown_rules)))
            if kind in {'input', 'textarea'} and value is not None:
                check(isinstance(value, str), iid + ':' + fid + ' expects text')
                if kind == 'input':
                    check('\n' not in str(value) and '\r' not in str(value), iid + ':' + fid + ' is single-line')
            if kind in {'dropdown', 'checkboxes', 'upload'} and value is not None:
                expected_list = kind != 'dropdown' or field.get('multiple')
                check(isinstance(value, list) if expected_list else isinstance(value, str), iid + ':' + fid + ' has wrong selection type')
                choices = value if isinstance(value, list) else [value]
                if kind in {'dropdown', 'checkboxes'}:
                    allowed = [o['label'] if isinstance(o, dict) else o for o in field.get('options', [])]
                    check(all(v in allowed for v in choices), iid + ':' + fid + ' contains a nonexistent option')
                if kind == 'checkboxes' and choices:
                    check(response.get('confirmed_by_operator') is True, iid + ':' + fid + ' cannot check declarations on behalf of the operator')
                if kind == 'upload' and choices:
                    attachments = response.get('attachments', [])
                    for ref in attachments:
                        verify(ref)
                    known = {a.get('name', a.get('path')) for a in attachments}
                    check(all(v in known for v in choices), iid + ':' + fid + ' cannot invent attached files')
            text = field_text(field, response)
            present = value is not None and bool(value)
            if field.get('purpose') == 'attestation' and present:
                check(response.get('confirmed_by_operator') is True, iid + ':' + fid + ' requires actual operator confirmation')
            if field.get('required') and not present:
                problems.append('Required field missing: ' + field['label'])
            if kind == 'checkboxes':
                for option in field.get('options', []):
                    if option.get('required') and option['label'] not in (value or []):
                        problems.append('Operator confirmation required: ' + option['label'])
            minimum = validations.get('min_length')
            if minimum is not None:
                check(type(minimum) is int and minimum >= 0, iid + ':' + fid + ' invalid min_length')
            if type(minimum) is int and present and isinstance(value, str):
                if len(value) < minimum:
                    problems.append('Minimum length not met: ' + field['label'])
            for ref in response.get('verbatim', []):
                path = verify(ref)
                if path:
                    check(path.read_text(encoding='utf-8', errors='replace').rstrip('\n') in text, iid + ':' + fid + ' omits declared complete evidence')
            if field.get('purpose') == 'documentation_fix':
                misplaced = set(response.get('content_tags', [])) & {'environment_report', 'execution_log', 'reproducer', 'upstream_inventory'}
                check(not misplaced and not DOC_DUMP.search(text), iid + ':' + fid + ' mixes evidence dumps into a documentation suggestion')
            fields.append(dict(field, text=text, has_value=present, value=value))
        if form_required and template.get('source', {}).get('status') != 'official':
            problems.append('模板并非已核验的官方快照')
        criterion_data = None
        if modern and reproduction.get('criterion_ref'):
            criterion_path = verify(reproduction['criterion_ref'])
            if criterion_path:
                criterion_data = read_json(criterion_path)
        blockers[iid] = problems
        cards.append(dict(id=iid, title=issue['title'], kind=issue.get('kind', 'other'),
                          template_id=template['id'], template_name=template.get('name', template['id']),
                          template_source=template.get('source', {}), finding=finding, reproduction=reproduction,
                          upstream=upstream, fields=fields, body=body_text(template, fields), blockers=problems,
                          runs=runs, criterion_data=criterion_data))
    if modern:
        validate_v2(pack, base, verify, check, read_json, safe_id)
    if pack.get('form_binding'):
        from field_contract import validate_binding
        validate_binding(pack, base, verify, check, read_json)
    report = dict(ok=not errors, errors=errors, warnings=warnings, blockers=blockers,
                pending_requests=pending, cards=cards,
                counts=dict(issues=len(cards), templates=len(templates), fields=sum(len(c['fields']) for c in cards)))
    enrich_cards(pack, report)
    report['counts']['fields'] = sum(len(c['fields']) for c in cards)
    if collect_artifacts:
        report['artifact_inventory'] = list(inventory.values())
    return report


def render(pack, report, output):
    if not report['ok']:
        raise ValueError('Cannot render an invalid pack')
    if output.exists():
        raise ValueError('Refusing to overwrite output directory: ' + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=output.name + '.tmp-', dir=output.parent))
    try:
        model = dict(run_id=pack['run_id'], library=pack['library'], workflow_status=pack['workflow_status'],
                     pending_requests=report['pending_requests'], cards=report['cards'],
                     schema_version=pack['schema_version'], counts=report['counts'], coverage=report['coverage'],
                     review_limitations=pack.get('review_limitations', []), validation_warnings=report['warnings'])
        asset = Path(__file__).resolve().parent.parent / 'assets/review.html'
        # Parse JSON rather than an object literal so keys such as __proto__
        # remain data. Quote twice: the outer JSON string is a JS string literal.
        data = json.dumps(json.dumps(model, ensure_ascii=False, allow_nan=False), ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
        shell = asset.read_text(encoding='utf-8')
        if shell.count('__REVIEW_DATA__') != 1:
            raise ValueError('HTML asset must contain exactly one data placeholder')
        (stage / 'REVIEW.html').write_text(shell.replace('__REVIEW_DATA__', data), encoding='utf-8')
        write_json(stage / 'issue_cards.json', model)
        write_json(stage / 'validation.json', {k:v for k,v in report.items() if k != 'cards'})
        for card in model['cards']:
            folder = stage / 'issues' / card['id']
            folder.mkdir(parents=True)
            (folder / 'title.txt').write_text(card['title'], encoding='utf-8')
            (folder / 'body.md').write_text(card['body'], encoding='utf-8')
            for field in card['fields']:
                if field['type'] != 'markdown':
                    (folder / (field['id'] + '.txt')).write_text(field['text'], encoding='utf-8')
        if output.exists():
            raise ValueError('Output appeared during rendering; refusing overwrite')
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pack', type=Path)
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--allow-pending', action='store_true')
    args = parser.parse_args()
    try:
        pack = read_json(args.pack)
        report = validate(pack, args.pack.resolve().parent, args.allow_pending)
        print(json.dumps({k:v for k,v in report.items() if k != 'cards'}, ensure_ascii=False))
        if not report['ok']:
            return 1
        if not args.validate_only:
            if not args.output_dir:
                parser.error('--output-dir is required for rendering')
            render(pack, report, args.output_dir.resolve())
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({'ok':False, 'errors':[str(exc)]}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
