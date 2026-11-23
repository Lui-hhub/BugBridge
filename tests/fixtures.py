"""Synthetic multi-template fixtures; no official-project or bug claims."""
import hashlib
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from normalize_template import normalize


def save(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return dict(path=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def form(root, tid, raw, purposes):
    ref = save(root, 'templates/' + tid + '.json', json.dumps(raw, ensure_ascii=False))
    result = normalize(raw, 'github_form', tid)
    result['source'] = dict(ref, status='unverified', limitation='Synthetic validation fixture, not an official project template.',
                            url='https://example.invalid/templates/' + tid, retrieved_at='2026-11-23')
    for field in result['fields']:
        if field['id'] in purposes:
            field['purpose'] = purposes[field['id']]
    return result


def make_pack(root):
    material = save(root, 'materials/cases.md', '# Synthetic material\nA CLI observation and a documentation wording example.\n')
    bug = form(root, 'bug', dict(name='Synthetic bug form', body=[
        dict(type='markdown', attributes=dict(value='Instructions are displayed, not submitted.')),
        dict(type='input', id='version', attributes=dict(label='Installed version'), validations=dict(required=True)),
        dict(type='textarea', id='problem', attributes=dict(label='What happened?', description='Describe the observed symptom.'), validations=dict(required=True)),
        dict(type='textarea', id='logs', attributes=dict(label='Log output', render='shell')),
        dict(type='dropdown', id='backend', attributes=dict(label='Backend', options=['local', 'other'], default=1), validations=dict(required=True)),
        dict(type='checkboxes', id='terms', attributes=dict(label='Review confirmation', options=[dict(label='I reviewed this report', required=True)])),
        dict(type='upload', id='attachment', attributes=dict(label='Attachments')),
    ]), dict(version='environment', problem='problem', logs='observed', backend='environment', terms='attestation', attachment='attachments'))
    doc = form(root, 'docs', dict(name='Synthetic documentation form', body=[
        dict(type='textarea', id='doc', attributes=dict(label='Documentation problem', description='Explain the mismatch and evidence.'), validations=dict(required=True)),
        dict(type='textarea', id='suggestion', attributes=dict(label='Suggested documentation change', description='How should the documentation improve?')),
    ]), dict(doc='problem', suggestion='documentation_fix'))
    mdref = save(root, 'templates/legacy.md', '---\nname: Legacy report\n---\n## Problem\n\n## Steps\n')
    md = normalize((root / mdref['path']).read_text(), 'markdown', 'legacy')
    md['source'] = dict(mdref, status='unverified', limitation='Synthetic Markdown fixture.')
    issues = []
    for iid, kind, tid, values in [
        ('cli-case', 'bug', 'bug', dict(version='local-existing-version', problem='Synthetic observation awaiting human review.', logs='line one\nline two', backend='local')),
        ('doc-case', 'documentation', 'docs', dict(doc='The guide and example use inconsistent terms.', suggestion='Replace the obsolete parameter name in the example and align it with the parameter reference.')),
        ('legacy-case', 'bug', 'legacy', dict(body='## Problem\n\nSynthetic case.\n\n## Steps\n\nSee supplied material.')),
    ]:
        issues.append(dict(id=iid, title='Synthetic ' + iid, kind=kind, template_id=tid, material_refs=[material['path']],
                           finding=dict(status='needs_review', reason='Synthetic test data, not a verified real-world bug.'),
                           reproduction=dict(status='not_applicable', reason='Rendering/contract fixture only; no target-library execution.',
                                             environment_selection=dict(status='not_needed', reason='Synthetic template test.')),
                           upstream=dict(status='not_searched', reason='Synthetic fixture; no external search was performed.'),
                           responses={fid:dict(value=value) for fid,value in values.items()}))
    return dict(schema_version='1.0', run_id='synthetic-template-review', workflow_status='ready_for_review',
                materials_path='synthetic-test-materials',
                library=dict(name='ExampleLib — synthetic fixture', repository='https://example.invalid/examplelib',
                             resolution_reason='Deliberately synthetic test identity; no official repository claim.'),
                sources=[material], templates=[bug,doc,md], issues=issues, requests=[])


def make_v2_pack(root):
    pack = make_pack(root)
    pack.update(schema_version='2.0', material_items=[], dispositions=[], review_limitations=['Synthetic fixtures only; no real issue claim.'])
    for index, issue in enumerate(pack['issues']):
        mid = 'claim-' + str(index + 1)
        pack['material_items'].append(dict(id=mid, refs=issue['material_refs'], description='One claim within the shared source file.'))
        pack['dispositions'].append(dict(material_id=mid, issue_id=issue['id'], decision='retained', reason='Retained synthetic example.'))
        issue['material_ids'] = [mid]
        issue['action'] = dict(type='new_issue', reason='Synthetic new-report candidate.')
        issue['upstream'] = dict(relation='unknown', coverage='not_searched', reason='No external search in synthetic tests.')
        issue['publication'] = dict(quality_review=dict(status='pending', checks=[]))
        issue['submission_todos'] = []
    return pack


def add_run(root, issue, rid='run-1', criterion_ref=None, outcome='observed'):
    """Construct a synthetic native-runner record, never a real library claim."""
    basis = save(root, 'criteria/basis.txt', 'Synthetic expected output: fixture result.\n')
    if criterion_ref is None:
        criterion_ref = save(root, 'criteria/criterion.json', json.dumps(dict(
            defined_at='2026-11-23T00:00:00+00:00', symptom='Output equals fixture result.',
            expected_behavior='fixture result', basis=[basis], metrics=[])))
    source = save(root, 'attempts/' + rid + '/source.py', "print('fixture result')\n")
    stdout = save(root, 'attempts/' + rid + '/stdout.txt', 'fixture result\n')
    stderr = save(root, 'attempts/' + rid + '/stderr.txt', '')
    result = dict(execution_performed=True, execution_status='returned', started_at='2026-11-23T00:01:00+00:00',
                  command=['/synthetic/existing/python', '/synthetic/source.py'],
                  environment=dict(verified=True, executable='/synthetic/existing/python', selection_reason='Synthetic record fixture, not a real execution.', library=dict(name='synthetic-runtime', version='fixture')),
                  **{key:dict(path=Path(ref['path']).name, sha256=ref['sha256']) for key, ref in [('source',source),('stdout',stdout),('stderr',stderr)]})
    result_ref = save(root, 'attempts/' + rid + '/result.json', json.dumps(result))
    assessment = dict(run_id=rid, result_sha256=result_ref['sha256'], source_sha256=source['sha256'], criterion_ref=criterion_ref,
                      assessed_at='2026-11-23T00:02:00+00:00', outcome=outcome,
                      reason='Synthetic outcome explicitly assessed from this run output.', evidence=[stdout])
    assessment_ref = save(root, 'assessments/' + rid + '.json', json.dumps(assessment))
    repro = issue['reproduction']
    repro.update(status='reproduced' if outcome == 'observed' else 'not_reproduced' if outcome == 'not_observed' else 'inconclusive',
                 criterion_ref=criterion_ref, environment_selection=dict(status='matched',reason='Synthetic existing-environment fixture.'))
    repro.setdefault('runs', []).append(dict(id=rid, result=result_ref, assessment=assessment_ref))
    repro.setdefault('selected_run_ids', []).append(rid)
    return dict(source=source, stdout=stdout, stderr=stderr, result=result_ref, assessment=assessment_ref, criterion=criterion_ref)
