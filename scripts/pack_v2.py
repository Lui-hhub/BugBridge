"""Evidence bindings and review states. Validation never executes a case."""
from datetime import datetime
import re

RELATIONS = {'unknown', 'same_issue', 'same_mechanism', 'related', 'no_match_in_scope', 'not_applicable'}
COVERAGE = {'complete_in_scope', 'partial', 'not_searched', 'not_applicable'}
ACTIONS = {'new_issue', 'existing_issue_comment', 'needs_evidence', 'no_submission'}
OUTCOMES = {'observed', 'not_observed', 'inconclusive', 'not_assessed'}
QUALITY_CHECKS = {'minimal_reproducer', 'concise_results', 'field_allocation', 'no_placeholders'}
PLACEHOLDER = re.compile(r'\[Not supplied\. The human reviewer|(?m:^\s*\[(?:TODO|TBD|FILL IN|HUMAN COMMENTARY)[^\n]*\]\s*$)', re.I)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def legacy_upstream(upstream):
    """Lossy adapter only for the original validator, never for display."""
    relation, coverage = upstream.get('relation'), upstream.get('coverage')
    status = relation
    if coverage in {'not_searched', 'not_applicable'}:
        status = coverage
    elif relation in {'unknown', 'no_match_in_scope'} and coverage == 'partial':
        status = 'incomplete'
    elif relation == 'unknown':
        status = 'incomplete'
    return dict(upstream, status=status)


def validate_v2(pack, base, verify, check, read_json, safe_id):
    items = pack.get('material_items', [])
    item_ids = [x.get('id') for x in items]
    check(bool(items) and all(safe_id(x) for x in item_ids) and len(set(item_ids)) == len(item_ids),
          'material_items need nonempty, safe, unique IDs')
    source_paths = {r.get('path') for r in pack.get('sources', [])}
    for item in items:
        check(bool(item.get('refs')) and set(item.get('refs', [])) <= source_paths,
              str(item.get('id')) + ': material item must cite declared frozen sources')
        check(bool(item.get('description')), str(item.get('id')) + ': material description missing')
    issues = {x['id']: x for x in pack.get('issues', [])}
    ledger = pack.get('dispositions', [])
    assigned = [x.get('material_id') for x in ledger]
    check(len(assigned) == len(set(assigned)), 'material dispositions contain duplicate assignments')
    check(set(assigned) == set(item_ids), 'material coverage has missing or unknown material IDs')
    entries = {x.get('material_id'): x for x in ledger}
    for row in ledger:
        mid, iid = row.get('material_id'), row.get('issue_id')
        check(iid in issues, str(mid) + ': unknown issue in material disposition')
        decision = row.get('decision')
        check(decision in {'retained', 'merged', 'excluded', 'needs_evidence'} and bool(row.get('reason')),
              str(mid) + ': disposition decision/reason missing')
        if iid in issues:
            fs = issues[iid].get('finding', {}).get('status')
            if fs == 'excluded':
                check(decision in {'excluded', 'merged'}, str(mid) + ': excluded group cannot retain a material item')
            if fs == 'deferred':
                check(decision in {'needs_evidence', 'merged'}, str(mid) + ': deferred group needs an evidence disposition')
        if decision == 'merged':
            target = entries.get(row.get('merged_into'))
            check(bool(target) and target.get('material_id') != mid and target.get('issue_id') == iid,
                  str(mid) + ': invalid material merge target')
            seen, current = set(), mid
            while current in entries and entries[current].get('decision') == 'merged':
                if current in seen:
                    check(False, str(mid) + ': material merge cycle')
                    break
                seen.add(current)
                current = entries[current].get('merged_into')
    for iid, issue in issues.items():
        bound = issue.get('material_ids', [])
        expected = {x['material_id'] for x in ledger if x.get('issue_id') == iid}
        check(bool(bound) and len(bound) == len(set(bound)) and set(bound) == expected,
              iid + ': issue material_ids do not match its disposition ledger')
        expected_refs = {ref for item in items if item['id'] in expected for ref in item.get('refs', [])}
        check(expected_refs <= set(issue.get('material_refs', [])), iid + ': issue omits its assigned material source files')
        current, seen = iid, set()
        while current in issues and issues[current].get('finding', {}).get('status') == 'merged':
            if current in seen:
                check(False, iid + ': issue merge cycle')
                break
            seen.add(current)
            current = issues[current]['finding'].get('merged_into')
        if issue.get('finding', {}).get('status') == 'merged' and current in issues:
            check(issues[current].get('finding', {}).get('status') not in {'excluded', 'deferred'},
                  iid + ': merge destination must resolve to a retained group')
        validate_reproduction(iid, issue, base, verify, check, read_json, safe_id)
        validate_upstream(iid, issue, pack, verify, check)
        validate_submission(iid, issue, verify, check, safe_id, read_json, pack)


def validate_reproduction(iid, issue, base, verify, check, read_json, safe_id):
    repro = issue.get('reproduction', {})
    runs = repro.get('runs', [])
    run_ids = [r.get('id') for r in runs]
    check(all(safe_id(x) for x in run_ids) and len(set(run_ids)) == len(run_ids), iid + ': run IDs must be safe and unique')
    selected = repro.get('selected_run_ids', [])
    check(len(selected) == len(set(selected)) and set(selected) <= set(run_ids), iid + ': invalid selected run IDs')
    criterion_ref = repro.get('criterion_ref', {})
    criterion = None
    if criterion_ref:
        path = verify(criterion_ref)
        if path:
            criterion = read_json(path)
            check(bool(criterion.get('symptom') and criterion.get('expected_behavior') and criterion.get('basis')),
                  iid + ': criterion needs symptom, expectation and independent basis')
            check(timestamp(criterion.get('defined_at')) is not None, iid + ': criterion needs a timezone-aware definition time')
            for ref in criterion.get('basis', []):
                verify(ref)
            for metric in criterion.get('metrics', []):
                check(all(metric.get(k) for k in ['name', 'unit', 'definition', 'scope']),
                      iid + ': metric needs name, unit, definition and measurement scope')
    if runs:
        check(bool(criterion) and bool(selected), iid + ': runs need a frozen criterion and explicit selected runs')
    outcomes, results = {}, {}
    for run in runs:
        rid = run.get('id')
        rp, ap = verify(run.get('result', {})), verify(run.get('assessment', {}))
        if not rp or not ap:
            continue
        result, assessment = read_json(rp), read_json(ap)
        results[rid] = result
        check(assessment.get('run_id') == rid and assessment.get('result_sha256') == run['result'].get('sha256'),
              iid + ':' + str(rid) + ': assessment is bound to another run/result')
        check(assessment.get('source_sha256') == result.get('source', {}).get('sha256'),
              iid + ':' + str(rid) + ': assessment is bound to another source version')
        ar = assessment.get('criterion_ref', {})
        cp = verify(ar)
        old_criterion = read_json(cp) if cp else {}
        check(bool(old_criterion.get('symptom') and old_criterion.get('expected_behavior') and old_criterion.get('basis')),
              iid + ':' + str(rid) + ': run criterion lacks symptom, expectation or basis')
        for ref in old_criterion.get('basis', []):
            verify(ref)
        check(bool(assessment.get('reason')) and assessment.get('outcome') in OUTCOMES,
              iid + ':' + str(rid) + ': symptom outcome/reason missing')
        start, defined = timestamp(result.get('started_at')), timestamp(old_criterion.get('defined_at'))
        assessed = timestamp(assessment.get('assessed_at'))
        check(start is not None and defined is not None and defined <= start,
              iid + ':' + str(rid) + ': criterion must be frozen before execution')
        check(assessed is not None and start is not None and assessed >= start,
              iid + ':' + str(rid) + ': assessment time predates execution')
        outcome = assessment.get('outcome')
        if outcome in {'observed', 'not_observed'}:
            check(result.get('execution_status') != 'setup_error', iid + ':' + str(rid) + ': setup failure cannot judge the symptom')
            check(bool(assessment.get('evidence')), iid + ':' + str(rid) + ': symptom judgment needs cited run output')
        output_paths = {(rp.parent / result.get(k, {}).get('path', '')).resolve() for k in ['stdout', 'stderr']}
        cites_output = False
        for evidence in assessment.get('evidence', []):
            ep = verify(evidence)
            if ep in output_paths:
                cites_output = True
        if outcome in {'observed', 'not_observed'}:
            check(cites_output, iid + ':' + str(rid) + ': assessment must cite this run stdout/stderr')
        if rid in selected:
            check(ar == criterion_ref, iid + ':' + str(rid) + ': selected assessment uses a different criterion')
            outcomes[rid] = outcome
    if repro.get('status') in {'reproduced', 'not_reproduced'}:
        check(bool(criterion) and bool(selected), iid + ': final judgment needs frozen criterion and selected runs')
        if repro['status'] == 'reproduced':
            check('observed' in outcomes.values(), iid + ': reproduced requires an observed selected run')
        else:
            check(bool(outcomes) and all(outcomes.get(r) == 'not_observed' for r in selected),
                  iid + ': not_reproduced requires not_observed for every selected run')
    publication = issue.get('publication', {}).get('reproducer')
    if publication:
        src = verify(publication.get('source', {}))
        bindings = publication.get('run_ids', [])
        check(bool(bindings) and len(bindings) == len(set(bindings)) and set(bindings) <= set(selected),
              iid + ': publication reproducer must bind selected runs')
        for rid in bindings:
            result = results.get(rid, {})
            check(src is not None and publication['source'].get('sha256') == result.get('source', {}).get('sha256'),
                  iid + ':' + str(rid) + ': publication code differs from the executed source; re-execute edits')
        fid = publication.get('field_id')
        value = issue.get('responses', {}).get(fid, {}).get('value', '') if fid != 'comment' else issue.get('action', {}).get('comment', {}).get('value', '')
        check(bool(fid) and src is not None and isinstance(value, str) and src.read_text(encoding='utf-8').rstrip('\n') in value,
              iid + ': publication code must occur in its declared submission field')


def validate_upstream(iid, issue, pack, verify, check):
    up = issue.get('upstream', {})
    relation, coverage = up.get('relation'), up.get('coverage')
    check(relation in RELATIONS and coverage in COVERAGE, iid + ': separate upstream relation and search coverage')
    if relation in {'same_issue', 'same_mechanism'}:
        check(any(m.get('relation') == relation or (relation == 'same_mechanism' and m.get('relation') == 'same_issue') for m in up.get('matches', [])),
              iid + ': overall relation needs a matching claim-level comparison')
    if coverage in {'partial', 'complete_in_scope'}:
        check(bool(up.get('checked_at') and up.get('scope') and up.get('stop_reason')),
              iid + ': searched scope, date and stopping reason missing')
        check(bool(up.get('queries') or up.get('matches')), iid + ': searched scope needs queries or inspected matches')
    if coverage == 'complete_in_scope':
        check(not up.get('gaps') and all(q.get('complete') is True and not q.get('gaps') for q in up.get('queries', [])),
              iid + ': complete search cannot hide query gaps')
    if coverage == 'partial':
        check(bool(up.get('gaps')) or any(q.get('gaps') for q in up.get('queries', [])), iid + ': partial search must describe gaps')
    if coverage in {'not_searched', 'not_applicable'}:
        check(not up.get('queries') and not up.get('matches'), iid + ': inspected matches cannot be labelled not searched')
    for query in up.get('queries', []):
        check(bool(query.get('query') and query.get('checked_at')) and type(query.get('complete')) is bool,
              iid + ': query text, date and completeness missing')
        verify(query.get('evidence', {}))
    for match in up.get('matches', []):
        check(match.get('relation') in RELATIONS and bool(match.get('claim_comparison')),
              iid + ': each match must compare the actual report request')
        check(bool(match.get('evidence')), iid + ': upstream match needs frozen inspection evidence')
        for ref in match.get('evidence', []):
            verify(ref)
        if match.get('kind') == 'pull_request':
            tracker = match.get('tracker_state', {})
            check(tracker.get('state') in {'open', 'closed', 'unknown'} and (tracker.get('merged') is None or type(tracker.get('merged')) is bool),
                  iid + ': PR needs separate tracker and merge state')
            check('merged' in tracker and not (tracker.get('state') == 'open' and tracker.get('merged') is True),
                  iid + ': inconsistent/missing PR merge state')
            scope, release = match.get('fix_scope', {}), match.get('release_inclusion', {})
            check(isinstance(scope, dict) and scope.get('covers_claim') in {'yes', 'no', 'partial', 'unknown'} and bool(scope.get('summary')),
                  iid + ': PR needs structured actual fix scope')
            if not isinstance(scope, dict):
                scope = {}
            check(release.get('status') in {'included', 'not_included', 'unknown', 'not_applicable'},
                  iid + ': PR needs independent release inclusion state')
            for facts in [tracker, scope, release]:
                for ref in facts.get('evidence', []):
                    verify(ref)
            if tracker.get('state') != 'unknown' or tracker.get('merged') is not None:
                check(bool(tracker.get('evidence')), iid + ': PR state/merge facts need evidence')
            if scope.get('covers_claim') != 'unknown':
                check(bool(scope.get('evidence')), iid + ': actual fix-scope assertion needs patch evidence')
            if release.get('status') in {'included', 'not_included'}:
                check(bool(release.get('version') and release.get('evidence')), iid + ': release inclusion needs version and evidence')


def validate_submission(iid, issue, verify, check, safe_id, read_json, pack):
    action = issue.get('action', {})
    kind = action.get('type')
    check(kind in ACTIONS and bool(action.get('reason')), iid + ': explicit next action/reason missing')
    finding = issue.get('finding', {}).get('status')
    if finding in {'excluded', 'merged'}:
        check(kind == 'no_submission', iid + ': excluded/merged record must use no_submission')
    if finding == 'deferred':
        check(kind in {'needs_evidence', 'no_submission'}, iid + ': deferred record cannot be a new report')
    retained = issue.get('publication', {}).get('retained_copy')
    if retained is not None:
        check(kind == 'new_issue', iid + ': retained copy requires original issue fields')
        check(isinstance(retained, dict), iid + ': retained copy must cite its original compiled draft')
        if isinstance(retained, dict):
            check(retained.get('requested_by_operator') is True and bool(retained.get('reason')),
                  iid + ': retaining a known draft requires an explicit operator request and reason')
            original_path = verify(retained.get('original_pack', {}))
            original = read_json(original_path) if original_path else {}
            check(original.get('schema_version') == '2.0' and bool(original.get('form_binding')),
                  iid + ': retained copy must reference a previously compiled pack')
            check(original.get('materials_path') == pack.get('materials_path') and
                  original.get('library', {}).get('repository') == pack.get('library', {}).get('repository'),
                  iid + ': retained copy changed the original material/project scope')
            previous = next((x for x in original.get('issues', []) if x.get('id') == iid), {})
            template = lambda data: next((x for x in data.get('templates', []) if x.get('id') == previous.get('template_id')), {})
            old_template, current_template = template(original), template(pack)
            check(old_template.get('fields') == current_template.get('fields') and
                  old_template.get('format') == current_template.get('format') and
                  old_template.get('source', {}).get('url') == current_template.get('source', {}).get('url'),
                  iid + ': retained copy changed the original form destination')
            check(previous.get('action', {}).get('type') == 'new_issue',
                  iid + ': retained copy must preserve the original field destination')
            check(previous.get('publication', {}).get('quality_review', {}).get('status') == 'reviewed',
                  iid + ': retained original draft must have a completed content review')
            check(issue.get('title') == previous.get('title') and issue.get('template_id') == previous.get('template_id'),
                  iid + ': retained copy changed the original title or template')
            values = lambda item: {key: value.get('value') for key, value in item.get('responses', {}).items()}
            check(values(issue) == values(previous), iid + ': retained copy changed the original field values')
    if issue.get('upstream', {}).get('relation') == 'same_issue':
        check(kind != 'new_issue' or retained is not None,
              iid + ': known same issue must not create another new-issue draft')
    if kind == 'existing_issue_comment':
        target = action.get('target_url')
        check(any(m.get('url') == target and m.get('kind') == 'issue' for m in issue.get('upstream', {}).get('matches', [])),
              iid + ': follow-up target must be an inspected official issue')
        check(isinstance(action.get('comment', {}).get('value', ''), str), iid + ': comment must be text')
    todos = issue.get('submission_todos', [])
    ids = [x.get('id') for x in todos]
    check(all(safe_id(x) for x in ids) and len(ids) == len(set(ids)), iid + ': submission todo IDs must be safe and unique')
    for todo in todos:
        check(bool(todo.get('label')) and todo.get('state') in {'pending', 'done', 'not_applicable'}, iid + ': todo label/state missing')
        if todo.get('kind') in {'policy', 'human_commentary'}:
            check(bool(todo.get('basis')), iid + ': policy-derived todo must cite this repository policy snapshot')
        for ref in todo.get('basis', []):
            verify(ref)
        if todo.get('state') == 'done' and todo.get('kind') in {'policy', 'human_commentary', 'attestation', 'attachment'}:
            check(todo.get('confirmed_by_operator') is True, iid + ': personal submission task requires actual operator confirmation')
    quality = issue.get('publication', {}).get('quality_review', {})
    if kind in {'new_issue', 'existing_issue_comment'}:
        check(quality.get('status') in {'pending', 'reviewed'}, iid + ': record publication quality-review state')
        checks = quality.get('checks', [])
        if quality.get('status') == 'reviewed':
            check(quality.get('reviewer') in {'agent', 'human'}, iid + ': identify publication quality reviewer')
            if quality.get('reviewer') == 'human':
                check(quality.get('confirmed_by_operator') is True, iid + ': human review requires actual operator confirmation')
            check(bool(quality.get('notes')) and timestamp(quality.get('reviewed_at')) is not None,
                  iid + ': quality review needs dated reasoning')
            check({x.get('id') for x in checks} == QUALITY_CHECKS and len(checks) == len(QUALITY_CHECKS),
                  iid + ': quality review must cover minimal code, concise results, field allocation and placeholders')
            for entry in checks:
                check(entry.get('status') in {'pass', 'needs_work', 'not_applicable'} and bool(entry.get('reason')),
                      iid + ': publication quality check needs a reasoned outcome')
            if issue.get('reproduction', {}).get('status') == 'reproduced':
                minimal = next((x for x in checks if x.get('id') == 'minimal_reproducer'), {})
                if minimal.get('status') == 'pass':
                    check(bool(issue.get('publication', {}).get('reproducer')), iid + ': minimal-code pass requires a bound publication reproducer')


def enrich_cards(pack, report):
    """Keep legacy data readable, explicitly exposing absent v2 guarantees."""
    modern = pack.get('schema_version') == '2.0'
    originals = {x['id']: x for x in pack['issues']}
    if not modern:
        report['warnings'].append('旧版 1.0：未验证逐条材料覆盖、冻结判据及选定运行关联；处置建议由旧状态推导，需核实。')
    for card in report['cards']:
        issue = originals[card['id']]
        up = dict(issue.get('upstream', {}))
        if not modern:
            old = up.get('status')
            up['relation'] = old if old in RELATIONS else 'unknown'
            up['coverage'] = ('partial' if old == 'incomplete' or any(not q.get('complete') or q.get('gaps') for q in up.get('queries', []))
                              else 'not_searched' if old == 'not_searched' else 'not_applicable' if old == 'not_applicable' else 'partial')
        card['upstream'] = up
        action = dict(issue.get('action', {}))
        if not modern:
            status = issue['finding']['status']
            kind = 'no_submission' if status in {'excluded', 'merged'} else 'existing_issue_comment' if up['relation'] == 'same_issue' else 'new_issue'
            action = dict(type=kind, reason='由旧格式状态推导；需核实处置及投稿内容。', inferred=True)
            if kind == 'existing_issue_comment':
                action['target_url'] = next((m['url'] for m in up.get('matches', []) if m.get('kind') == 'issue'), '')
                explicit = issue.get('action', {})
                if explicit:
                    if explicit.get('type') != kind or explicit.get('target_url') != action['target_url'] or not isinstance(explicit.get('comment', {}).get('value'), str):
                        report['errors'].append(card['id'] + ': legacy follow-up must supply text for the inspected target issue')
                        report['ok'] = False
                    else:
                        action['comment'] = explicit['comment']
        card['action'] = action
        card['instructions'] = [f for f in card['fields'] if f['type'] == 'markdown']
        card['fields'] = [f for f in card['fields'] if f['type'] != 'markdown']
        card['submission_todos'] = list(issue.get('submission_todos', []))
        card['quality_warnings'] = []
        card['review_notices'] = []
        quality = issue.get('publication', {}).get('quality_review', {})
        if action.get('type') in {'new_issue', 'existing_issue_comment'}:
            if quality.get('status') != 'reviewed':
                card['quality_warnings'].append('投稿内容尚未完成精简与字段语义审查。')
            for entry in quality.get('checks', []):
                if entry.get('status') == 'needs_work':
                    card['quality_warnings'].append(entry.get('reason', ''))
        if action.get('type') != 'new_issue':
            if not modern:
                card['legacy_body'] = card['body']
            card['fields'], card['body'], card['blockers'] = [], '', []
            report['blockers'][card['id']] = []
            if action.get('type') == 'existing_issue_comment':
                value = action.get('comment', {}).get('value', '')
                card['fields'] = [dict(id='comment', label='补充已有 issue 的评论（非官方表单字段）', type='textarea', purpose='body', required=True, text=value, value=value, description='复制到所链接 issue 的评论框。')]
                card['body'] = value
                if not value:
                    card['blockers'].append('尚未整理已有 issue 的补充评论。')
        payload = '\n'.join(f['text'] for f in card['fields'])
        if PLACEHOLDER.search(payload):
            card['quality_warnings'].append('复制内容包含未填写的内部占位说明；提交前需整理。')
        if not modern and action.get('type') in {'new_issue', 'existing_issue_comment'} and 'Human commentary — required before submission' in (payload + card.get('legacy_body', '')):
            card['submission_todos'].append(dict(id='legacy-human-commentary', kind='human_commentary', state='pending', label='旧草稿声明尚缺人类解释；请核对已保存政策并由操作者填写。', basis_unverified=True))
        if up.get('coverage') == 'partial':
            card['review_notices'].append('上游检索部分完成；已有匹配仍有效，不能据此断言首次发现。')
        if up.get('coverage') == 'not_searched' and action.get('type') in {'new_issue', 'existing_issue_comment'}:
            card['quality_warnings'].append('尚未检索上游；提交前需进行查重比较。')
        card['publication'] = issue.get('publication', {})
        card['material_ids'] = issue.get('material_ids', [])
        pending = [x for x in card['submission_todos'] if x.get('state') == 'pending']
        card['readiness'] = dict(form_complete=not card['blockers'], pending_todos=len(pending),
                                 quality_review=quality.get('status', 'not_recorded'),
                                 status='archive' if action.get('type') in {'needs_evidence', 'no_submission'} else
                                 'needs_work' if card['blockers'] or pending or card['quality_warnings'] else 'prepared_for_human_review')
        for run, binding in zip(card.get('runs', []), issue.get('reproduction', {}).get('runs', [])):
            run['id'] = binding.get('id', binding.get('result', {}).get('path', ''))
            run['selected'] = binding.get('id') in issue.get('reproduction', {}).get('selected_run_ids', [])
            run['assessment_ref'] = binding.get('assessment')
    report['coverage'] = dict(verified=modern and report['ok'], material_items=len(pack.get('material_items', [])), dispositions=len(pack.get('dispositions', [])))
    report['review_states'] = {c['id']: dict(c['readiness'], content_warnings=c['quality_warnings'], review_notices=c['review_notices']) for c in report['cards']}
    report['validation_scope'] = 'Artifact hashes, template/field constraints and declared data relationships; semantic correctness and human submission decisions are not certified.'
    report['counts'].update(report_candidates=sum(c['action']['type'] == 'new_issue' for c in report['cards']),
                            followups=sum(c['action']['type'] == 'existing_issue_comment' for c in report['cards']),
                            archives=sum(c['action']['type'] in {'needs_evidence', 'no_submission'} for c in report['cards']))
