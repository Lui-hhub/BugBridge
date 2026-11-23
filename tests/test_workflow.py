import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from fixtures import SCRIPTS, form, make_pack, save
from normalize_template import normalize
from review_pack import artifact, render, validate


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pack = make_pack(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, pending=False):
        return validate(self.pack, self.root, pending)

    def test_dynamic_template_counts_and_markdown_identity(self):
        result = self.report()
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual([len(c['fields']) for c in result['cards']], [6,2,1])
        self.assertEqual(len(result['cards'][0]['instructions']), 1)
        self.assertEqual(result['cards'][2]['body'], self.pack['issues'][2]['responses']['body']['value'])
        self.assertTrue(any('Operator confirmation' in x for x in result['blockers']['cli-case']))
        self.assertNotIn('Instructions are displayed', result['cards'][0]['body'])

    def test_normalized_label_tampering_is_rejected(self):
        self.pack['templates'][1]['fields'][1]['label'] = 'Unrelated field'
        self.assertTrue(any('snapshot' in x for x in self.report()['errors']))

    def test_response_cannot_use_another_templates_field(self):
        self.pack['issues'][1]['responses']['version'] = dict(value='invented')
        self.assertFalse(self.report()['ok'])

    def test_doc_suggestion_rejects_evidence_dump(self):
        self.pack['issues'][1]['responses']['suggestion'] = dict(value='Collecting environment information...\nPyTorch version: x', content_tags=['environment_report'])
        self.assertTrue(any('documentation suggestion' in x for x in self.report()['errors']))

    def test_doc_change_can_include_environment_words_and_replacement_code(self):
        self.pack['issues'][1]['responses']['suggestion']['value'] = 'Correct the environment setup example:\n```sh\nexport EXAMPLE_OPTION=1\n```\nLink to the parameter reference.'
        self.assertTrue(self.report()['ok'])

    def test_no_checkbox_confirmation_on_operators_behalf(self):
        response = dict(value=['I reviewed this report'])
        self.pack['issues'][0]['responses']['terms'] = response
        self.assertFalse(self.report()['ok'])
        response['confirmed_by_operator'] = True
        self.assertTrue(self.report()['ok'])

    def test_dropdown_defaults_do_not_invent_an_answer(self):
        del self.pack['issues'][0]['responses']['backend']
        report = self.report()
        self.assertTrue(report['ok'])
        field = next(f for f in report['cards'][0]['fields'] if f['id']=='backend')
        self.assertEqual(field['text'], '')
        self.assertTrue(any('Backend' in x for x in report['blockers']['cli-case']))

    def test_invalid_dropdown_option_and_invented_attachment_rejected(self):
        self.pack['issues'][0]['responses']['backend']['value'] = 'not-an-option'
        self.assertFalse(self.report()['ok'])
        self.pack['issues'][0]['responses']['backend']['value'] = 'local'
        self.pack['issues'][0]['responses']['attachment'] = dict(value=['fake.png'])
        self.assertFalse(self.report()['ok'])
        ref = save(self.root, 'materials/image.txt', 'Synthetic attachment')
        self.pack['issues'][0]['responses']['attachment'] = dict(value=['image.txt'], attachments=[dict(ref,name='image.txt')])
        self.assertTrue(self.report()['ok'])

    def test_future_control_and_rule_are_visible_blockers(self):
        raw = dict(name='Future', body=[dict(type='future-control', id='future', attributes=dict(label='Future input'), validations=dict(pattern='x'))])
        self.pack['templates'][0] = form(self.root, 'bug', raw, dict(future='other'))
        self.pack['issues'][0]['responses'] = {}
        report = self.report()
        self.assertTrue(report['ok'])
        self.assertTrue(any('Unsupported control' in x for x in report['blockers']['cli-case']))
        self.assertTrue(any('Unreviewed constraints' in x for x in report['blockers']['cli-case']))

    def set_missing_environment(self):
        repro = self.pack['issues'][0]['reproduction']
        repro.update(status='awaiting_environment', reason='Required runtime absent.',
                     environment_selection=dict(status='missing', reason='Project environment not found.'))
        self.pack['workflow_status'] = 'awaiting_environment'

    def test_missing_environment_requires_an_actual_operator_question(self):
        self.set_missing_environment()
        self.assertFalse(self.report(pending=True)['ok'])
        self.pack['requests'] = [dict(id='env-1', type='environment', issue_ids=['cli-case'], state='pending',
                                     asked_at='2026-11-23T10:00:00+08:00', requirements='Project runtime.',
                                     discovery_notes='Inspected project configuration; expected environment was absent.',
                                     question='Please provide the existing environment path or launch method.')]
        self.assertFalse(self.report()['ok'])
        self.assertTrue(self.report(pending=True)['ok'])
        self.pack['workflow_status'] = 'ready_for_review'
        self.assertFalse(self.report(pending=True)['ok'])

    def test_missing_environment_cannot_be_silently_called_not_reproduced(self):
        self.set_missing_environment()
        self.pack['issues'][0]['reproduction']['status'] = 'not_reproduced'
        self.assertFalse(self.report(pending=True)['ok'])

    def test_skip_requires_explicit_operator_decision(self):
        repro = self.pack['issues'][0]['reproduction']
        repro.update(status='skipped_by_operator', environment_selection=dict(status='operator_skipped',reason='Operator choice.'))
        self.assertFalse(self.report()['ok'])
        repro['operator_decision'] = 'Operator explicitly requested a text-only report.'
        self.assertTrue(self.report()['ok'])

    def test_no_match_does_not_hide_search_gaps(self):
        up = self.pack['issues'][1]['upstream']
        up.update(status='no_match_in_scope',checked_at='2026-11-23',queries=[dict(query='bounded query',complete=False,gaps=['page cap'])])
        self.assertFalse(self.report()['ok'])
        up['status'] = 'incomplete'
        self.assertTrue(self.report()['ok'])

    def test_pr_match_needs_trigger_and_fix_scope(self):
        up = self.pack['issues'][1]['upstream']
        up.update(status='same_mechanism',checked_at='2026-11-23',matches=[dict(url='https://example.invalid/examplelib/pull/1',kind='pull_request',reason='Related mechanism.')])
        self.assertFalse(self.report()['ok'])
        up['matches'][0].update(trigger_comparison='Same parameter boundary; entry point differs.',fix_scope='Proposed guard, release inclusion unknown.')
        self.assertTrue(self.report()['ok'])

    def test_unofficial_upstream_url_is_not_an_official_match(self):
        up=self.pack['issues'][1]['upstream']
        up.update(status='related',checked_at='2026-11-23',matches=[dict(url='https://unrelated.invalid/post',kind='issue',reason='Same words.',trigger_comparison='Not an inspected official issue.')])
        self.assertFalse(self.report()['ok'])

    def test_claim_of_reproduction_needs_actual_run(self):
        self.pack['issues'][0]['reproduction'].update(status='reproduced',criterion='Observed fixture result.',
            environment_selection=dict(status='matched',reason='Existing fixture environment.'))
        self.assertFalse(self.report()['ok'])

    def test_snapshot_hash_and_path_escape_guards(self):
        ref = self.pack['sources'][0]
        (self.root/ref['path']).write_text('tampered')
        self.assertFalse(self.report()['ok'])
        with self.assertRaises(ValueError):
            artifact(self.root,dict(path='../outside.txt',sha256='x'))

    def test_complete_evidence_reference_cannot_be_truncated(self):
        ref=save(self.root,'materials/report.txt','Complete\nreport\nend\n')
        response=self.pack['issues'][1]['responses']['doc']
        response.update(value='Only a summary',verbatim=[ref])
        self.assertFalse(self.report()['ok'])
        response['value']='Evidence:\n```text\nComplete\nreport\nend\n```'
        self.assertTrue(self.report()['ok'])

    def test_render_exact_fields_and_refuse_overwrite(self):
        out=self.root/'review'
        report=self.report()
        render(self.pack,report,out)
        path=out/'issues/doc-case/suggestion.txt'
        self.assertEqual(path.read_text(),self.pack['issues'][1]['responses']['suggestion']['value'])
        self.assertTrue((out/'REVIEW.html').is_file())
        with self.assertRaises(ValueError):render(self.pack,report,out)
        self.assertEqual(path.read_text(),self.pack['issues'][1]['responses']['suggestion']['value'])

    def test_embedded_json_cannot_close_the_script(self):
        payload='</script><script>globalThis.compromised=true</script> & \u2028'
        self.pack['issues'][1]['responses']['doc']['value']=payload
        out=self.root/'review';render(self.pack,self.report(),out)
        html=(out/'REVIEW.html').read_text()
        self.assertNotIn(payload,html)
        self.assertIn('\\u003c/script>',html)
        model=json.loads((out/'issue_cards.json').read_text())
        self.assertEqual(model['cards'][1]['fields'][0]['text'],payload)

    def test_render_language_wraps_only_the_combined_preview(self):
        value='literal ``` marker\nsecond line'
        self.pack['issues'][0]['responses']['logs']['value']=value
        card=self.report()['cards'][0]
        self.assertIn('````shell\n'+value+'\n````',card['body'])
        self.assertEqual(next(f for f in card['fields'] if f['id']=='logs')['text'],value)

    def test_normalizer_keeps_optional_ids_and_duplicate_ids_fail(self):
        raw=dict(body=[dict(type='textarea',attributes=dict(label='No supplied ID'))])
        field=normalize(raw,'github_form','x')['fields'][0]
        self.assertEqual(field['id'],'element-1');self.assertIsNone(field['source_id'])
        raw['body'].append(dict(type='input',id='element-1',attributes=dict(label='Collision')))
        with self.assertRaises(ValueError):normalize(raw,'github_form','x')

    def test_valid_official_id_can_start_with_underscore(self):
        raw=dict(body=[dict(type='input',id='_runtime',attributes=dict(label='Existing runtime'))])
        self.pack['templates'][0]=form(self.root,'bug',raw,dict(_runtime='environment'))
        self.pack['issues'][0]['responses']={'_runtime':dict(value='local')}
        self.assertTrue(self.report()['ok'])

    @unittest.skipUnless(shutil.which('node'),'Node needed only for optional UI validation')
    def test_actual_html_field_copy_and_dynamic_controls(self):
        self.pack['issues'][1]['responses']['doc']['value']='Untrusted </script><script>globalThis.compromised=true</script> text'
        self.pack['library']['__proto__']={'injected':'must remain ordinary data'}
        out=self.root/'ui';render(self.pack,self.report(),out)
        proc=subprocess.run([shutil.which('node'),str(Path(__file__).with_name('test_review_ui.cjs')),
                             str(out/'REVIEW.html'),str(out/'issue_cards.json')],capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,0,proc.stderr)
        result=json.loads(proc.stdout)
        self.assertTrue(result['pass']);self.assertEqual(result['records'],3)
        self.assertGreater(result['copied_fields'],2)
        self.assertEqual(result['dropdowns'],1);self.assertEqual(result['uploads'],1)

    def test_normalizer_cli_reads_existing_yaml_without_installing(self):
        try:import yaml
        except ImportError:self.skipTest('No existing YAML parser')
        source=self.root/'templates/form.yml';source.write_text('name: Yaml form\nbody:\n  - type: input\n    attributes:\n      label: Local runtime\n')
        out=self.root/'normalized.json'
        proc=subprocess.run([sys.executable,str(SCRIPTS/'normalize_template.py'),'--input',str(source),
            '--format','github_form','--id','yaml','--output',str(out),'--artifact-root',str(self.root)],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)
        data=json.loads(out.read_text());self.assertEqual(data['source']['path'],'templates/form.yml')
        self.assertEqual(data['fields'][0]['label'],'Local runtime')


class RunnerTests(unittest.TestCase):
    def run_fixture(self,root,code,wall=3):
        source=root/'case.py';source.write_text(code)
        env=dict(verified=True,executable=sys.executable,library=dict(name='python-stdlib',version=sys.version.split()[0]),selection_reason='Existing interpreter for a benign runner fixture.')
        (root/'env.json').write_text(json.dumps(env))
        command=[sys.executable,str(SCRIPTS/'run_case.py'),'--environment',str(root/'env.json'),'--source',str(source),
                 '--output-dir',str(root/'attempt'),'--reviewed-local-code','--wall-seconds',str(wall),
                 '--cpu-seconds','2','--',sys.executable,'{source}']
        proc=subprocess.run(command,capture_output=True,text=True,timeout=8)
        self.assertEqual(proc.returncode,0,proc.stderr)
        return json.loads((root/'attempt/result.json').read_text())

    @unittest.skipUnless(sys.platform.startswith('linux'),'POSIX /proc helper')
    def test_existing_interpreter_and_exact_execution_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);code="import sys\nprint('fixture result')\nprint('fixture stderr',file=sys.stderr)\n"
            result=self.run_fixture(root,code)
            self.assertEqual(result['execution_status'],'returned')
            self.assertEqual(result['environment']['executable'],sys.executable)
            self.assertEqual((root/'attempt/source.py').read_text(),code)
            for key in ['source','stdout','stderr']:
                self.assertEqual(hashlib.sha256((root/'attempt'/result[key]['path']).read_bytes()).hexdigest(),result[key]['sha256'])

    @unittest.skipUnless(sys.platform.startswith('linux'),'POSIX /proc helper')
    def test_external_timeout_is_not_reported_as_native_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=self.run_fixture(Path(tmp),'import time\ntime.sleep(5)\n',wall=.2)
            self.assertEqual(result['execution_status'],'timeout')
            self.assertEqual(result['returncode'],-9)

    def test_environment_is_required_and_no_install_is_attempted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'env.json').write_text(json.dumps(dict(verified=False)))
            (root/'case.py').write_text("raise RuntimeError('must not execute')")
            proc=subprocess.run([sys.executable,str(SCRIPTS/'run_case.py'),'--environment',str(root/'env.json'),
                                 '--source',str(root/'case.py'),'--output-dir',str(root/'attempt'),'--reviewed-local-code',
                                 '--',sys.executable,'{source}'],capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0)
            self.assertFalse((root/'attempt').exists())

    def test_unbounded_budget_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            env=dict(verified=True,executable=sys.executable,library=dict(name='python-stdlib',version=sys.version.split()[0]),selection_reason='Existing test interpreter.')
            (root/'env.json').write_text(json.dumps(env));(root/'case.py').write_text("print('must not execute')")
            proc=subprocess.run([sys.executable,str(SCRIPTS/'run_case.py'),'--environment',str(root/'env.json'),
                '--source',str(root/'case.py'),'--output-dir',str(root/'attempt'),'--reviewed-local-code',
                '--wall-seconds','inf','--',sys.executable,'{source}'],capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0);self.assertFalse((root/'attempt').exists())

    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('node'),'Existing Node and POSIX /proc needed')
    def test_runner_is_not_limited_to_python_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);executable=shutil.which('node')
            version=subprocess.check_output([executable,'--version'],text=True).strip()
            source=root/'case.js';source.write_text("console.log('JavaScript fixture')")
            env=dict(verified=True,executable=executable,library=dict(name='node-core',version=version),selection_reason='Existing local Node for a benign fixture.')
            (root/'env.json').write_text(json.dumps(env))
            proc=subprocess.run([sys.executable,str(SCRIPTS/'run_case.py'),'--environment',str(root/'env.json'),
                '--source',str(source),'--output-dir',str(root/'attempt'),'--reviewed-local-code','--',executable,'{source}'],capture_output=True,text=True,timeout=5)
            self.assertEqual(proc.returncode,0,proc.stderr)
            result=json.loads((root/'attempt/result.json').read_text())
            self.assertEqual(result['execution_status'],'returned')
            self.assertEqual((root/'attempt/stdout.txt').read_text(),'JavaScript fixture\n')


if __name__=='__main__':unittest.main()
