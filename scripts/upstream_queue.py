#!/usr/bin/env python3
"""Persistent, authenticated, read-only GitHub retrieval after reproduction."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

from github_http import (API_VERSION, SAFE_HEADERS, GitHubClient, Response, number,
                         resolve_credential, retry_decision, safe_text, validate_url)
from review_pack import artifact, read_json, sha, validate, write_json

DEFAULT_LIMITS = dict(max_pages=2, max_records=10, max_attempts=4, max_requests=100,
                      max_seconds=7200, cache_max_age_seconds=300)
MAX_LIMITS = dict(max_pages=10, max_records=100, max_attempts=8, max_requests=1000,
                  max_seconds=86400, cache_max_age_seconds=86400)
TERMINAL = {'succeeded','partial','failed','stale','cancelled'}
AUTH_NOTICE = ('GitHub authentication is required. Configure a personal access token locally '
               '(ISSUE_REVIEW_GITHUB_PAT, GH_TOKEN or GITHUB_TOKEN), or an existing authenticated CLI. '
               'Tell the operator what is missing; ask for configuration location/method, never the secret. '
               'Then run resume and work; independent local work can continue.')


class EvidenceIntegrityError(ValueError):
    pass


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def utc(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat()


def default_state():
    return Path(os.environ.get('XDG_STATE_HOME',Path.home()/'.local/state'))/'issue-materials-to-html/upstream.sqlite3'


@contextmanager
def worker_lock(path):
    """OS releases this lock on a crash; separate workers cannot overlap requests."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b');locked=False
    try:
        if os.name=='nt':
            import msvcrt
            if not path.stat().st_size:stream.write(b'0');stream.flush()
            stream.seek(0)
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);locked=True
            except OSError:pass
        else:
            import fcntl
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);locked=True
            except BlockingIOError:pass
        yield locked
    finally:
        if locked:
            if os.name=='nt':
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)
        stream.close()


def checked_plan(raw):
    if not isinstance(raw,dict) or set(raw)-{'schema_version','claim','queries','limits','documentation_basis'}:
        raise ValueError('Unknown search-plan keys')
    if raw.get('schema_version')!='1.0' or not isinstance(raw.get('claim'),str) or not raw['claim'].strip():
        raise ValueError('Search plan needs schema_version 1.0 and the concrete requested correction in claim')
    queries=raw.get('queries')
    if not isinstance(queries,list) or not 1<=len(queries)<=8 or not all(isinstance(q,str) and q.strip() for q in queries):
        raise ValueError('Provide one to eight specific query fragments after reproduction')
    if len(set(queries))!=len(queries) or any(re.search(r'\b(?:repo|org|user):|\bis:(?:issue|pr|pull-request)\b',q,re.I) for q in queries):
        raise ValueError('Queries must be unique fragments; repository and issue/PR qualifiers are supplied by the queue')
    limits=dict(DEFAULT_LIMITS);supplied=raw.get('limits',{})
    if not isinstance(supplied,dict) or set(supplied)-set(limits):raise ValueError('Unknown retrieval limits')
    limits.update(supplied)
    for name,value in limits.items():
        minimum=0 if name=='cache_max_age_seconds' else 1
        if type(value) is not int or not minimum<=value<=MAX_LIMITS[name]:raise ValueError('Invalid bounded limit: '+name)
    basis=raw.get('documentation_basis',[])
    if not isinstance(basis,list):raise ValueError('documentation_basis must be frozen artifact references')
    return dict(schema_version='1.0',claim=raw['claim'],queries=queries,limits=limits,documentation_basis=basis)


def source_binding(pack_path,iid,plan,full=True):
    path=Path(pack_path).resolve();pack=read_json(path)
    if pack.get('schema_version')!='2.0':raise ValueError('Queue requires schema 2.0 evidence; preserve legacy reports unchanged')
    if full:
        report=validate(pack,path.parent,allow_pending=True)
        if not report['ok']:raise ValueError('Invalid source evidence: '+'; '.join(report['errors']))
    issue=next((i for i in pack['issues'] if i['id']==iid),None)
    if issue is None:raise ValueError('Unknown issue ID')
    if issue['finding']['status'] in {'excluded','merged','deferred'}:raise ValueError('Excluded/merged/deferred claims are not eligible')
    repro=issue['reproduction']
    if issue['kind']=='documentation':
        if issue['finding']['status']!='supported' or not plan['documentation_basis']:
            raise ValueError('Documentation searches require established support and frozen documentation_basis')
        for ref in plan['documentation_basis']:artifact(path.parent,ref)
    elif repro['status']!='reproduced':
        raise ValueError('Runtime searches start only after successful criterion-bound reproduction')
    if not full and repro.get('criterion_ref'):
        criterion=read_json(artifact(path.parent,repro['criterion_ref']))
        for ref in criterion.get('basis',[]):artifact(path.parent,ref)
        selected=set(repro.get('selected_run_ids',[]))
        for run in repro.get('runs',[]):
            if run['id'] not in selected:continue
            result_path=artifact(path.parent,run['result']);result=read_json(result_path)
            for key in ['source','stdout','stderr']:artifact(result_path.parent,result[key])
            assessment=read_json(artifact(path.parent,run['assessment']))
            for ref in assessment.get('evidence',[]):artifact(path.parent,ref)
    match=re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/?',pack['library'].get('repository',''))
    if not match:raise ValueError('This retriever supports canonical GitHub repositories only')
    if any(part in {'.','..'} for part in match[1].split('/')):raise ValueError('Invalid canonical repository identity')
    sources={r['path']:r for r in pack['sources']}
    material_refs=[]
    for name in sorted(issue['material_refs']):
        ref=sources[name];artifact(path.parent,ref);material_refs.append(ref)
    return dict(repository=match[1],materials_path=pack['materials_path'],issue_id=iid,
        title=issue['title'],kind=issue['kind'],claim=plan['claim'],
        material_items=[m for m in pack['material_items'] if m['id'] in issue['material_ids']],
        material_refs=material_refs,reproduction=repro,documentation_basis=plan['documentation_basis'])


class Queue:
    def __init__(self,state,clock=time.time):
        self.path=Path(state).resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
        self.objects=self.path.with_suffix('.artifacts');self.objects.mkdir(exist_ok=True)
        self.clock=clock
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE,source_path TEXT,binding TEXT,plan TEXT,
                    state TEXT,created REAL,deadline REAL,gaps TEXT,requests INTEGER DEFAULT 0,auth TEXT);
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY,job TEXT,url TEXT,meta TEXT,state TEXT,attempts INTEGER DEFAULT 0,
                    available REAL DEFAULT 0,result TEXT,error TEXT,UNIQUE(job,url));
                CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY,task INTEGER,record TEXT);
                CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY,entry TEXT);
                CREATE TABLE IF NOT EXISTS records (
                    job TEXT,number INTEGER,kind TEXT,url TEXT,title TEXT,selected INTEGER,UNIQUE(job,number,kind));
                CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY,pause REAL,auth_blocked TEXT);
                INSERT OR IGNORE INTO control VALUES(1,0,NULL);
                CREATE TABLE IF NOT EXISTS budgets (resource TEXT PRIMARY KEY,remaining INTEGER,reset REAL);
                CREATE TABLE IF NOT EXISTS pacing (resource TEXT PRIMARY KEY,next_at REAL);
            ''')
        if os.name!='nt':self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        db.execute('PRAGMA busy_timeout=15000')
        try:
            with db:yield db
        finally:db.close()

    def blob(self,content,suffix='json'):
        name='objects/'+hashlib.sha256(content).hexdigest()+'.'+suffix
        target=self.objects/name;target.parent.mkdir(exist_ok=True)
        if not target.exists():
            fd,tmp=tempfile.mkstemp(dir=target.parent,prefix='.object-')
            try:
                with os.fdopen(fd,'wb') as stream:stream.write(content)
                os.replace(tmp,target)
            finally:
                if Path(tmp).exists():Path(tmp).unlink()
        if sha(target)!=hashlib.sha256(content).hexdigest():raise EvidenceIntegrityError('Frozen retrieval object changed')
        return dict(path=name,sha256=sha(target))

    def gap(self,db,jid,reason):
        gaps=json.loads(db.execute('SELECT gaps FROM jobs WHERE id=?',(jid,)).fetchone()[0])
        if reason not in gaps:
            gaps.append(reason);db.execute('UPDATE jobs SET gaps=? WHERE id=?',(canonical(gaps),jid))

    def task(self,db,jid,url,meta):
        validate_url(url)
        binding=json.loads(db.execute('SELECT binding FROM jobs WHERE id=?',(jid,)).fetchone()[0])
        parts=urlsplit(url)
        if meta['kind']=='search':
            if parts.path!='/search/issues' or parse_qs(parts.query).get('q')!=[meta['query']]:
                raise ValueError('Pagination changed the declared search scope')
        elif not parts.path.startswith('/repos/'+binding['repository']+'/'):
            alias=meta.get('repository_alias');tail=meta.get('resource_tail')
            if not alias or parts.path!=alias+tail:
                raise ValueError('Returned resource is outside the declared repository')
        return db.execute('INSERT OR IGNORE INTO tasks(job,url,meta,state) VALUES(?,?,?,?)',(jid,url,canonical(meta),'queued')).rowcount==1

    def enqueue(self,pack_path,iid,raw_plan,refresh=False):
        plan=checked_plan(raw_plan)
        if refresh:plan['limits']['cache_max_age_seconds']=0
        binding=source_binding(pack_path,iid,plan)
        fingerprint=digest([binding,plan,uuid.uuid4().hex if refresh else None]);jid=fingerprint[:24];now=self.clock()
        with self.connect() as db:
            old=db.execute('SELECT id FROM jobs WHERE fingerprint=?',(fingerprint,)).fetchone()
            if old:return dict(job_id=old['id'],reused=True)
            db.execute('INSERT INTO jobs(id,fingerprint,source_path,binding,plan,state,created,deadline,gaps) VALUES(?,?,?,?,?,?,?,?,?)',
                (jid,fingerprint,str(Path(pack_path).resolve()),canonical(binding),canonical(plan),'queued',now,now+plan['limits']['max_seconds'],'[]'))
            for index,terms in enumerate(plan['queries']):
                for kind,qualifier in [('issue','is:issue'),('pull_request','is:pr')]:
                    query='repo:'+binding['repository']+' '+qualifier+' ('+terms+')'
                    url='https://api.github.com/search/issues?'+urlencode(dict(q=query,per_page=100,page=1,advanced_search='true'))
                    self.task(db,jid,url,dict(kind='search',query_id=str(index+1)+'-'+kind,query=query,page=1,resource='search'))
        return dict(job_id=jid,reused=False,state='queued',source_pack_sha256=sha(Path(pack_path)))

    def add_record(self,db,job,item,kind=None):
        plan=json.loads(job['plan']);binding=json.loads(job['binding']);repo=binding['repository']
        kind=kind or ('pull_request' if item.get('pull_request') else 'issue')
        number_=item.get('number');url=item.get('url','');html=item.get('html_url','')
        if type(number_) is not int or number_<=0 or not url.startswith('https://api.github.com/repos/'+repo+'/'):
            self.gap(db,job['id'],'A returned record was outside the declared repository or lacked its ID');return
        validate_url(url)
        if not html.startswith('https://github.com/'+repo+'/'):
            self.gap(db,job['id'],'A returned record lacked a verified repository HTML URL');return
        if db.execute('SELECT 1 FROM records WHERE job=? AND number=? AND kind=?',(job['id'],number_,kind)).fetchone():return
        count=db.execute('SELECT count(*) FROM records WHERE job=? AND selected=1',(job['id'],)).fetchone()[0]
        selected=int(count<plan['limits']['max_records'])
        db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',(job['id'],number_,kind,html,item.get('title',''),selected))
        if not selected:
            self.gap(db,job['id'],'Detail-record budget reached; additional search hits remain uninspected');return
        common=dict(number=number_,record_kind=kind,page=1,resource='core')
        primary=item.get('pull_request',{}).get('url') if kind=='pull_request' else url
        primary=primary or url
        self.task(db,job['id'],primary,dict(common,kind='pr' if kind=='pull_request' else 'issue'))
        comments=item.get('comments_url')
        if comments:self.task(db,job['id'],comments+('?' if '?' not in comments else '&')+'per_page=100',dict(common,kind='comments'))
        if kind=='pull_request':
            self.task(db,job['id'],'https://api.github.com/repos/'+repo+'/pulls/'+str(number_)+'/files?per_page=100',dict(common,kind='files'))
        else:
            self.task(db,job['id'],'https://api.github.com/repos/'+repo+'/issues/'+str(number_)+'/timeline?per_page=100',dict(common,kind='timeline'))

    def process_body(self,db,job,task,body,headers):
        meta=json.loads(task['meta']);plan=json.loads(job['plan']);jid=job['id']
        if meta['kind']=='search':
            if not isinstance(body,dict) or not isinstance(body.get('items'),list) or type(body.get('total_count')) is not int:
                raise ValueError('Malformed search response; no completeness conclusion is allowed')
            if body.get('incomplete_results') is not False:self.gap(db,jid,'GitHub reported missing or true incomplete_results for '+meta['query_id'])
            if body['total_count']>1000:self.gap(db,jid,'GitHub search result cap reached for '+meta['query_id'])
            for item in body['items']:
                if not isinstance(item,dict):raise ValueError('Malformed search hit')
                self.add_record(db,job,item)
        elif meta['kind'] in {'comments','files','timeline'}:
            if not isinstance(body,list):raise ValueError('Expected a list response for '+meta['kind'])
            if meta['kind']=='timeline':
                for event in body:
                    linked=event.get('source',{}).get('issue',{}) if isinstance(event,dict) else {}
                    if linked.get('pull_request'):self.add_record(db,job,linked,'pull_request')
        elif not isinstance(body,dict):raise ValueError('Expected an issue/PR object')
        links=re.findall(r'<([^>]+)>\s*;\s*rel="next"',headers.get('link',''))
        if links:
            if meta['page']>=plan['limits']['max_pages']:
                self.gap(db,jid,'Pagination budget reached for '+meta.get('query_id',meta['kind']+':'+str(meta.get('number'))))
            else:
                next_meta=dict(meta,page=meta['page']+1)
                if meta['kind']!='search':
                    tail=re.sub(r'^/(?:repos/[^/]+/[^/]+|repositories/[0-9]+)/','',urlsplit(task['url']).path)
                    alias=re.fullmatch(r'(/repositories/[0-9]+/)(.*)',urlsplit(links[0]).path)
                    if alias and alias[2]==tail:
                        if meta.get('repository_alias') and meta['repository_alias']!=alias[1]:
                            raise ValueError('Pagination changed its repository ID')
                        next_meta.update(repository_alias=alias[1],resource_tail=tail)
                if not self.task(db,jid,links[0],next_meta):self.gap(db,jid,'Repeated pagination link for '+meta.get('query_id',meta['kind']))
        elif meta['kind']=='search' and body['total_count']>meta['page']*100:
            self.gap(db,jid,'Search pagination link missing for '+meta['query_id'])

    def cache_entry(self,db,url):
        row=db.execute('SELECT entry FROM cache WHERE key=?',(digest([url,API_VERSION]),)).fetchone()
        if not row:return None
        entry=json.loads(row[0])
        try:
            envelope=read_json(artifact(self.objects,entry['evidence']))
            raw=artifact(self.objects,envelope['body']).read_bytes()
            return dict(entry,envelope=envelope,body_bytes=raw)
        except (OSError,ValueError,KeyError):
            db.execute('DELETE FROM cache WHERE key=?',(digest([url,API_VERSION]),));return None

    def record_response(self,url,response,now,credential):
        raw=response.body.replace(credential.token.encode(),b'[REDACTED]') if credential else response.body
        body=self.blob(raw,'body')
        headers={k.lower():safe_text(str(v),credential) for k,v in response.headers.items() if k.lower() in SAFE_HEADERS}
        envelope=dict(url=url,resolved_url=response.url,status=response.status,headers=headers,
                      api_version=API_VERSION,retrieved_at=utc(now),body=body,
                      redacted=response.redacted or raw!=response.body)
        return self.blob((canonical(envelope)+'\n').encode()),envelope,raw

    def finish_job(self,db,jid):
        job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if job['state'] in TERMINAL:return
        states=[r[0] for r in db.execute('SELECT state FROM tasks WHERE job=?',(jid,))]
        if any(s in {'queued','running'} for s in states):return
        good=states.count('succeeded')
        state='partial' if good and (json.loads(job['gaps']) or 'failed' in states) else 'succeeded' if good else 'failed'
        db.execute('UPDATE jobs SET state=? WHERE id=?',(state,jid))

    def fail_task(self,db,task,reason):
        db.execute('UPDATE tasks SET state="failed",error=? WHERE id=?',(reason,task['id']))
        self.gap(db,task['job'],reason);self.finish_job(db,task['job'])

    def step(self,credential=None,client=None):
        with worker_lock(self.path.with_suffix('.worker.lock')) as locked:
            if not locked:return dict(state='busy')
            return self._step(credential,client)

    def _step(self,credential,client):
        now=self.clock()
        with self.connect() as db:
            # Holding the OS lock proves a prior running task has no live network worker.
            db.execute('UPDATE tasks SET state="queued" WHERE state="running"')
            for job in db.execute('SELECT * FROM jobs WHERE state NOT IN ("succeeded","partial","failed","stale","cancelled")').fetchall():
                if now>=job['deadline']:
                    self.gap(db,job['id'],'Declared retrieval time budget exhausted')
                    db.execute('UPDATE tasks SET state="failed",error="Time budget exhausted" WHERE job=? AND state="queued"',(job['id'],))
                    self.finish_job(db,job['id'])
            if not db.execute('SELECT 1 FROM jobs WHERE state NOT IN ("succeeded","partial","failed","stale","cancelled") LIMIT 1').fetchone():
                return dict(state='idle')
            control=db.execute('SELECT * FROM control WHERE id=1').fetchone()
            if control['auth_blocked']:
                return dict(state='needs_auth',notice=control['auth_blocked'])
            if control['pause']>now:return dict(state='waiting_rate',resume_at=control['pause'])
            tasks=db.execute('SELECT t.*,j.created FROM tasks t JOIN jobs j ON t.job=j.id WHERE t.state="queued" AND j.state NOT IN ("succeeded","partial","failed","stale","cancelled") ORDER BY j.created,t.id').fetchall()
            task=None;next_times=[]
            for candidate in tasks:
                meta=json.loads(candidate['meta']);available=candidate['available']
                budget=db.execute('SELECT * FROM budgets WHERE resource=?',(meta['resource'],)).fetchone()
                if budget and budget['remaining']==0 and budget['reset']>now:available=max(available,budget['reset']+1)
                pace=db.execute('SELECT next_at FROM pacing WHERE resource=?',(meta['resource'],)).fetchone()
                if pace:available=max(available,pace[0])
                if available<=now:task=candidate;break
                db.execute('UPDATE jobs SET state="waiting_rate" WHERE id=?',(candidate['job'],))
                next_times.append(available)
            if task is None:
                return dict(state='waiting_rate',resume_at=min(next_times)) if next_times else dict(state='idle')
            job=db.execute('SELECT * FROM jobs WHERE id=?',(task['job'],)).fetchone();plan=json.loads(job['plan'])
            try:
                current=source_binding(job['source_path'],json.loads(job['binding'])['issue_id'],plan,full=False)
                if current!=json.loads(job['binding']):raise ValueError('Bound claim or reproduction revision changed')
            except (OSError,ValueError,KeyError,TypeError):
                self.gap(db,job['id'],'Source evidence changed/unavailable; re-enqueue the current verified revision')
                db.execute('UPDATE jobs SET state="stale" WHERE id=?',(job['id'],))
                return dict(state='stale',job_id=job['id'])
            cached=self.cache_entry(db,task['url'])
            fresh=cached and now-cached['checked_at']<plan['limits']['cache_max_age_seconds']
            if not fresh and not credential:
                db.execute('UPDATE control SET auth_blocked=? WHERE id=1',(AUTH_NOTICE,))
                db.execute('UPDATE jobs SET state="needs_auth" WHERE state NOT IN ("succeeded","partial","failed","stale","cancelled")')
                return dict(state='needs_auth',notice=AUTH_NOTICE)
            if not fresh and job['requests']>=plan['limits']['max_requests']:
                self.gap(db,job['id'],'Declared network request budget exhausted')
                db.execute('UPDATE tasks SET state="failed",error="Request budget exhausted" WHERE job=? AND state="queued"',(job['id'],))
                self.finish_job(db,job['id']);return dict(state='budget_exhausted',job_id=job['id'])
            if task['attempts']>=plan['limits']['max_attempts']:
                self.fail_task(db,task,'Retry budget exhausted for '+json.loads(task['meta'])['kind'])
                return dict(state='failed',job_id=job['id'])
            attempt=task['attempts']+1
            db.execute('UPDATE tasks SET state="running",attempts=? WHERE id=?',(attempt,task['id']))
            db.execute('UPDATE jobs SET state="running",requests=requests+?,auth=? WHERE id=?',
                (0 if fresh else 1,canonical(dict(source=credential.source,kind=credential.kind)) if credential else None,job['id']))
            db.commit()  # Crash recovery retains the attempt and request budget.
            if fresh:
                evidence=cached['evidence'];envelope=cached['envelope'];raw=cached['body_bytes'];decision=dict(kind='success')
                checked_at=cached['checked_at'];from_cache=True
            else:
                original_evidence=None
                try:
                    physical_requests=0
                    def before_request(url):
                        nonlocal physical_requests
                        if self.clock()>=job['deadline']:raise ValueError('Request time budget exhausted')
                        if physical_requests:
                            current=db.execute('SELECT requests FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]
                            if current>=plan['limits']['max_requests']:raise ValueError('Redirect request budget exhausted')
                            db.execute('UPDATE jobs SET requests=requests+1 WHERE id=?',(job['id'],));db.commit()
                        physical_requests+=1
                    actual_client=client or GitHubClient(credential,before_request=before_request)
                    if isinstance(actual_client,GitHubClient):actual_client.before_request=before_request
                    response=actual_client.get(task['url'],cached=cached)
                    decision=retry_decision(response,attempt,now)
                    evidence,envelope,raw=self.record_response(task['url'],response,self.clock(),credential)
                except (OSError,TimeoutError,HTTPException) as exc:
                    decision=dict(kind='transient',resume_at=self.clock()+min(60,2**attempt),reason='Network failure: '+type(exc).__name__)
                    evidence=None;envelope={};raw=b''
                except EvidenceIntegrityError:
                    decision=dict(kind='permanent',reason='Stored retrieval evidence integrity failed; preserve and inspect cached objects')
                    evidence=None;envelope={};raw=b''
                except ValueError:
                    decision=dict(kind='permanent',reason='Unsafe URL, redirect, or response byte limit; inspect request evidence')
                    evidence=None;envelope={};raw=b''
                checked_at=self.clock();from_cache=False
                if decision['kind']=='success' and response.status==304:
                    if not cached:
                        decision=dict(kind='permanent',reason='304 received without a verified cached payload')
                    else:
                        # Keep the 304 attempt and link the original verified body separately.
                        original_evidence=cached['evidence'];raw=cached['body_bytes']
                        envelope=dict(cached['envelope'],headers=dict(cached['envelope']['headers'],**envelope['headers']))
                        from_cache=True
                else:original_evidence=None
                headers=envelope.get('headers',{})
                resource=headers.get('x-ratelimit-resource',json.loads(task['meta'])['resource'])
                if 'x-ratelimit-remaining' in headers and 'x-ratelimit-reset' in headers:
                    remaining=max(0,int(number(headers['x-ratelimit-remaining'])))
                    reset=number(headers['x-ratelimit-reset'])
                    db.execute('INSERT OR REPLACE INTO budgets VALUES(?,?,?)',(resource,remaining,reset))
                    if remaining:
                        interval=max(.1,(reset-self.clock())/remaining)
                        db.execute('INSERT OR REPLACE INTO pacing VALUES(?,?)',(resource,self.clock()+interval))
            result=dict(evidence=evidence,checked_at=utc(checked_at),cached=bool(from_cache),network_request=not fresh,
                        decision=decision['kind'])
            if not fresh and original_evidence:result['cached_body_evidence']=original_evidence
            db.execute('INSERT INTO attempts(task,record) VALUES(?,?)',(task['id'],canonical(result)))
            db.execute('UPDATE tasks SET result=? WHERE id=?',(canonical(result),task['id']))
            if db.execute('SELECT state FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='cancelled':
                db.execute('UPDATE tasks SET state="cancelled" WHERE id=?',(task['id'],))
                return dict(state='cancelled',job_id=job['id'])
            if decision['kind']=='success':
                try:
                    current=source_binding(job['source_path'],json.loads(job['binding'])['issue_id'],plan,full=False)
                    if current!=json.loads(job['binding']):raise ValueError('Evidence changed during retrieval')
                except (OSError,ValueError,KeyError,TypeError):
                    self.gap(db,job['id'],'Bound source changed during retrieval; preserve this earlier revision separately')
                    db.execute('UPDATE jobs SET state="stale" WHERE id=?',(job['id'],))
                    db.execute('UPDATE tasks SET state="succeeded" WHERE id=?',(task['id'],))
                    return dict(state='stale',job_id=job['id'])
            if decision['kind']=='success':
                try:
                    body=json.loads(raw)
                    self.process_body(db,job,task,body,envelope.get('headers',{}))
                except (ValueError,KeyError,TypeError,AttributeError):
                    self.fail_task(db,task,'Malformed response or unsafe returned link for '+json.loads(task['meta'])['kind'])
                    return dict(state='failed',job_id=job['id'])
                cache_ref=result.get('cached_body_evidence') or evidence
                if not fresh:
                    entry=dict(evidence=cache_ref,checked_at=checked_at,
                        etag=envelope.get('headers',{}).get('etag'),last_modified=envelope.get('headers',{}).get('last-modified'))
                    db.execute('INSERT OR REPLACE INTO cache VALUES(?,?)',(digest([task['url'],API_VERSION]),canonical(entry)))
                db.execute('UPDATE tasks SET state="succeeded",error=NULL WHERE id=?',(task['id'],))
                self.finish_job(db,job['id']);return dict(state='retrieved',job_id=job['id'],cached=bool(from_cache))
            reason=decision['reason']
            if decision['kind']=='rate_limit' and decision.get('global_pause'):
                db.execute('UPDATE control SET pause=max(pause,?) WHERE id=1',(decision['resume_at'],))
            if decision['kind']=='needs_auth':
                db.execute('UPDATE control SET auth_blocked=? WHERE id=1',(reason,))
                db.execute('UPDATE tasks SET state="queued",error=? WHERE id=?',(reason,task['id']))
                db.execute('UPDATE jobs SET state="needs_auth" WHERE state NOT IN ("succeeded","partial","failed","stale","cancelled")')
                return dict(state='needs_auth',notice=reason)
            if decision['kind'] in {'rate_limit','transient'} and attempt<plan['limits']['max_attempts']:
                resume=max(self.clock()+1,decision['resume_at'])
                db.execute('UPDATE tasks SET state="queued",available=?,error=? WHERE id=?',(resume,reason,task['id']))
                db.execute('UPDATE jobs SET state="waiting_rate" WHERE id=?',(job['id'],))
                return dict(state='waiting_rate',job_id=job['id'],resume_at=resume)
            self.fail_task(db,task,reason+'; bounded retrieval stopped')
            return dict(state='failed',job_id=job['id'])

    def resume(self):
        with self.connect() as db:
            db.execute('UPDATE control SET auth_blocked=NULL WHERE id=1')
            db.execute('UPDATE jobs SET state="queued" WHERE state="needs_auth"')
        return dict(state='resumed',notice='Correct credentials before starting a new worker; rate-limit deadlines remain in force')

    def cancel(self,jid,reason):
        if not isinstance(reason,str) or not reason.strip():raise ValueError('Cancellation needs a disposition reason')
        with self.connect() as db:
            job=db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()
            if not job:raise ValueError('Unknown job ID')
            if job['state'] in TERMINAL:return dict(job_id=jid,state=job['state'],changed=False)
            self.gap(db,jid,'Retrieval cancelled: '+reason)
            db.execute('UPDATE jobs SET state="cancelled" WHERE id=?',(jid,))
            db.execute('UPDATE tasks SET state="cancelled" WHERE job=? AND state="queued"',(jid,))
        return dict(job_id=jid,state='cancelled',changed=True)

    def status(self,jid=None):
        with self.connect() as db:
            rows=db.execute('SELECT * FROM jobs'+(' WHERE id=?' if jid else '')+' ORDER BY created',(jid,) if jid else ()).fetchall()
            if jid and not rows:raise ValueError('Unknown job ID')
            items=[]
            for row in rows:
                counts={r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM tasks WHERE job=? GROUP BY state',(row['id'],))}
                items.append(dict(job_id=row['id'],issue_id=json.loads(row['binding'])['issue_id'],state=row['state'],
                    task_counts=counts,network_requests=row['requests'],deadline=utc(row['deadline']),gaps=json.loads(row['gaps']),
                    auth=json.loads(row['auth']) if row['auth'] else None))
            control=db.execute('SELECT * FROM control WHERE id=1').fetchone()
            return dict(jobs=items,notice=control['auth_blocked'],global_resume_at=control['pause'])

    def export(self,jid,output):
        output=Path(output).resolve()
        if output.exists():raise ValueError('Export directory exists; preserve history and choose another revision')
        output.parent.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix=output.name+'.tmp-',dir=output.parent));refs={}
        def copy_ref(ref):
            if not ref:return
            source=artifact(self.objects,ref);target=stage/ref['path'];target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():target.write_bytes(source.read_bytes())
            refs[ref['path']]=ref
            value=read_json(source)
            if isinstance(value,dict) and 'body' in value:
                body=value['body'];src=artifact(self.objects,body);dest=stage/body['path'];dest.parent.mkdir(parents=True,exist_ok=True)
                if not dest.exists():dest.write_bytes(src.read_bytes())
                refs[body['path']]=body
        try:
            with self.connect() as db:
                db.execute('BEGIN')
                job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
                if not job:raise ValueError('Unknown job ID')
                tasks=[]
                for task in db.execute('SELECT * FROM tasks WHERE job=? ORDER BY id',(jid,)):
                    attempts=[json.loads(r[0]) for r in db.execute('SELECT record FROM attempts WHERE task=? ORDER BY id',(task['id'],))]
                    for attempt in attempts:
                        copy_ref(attempt.get('evidence'));copy_ref(attempt.get('cached_body_evidence'))
                    tasks.append(dict(url=task['url'],meta=json.loads(task['meta']),state=task['state'],
                        attempts=attempts,error=task['error'],resume_at=task['available']))
                records=[dict(r) for r in db.execute('SELECT number,kind,url,title,selected FROM records WHERE job=? ORDER BY rowid',(jid,))]
                queries=[]
                for query_id in dict.fromkeys(t['meta'].get('query_id') for t in tasks if t['meta']['kind']=='search'):
                    group=[t for t in tasks if t['meta'].get('query_id')==query_id]
                    times=[a['checked_at'] for t in group for a in t['attempts'] if a['decision']=='success']
                    query_evidence=dict(query_id=query_id,query=group[0]['meta']['query'],requests=group,
                                        gaps=json.loads(job['gaps']),semantic_assessment_required=True)
                    query_path=stage/'queries'/(query_id+'.json');query_path.parent.mkdir(exist_ok=True);write_json(query_path,query_evidence)
                    query_ref=dict(path=query_path.relative_to(stage).as_posix(),sha256=sha(query_path));refs[query_ref['path']]=query_ref
                    queries.append(dict(query=group[0]['meta']['query'],checked_at=min(times) if times else None,
                        complete=job['state']=='succeeded' and all(t['state']=='succeeded' for t in group),
                        gaps=json.loads(job['gaps']),evidence=query_ref))
                snapshot=dict(schema_version='1.0',job_id=jid,state=job['state'],exported_at=utc(self.clock()),
                    binding=json.loads(job['binding']),plan=json.loads(job['plan']),gaps=json.loads(job['gaps']),
                    tasks=tasks,queries=queries,records=records,sources=list(refs.values()),semantic_assessment_required=True,
                    notice='Retrieval evidence only; no automatic duplicate, novelty, fix-coverage or release-inclusion judgment.')
                write_json(stage/'retrieval.json',snapshot)
            if output.exists():raise ValueError('Export output appeared during assembly')
            stage.rename(output)
        except BaseException:
            import shutil
            shutil.rmtree(stage,ignore_errors=True);raise
        return dict(evidence=str(output/'retrieval.json'),state=snapshot['state'],records=len(records),assessment_required=True)


def work(queue,credential,once=False,idle_exit=30,client=None):
    stopping=False;idle_since=None
    def stop(signum,frame):
        nonlocal stopping
        stopping=True
    if not once:
        for signum in [signal.SIGTERM,signal.SIGINT]:signal.signal(signum,stop)
    while not stopping:
        result=queue.step(credential,client)
        if once or result['state']=='needs_auth':return result
        if result['state']=='idle':
            if idle_since is None:idle_since=time.monotonic()
            if time.monotonic()-idle_since>=idle_exit:return result
        else:idle_since=None
        if result['state'] in {'idle','busy','waiting_rate'}:time.sleep(.25)
    return dict(state='stopped',notice='Saved tasks resume with the next worker')


def run_worker(queue,once,idle_exit,allow_cli):
    if once:return work(queue,resolve_credential(allow_cli=allow_cli),once=True)
    info_path=queue.path.with_suffix('.worker.json')
    with worker_lock(queue.path.with_suffix('.daemon.lock')) as locked:
        if not locked:
            try:info=read_json(info_path)
            except (OSError,ValueError):info=dict(queue=str(queue.path))
            return dict(info,state='already_running')
        credential=resolve_credential(allow_cli=allow_cli)
        pending=any(j['state'] not in TERMINAL for j in queue.status()['jobs'])
        info=dict(state='started',queue=str(queue.path),pid=os.getpid(),
            auth=dict(source=credential.source,kind=credential.kind) if credential else None,
            notice=AUTH_NOTICE if not credential and pending else None)
        for key,name in [('stdout_log','ISSUE_REVIEW_WORKER_STDOUT'),('stderr_log','ISSUE_REVIEW_WORKER_STDERR')]:
            if os.environ.get(name):info[key]=os.environ[name]
        fd,tmp=tempfile.mkstemp(prefix='.worker-info-',dir=queue.path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(canonical(info)+'\n')
            os.replace(tmp,info_path)
        finally:
            if Path(tmp).exists():Path(tmp).unlink()
        print(canonical(info),flush=True)
        try:return work(queue,credential,idle_exit=idle_exit)
        finally:info_path.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,default=default_state())
    sub=parser.add_subparsers(dest='command',required=True)
    child=sub.add_parser('enqueue');child.add_argument('--pack',type=Path,required=True);child.add_argument('--issue-id',required=True)
    child.add_argument('--plan',type=Path,required=True);child.add_argument('--refresh',action='store_true')
    child=sub.add_parser('work');child.add_argument('--once',action='store_true');child.add_argument('--background',action='store_true')
    child.add_argument('--idle-exit',type=float,default=30);child.add_argument('--no-cli-auth',action='store_true')
    child=sub.add_parser('status');child.add_argument('--job')
    sub.add_parser('resume')
    child=sub.add_parser('export');child.add_argument('--job',required=True);child.add_argument('--output-dir',type=Path,required=True)
    child=sub.add_parser('cancel');child.add_argument('--job',required=True);child.add_argument('--reason',required=True)
    args=parser.parse_args()
    try:
        queue=Queue(args.state)
        if args.command=='enqueue':result=queue.enqueue(args.pack,args.issue_id,read_json(args.plan),args.refresh)
        elif args.command=='status':result=queue.status(args.job)
        elif args.command=='resume':result=queue.resume()
        elif args.command=='export':result=queue.export(args.job,args.output_dir)
        elif args.command=='cancel':result=queue.cancel(args.job,args.reason)
        else:
            if args.idle_exit<0 or args.idle_exit>3600:raise ValueError('idle-exit must be between 0 and 3600 seconds')
            if args.background:
                if args.once:raise ValueError('--once and --background cannot be combined')
                logs=queue.path.parent/'upstream-logs';logs.mkdir(exist_ok=True);token=uuid.uuid4().hex[:12]
                stdout,stderr=logs/(token+'.stdout.txt'),logs/(token+'.stderr.txt')
                command=[sys.executable,str(Path(__file__).resolve()),'--state',str(queue.path),'work','--idle-exit',str(args.idle_exit)]
                if args.no_cli_auth:command.append('--no-cli-auth')
                detached=dict(start_new_session=True) if os.name!='nt' else dict(creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP)
                child_env=dict(os.environ,ISSUE_REVIEW_WORKER_STDOUT=str(stdout),ISSUE_REVIEW_WORKER_STDERR=str(stderr))
                with stdout.open('xb') as out,stderr.open('xb') as err:
                    process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err,env=child_env,**detached)
                until=time.monotonic()+10
                while time.monotonic()<until:
                    lines=stdout.read_text().splitlines()
                    if lines:
                        result=json.loads(lines[0]);result.setdefault('pid',process.pid)
                        result.setdefault('stdout_log',str(stdout));result.setdefault('stderr_log',str(stderr));break
                    if process.poll() is not None:raise ValueError('Worker startup failed; inspect its safe local log')
                    time.sleep(.05)
                else:process.terminate();process.wait(timeout=3);raise ValueError('Worker startup timed out')
            else:
                result=run_worker(queue,args.once,args.idle_exit,not args.no_cli_auth)
        print(canonical(result),flush=True)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        message=str(exc) if isinstance(exc,ValueError) else type(exc).__name__+': check local paths and queue configuration'
        for name in ['ISSUE_REVIEW_GITHUB_PAT','GH_TOKEN','GITHUB_TOKEN']:
            if os.environ.get(name):message=message.replace(os.environ[name],'[REDACTED]')
        parser.exit(1,'Queue operation failed: '+message+'\n')


if __name__=='__main__':main()
