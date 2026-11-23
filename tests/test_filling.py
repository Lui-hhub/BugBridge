"""Content-only publication with the same view across unrelated official forms."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from fixtures import add_run, form, SCRIPTS, save
from test_service import service_pack
from field_contract import read_fields_json
from fill_fields import compile_pack, prepare, prepare_review
from layout_guard import MANIFEST, SKILL_ROOT, VIEW_FILES, ensure_layout
from review_pack import artifact, read_json, sha, validate, write_json
from serve_review import ReviewApplication, ReviewServer
from template_cache import TemplateCache


class FillingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.pack=service_pack(self.root)
        self.base=self.root/'investigation.json';write_json(self.base,self.pack)
        self.filling=self.root/'filling';prepare(self.base,self.filling)
        self.contract=self.filling/'contract.json';self.answers=self.filling/'answers.json';self.review=self.filling/'review.json'

    def tearDown(self):
        self.tmp.cleanup()

    def change_answers(self, change):
        data=read_json(self.answers);change(data);write_json(self.answers,data)

    def reviewed(self):
        prepare_review(self.base,self.contract,self.answers,self.review)
        review=read_json(self.review)
        for entry in review['reviews'].values():
            entry.update(status='reviewed',notes='Reviewed synthetic field allocation, output and canonical copy text; no real defect claim.')
            for check in entry['checks']:
                check.update(status='not_applicable' if check['id']=='minimal_reproducer' else 'pass',reason='Synthetic content-only fixture.')
        write_json(self.review,review)

    def compile(self, directory='compiled'):
        if not self.review.exists():self.reviewed()
        result=compile_pack(self.base,self.contract,self.answers,self.review,self.root/directory)
        return Path(result['pack'])

    def test_program_generates_exact_fill_slots_and_excludes_personal_controls(self):
        contract=read_json(self.contract);answers=read_json(self.answers)
        case=contract['cases']['cli-case']
        self.assertEqual([f['id'] for f in case['fields']],['version','problem','logs','backend'])
        self.assertEqual([f['id'] for f in case['manual_fields']],['terms','attachment'])
        self.assertEqual(set(answers['issues']['cli-case']),{'title','responses'})
        self.assertNotIn('terms',answers['issues']['cli-case']['responses'])
        self.assertNotIn('fields',answers['issues']['cli-case'])
        self.assertEqual(len(contract['cases']['legacy-case']['fields']),1)
        schema=read_json(self.filling/'answers.schema.json')
        self.assertFalse(schema['additionalProperties'])
        self.assertFalse(schema['properties']['issues']['additionalProperties'])
        with self.assertRaises(ValueError):prepare(self.base,self.filling)

    def test_compilation_is_independent_of_original_input_directory(self):
        compiled=self.compile();compiled_root=compiled.parent
        for path in [self.root/'materials',self.root/'templates',self.filling]:shutil.rmtree(path)
        self.base.unlink()
        pack=read_json(compiled);report=validate(pack,compiled_root)
        self.assertTrue(report['ok'],report['errors'])
        binding=pack['form_binding']
        for name in ['base','contract','schema','answers','review','layout']:self.assertTrue(artifact(compiled_root,binding[name]).is_file())
        app=ReviewApplication(compiled,self.root/'progress.sqlite3')
        public=app.public()
        self.assertEqual(len(public['items']),3)
        self.assertEqual([f['id'] for f in public['items'][0]['fields']],['title','version','problem','logs','backend'])
        self.assertTrue(all(i['publication']['quality_review']['reviewer']=='agent' for i in pack['issues']))

    def test_answer_object_order_cannot_reorder_the_official_fields(self):
        self.change_answers(lambda a:a['issues']['cli-case'].update(responses=dict(reversed(list(a['issues']['cli-case']['responses'].items())))))
        compiled=self.compile();app=ReviewApplication(compiled,self.root/'progress.sqlite3')
        self.assertEqual([f['id'] for f in app.public()['items'][0]['fields']],['title','version','problem','logs','backend'])

    def test_new_fields_or_page_settings_in_answers_are_rejected(self):
        self.change_answers(lambda a:a['issues']['cli-case'].update(layout='two columns'))
        with self.assertRaisesRegex(ValueError,'unexpected or missing keys'):self.reviewed()
        self.assertFalse((self.root/'compiled').exists())

    def test_unknown_missing_or_personal_response_ids_are_rejected(self):
        original=read_json(self.answers)
        for name, mutate in [
            ('extra',lambda r:r.update(invented='new field')),
            ('missing',lambda r:r.pop('problem')),
            ('personal',lambda r:r.update(terms=['I reviewed this report'])),
        ]:
            value=copy.deepcopy(original);mutate(value['issues']['cli-case']['responses']);write_json(self.answers,value)
            with self.assertRaisesRegex(ValueError,'response IDs'):prepare_review(self.base,self.contract,self.answers,self.root/(name+'.json'))

    def test_non_value_objects_wrong_options_and_input_newlines_are_rejected(self):
        original=read_json(self.answers)
        for fid,value in [('problem',dict(value='text',label='replacement label')),('backend','not-an-option'),('version','two\nlines'),('logs',[])]:
            current=copy.deepcopy(original);current['issues']['cli-case']['responses'][fid]=value;write_json(self.answers,current)
            with self.assertRaises(ValueError):prepare_review(self.base,self.contract,self.answers,self.root/(fid+'.json'))

    def test_required_empty_value_and_internal_placeholder_are_rejected(self):
        original=read_json(self.answers)
        for value in ['',None,'   ','[Not supplied. The human reviewer must write this.]']:
            answer=copy.deepcopy(original);answer['issues']['cli-case']['responses']['problem']=value;write_json(self.answers,answer)
            with self.assertRaises(ValueError):prepare_review(self.base,self.contract,self.answers,self.root/'review-other.json')

    def test_duplicate_json_keys_do_not_silently_drop_an_answer(self):
        self.answers.write_text('{"issues":{},"issues":{}}')
        with self.assertRaisesRegex(ValueError,'Duplicate JSON key'):prepare_review(self.base,self.contract,self.answers,self.review)

    def test_readonly_contract_schema_and_source_pack_changes_are_rejected(self):
        original=self.contract.read_bytes();contract=read_json(self.contract)
        contract['cases']['cli-case']['fields'].reverse();write_json(self.contract,contract)
        with self.assertRaisesRegex(ValueError,'Readonly contract'):self.reviewed()
        self.contract.write_bytes(original)
        schema_path=self.filling/'answers.schema.json';schema=read_json(schema_path);schema['additionalProperties']=True;write_json(schema_path,schema)
        with self.assertRaisesRegex(ValueError,'answer schema'):self.reviewed()
        # Rebuild a fresh form to check a different investigation source revision.
        prepare(self.base,self.root/'filling-other')
        self.contract=self.root/'filling-other/contract.json';self.answers=self.root/'filling-other/answers.json'
        self.pack['issues'][0]['title']='Changed source';write_json(self.base,self.pack)
        with self.assertRaisesRegex(ValueError,'Readonly contract'):self.reviewed()

    def test_content_review_is_bound_to_the_exact_answer_version(self):
        self.reviewed()
        self.change_answers(lambda a:a['issues']['cli-case']['responses'].update(problem='A different valid description.'))
        with self.assertRaisesRegex(ValueError,'another contract/answer version'):self.compile()
        self.assertFalse((self.root/'compiled').exists())

    def test_review_cannot_claim_human_signoff_or_change_check_structure(self):
        self.reviewed();original=read_json(self.review)
        for mutate in [lambda r:r['reviews']['cli-case'].update(reviewer='human'),lambda r:r['reviews']['cli-case']['checks'].pop(),lambda r:r['reviews']['cli-case']['checks'].__setitem__(0,'malformed')]:
            current=copy.deepcopy(original);mutate(current);write_json(self.review,current)
            with self.assertRaises(ValueError):self.compile()

    def test_post_compile_text_or_template_changes_are_detected(self):
        compiled=self.compile();original=read_json(compiled)
        for mutate in [
            lambda p:p['issues'][0]['responses']['problem'].update(value='Unbound edit.'),
            lambda p:p['templates'][0]['fields'][1].update(label='Renamed label'),
            lambda p:p['issues'][0]['finding'].update(reason='Changed investigation after review.'),
        ]:
            pack=copy.deepcopy(original);mutate(pack)
            report=validate(pack,compiled.parent)
            self.assertFalse(report['ok']);self.assertTrue(any('Form binding:' in e for e in report['errors']),report['errors'])

    def test_source_copy_and_compiled_output_are_never_overwritten(self):
        before=self.base.read_bytes();compiled=self.compile()
        self.assertEqual(self.base.read_bytes(),before)
        with self.assertRaises(ValueError):self.compile()
        self.assertTrue(compiled.is_file())

    def test_service_requires_compiled_new_reports(self):
        with self.assertRaisesRegex(ValueError,'must be compiled'):ReviewApplication(self.base,self.root/'state-unbound.sqlite3')
        compiled=self.compile()
        self.assertEqual(len(ReviewApplication(compiled,self.root/'state.sqlite3').public()['items']),3)

    def test_existing_issue_has_only_one_comment_slot_and_a_frozen_target(self):
        issue=self.pack['issues'][0];target='https://example.invalid/examplelib/issues/1'
        evidence=save(self.root,'upstream/match.json','{"synthetic":true}')
        issue['upstream']=dict(relation='same_issue',coverage='partial',checked_at='2026-11-23',scope='One synthetic existing report.',
            reason='The existing report requests the same correction.',stop_reason='Synthetic bounded inspection.',gaps=['Other reports were not inspected.'],
            matches=[dict(url=target,title='Synthetic existing issue',kind='issue',relation='same_issue',reason='Same correction.',
                          trigger_comparison='Same fixture inputs.',claim_comparison='Same requested correction.',evidence=[evidence])])
        issue['action']=dict(type='existing_issue_comment',reason='Supplement the inspected existing report.',target_url=target,comment=dict(value='Additional synthetic evidence.'))
        issue['responses']={};write_json(self.base,self.pack)
        self.filling=self.root/'comment-filling';prepare(self.base,self.filling)
        self.contract=self.filling/'contract.json';self.answers=self.filling/'answers.json'
        self.assertEqual(set(read_json(self.answers)['issues']['cli-case']),{'comment'})
        self.assertEqual(read_json(self.contract)['cases']['cli-case']['target_url'],target)
        compiled=self.compile();item=ReviewApplication(compiled,self.root/'comment-state.sqlite3').public()['items'][0]
        self.assertEqual(item['official_url'],target)
        self.assertEqual([f['id'] for f in item['fields']],['comment'])
        self.assertEqual(item['fields'][0]['text'],'Additional synthetic evidence.')

    def test_submission_code_and_full_required_logs_survive_value_only_assembly(self):
        issue=self.pack['issues'][0];refs=add_run(self.root,issue)
        source=(self.root/refs['source']['path']).read_text()
        full_log='Traceback (synthetic fixture):\n  all required lines\nFixtureError: complete output\n'
        log_ref=save(self.root,'evidence/required-traceback.txt',full_log)
        issue['publication']['reproducer']=dict(source=refs['source'],run_ids=['run-1'],field_id='problem',language='python')
        issue['responses']['problem']['value']='Synthetic source record only.\n```python\n'+source+'```'
        issue['responses']['logs'].update(value=full_log,verbatim=[log_ref])
        write_json(self.base,self.pack);self.filling=self.root/'evidence-filling';prepare(self.base,self.filling)
        self.contract=self.filling/'contract.json';self.answers=self.filling/'answers.json'
        self.reviewed();compiled=self.compile()
        item=ReviewApplication(compiled,self.root/'evidence-state.sqlite3').public()['items'][0]
        self.assertEqual(next(f['text'] for f in item['fields'] if f['id']=='logs'),full_log)
        # Valid JSON cannot replace evidence with shorter, unexecuted code or logs.
        self.review.unlink()
        self.change_answers(lambda a:a['issues']['cli-case']['responses'].update(problem='Unbound shortened program.',logs='Only the final line.'))
        self.reviewed()
        with self.assertRaisesRegex(ValueError,'publication code|verbatim'):self.compile('invalid-evidence')

    def test_cached_pytorch_documentation_fields_keep_their_official_destinations(self):
        template=TemplateCache().export('pt','documentation',self.root)
        self.pack['templates']=[template];self.pack['issues']=self.pack['issues'][1:2]
        self.pack['material_items']=self.pack['material_items'][1:2];self.pack['dispositions']=self.pack['dispositions'][1:2]
        issue=self.pack['issues'][0];issue['template_id']=template['id']
        fields=[f for f in template['fields'] if f['type']!='markdown' and f.get('purpose')!='attestation']
        issue['responses']={f['id']:dict(value='Proposed replacement wording.' if f.get('purpose')=='documentation_fix' else 'Synthetic documentation mismatch.') for f in fields}
        write_json(self.base,self.pack);self.filling=self.root/'pt-filling';prepare(self.base,self.filling)
        self.contract=self.filling/'contract.json';self.answers=self.filling/'answers.json'
        compiled=self.compile();item=ReviewApplication(compiled,self.root/'pt-state.sqlite3').public()['items'][0]
        self.assertEqual([f['id'] for f in item['fields']],['title']+[f['id'] for f in fields])
        fix=next(f for f in fields if f.get('purpose')=='documentation_fix')
        self.assertEqual(next(f['text'] for f in item['fields'] if f['id']==fix['id']),'Proposed replacement wording.')

    def test_other_library_can_use_a_different_template_without_view_changes(self):
        # No PyTorch/TF-specific frontend branch is involved.
        raw=dict(name='New Engine report',body=[
            dict(type='markdown',attributes=dict(value='New engine instructions.')),
            dict(type='textarea',id='symptom',attributes=dict(label='Unexpected engine behavior'),validations=dict(required=True)),
            dict(type='dropdown',id='modes',attributes=dict(label='Execution modes',options=['fast','accurate'],multiple=True)),
        ])
        template=form(self.root,'engine',raw,dict(symptom='problem',modes='other'))
        template['source'].update(status='official',limitation='',url='https://example.invalid/newengine/blob/fixture/.github/ISSUE_TEMPLATE/engine.json',retrieved_at='2026-11-23')
        self.pack['library'].update(name='New Engine (synthetic)',repository='https://example.invalid/newengine')
        self.pack['templates']=[template];self.pack['issues']=self.pack['issues'][:1]
        self.pack['material_items']=self.pack['material_items'][:1];self.pack['dispositions']=self.pack['dispositions'][:1]
        issue=self.pack['issues'][0];issue['template_id']='engine';issue['responses']=dict(symptom=dict(value='Synthetic observation.'),modes=dict(value=['accurate','fast']))
        write_json(self.base,self.pack);self.filling=self.root/'engine-filling';prepare(self.base,self.filling)
        self.contract=self.filling/'contract.json';self.answers=self.filling/'answers.json'
        before=ensure_layout()['files'];compiled=self.compile()
        app=ReviewApplication(compiled,self.root/'engine-state.sqlite3');public=app.public()
        self.assertEqual([f['label'] for f in public['items'][0]['fields']],['Title','Unexpected engine behavior','Execution modes'])
        self.assertEqual(public['items'][0]['fields'][2]['text'],'accurate\nfast')
        self.assertEqual(ensure_layout()['files'],before)

    def test_cached_tensorflow_form_compiles_through_the_same_path(self):
        template=TemplateCache().export('tf','tensorflow_issue_template',self.root)
        self.pack['templates']=[template];self.pack['issues']=self.pack['issues'][:1]
        self.pack['material_items']=self.pack['material_items'][:1];self.pack['dispositions']=self.pack['dispositions'][:1]
        issue=self.pack['issues'][0];issue['template_id']=template['id']
        values={'issue-type':'Bug','tf-nightly':'No','source':'binary','tfversion':'synthetic','Code':'Yes',
                'what-happened':'Synthetic UI fixture, no TensorFlow defect claim.','code-to-reproduce':'print("fixture")\n','logs':'fixture\n'}
        issue['responses']={key:dict(value=value) for key,value in values.items()}
        write_json(self.base,self.pack);self.filling=self.root/'tf-filling';prepare(self.base,self.filling)
        self.contract=self.filling/'contract.json';self.answers=self.filling/'answers.json'
        compiled=self.compile();public=ReviewApplication(compiled,self.root/'tf-state.sqlite3').public()
        self.assertEqual(sum(f['type']=='dropdown' for f in public['items'][0]['fields']),4)
        self.assertEqual(next(f for f in public['items'][0]['fields'] if f['id']=='code-to-reproduce')['text'],'print("fixture")\n')

    @unittest.skipUnless(shutil.which('node'),'Existing Node for DOM/HTTP integration')
    def test_compiled_data_uses_the_existing_copy_only_view_and_real_api(self):
        self.change_answers(lambda a:a['issues']['cli-case']['responses'].update(problem='Untrusted <style>body{display:none}</style><script>globalThis.compromised=true</script> content.'))
        compiled=self.compile();app=ReviewApplication(compiled,self.root/'ui-state.sqlite3')
        server=ReviewServer(('127.0.0.1',0),app);thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start()
        try:
            result=subprocess.run([shutil.which('node'),str(Path(__file__).with_name('test_service_ui.cjs')),'http://127.0.0.1:'+str(server.server_port),str(SKILL_ROOT/'assets')],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)['pass'])
        finally:server.shutdown();server.server_close();thread.join(timeout=2)


class LayoutGuardTests(unittest.TestCase):
    def test_modified_view_is_rejected_without_changing_the_live_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in [*VIEW_FILES,MANIFEST]:
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(SKILL_ROOT/name,target)
            self.assertTrue(ensure_layout(root)['ok'])
            (root/VIEW_FILES[0]).write_text('A regenerated page that must not be accepted.')
            with self.assertRaisesRegex(ValueError,'view file changed'):ensure_layout(root)
            state=root/'uncreated/state.sqlite3'
            # Integrity fails before database creation or page delivery.
            with self.assertRaises(ValueError):ReviewApplication(root/'missing-pack.json',state,layout_root=root)
            self.assertFalse(state.exists())

    def test_page_requests_fail_closed_after_an_accidental_view_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pack=service_pack(root);write_json(root/'pack.json',pack)
            layout=root/'view-root'
            for name in [*VIEW_FILES,MANIFEST]:
                target=layout/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(SKILL_ROOT/name,target)
            app=ReviewApplication(root/'pack.json',root/'state.sqlite3',allow_unbound_draft=True,layout_root=layout)
            server=ReviewServer(('127.0.0.1',0),app);thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start()
            try:
                (layout/'assets/review-app.css').write_text('accidental edit')
                with self.assertRaises(HTTPError) as error:urlopen('http://127.0.0.1:'+str(server.server_port),timeout=3)
                self.assertEqual(error.exception.code,503)
            finally:server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=='__main__':unittest.main()
