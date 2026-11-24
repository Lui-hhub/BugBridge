"""Local HTTP/persistence regressions, synthetic reports only."""
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fixtures import SCRIPTS, form, make_pack, make_v2_pack, save
from pack_v2 import QUALITY_CHECKS
from review_pack import write_json
from serve_review import DEFAULT_TTL_SECONDS, ProgressStore, ReviewApplication, ReviewServer


def service_pack(root):
    pack=make_v2_pack(root)
    for template in pack['templates']:
        template['source'].update(status='official', limitation='', url='https://example.invalid/examplelib/blob/fixture/.github/ISSUE_TEMPLATE/'+template['id']+('.md' if template['format']=='markdown' else '.json'),retrieved_at='2026-11-23')
    for issue in pack['issues']:
        issue['publication']['quality_review']=dict(status='reviewed',reviewer='agent',reviewed_at='2026-11-23T00:10:00+00:00',notes='Synthetic content reviewed for HTTP/UI tests; not a real bug claim.',
            checks=[dict(id=k,status='not_applicable' if k=='minimal_reproducer' else 'pass',reason='Synthetic rendering fixture.') for k in sorted(QUALITY_CHECKS)])
    return pack


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.pack=service_pack(self.root);self.path=self.root/'pack.json';write_json(self.path,self.pack)
        self.state=self.root/'separate-state/review.sqlite3'
        self.start()

    def start(self, ttl_seconds=DEFAULT_TTL_SECONDS):
        self.app=ReviewApplication(self.path,self.state,allow_unbound_draft=True)
        self.server=ReviewServer(('127.0.0.1',0),self.app,ttl_seconds=ttl_seconds)
        self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.02),daemon=True)
        self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

    def tearDown(self):
        self.stop();self.tmp.cleanup()

    def get(self,path='/api/review'):
        with urlopen(self.url+path,timeout=3) as response:return json.load(response)

    def post(self,path,body,headers=None):
        request=Request(self.url+path,json.dumps(body).encode(),headers=headers or {'Content-Type':'application/json'})
        with urlopen(request,timeout=3) as response:return json.load(response)

    def test_main_projection_contains_only_official_payloads_and_navigation(self):
        public=self.get();self.assertEqual(len(public['items']),3)
        issue=public['items'][0]
        self.assertEqual([f['id'] for f in issue['fields']],['title','version','problem','logs','backend'])
        self.assertNotIn('instructions',json.dumps(public))
        for hidden in ['runs','criterion_data','blockers','schema_version','validation_scope','upstream']:
            self.assertNotIn(hidden,public)
            self.assertNotIn(hidden,issue)
        self.assertIn('template=bug.json',issue['official_url'])
        with urlopen(self.url,timeout=3) as response:text=response.read().decode()
        self.assertNotIn('<select',text);self.assertNotIn('<input',text)

    def test_copy_and_selection_survive_new_service_instance(self):
        data=self.get();item=data['items'][1];field=item['fields'][0]
        self.post('/api/copied',dict(issue_id=item['id'],key=field['key']))
        self.post('/api/selection',dict(issue_id=item['id']))
        self.stop();self.start()
        data=self.get();self.assertEqual(data['selected_id'],item['id'])
        self.assertTrue(data['items'][1]['fields'][0]['copied'])
        self.assertFalse(data['items'][1]['fields'][1]['copied'])

    def test_only_changed_payload_loses_copy_progress(self):
        item=self.get()['items'][1]
        for field in item['fields']:self.post('/api/copied',dict(issue_id=item['id'],key=field['key']))
        self.pack['issues'][1]['responses']['doc']['value']='Updated synthetic description.'
        write_json(self.path,self.pack)
        changed=self.get()['items'][1]
        self.assertTrue(changed['fields'][0]['copied']);self.assertFalse(changed['fields'][1]['copied']);self.assertTrue(changed['fields'][2]['copied'])
        with self.assertRaises(HTTPError) as error:self.post('/api/copied',dict(issue_id=item['id'],key=item['fields'][1]['key']))
        self.assertEqual(error.exception.code,409)

    def test_state_is_retained_when_run_directory_identity_changes(self):
        item=self.get()['items'][1];field=item['fields'][0]
        self.post('/api/copied',dict(issue_id=item['id'],key=field['key']))
        self.pack['run_id']='another-output-directory'
        write_json(self.path,self.pack)
        self.assertTrue(self.get()['items'][1]['fields'][0]['copied'])

    def test_new_instruction_block_does_not_reset_unchanged_generated_id_fields(self):
        raw=dict(name='Synthetic no-ID form',body=[dict(type='textarea',attributes=dict(label='Problem')),dict(type='textarea',attributes=dict(label='Environment'))])
        def install(raw, values):
            model=form(self.root,'bug',raw,{})
            for field in model['fields']:
                if field['type']!='markdown':field['purpose']='problem' if field['label']=='Problem' else 'environment'
            model['source'].update(status='official',limitation='',url='https://example.invalid/examplelib/blob/fixture/.github/ISSUE_TEMPLATE/bug.json',retrieved_at='2026-11-23')
            self.pack['templates'][0]=model
            self.pack['issues'][0]['responses']={fid:dict(value=value) for fid,value in values.items()}
            write_json(self.path,self.pack)
        install(raw,{'element-1':'Same problem.','element-2':'Same environment.'})
        item=self.get()['items'][0]
        for field in item['fields']:self.post('/api/copied',dict(issue_id=item['id'],key=field['key']))
        raw['body'].insert(0,dict(type='markdown',attributes=dict(value='New official instructions.')))
        install(raw,{'element-2':'Same problem.','element-3':'Same environment.'})
        self.assertTrue(all(f['copied'] for f in self.get()['items'][0]['fields']))

    def test_internal_placeholders_and_unreviewed_content_are_held_back(self):
        self.pack['issues'][1]['responses']['doc']['value']='[Not supplied. The human reviewer must write something.]'
        self.pack['issues'][2]['publication']['quality_review']['status']='pending'
        write_json(self.path,self.pack)
        self.assertEqual([i['id'] for i in self.get()['items']],['cli-case'])
        self.assertEqual(len(self.app.records()['held']),2)

    def test_optional_empty_fields_and_instructions_do_not_create_copy_steps(self):
        self.pack['issues'][0]['responses']['logs']['value']=''
        write_json(self.path,self.pack)
        self.assertNotIn('logs',[f['id'] for f in self.get()['items'][0]['fields']])
        self.assertEqual(len(self.get()['items'][2]['fields']),2)

    def test_archive_keeps_investigation_without_a_main_page_card(self):
        issue=self.pack['issues'][2];issue['finding']['status']='excluded';issue['action']['type']='no_submission';issue['responses']={}
        self.pack['dispositions'][2]['decision']='excluded';write_json(self.path,self.pack)
        self.assertEqual(len(self.get()['items']),2)
        self.assertEqual(len(self.app.records()['records']),3)
        with urlopen(self.url+'/records/legacy-case',timeout=3) as response:self.assertEqual(response.status,200)

    def test_legacy_followup_uses_one_comment_and_the_inspected_target(self):
        pack=make_pack(self.root)
        for template in pack['templates']:template['source'].update(self.pack['templates'][pack['templates'].index(template)]['source'])
        issue=pack['issues'][0];target='https://example.invalid/examplelib/issues/1'
        issue['upstream'].update(status='same_issue',checked_at='2026-11-23',matches=[dict(url=target,kind='issue',reason='Same synthetic report.',trigger_comparison='Same input boundary.')])
        issue['action']=dict(type='existing_issue_comment',target_url=target,comment=dict(value='A concise additional observation.'))
        write_json(self.path,pack)
        item=self.get()['items'][0]
        self.assertEqual(item['official_url'],target)
        self.assertEqual([f['id'] for f in item['fields']],['comment'])
        self.assertEqual(item['fields'][0]['text'],'A concise additional observation.')

    def test_invalid_update_does_not_replace_last_valid_copy_payloads(self):
        previous=self.get()['items']
        self.path.write_text('{broken')
        current=self.get();self.assertEqual(current['items'],previous);self.assertTrue(current['notice'])
        self.assertIsNotNone(self.app.last_error)
        with self.assertRaises(ValueError):ReviewApplication(self.path,self.root/'other-state.sqlite3')

    def test_default_lifetime_is_three_hours_and_invalid_lifetimes_are_rejected(self):
        self.assertEqual(DEFAULT_TTL_SECONDS,10800)
        self.assertEqual(self.server.ttl_seconds,10800)
        self.assertEqual((self.server.expires_at-self.server.started_at).total_seconds(),10800)
        for value in [0,-1,True,float('inf'),10**100]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                ReviewServer(('127.0.0.1',0),self.app,ttl_seconds=value)

    def test_expiry_releases_port_despite_requests_and_reload_and_keeps_progress(self):
        self.stop();self.start(ttl_seconds=1)
        item=self.get()['items'][1];field=item['fields'][0]
        self.post('/api/copied',dict(issue_id=item['id'],key=field['key']))
        self.post('/api/selection',dict(issue_id=item['id']))
        port=self.server.server_port
        self.pack['run_id']='reloaded-before-expiry';write_json(self.path,self.pack)
        self.get()
        self.assertEqual(self.app.pack['run_id'],'reloaded-before-expiry')
        until=time.monotonic()+4
        requests=0
        while self.thread.is_alive() and time.monotonic()<until:
            try:
                self.get('/health');requests+=1
            except OSError:
                pass
            self.thread.join(timeout=.03)
        self.assertGreater(requests,1)
        self.assertFalse(self.thread.is_alive(),'Requests or reload extended the service lifetime')
        with self.assertRaises(OSError):socket.create_connection(('127.0.0.1',port),timeout=.2)
        with ReviewServer(('127.0.0.1',port),self.app):
            pass
        self.start()
        restored=self.get()
        self.assertEqual(restored['selected_id'],item['id'])
        self.assertTrue(restored['items'][1]['fields'][0]['copied'])

    def test_foreground_service_exits_and_closes_port_at_deadline(self):
        result=subprocess.run([sys.executable,str(SCRIPTS/'serve_review.py'),str(self.path),'--state',str(self.root/'foreground/state.sqlite3'),
                               '--port','0','--ttl-seconds','1','--allow-unbound-draft'],capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr)
        info=json.loads(result.stdout)
        self.assertEqual(info['ttl_seconds'],1)
        self.assertEqual((datetime.fromisoformat(info['expires_at'])-datetime.fromisoformat(info['started_at'])).total_seconds(),1)
        with self.assertRaises(OSError):urlopen(info['url']+'/health',timeout=.2)

    def test_cli_rejects_unlimited_or_invalid_lifetime(self):
        for value in ['0','-1','never','inf',str(10**100)]:
            with self.subTest(value=value):
                result=subprocess.run([sys.executable,str(SCRIPTS/'serve_review.py'),str(self.path),'--ttl-seconds',value],
                                      capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,2)
                self.assertIn('--ttl-seconds',result.stderr)
                self.assertEqual(result.stdout,'')

    @unittest.skipUnless(os.name=='posix','POSIX cleanup for detached service test')
    def test_background_launcher_exits_while_service_remains_available(self):
        result=subprocess.run([sys.executable,str(SCRIPTS/'serve_review.py'),str(self.path),'--state',str(self.root/'background/state.sqlite3'),'--port','0','--background','--allow-unbound-draft'],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        info=json.loads(result.stdout)
        try:
            self.assertEqual(info['ttl_seconds'],10800)
            self.assertEqual((datetime.fromisoformat(info['expires_at'])-datetime.fromisoformat(info['started_at'])).total_seconds(),10800)
            with urlopen(info['url']+'/api/review',timeout=3) as response:data=json.load(response)
            self.assertEqual(len(data['items']),3)
            self.assertTrue(Path(info['stdout_log']).is_file())
        finally:
            os.kill(info['pid'],signal.SIGTERM)

    @unittest.skipUnless(os.name=='posix','POSIX cleanup for detached service test')
    def test_background_service_uses_requested_lifetime_and_releases_port(self):
        result=subprocess.run([sys.executable,str(SCRIPTS/'serve_review.py'),str(self.path),'--state',str(self.root/'expiring-background/state.sqlite3'),
                               '--port','0','--background','--ttl-seconds','1','--allow-unbound-draft'],capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr)
        info=json.loads(result.stdout)
        try:
            self.assertEqual(info['ttl_seconds'],1)
            self.assertEqual((datetime.fromisoformat(info['expires_at'])-datetime.fromisoformat(info['started_at'])).total_seconds(),1)
            with urlopen(info['url']+'/health',timeout=.5) as response:self.assertTrue(json.load(response)['ok'])
            until=time.monotonic()+4
            while time.monotonic()<until:
                try:
                    with urlopen(info['url']+'/health',timeout=.3) as response:response.read()
                except OSError:
                    break
                time.sleep(.03)
            else:
                self.fail('Detached service did not release its port at expiry')
        finally:
            try:os.kill(info['pid'],signal.SIGTERM)
            except ProcessLookupError:pass

    def test_cross_origin_or_non_json_mutation_is_rejected(self):
        field=self.get()['items'][0]['fields'][0];body=dict(issue_id='cli-case',key=field['key'])
        with self.assertRaises(HTTPError) as error:self.post('/api/copied',body,{'Content-Type':'application/json','Origin':'https://unrelated.invalid'})
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(HTTPError) as error:self.post('/api/copied',body,{'Content-Type':'text/plain'})
        self.assertEqual(error.exception.code,415)
        self.assertFalse(self.get()['items'][0]['fields'][0]['copied'])

    def test_cached_tensorflow_form_exposes_prepared_values_and_raw_code(self):
        from template_cache import TemplateCache
        template=TemplateCache().export('tf','tensorflow_issue_template',self.root)
        self.pack['templates']=[template]
        issue=self.pack['issues'][0];self.pack['issues']=[issue]
        self.pack['material_items']=self.pack['material_items'][:1];self.pack['dispositions']=self.pack['dispositions'][:1]
        issue['template_id']=template['id']
        code='print("synthetic UI fixture, not a TensorFlow bug")\n'
        values={'issue-type':'Bug','tf-nightly':'No','source':'binary','tfversion':'synthetic-ui-fixture','Code':'Yes',
                'what-happened':'A synthetic form-rendering test, with no claim about TensorFlow execution.','code-to-reproduce':code,'logs':'synthetic log\n'}
        issue['responses']={key:dict(value=value) for key,value in values.items()}
        write_json(self.path,self.pack)
        item=self.get()['items'][0]
        self.assertEqual(sum(f['type']=='dropdown' for f in item['fields']),4)
        self.assertEqual([f['id'] for f in item['fields']],['title','issue-type','tf-nightly','source','tfversion','Code','what-happened','code-to-reproduce','logs'])
        field=next(f for f in item['fields'] if f['id']=='code-to-reproduce')
        self.assertEqual(field['text'],code);self.assertEqual(field['render'],'shell')
        self.assertNotIn('```',field['text'])

    @unittest.skipUnless(shutil.which('node'),'Existing Node for DOM emulation')
    def test_copy_only_frontend_uses_real_local_api_and_restores_progress(self):
        self.pack['issues'][0]['responses']['problem']['value']='Untrusted </script><script>globalThis.compromised=true</script> text'
        write_json(self.path,self.pack)
        proc=subprocess.run([shutil.which('node'),str(Path(__file__).with_name('test_service_ui.cjs')),self.url,str(SCRIPTS.parent/'assets')],capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr)
        result=json.loads(proc.stdout);self.assertTrue(result['pass']);self.assertGreater(result['copied_fields'],3)


if __name__=='__main__':unittest.main()
