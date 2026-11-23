"""Contract regressions derived from real pack failures; only synthetic programs."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from fixtures import SCRIPTS, add_run, make_v2_pack, save
from assemble_pack import Builder
from pack_v2 import QUALITY_CHECKS
from review_pack import render, validate, write_json


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pack = make_v2_pack(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def report(self):
        return validate(self.pack, self.root)

    def assert_error(self, fragment):
        report = self.report()
        self.assertFalse(report['ok'])
        self.assertTrue(any(fragment in e for e in report['errors']), report['errors'])

    def rewrite(self, ref, change):
        value = json.loads((self.root/ref['path']).read_text())
        change(value)
        return save(self.root, ref['path'], json.dumps(value))

    def test_multiple_claims_in_one_file_are_independently_accounted_for(self):
        report = self.report()
        self.assertTrue(report['ok'], report['errors'])
        self.assertTrue(report['coverage']['verified'])
        self.assertEqual(report['coverage']['material_items'], 3)
        self.assertEqual(len({tuple(i['refs']) for i in self.pack['material_items']}), 1)

    def test_missing_duplicate_and_unknown_material_assignments(self):
        original = copy.deepcopy(self.pack['dispositions'])
        self.pack['dispositions'].pop()
        self.assert_error('material coverage')
        self.pack['dispositions'] = original + [copy.deepcopy(original[0])]
        self.assert_error('duplicate assignments')
        self.pack['dispositions'] = original
        self.pack['dispositions'][0]['material_id'] = 'not-supplied'
        self.assert_error('material coverage')

    def test_issue_material_mapping_must_match_ledger(self):
        self.pack['issues'][0]['material_ids'] = ['claim-2']
        self.assert_error('do not match')

    def test_invalid_and_cyclic_material_merges(self):
        rows = self.pack['dispositions']
        rows[0].update(decision='merged', merged_into='missing')
        self.assert_error('invalid material merge')
        rows[0]['merged_into'] = 'claim-2'
        rows[1].update(decision='merged', merged_into='claim-1', issue_id=rows[0]['issue_id'])
        self.pack['issues'][0]['material_ids'].append('claim-2')
        self.pack['issues'][1]['material_ids'] = []
        self.assert_error('material merge cycle')

    def test_issue_merge_cycle_is_rejected(self):
        a, b = self.pack['issues'][:2]
        for source, target in [(a,b),(b,a)]:
            source['finding'].update(status='merged', merged_into=target['id'])
            source['action']['type'] = 'no_submission'
            source['responses'] = {}
        self.assert_error('issue merge cycle')

    def test_selected_run_assessment_has_exact_source_result_and_prior_criterion(self):
        refs = add_run(self.root, self.pack['issues'][0])
        report = self.report()
        self.assertTrue(report['ok'], report['errors'])
        self.assertEqual(report['cards'][0]['runs'][0]['assessment']['outcome'], 'observed')
        self.assertEqual(report['cards'][0]['criterion_data']['expected_behavior'], 'fixture result')
        bad = self.rewrite(refs['assessment'], lambda a:a.update(source_sha256='old-source'))
        self.pack['issues'][0]['reproduction']['runs'][0]['assessment'] = bad
        self.assert_error('another source version')

    def test_assessment_for_another_run_is_rejected(self):
        refs = add_run(self.root, self.pack['issues'][0])
        bad = self.rewrite(refs['assessment'], lambda a:a.update(result_sha256='different-result'))
        self.pack['issues'][0]['reproduction']['runs'][0]['assessment'] = bad
        self.assert_error('another run/result')

    def test_criterion_defined_after_execution_is_rejected(self):
        issue = self.pack['issues'][0]
        refs = add_run(self.root, issue)
        criterion = self.rewrite(refs['criterion'], lambda c:c.update(defined_at='2026-11-23T00:10:00+00:00'))
        issue['reproduction']['criterion_ref'] = criterion
        assessment = self.rewrite(refs['assessment'], lambda a:a.update(criterion_ref=criterion))
        issue['reproduction']['runs'][0]['assessment'] = assessment
        self.assert_error('before execution')

    def test_old_probe_can_be_retained_but_not_substituted_for_selected_criterion(self):
        issue = self.pack['issues'][0]
        old = add_run(self.root, issue, rid='v1')
        criterion = self.rewrite(old['criterion'], lambda c:c.update(symptom='Refined symptom.'))
        # Restore the v1 frozen criterion at its own path, then save a distinct v2.
        old_criterion = save(self.root, 'criteria/v1.json', json.dumps(dict(defined_at='2026-11-23T00:00:00+00:00', symptom='Old probe.', expected_behavior='fixture result', basis=[save(self.root,'criteria/old-basis.txt','Old basis')], metrics=[])))
        old_assessment = self.rewrite(old['assessment'], lambda a:a.update(criterion_ref=old_criterion))
        issue['reproduction']['runs'][0]['assessment'] = old_assessment
        current = add_run(self.root, issue, rid='v2', criterion_ref=criterion)
        issue['reproduction']['selected_run_ids'] = ['v2']
        self.assertTrue(self.report()['ok'], self.report()['errors'])
        issue['reproduction']['selected_run_ids'] = ['v1']
        self.assert_error('different criterion')

    def test_exit_zero_does_not_mean_symptom_observed(self):
        issue = self.pack['issues'][0]
        add_run(self.root, issue, outcome='not_assessed')
        issue['reproduction']['status'] = 'reproduced'
        self.assert_error('observed selected run')

    def test_assessment_must_cite_its_own_output(self):
        issue = self.pack['issues'][0]
        refs = add_run(self.root, issue)
        unrelated = save(self.root, 'materials/unrelated.txt', 'Some other observation.')
        assessment = self.rewrite(refs['assessment'], lambda a:a.update(evidence=[unrelated]))
        issue['reproduction']['runs'][0]['assessment'] = assessment
        self.assert_error('this run stdout/stderr')

    def test_metric_definition_requires_unit_and_scope(self):
        issue = self.pack['issues'][0]
        refs = add_run(self.root, issue)
        criterion = self.rewrite(refs['criterion'], lambda c:c.update(metrics=[dict(name='event_delta', definition='Net memory change within one profiler event.')]))
        issue['reproduction']['criterion_ref'] = criterion
        issue['reproduction']['runs'][0]['assessment'] = self.rewrite(refs['assessment'],lambda a:a.update(criterion_ref=criterion))
        self.assert_error('measurement scope')

    def test_publication_code_must_be_the_executed_code(self):
        issue = self.pack['issues'][0]
        refs = add_run(self.root, issue)
        issue['responses']['problem']['value'] += '\n```python\n'+(self.root/refs['source']['path']).read_text()+'```'
        issue['publication']['reproducer'] = dict(source=refs['source'], run_ids=['run-1'], field_id='problem', language='python')
        self.assertTrue(self.report()['ok'], self.report()['errors'])
        edited = save(self.root, 'reproductions/smaller.py', "print('edited, not executed')\n")
        issue['publication']['reproducer']['source'] = edited
        self.assert_error('re-execute edits')

    def match(self, kind='issue'):
        evidence = save(self.root,'upstream/inspection.json','{"synthetic":true}')
        return dict(url='https://example.invalid/examplelib/'+('pull' if kind=='pull_request' else 'issues')+'/1',
                    title='Synthetic upstream record', kind=kind, relation='same_issue',
                    reason='Same report request.', claim_comparison='Same requested correction.',
                    trigger_comparison='Same relevant inputs and entry point.', evidence=[evidence])

    def searched(self, issue, relation='same_issue'):
        issue['upstream'] = dict(relation=relation, coverage='partial', checked_at='2026-11-23', scope='One exact report and patch.',
                                 stop_reason='Synthetic access limit; no retries.', gaps=['Other queries not inspected.'],
                                 reason='Saved positive match remains valid.', matches=[self.match()])

    def test_positive_match_and_partial_search_coexist(self):
        issue = self.pack['issues'][0]
        self.searched(issue)
        issue['action'].update(type='existing_issue_comment', target_url=issue['upstream']['matches'][0]['url'], comment=dict(value='A short additional observation.'))
        issue['responses'] = {}
        report = self.report()
        self.assertTrue(report['ok'], report['errors'])
        self.assertEqual(report['cards'][0]['upstream']['relation'],'same_issue')
        self.assertEqual(report['cards'][0]['upstream']['coverage'],'partial')
        self.assertEqual([f['id'] for f in report['cards'][0]['fields']],['comment'])
        self.assertEqual(report['cards'][0]['body'],'A short additional observation.')
        issue['publication']['quality_review'] = dict(status='reviewed', reviewer='agent', reviewed_at='2026-11-23T00:10:00+00:00',
            notes='Reviewed this synthetic follow-up independently of the remaining search gap.',
            checks=[dict(id=k,status='not_applicable' if k=='minimal_reproducer' else 'pass',reason='Synthetic comment does not claim execution.') for k in sorted(QUALITY_CHECKS)])
        card=self.report()['cards'][0]
        self.assertEqual(card['readiness']['status'],'prepared_for_human_review')
        self.assertTrue(card['review_notices'])

    def test_known_same_issue_cannot_be_a_new_report(self):
        self.searched(self.pack['issues'][0])
        self.assert_error('another new-issue')

    def test_runtime_mechanism_match_does_not_claim_a_duplicate_documentation_request(self):
        issue = self.pack['issues'][1]
        self.searched(issue, relation='related')
        issue['upstream']['matches'][0].update(relation='related', mechanism_relation='same_mechanism', claim_comparison='Known runtime rejection; documentation class correction not previously requested here.')
        self.assertTrue(self.report()['ok'], self.report()['errors'])
        issue['upstream']['relation'] = 'same_issue'
        self.assert_error('claim-level comparison')

    def test_complete_search_cannot_hide_gaps(self):
        issue = self.pack['issues'][0]
        self.searched(issue, relation='related')
        issue['upstream']['coverage'] = 'complete_in_scope'
        self.assert_error('hide query gaps')

    def test_pr_closed_merge_scope_and_release_are_independent(self):
        issue = self.pack['issues'][1]
        self.searched(issue, relation='related')
        match = self.match('pull_request')
        match.update(relation='related', tracker_state=dict(state='closed', merged=False, evidence=match['evidence']),
                     fix_scope=dict(summary='Only changes another entry point.', covers_claim='no', evidence=match['evidence']),
                     release_inclusion=dict(status='unknown', evidence=[]))
        issue['upstream']['matches'] = [match]
        self.assertTrue(self.report()['ok'], self.report()['errors'])
        match['release_inclusion'].update(status='included',version='fixture-version')
        self.assert_error('release inclusion needs version and evidence')

    def test_archive_needs_no_template_or_fake_not_applicable_fields(self):
        issue = self.pack['issues'][0]
        issue['finding']['status'] = 'excluded'
        issue['action']['type'] = 'no_submission'
        del issue['template_id']
        issue['responses'] = {}
        self.pack['dispositions'][0]['decision'] = 'excluded'
        report = self.report()
        self.assertTrue(report['ok'], report['errors'])
        self.assertEqual(report['cards'][0]['fields'], [])
        self.assertEqual(report['cards'][0]['blockers'], [])
        self.assertEqual(report['counts']['archives'],1)

    def test_policy_todo_is_repository_specific_and_does_not_block_html(self):
        issue = self.pack['issues'][1]
        policy = save(self.root,'tracker/policy.md','Synthetic project asks for author explanation.')
        issue['submission_todos'] = [dict(id='author-note',kind='human_commentary',label='Add the human author explanation required by this project.',state='pending',basis=[policy])]
        report = self.report()
        self.assertTrue(report['ok'], report['errors'])
        self.assertEqual(report['cards'][1]['readiness']['pending_todos'],1)
        self.assertEqual(report['cards'][0]['submission_todos'],[])
        render(self.pack,report,self.root/'review')
        issue['submission_todos'][0]['state']='done'
        self.assert_error('actual operator confirmation')

    def test_unfilled_internal_placeholder_is_visible_quality_work(self):
        self.pack['issues'][1]['responses']['doc']['value']='[Not supplied. The human reviewer must explain this.]'
        report = self.report()
        self.assertTrue(report['ok'])
        self.assertEqual(report['cards'][1]['readiness']['status'],'needs_work')
        self.assertTrue(any('占位' in s for s in report['cards'][1]['quality_warnings']))

    def test_quality_review_is_not_a_human_signoff(self):
        issue = self.pack['issues'][1]
        issue['publication']['quality_review'] = dict(status='reviewed',reviewer='agent',reviewed_at='2026-11-23T00:10:00+00:00',notes='Checked synthetic field semantics.',checks=[dict(id=k,status='pass',reason='Explicit synthetic review.') for k in sorted(QUALITY_CHECKS)])
        self.assertTrue(self.report()['ok'], self.report()['errors'])
        issue['publication']['quality_review']['reviewer']='human'
        self.assert_error('actual operator confirmation')

    @unittest.skipUnless(shutil.which('node'),'Existing Node required')
    def test_modern_ui_has_comment_archive_todos_and_independent_evidence_copy(self):
        first, second, third = self.pack['issues']
        # Keep the first form to cover every dynamic input control.
        self.searched(second)
        second['action'].update(type='existing_issue_comment', target_url=second['upstream']['matches'][0]['url'], comment=dict(value='Short follow-up.'))
        second['responses']={}
        add_run(self.root, third)
        third['finding']['status']='excluded';third['action']['type']='no_submission';third['responses']={}
        self.pack['dispositions'][2]['decision']='excluded'
        third.pop('template_id')
        out=self.root/'ui';report=self.report();self.assertTrue(report['ok'],report['errors']);render(self.pack,report,out)
        proc=subprocess.run([shutil.which('node'),str(Path(__file__).with_name('test_review_ui.cjs')),str(out/'REVIEW.html'),str(out/'issue_cards.json')],capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,0,proc.stderr)
        result=json.loads(proc.stdout)
        self.assertEqual(result['followup_records'],1);self.assertEqual(result['archive_records'],1)
        self.assertEqual(result['evidence_source_copies'],1)


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.pack=make_v2_pack(self.root);self.path=self.root/'pack.json';write_json(self.path,self.pack)

    def tearDown(self):
        self.tmp.cleanup()

    def test_freeze_register_and_exact_field_text_without_inventing_a_judgment(self):
        builder=Builder(self.path)
        original=self.root/'input.txt';original.write_text('Another claim.\n')
        ref=builder.freeze(original,'materials/additional.txt')
        with self.assertRaises(FileExistsError):builder.freeze(original,'materials/additional.txt')
        builder.add_material('extra',[ref['path']],'Another claim in its own file.')
        builder.add_issue('extra-issue','Extra synthetic claim','bug','new_issue','Needs investigation.','bug')
        builder.assign('extra','extra-issue','retained','Assigned once.')
        builder.set_response('extra-issue','problem',original)
        builder.save()
        self.assertEqual(builder.issue('extra-issue')['responses']['problem']['value'],'Another claim.\n')
        self.assertEqual(builder.issue('extra-issue')['reproduction']['status'],'insufficient_material')
        with self.assertRaises(ValueError):builder.assign('extra','extra-issue','retained','Duplicate.')

    def test_run_registration_uses_explicit_assessment_and_selection(self):
        refs=add_run(self.root,self.pack['issues'][0])
        # The builder registers existing immutable records rather than executing them.
        builder=Builder(self.path)
        builder.register_run('cli-case','run-1',refs['result']['path'],refs['assessment']['path'],select=True)
        self.assertEqual(builder.issue('cli-case')['reproduction']['selected_run_ids'],['run-1'])
        self.assertEqual(builder.issue('cli-case')['reproduction']['criterion_ref'],refs['criterion'])
        self.assertEqual(builder.issue('cli-case')['reproduction']['status'],'not_applicable')

    def test_init_cli_is_a_draft_and_refuses_overwrite(self):
        path=self.root/'fresh/pack.json'
        command=[sys.executable,str(SCRIPTS/'assemble_pack.py'),'--pack',str(path),'init','--run-id','fresh-case','--materials-path','supplied/path','--library','Synthetic','--repository','https://example.invalid/examplelib','--resolution-reason','Synthetic resolved identity.']
        proc=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertEqual(json.loads(path.read_text())['schema_version'],'2.0')
        self.assertNotEqual(subprocess.run(command,capture_output=True,text=True).returncode,0)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Existing POSIX runner')
    def test_benign_run_to_selected_publication_to_html(self):
        # One real benign execution checks the seam between the two helpers.
        now=lambda:datetime.now(timezone.utc).isoformat()
        code="print('fixture result')\n"
        source=save(self.root,'reproductions/minimal.py',code)
        basis=save(self.root,'criteria/expectation.txt','The fixture prints the literal fixture result.')
        criterion=save(self.root,'criteria/live.json',json.dumps(dict(defined_at=now(),symptom='Prints fixture result.',expected_behavior='fixture result',basis=[basis],metrics=[])))
        env=dict(verified=True,executable=sys.executable,library=dict(name='python-stdlib',version=sys.version.split()[0]),selection_reason='Existing interpreter for benign helper integration.')
        write_json(self.root/'environment.json',env)
        command=[sys.executable,str(SCRIPTS/'run_case.py'),'--environment',str(self.root/'environment.json'),'--source',str(self.root/source['path']),
                 '--output-dir',str(self.root/'attempts/live'),'--reviewed-local-code','--wall-seconds','3','--cpu-seconds','2','--',sys.executable,'{source}']
        proc=subprocess.run(command,capture_output=True,text=True,timeout=6)
        self.assertEqual(proc.returncode,0,proc.stderr)
        result_ref=dict(path='attempts/live/result.json',sha256=hashlib.sha256((self.root/'attempts/live/result.json').read_bytes()).hexdigest())
        output=self.root/'attempts/live/stdout.txt'
        self.assertEqual(output.read_text(),'fixture result\n')
        stdout=dict(path='attempts/live/stdout.txt',sha256=hashlib.sha256(output.read_bytes()).hexdigest())
        assessment=save(self.root,'assessments/live.json',json.dumps(dict(run_id='live',result_sha256=result_ref['sha256'],source_sha256=source['sha256'],criterion_ref=criterion,
                        assessed_at=now(),outcome='observed',reason='Compared the actual literal stdout to the independent fixed expectation.',evidence=[stdout])))
        builder=Builder(self.path)
        builder.register_run('cli-case','live',result_ref['path'],assessment['path'],select=True)
        issue=builder.issue('cli-case')
        issue['reproduction'].update(status='reproduced',reason='The benign fixture output matched the criterion.',environment_selection=dict(status='matched',reason='Existing interpreter.'))
        issue['responses']['problem']['value']='Benign fixture, not a defect.\n```python\n'+code+'```\nObserved: fixture result.'
        issue['publication']['reproducer']=dict(source=source,run_ids=['live'],field_id='problem',language='python')
        builder.save()
        report=validate(builder.pack,self.root)
        self.assertTrue(report['ok'],report['errors'])
        render(builder.pack,report,self.root/'review')
        self.assertEqual((self.root/'review/issues/cli-case/problem.txt').read_text(),issue['responses']['problem']['value'])


if __name__=='__main__':unittest.main()
