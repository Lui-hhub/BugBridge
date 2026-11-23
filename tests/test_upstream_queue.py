"""Durable retrieval against synthetic evidence and local/fake HTTP, never real bugs."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from fixtures import SCRIPTS, add_run, make_v2_pack, save
from github_http import Credential, GitHubClient, Response, resolve_credential, retry_decision, validate_url
from review_pack import artifact, read_json, write_json
from upstream_queue import Queue, work


TOKEN='ghp_synthetic_fixture_not_a_real_token'


class Clock:
    def __init__(self):self.value=1800000000.0
    def __call__(self):return self.value


def hit(number=1,pr=False):
    base='https://api.github.com/repos/example/engine/issues/'+str(number)
    value=dict(number=number,url=base,html_url='https://github.com/example/engine/'+('pull/' if pr else 'issues/')+str(number),
               title='Synthetic report '+str(number),comments_url=base+'/comments')
    if pr:value['pull_request']=dict(url='https://api.github.com/repos/example/engine/pulls/'+str(number))
    return value


class FakeClient:
    def __init__(self,handler=None):self.calls=[];self.handler=handler
    def get(self,url,cached=None):
        self.calls.append((url,cached))
        if self.handler:return self.handler(url,cached)
        parts=urlsplit(url);query=parse_qs(parts.query)
        if parts.path=='/search/issues':
            pr='is:pr' in query['q'][0]
            body=dict(total_count=1,incomplete_results=False,items=[hit(2,True) if pr else hit()])
        elif parts.path.endswith('/files'):body=[dict(filename='engine.py',status='modified',patch='@@ synthetic patch @@')]
        elif parts.path.endswith(('/comments','/timeline')):body=[]
        else:body=dict(number=2 if '/pulls/' in parts.path else 1,title='Synthetic detail',state='closed',merged=False)
        return Response(200,{'etag':'"fixture"'},json.dumps(body).encode(),url)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.pack=make_v2_pack(self.root);self.pack['library']['repository']='https://github.com/example/engine'
        add_run(self.root,self.pack['issues'][0]);self.path=self.root/'pack.json';write_json(self.path,self.pack)
        self.plan=dict(schema_version='1.0',claim='Synthetic requested correction.',queries=['specific synthetic symptom'],
                       limits=dict(max_pages=1,max_records=5,max_attempts=3,max_requests=40,max_seconds=3600,cache_max_age_seconds=300))
        self.clock=Clock();self.queue=Queue(self.root/'state/upstream.sqlite3',self.clock)
        self.auth=Credential(TOKEN,'test','personal_access_token')

    def tearDown(self):self.tmp.cleanup()

    def enqueue(self,plan=None,refresh=False):
        return self.queue.enqueue(self.path,'cli-case',plan or self.plan,refresh)['job_id']

    def drain(self,client,queue=None):
        queue=queue or self.queue
        for _ in range(100):
            result=queue.step(self.auth,client)
            if result['state']=='idle':return
            if result['state']=='waiting_rate':self.clock.value=max(self.clock.value,result['resume_at']+.1)
            if result['state']=='needs_auth':self.fail(result)
        self.fail('Queue did not terminate within the fixture bound')

    def test_only_criterion_bound_success_is_eligible_and_enqueue_is_idempotent(self):
        first=self.enqueue();self.assertEqual(first,self.enqueue())
        self.assertNotEqual(first,self.enqueue(refresh=True))
        self.pack['issues'][0]['reproduction']['status']='inconclusive';write_json(self.path,self.pack)
        with self.assertRaisesRegex(ValueError,'successful'):self.enqueue()

    def test_documentation_requires_supported_evidence_instead_of_runtime_execution(self):
        with self.assertRaisesRegex(ValueError,'Documentation'):self.queue.enqueue(self.path,'doc-case',self.plan)
        self.pack['issues'][1]['finding']['status']='supported';write_json(self.path,self.pack)
        plan=dict(self.plan,documentation_basis=[self.pack['sources'][0]])
        self.assertTrue(self.queue.enqueue(self.path,'doc-case',plan)['job_id'])

    def test_empty_bad_or_overbroad_plans_are_rejected_before_any_request(self):
        for mutate in [lambda p:p.update(queries=[]),lambda p:p.update(queries=['repo:other/private symptom']),
                       lambda p:p['limits'].update(max_requests=0),lambda p:p.update(layout='new page')]:
            plan=copy.deepcopy(self.plan);mutate(plan)
            with self.assertRaises(ValueError):self.enqueue(plan)

    def test_collects_issues_prs_comments_and_patch_files_without_semantic_judgments(self):
        before=self.path.read_bytes();jid=self.enqueue();client=FakeClient();self.drain(client)
        self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'succeeded')
        queries=[parse_qs(urlsplit(u).query)['q'][0] for u,_ in client.calls if '/search/issues?' in u]
        self.assertTrue(any('is:issue' in q for q in queries));self.assertTrue(any('is:pr' in q for q in queries))
        self.assertTrue(all('repo:example/engine' in q and 'state:open' not in q for q in queries))
        exported=self.queue.export(jid,self.root/'export');snapshot=read_json(Path(exported['evidence']))
        self.assertEqual({r['kind'] for r in snapshot['records']},{'issue','pull_request'})
        self.assertTrue(snapshot['semantic_assessment_required']);self.assertNotIn('relation',snapshot)
        self.assertTrue(any('/files?' in u for u,_ in client.calls));self.assertTrue(any('/comments?' in u for u,_ in client.calls))
        self.assertEqual(self.path.read_bytes(),before)
        for ref in snapshot['sources']:artifact(self.root/'export',ref)
        with self.assertRaises(ValueError):self.queue.export(jid,self.root/'export')

    def test_missing_authentication_is_visible_and_does_not_make_requests(self):
        jid=self.enqueue();client=FakeClient();result=self.queue.step(None,client)
        self.assertEqual(result['state'],'needs_auth');self.assertIn('operator',result['notice']);self.assertEqual(client.calls,[])
        restarted=Queue(self.queue.path,self.clock)
        self.assertEqual(restarted.status(jid)['jobs'][0]['state'],'needs_auth')
        restarted.resume();self.drain(client,restarted)
        self.assertEqual(restarted.status(jid)['jobs'][0]['state'],'succeeded')

    def test_invalid_token_stops_all_jobs_until_explicit_resume(self):
        self.enqueue();calls=FakeClient(lambda u,c:Response(401,{},b'{"message":"Bad credentials"}',u))
        self.assertEqual(self.queue.step(self.auth,calls)['state'],'needs_auth')
        self.assertEqual(self.queue.step(self.auth,calls)['state'],'needs_auth');self.assertEqual(len(calls.calls),1)
        self.queue.resume();self.drain(FakeClient())

    def test_rate_limit_is_durable_and_waiting_does_not_block_the_caller(self):
        jid=self.enqueue()
        client=FakeClient(lambda u,c:Response(429,{'retry-after':'120','x-ratelimit-remaining':'0','x-ratelimit-reset':str(self.clock.value+180)},b'{"message":"secondary rate limit"}',u))
        start=time.monotonic();result=self.queue.step(self.auth,client)
        self.assertLess(time.monotonic()-start,1)
        self.assertGreaterEqual(result['resume_at'],self.clock.value+180)
        restarted=Queue(self.queue.path,self.clock)
        self.assertEqual(restarted.step(self.auth,client)['state'],'waiting_rate');self.assertEqual(len(client.calls),1)
        self.clock.value=result['resume_at']+1;self.drain(FakeClient(),restarted)
        self.assertEqual(restarted.status(jid)['jobs'][0]['state'],'succeeded')

    def test_search_budget_does_not_use_the_core_budget(self):
        jid=self.enqueue();client=FakeClient();self.queue.step(self.auth,client)
        with self.queue.connect() as db:
            db.execute('INSERT INTO budgets VALUES("search",0,?)',(self.clock.value+100,))
        result=self.queue.step(self.auth,client)
        self.assertEqual(result['state'],'retrieved');self.assertNotIn('/search/issues?',client.calls[-1][0])

    def test_response_headers_pace_searches_without_blocking_core_reads(self):
        self.enqueue();normal=FakeClient()
        def handler(url,cached):
            response=normal.get(url,cached)
            if '/search/issues?' in url:
                response.headers.update({'x-ratelimit-resource':'search','x-ratelimit-remaining':'2','x-ratelimit-reset':str(self.clock.value+60)})
            return response
        client=FakeClient(handler);self.queue.step(self.auth,client)
        self.queue.step(self.auth,client)
        self.assertNotIn('/search/issues?',client.calls[-1][0])
        with self.queue.connect() as db:next_time=db.execute('SELECT next_at FROM pacing WHERE resource="search"').fetchone()[0]
        self.assertGreaterEqual(next_time,self.clock.value+30)

    def test_exhausted_secondary_retry_budget_still_pauses_other_jobs(self):
        plan=copy.deepcopy(self.plan);plan['limits']['max_attempts']=1;self.enqueue(plan)
        client=FakeClient(lambda u,c:Response(429,{'retry-after':'120'},b'{"message":"secondary rate limit"}',u))
        self.assertEqual(self.queue.step(self.auth,client)['state'],'failed')
        self.enqueue(refresh=True)
        self.assertEqual(self.queue.step(self.auth,client)['state'],'waiting_rate');self.assertEqual(len(client.calls),1)

    def test_worker_automatically_resumes_a_deferred_request(self):
        jid=self.enqueue();normal=FakeClient();failed=False
        def handler(url,cached):
            nonlocal failed
            if not failed:
                failed=True;return Response(429,{'retry-after':'1'},b'{"message":"secondary rate limit"}',url)
            return normal.get(url,cached)
        def advance(seconds):self.clock.value+=seconds
        with patch('upstream_queue.signal.signal'),patch('upstream_queue.time.sleep',side_effect=advance):
            result=work(self.queue,self.auth,idle_exit=0,client=FakeClient(handler))
        self.assertEqual(result['state'],'idle');self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'succeeded')

    def test_cancel_retains_evidence_and_prevents_more_network_requests(self):
        jid=self.enqueue();client=FakeClient();self.queue.step(self.auth,client);count=len(client.calls)
        self.queue.cancel(jid,'An inspected same-issue match already suffices.')
        self.assertEqual(self.queue.step(self.auth,client)['state'],'idle');self.assertEqual(len(client.calls),count)
        snapshot=read_json(Path(self.queue.export(jid,self.root/'cancelled')['evidence']))
        self.assertEqual(snapshot['state'],'cancelled');self.assertTrue(snapshot['records'])

    def test_cross_repository_pagination_is_refused(self):
        plan=copy.deepcopy(self.plan);plan['limits']['max_pages']=2;jid=self.enqueue(plan)
        client=FakeClient(lambda u,c:Response(200,{'link':'<https://api.github.com/search/issues?q=repo%3Aother%2Fprivate>; rel="next"'},
            b'{"total_count":200,"incomplete_results":false,"items":[]}',u))
        self.drain(client);self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'failed')
        self.assertTrue(all('repo%3Aexample%2Fengine' in url for url,_ in client.calls))

    def test_official_numeric_repository_pagination_urls_are_followed(self):
        plan=copy.deepcopy(self.plan);plan['limits']['max_pages']=2;jid=self.enqueue(plan);normal=FakeClient()
        def handler(url,cached):
            if '/repositories/123/issues/1/comments' in url:return Response(200,{},b'[]',url)
            response=normal.get(url,cached)
            if '/repos/example/engine/issues/1/comments?' in url:
                response.headers['link']='<https://api.github.com/repositories/123/issues/1/comments?per_page=100&page=2>; rel="next"'
            return response
        client=FakeClient(handler);self.drain(client)
        self.assertTrue(any('/repositories/123/' in url for url,_ in client.calls))
        self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'succeeded')

    def test_repeated_pagination_link_is_a_gap(self):
        plan=copy.deepcopy(self.plan);plan['limits']['max_pages']=2;jid=self.enqueue(plan)
        client=FakeClient(lambda u,c:Response(200,{'link':'<'+u+'>; rel="next"'},
            b'{"total_count":200,"incomplete_results":false,"items":[]}',u))
        self.drain(client);status=self.queue.status(jid)['jobs'][0]
        self.assertEqual(status['state'],'partial');self.assertTrue(any('Repeated pagination' in gap for gap in status['gaps']))

    def test_failed_search_preserves_positive_matches_and_exports_partial_results(self):
        jid=self.enqueue();normal=FakeClient()
        def handler(url,cached):
            if '/search/issues?' in url and 'is%3Apr' in url:return Response(422,{},b'{"message":"Bad query"}',url)
            return normal.get(url,cached)
        self.drain(FakeClient(handler));status=self.queue.status(jid)['jobs'][0]
        self.assertEqual(status['state'],'partial');self.assertTrue(status['gaps'])
        snapshot=read_json(Path(self.queue.export(jid,self.root/'partial')['evidence']))
        self.assertTrue(snapshot['records']);self.assertNotIn('no_match_in_scope',json.dumps(snapshot))

    def test_retry_request_and_time_budgets_are_finite(self):
        jid=self.enqueue();client=FakeClient(lambda u,c:Response(503,{},b'{}',u));self.drain(client)
        self.assertEqual(len(client.calls),6);self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'failed')
        plan=copy.deepcopy(self.plan);plan['limits']['max_requests']=1
        second=self.enqueue(plan);self.drain(FakeClient())
        self.assertEqual(self.queue.status(second)['jobs'][0]['network_requests'],1)
        third=self.enqueue(refresh=True);self.clock.value+=4000;self.queue.step(self.auth,FakeClient())
        self.assertEqual(self.queue.status(third)['jobs'][0]['state'],'failed')

    def test_reproduction_or_claim_changes_make_pending_work_stale(self):
        jid=self.enqueue();self.pack['issues'][0]['title']='A changed correction';write_json(self.path,self.pack)
        client=FakeClient();self.assertEqual(self.queue.step(self.auth,client)['state'],'stale');self.assertFalse(client.calls)
        self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'stale')

    def test_selected_source_tampering_and_changes_during_http_are_detected(self):
        jid=self.enqueue();run=self.pack['issues'][0]['reproduction']['runs'][0]
        result=read_json(artifact(self.root,run['result']))
        source=artifact(self.root/Path(run['result']['path']).parent,result['source']);original=source.read_bytes()
        source.write_text('Changed source without changing its frozen hash')
        client=FakeClient();self.assertEqual(self.queue.step(self.auth,client)['state'],'stale');self.assertFalse(client.calls)
        source.write_bytes(original);second=self.enqueue(refresh=True)
        def handler(url,cached):
            self.pack['issues'][0]['title']='Changed while HTTP was in progress';write_json(self.path,self.pack)
            return FakeClient().get(url,cached)
        self.assertEqual(self.queue.step(self.auth,FakeClient(handler))['state'],'stale')
        self.assertEqual(self.queue.status(second)['jobs'][0]['state'],'stale')

    def test_changed_unrelated_candidate_does_not_invalidate_this_claim(self):
        jid=self.enqueue();self.pack['issues'][1]['title']='Unrelated draft edit';write_json(self.path,self.pack)
        self.drain(FakeClient());self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'succeeded')

    def test_fresh_cache_is_shared_between_jobs_and_304_reuses_the_full_body(self):
        self.enqueue();client=FakeClient();self.drain(client);initial=len(client.calls)
        plan=copy.deepcopy(self.plan);plan['claim']='Another requested correction using the same queries'
        jid=self.enqueue(plan);self.drain(client);self.assertEqual(len(client.calls),initial)
        refreshed=self.enqueue(plan,refresh=True)
        revalidate=FakeClient(lambda u,c:Response(304,{'etag':'"checked"'},b'',u))
        self.drain(revalidate)
        self.assertTrue(all(c and c['etag'] for _,c in revalidate.calls))
        snapshot=read_json(Path(self.queue.export(refreshed,self.root/'revalidated')['evidence']))
        self.assertEqual(len(snapshot['records']),2)
        self.assertTrue(any(a.get('cached_body_evidence') for t in snapshot['tasks'] for a in t['attempts']))

    def test_304_without_cache_is_a_failure_and_cannot_mean_no_results(self):
        jid=self.enqueue();self.drain(FakeClient(lambda u,c:Response(304,{},b'',u)))
        self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'failed')

    def test_malformed_success_and_changed_cache_are_never_accepted_as_complete(self):
        jid=self.enqueue();self.drain(FakeClient(lambda u,c:Response(200,{},b'<html>login failed</html>',u)))
        self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'failed')
        plan=copy.deepcopy(self.plan);plan['claim']='Cache integrity fixture';second=self.enqueue(plan)
        client=FakeClient();self.drain(client);prior=len(client.calls)
        with self.queue.connect() as db:
            entry=json.loads(db.execute('SELECT entry FROM cache LIMIT 1').fetchone()[0])
        envelope=read_json(artifact(self.queue.objects,entry['evidence']))
        (self.queue.objects/envelope['body']['path']).write_text('Changed cached payload')
        third=self.enqueue(dict(plan,claim='Another cache consumer'));self.drain(client)
        self.assertGreater(len(client.calls),prior)
        status=self.queue.status(third)['jobs'][0];self.assertEqual(status['state'],'partial')
        self.assertTrue(any('integrity' in gap for gap in status['gaps']))

    def test_redirects_count_against_the_network_request_budget(self):
        plan=copy.deepcopy(self.plan);plan['limits']['max_requests']=1;jid=self.enqueue(plan);requests=[]
        class Opener:
            def open(self,request,timeout):
                requests.append(request)
                raise HTTPError(request.full_url,302,'Redirect',{'Location':request.full_url+'&moved=1'},io.BytesIO(b''))
        self.drain(GitHubClient(self.auth,opener=Opener()))
        self.assertEqual(len(requests),1);self.assertEqual(self.queue.status(jid)['jobs'][0]['network_requests'],1)

    def test_time_budget_can_end_a_long_rate_wait_and_worker_then_becomes_idle(self):
        jid=self.enqueue();client=FakeClient(lambda u,c:Response(429,{'retry-after':'99999'},b'{"message":"secondary rate limit"}',u))
        self.queue.step(self.auth,client);self.clock.value+=4000
        self.assertEqual(self.queue.step(self.auth,client)['state'],'idle')
        self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'failed')

    def test_pagination_and_detail_caps_are_visible_gaps(self):
        jid=self.enqueue()
        def handler(url,cached):
            if '/search/issues?' in url:
                return Response(200,{'link':'<'+url+'&page=2>; rel="next"'},json.dumps(dict(total_count=1200,incomplete_results=True,items=[hit()])).encode(),url)
            return FakeClient().get(url,cached)
        self.drain(FakeClient(handler));status=self.queue.status(jid)['jobs'][0]
        self.assertEqual(status['state'],'partial');self.assertTrue(any('Pagination' in g for g in status['gaps']))
        self.assertTrue(any('incomplete_results' in g for g in status['gaps']))

    def test_crash_after_claim_can_be_resumed_without_losing_attempt_budget(self):
        jid=self.enqueue()
        with self.queue.connect() as db:db.execute('UPDATE tasks SET state="running",attempts=1 WHERE id=(SELECT min(id) FROM tasks)')
        restarted=Queue(self.queue.path,self.clock);self.drain(FakeClient(),restarted)
        self.assertEqual(restarted.status(jid)['jobs'][0]['state'],'succeeded')

    def test_credentials_are_absent_from_database_objects_and_export_even_if_echoed(self):
        jid=self.enqueue();normal=FakeClient()
        def echo(url,cached):
            response=normal.get(url,cached)
            if '/search/issues?' in url:
                body=json.loads(response.body);body['message']=TOKEN;response.body=json.dumps(body).encode()
            response.headers['set-cookie']=TOKEN;response.headers['etag']=TOKEN
            return response
        self.drain(FakeClient(echo));self.queue.export(jid,self.root/'safe-export')
        for base in [self.queue.path.parent,self.root/'safe-export']:
            for path in base.rglob('*'):
                if path.is_file():self.assertNotIn(TOKEN.encode(),path.read_bytes(),str(path))

    def test_two_workers_serialize_the_network_and_keep_all_tasks(self):
        jid=self.enqueue();active=0;maximum=0;guard=threading.Lock();normal=FakeClient()
        def handler(url,cached):
            nonlocal active,maximum
            with guard:active+=1;maximum=max(maximum,active)
            time.sleep(.015)
            result=normal.get(url,cached)
            with guard:active-=1
            return result
        errors=[]
        def worker():
            try:
                queue=Queue(self.queue.path,self.clock)
                for _ in range(200):
                    result=queue.step(self.auth,FakeClient(handler))
                    if result['state']=='idle':return
                    if result['state']=='busy':time.sleep(.005)
            except Exception as exc:errors.append(exc)
        threads=[threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=5)
        self.assertFalse(errors,errors);self.assertTrue(all(not t.is_alive() for t in threads));self.assertEqual(maximum,1)
        self.assertEqual(self.queue.status(jid)['jobs'][0]['state'],'succeeded')

    def test_actual_local_http_responses_flow_into_exported_evidence(self):
        jid=self.enqueue();normal=FakeClient()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                response=normal.get('https://api.github.com'+self.path)
                self.send_response(response.status);self.end_headers();self.wfile.write(response.body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        class LocalBridge:
            def get(self,url,cached=None):
                parts=urlsplit(url)
                with urlopen('http://127.0.0.1:'+str(server.server_port)+parts.path+'?'+parts.query,timeout=3) as response:
                    return Response(response.status,{},response.read(),url)
        try:
            self.drain(LocalBridge());snapshot=read_json(Path(self.queue.export(jid,self.root/'http-export')['evidence']))
            self.assertEqual(snapshot['state'],'succeeded');self.assertEqual(len(snapshot['records']),2)
        finally:server.shutdown();server.server_close();thread.join(timeout=2)

    def test_cli_roundtrip_reports_missing_auth_as_one_json_result(self):
        plan_path=self.root/'plan.json';write_json(plan_path,self.plan)
        env=dict(os.environ)
        for key in ['ISSUE_REVIEW_GITHUB_PAT','GH_TOKEN','GITHUB_TOKEN']:env.pop(key,None)
        prefix=[sys.executable,str(SCRIPTS/'upstream_queue.py'),'--state',str(self.queue.path)]
        def command(args):
            result=subprocess.run(prefix+args,capture_output=True,text=True,timeout=10,env=env)
            self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
        jid=command(['enqueue','--pack',str(self.path),'--issue-id','cli-case','--plan',str(plan_path)])['job_id']
        result=command(['work','--once','--no-cli-auth']);self.assertEqual(result['state'],'needs_auth')
        self.assertTrue(command(['status','--job',jid])['notice'])
        exported=command(['export','--job',jid,'--output-dir',str(self.root/'cli-export')])
        self.assertEqual(read_json(Path(exported['evidence']))['state'],'needs_auth')
        self.assertEqual(command(['cancel','--job',jid,'--reason','Synthetic CLI fixture completed.'])['state'],'cancelled')

    @unittest.skipUnless(os.name=='posix','POSIX detached-worker lifetime check')
    def test_background_worker_returns_pid_and_exits_after_idle(self):
        env=dict(os.environ)
        for key in ['ISSUE_REVIEW_GITHUB_PAT','GH_TOKEN','GITHUB_TOKEN']:env.pop(key,None)
        result=subprocess.run([sys.executable,str(SCRIPTS/'upstream_queue.py'),'--state',str(self.queue.path),'work','--background','--idle-exit','1','--no-cli-auth'],
            capture_output=True,text=True,timeout=10,env=env)
        self.assertEqual(result.returncode,0,result.stderr);info=json.loads(result.stdout)
        self.assertIn('pid',info);self.assertTrue(Path(info['stdout_log']).is_file())
        # No jobs were enqueued: the process may finish after its finite idle period.
        time.sleep(1.3)
        self.assertIn('"state":"idle"',Path(info['stdout_log']).read_text())

    @unittest.skipUnless(os.name=='posix','POSIX detached-worker cleanup')
    def test_starting_background_work_twice_reuses_the_live_worker(self):
        command=[sys.executable,str(SCRIPTS/'upstream_queue.py'),'--state',str(self.queue.path),'work','--background','--idle-exit','5','--no-cli-auth']
        env=dict(os.environ)
        for key in ['ISSUE_REVIEW_GITHUB_PAT','GH_TOKEN','GITHUB_TOKEN']:env.pop(key,None)
        first=subprocess.run(command,capture_output=True,text=True,timeout=10,env=env);self.assertEqual(first.returncode,0,first.stderr)
        info=json.loads(first.stdout)
        try:
            second=subprocess.run(command,capture_output=True,text=True,timeout=10,env=env);self.assertEqual(second.returncode,0,second.stderr)
            again=json.loads(second.stdout);self.assertEqual(again['state'],'already_running');self.assertEqual(again['pid'],info['pid'])
        finally:os.kill(info['pid'],signal.SIGTERM)


class HttpPolicyTests(unittest.TestCase):
    def test_pat_precedes_other_configured_credentials_and_cli_fallback(self):
        auth=resolve_credential({'GH_TOKEN':'gho_other','GITHUB_TOKEN':TOKEN},allow_cli=False)
        self.assertEqual(auth.source,'GITHUB_TOKEN');self.assertNotIn(TOKEN,repr(auth))
        self.assertIsNone(resolve_credential({},allow_cli=False))
        with self.assertRaises(ValueError):resolve_credential({'GH_TOKEN':'bad\nheader'},allow_cli=False)

    def test_rate_policy_honors_deadlines_and_does_not_retry_permissions(self):
        response=Response(403,{'retry-after':'20','x-ratelimit-remaining':'0','x-ratelimit-reset':'200'},b'{}','')
        self.assertEqual(retry_decision(response,1,100)['resume_at'],200)
        secondary=Response(403,{},b'{"message":"secondary rate limit"}','')
        self.assertEqual(retry_decision(secondary,1,100)['resume_at'],160)
        self.assertEqual(retry_decision(secondary,2,100)['resume_at'],220)
        self.assertEqual(retry_decision(Response(403,{},b'{"message":"Forbidden"}',''),1,100)['kind'],'permanent')

    def test_transport_authenticates_get_and_sends_conditional_headers(self):
        requests=[]
        class Result(io.BytesIO):
            status=200;headers={'ETag':'"etag"','Set-Cookie':TOKEN}
        class Opener:
            def open(self,request,timeout):requests.append(request);return Result(b'{"echo":"'+TOKEN.encode()+b'"}')
        client=GitHubClient(Credential(TOKEN),opener=Opener())
        response=client.get('https://api.github.com/repos/example/engine/issues/1',cached={'etag':'"prior"'})
        self.assertEqual(requests[0].method,'GET');self.assertEqual(requests[0].get_header('Authorization'),'Bearer '+TOKEN)
        self.assertEqual(requests[0].get_header('If-none-match'),'"prior"');self.assertNotIn(TOKEN.encode(),response.body)
        self.assertNotIn('set-cookie',response.headers)

    def test_external_redirect_is_rejected_before_sending_a_second_request(self):
        requests=[]
        class Opener:
            def open(self,request,timeout):
                requests.append(request)
                raise HTTPError(request.full_url,302,'Redirect',{'Location':'https://evil.invalid/steal'},io.BytesIO(b''))
        with self.assertRaises(ValueError):GitHubClient(Credential(TOKEN),opener=Opener()).get('https://api.github.com/repos/example/engine/issues/1')
        self.assertEqual(len(requests),1)
        for url in ['http://api.github.com/search/issues','https://api.github.com/user','https://api.github.com/search/issues?access_token=secret']:
            with self.assertRaises(ValueError):validate_url(url)

    def test_documented_same_origin_redirect_is_followed_with_get(self):
        requests=[]
        class Result(io.BytesIO):status=200;headers={}
        class Opener:
            def open(self,request,timeout):
                requests.append(request)
                if len(requests)==1:raise HTTPError(request.full_url,301,'Moved',{'Location':'https://api.github.com/repos/example/engine/issues/2'},io.BytesIO(b''))
                return Result(b'{"number":2}')
        response=GitHubClient(Credential(TOKEN),opener=Opener()).get('https://api.github.com/repos/example/engine/issues/1')
        self.assertEqual(response.status,200);self.assertEqual(len(requests),2);self.assertTrue(response.url.endswith('/2'))


if __name__=='__main__':unittest.main()
