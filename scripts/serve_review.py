#!/usr/bin/env python3
"""Local copy-only review service with SQLite progress. Never executes issue code."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlparse
import uuid

from pack_v2 import PLACEHOLDER
from layout_guard import ensure_layout, SKILL_ROOT
from review_pack import read_json, validate
from editor_route import verified_route


DEFAULT_TTL_SECONDS = 3 * 60 * 60


def service_ttl_seconds(value):
    seconds = int(value)
    if not 0 < seconds <= threading.TIMEOUT_MAX:
        raise argparse.ArgumentTypeError('Service lifetime must be positive and within the supported timer range.')
    return seconds


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def default_state_path():
    return SKILL_ROOT/'.local/state/review.sqlite3'


class ProgressStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS copied (
                  state_key TEXT PRIMARY KEY, scope TEXT NOT NULL, issue_id TEXT NOT NULL,
                  field_id TEXT NOT NULL, payload_hash TEXT NOT NULL, copied_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS selection (
                  scope TEXT PRIMARY KEY, issue_id TEXT NOT NULL);
            ''')

    def connect(self):
        return sqlite3.connect(str(self.path), timeout=5)

    def copied_keys(self, scope):
        with self.connect() as db:
            return {r[0] for r in db.execute('SELECT state_key FROM copied WHERE scope=?', (scope,))}

    def record_copy(self, scope, issue_id, field):
        with self.connect() as db:
            db.execute('''INSERT INTO copied(state_key,scope,issue_id,field_id,payload_hash,copied_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(state_key) DO UPDATE SET
                copied_at=excluded.copied_at''',
                (field['key'], scope, issue_id, field['id'], digest(field['text']), datetime.now(timezone.utc).isoformat()))

    def select(self, scope, issue_id):
        with self.connect() as db:
            db.execute('INSERT INTO selection VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET issue_id=excluded.issue_id', (scope, issue_id))

    def selected(self, scope):
        with self.connect() as db:
            row = db.execute('SELECT issue_id FROM selection WHERE scope=?', (scope,)).fetchone()
            return row[0] if row else None


def official_url(pack, card):
    if card['action']['type'] == 'existing_issue_comment':
        return card['action'].get('target_url', '')
    repository = pack['library']['repository'].rstrip('/')
    source = card.get('template_source', {})
    if source.get('status') == 'verified_editor':
        return source['url']
    filename = Path(urlparse(source.get('url', '')).path).name
    return repository + '/issues/new' + ('?template=' + quote(filename) if filename else '/choose')


def project(pack, report):
    """Only copy payloads go into the main page; investigation data stays separate."""
    scope = digest([pack['library']['repository'].rstrip('/').casefold(), pack['materials_path']])
    originals = {i['id']: i for i in pack['issues']}
    items, held = [], []
    for card in report['cards']:
        if card['action']['type'] not in {'new_issue', 'existing_issue_comment'} or card['finding']['status'] in {'excluded', 'merged'}:
            continue
        issue = originals[card['id']]
        fields, reasons, notes = [], [], []
        personal_labels = set()
        if pack['schema_version'] == '2.0':
            quality = issue.get('publication', {}).get('quality_review', {})
            if quality.get('status') != 'reviewed' or any(x.get('status') == 'needs_work' for x in quality.get('checks', [])):
                reasons.append('投稿正文尚待整理与语义检查')
        if card['action']['type'] == 'new_issue':
            fields.append(dict(id='title', label='Title', type='input', text=card['title'], render=None))
        for field in card['fields']:
            kind, text = field['type'], field['text']
            if kind in {'checkboxes', 'upload'} or field.get('purpose')=='attestation':
                if kind=='checkboxes' or field.get('purpose')=='attestation':
                    personal_labels.add(field['label'])
                if field.get('purpose')=='attestation' or (kind == 'checkboxes' and (field.get('required') or any(x.get('required') for x in field.get('options', [])))):
                    notes.append('请在官方页面完成本人确认。')
                elif kind == 'upload' and field.get('value'):
                    notes.append('请在官方页面上传对应附件。')
                continue
            if kind not in {'input', 'textarea', 'dropdown'}:
                reasons.append('官方控件尚待核对：' + field['label'])
                continue
            if not text:
                if field.get('required'):
                    reasons.append('尚缺内容：' + field['label'])
                continue
            if PLACEHOLDER.search(text):
                reasons.append('正文仍有内部占位内容')
            label = '补充评论' if card['action']['type']=='existing_issue_comment' else field['label']
            fields.append(dict(id=field['id'], label=label, type=kind, text=text,
                               render=field.get('render'), purpose=field.get('purpose'), source_id=field.get('source_id'), multiple=field.get('multiple', False)))
        route = dict(format=card.get('template_format'), source=card.get('template_source', {}))
        if card['action']['type'] == 'new_issue' and not verified_route(route, pack['library']['repository']):
            reasons.append('官方模板或普通编辑器入口尚待核对')
        if pack.get('workflow_status') == 'awaiting_environment' and card['reproduction']['status'] == 'awaiting_environment':
            reasons.append('等待操作者提供匹配环境')
        for problem in card['blockers']:
            if not problem.startswith('Operator confirmation required:') and problem not in {'Required field missing: '+label for label in personal_labels}:
                reasons.append(problem)
        if not fields:
            reasons.append('尚无投稿内容')
        if reasons:
            held.append(dict(id=card['id'], title=card['title'], reasons=list(dict.fromkeys(reasons))))
            continue
        url = official_url(pack, card)
        if issue.get('operator_note'):
            notes.append(issue['operator_note'])
        else:
            notes.extend(t['label'] for t in card.get('submission_todos', []) if t.get('state') == 'pending')
        destinations = {}
        for field in fields:
            identity = field['id'] if field['id'] in {'title','comment'} else field.get('source_id') or field['label']
            location = (identity, field['type'])
            occurrence = destinations.get(location, 0)
            destinations[location] = occurrence + 1
            field['key'] = digest([scope, card['id'], card['action']['type'], url, identity, occurrence,
                                   field['label'], field['type'], field['text']])
        items.append(dict(id=card['id'], title=card['title'],
                          kind='已有问题补充' if card['action']['type'] == 'existing_issue_comment' else '文档问题' if card['kind']=='documentation' else '问题报告',
                          official_url=url, official_label='打开已有 issue' if card['action']['type']=='existing_issue_comment' else '打开官方填写页',
                          fields=fields, note=' '.join(dict.fromkeys(notes)), record_url='/records/'+quote(card['id'])))
    return dict(scope=scope, library=pack['library']['name'], items=items, held=held)


class ReviewApplication:
    def __init__(self, pack_path, state_path, allow_pending=False, allow_unbound_draft=False, layout_root=SKILL_ROOT):
        self.layout_root = Path(layout_root)
        ensure_layout(self.layout_root)
        self.pack_path = Path(pack_path).resolve()
        self.store = ProgressStore(state_path)
        self.allow_pending = allow_pending
        self.allow_unbound_draft = allow_unbound_draft
        self.lock = threading.RLock()
        self.stamp = None
        self.last_error = None
        self.reload(initial=True)

    def reload(self, initial=False):
        with self.lock:
            try:
                stamp = hashlib.sha256(self.pack_path.read_bytes()).hexdigest()
                if stamp == self.stamp:
                    return
                pack = read_json(self.pack_path)
                if pack.get('schema_version')=='2.0' and not pack.get('form_binding') and not self.allow_unbound_draft:
                    raise ValueError('New reports must be compiled through fill_fields.py. Use --allow-unbound-draft only for explicitly identified historical data.')
                report = validate(pack, self.pack_path.parent, self.allow_pending)
                if not report['ok']:
                    raise ValueError('\n'.join(report['errors']))
                model = project(pack, report)
                self.pack, self.report, self.model = pack, report, model
                self.stamp, self.last_error = stamp, None
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.last_error = str(exc)
                if initial:
                    raise

    def public(self):
        self.reload()
        with self.lock:
            model = json.loads(json.dumps(self.model))
            keys = self.store.copied_keys(model['scope'])
            for item in model['items']:
                for field in item['fields']:
                    field['copied'] = field['key'] in keys
            ids = {i['id'] for i in model['items']}
            chosen = self.store.selected(model['scope'])
            return dict(library=model['library'], scope=model['scope'], items=model['items'],
                        selected_id=chosen if chosen in ids else None,
                        notice='新稿尚未通过检查，当前保留上一版内容。' if self.last_error else '',
                        records_url='/records')

    def copied(self, issue_id, key):
        self.reload()
        with self.lock:
            for item in self.model['items']:
                if item['id'] == issue_id:
                    for field in item['fields']:
                        if field['key'] == key:
                            self.store.record_copy(self.model['scope'], issue_id, field)
                            return
        raise ValueError('内容已更新，请刷新后复制当前版本。')

    def select(self, issue_id):
        self.reload()
        with self.lock:
            if not any(i['id'] == issue_id for i in self.model['items']):
                raise ValueError('问题不存在或尚待整理。')
            self.store.select(self.model['scope'], issue_id)

    def records(self, issue_id=None):
        self.reload()
        with self.lock:
            if issue_id is not None:
                card = next((c for c in self.report['cards'] if c['id']==issue_id), None)
                if not card:
                    raise KeyError(issue_id)
                return dict(card=card, validation_scope=self.report['validation_scope'],
                            source_pack=str(self.pack_path), limitations=self.pack.get('review_limitations', []))
            return dict(records=[dict(id=c['id'], title=c['title'], reason=c['finding']['reason']) for c in self.report['cards']],
                        held=self.model['held'], coverage=self.report['coverage'], warnings=self.report['warnings'], last_error=self.last_error)


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app, ttl_seconds=DEFAULT_TTL_SECONDS):
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 0 < ttl_seconds <= threading.TIMEOUT_MAX:
            raise ValueError('Service lifetime must be a positive integer within the supported timer range.')
        self.app = app
        super().__init__(address, Handler)
        self.ttl_seconds = ttl_seconds
        self.started_at = datetime.now(timezone.utc)
        self.expires_at = self.started_at + timedelta(seconds=ttl_seconds)
        self._deadline = time.monotonic() + ttl_seconds

    def serve_forever(self, poll_interval=.5):
        # shutdown() must run outside the serving thread. The monotonic deadline
        # is fixed when the port is opened, independent of requests or reloads.
        expiry = threading.Timer(max(0, self._deadline - time.monotonic()), self.shutdown)
        expiry.daemon = True
        expiry.start()
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            expiry.cancel()
            self.server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        data = json.dumps(body, ensure_ascii=False).encode() if content_type.startswith('application/json') else body.encode() if isinstance(body,str) else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def local_host(self):
        value = self.headers.get('Host', '')
        return value in {'localhost:'+str(self.server.server_port), '127.0.0.1:'+str(self.server.server_port)}

    def do_GET(self):
        if not self.local_host():
            self.send(403, dict(error='Use the localhost service address.'))
            return
        path = urlparse(self.path).path
        static = {'/':'review-app.html', '/review-app.js':'review-app.js', '/review-app.css':'review-app.css', '/markdown.js':'markdown.js'}
        if path in static:
            try:
                ensure_layout(self.server.app.layout_root)
            except ValueError:
                self.send(503,dict(error='页面文件未通过一致性检查，请恢复已确认的版本。'))
                return
            file = self.server.app.layout_root/'assets'/static[path]
            kind = 'text/html' if file.suffix=='.html' else 'text/css' if file.suffix=='.css' else 'text/javascript'
            self.send(200,file.read_bytes(),kind+'; charset=utf-8')
        elif path == '/favicon.ico':
            self.send(204,b'','image/x-icon')
        elif path == '/api/review':
            self.send(200,self.server.app.public())
        elif path == '/health':
            self.send(200,dict(ok=True))
        elif path == '/records' or path.startswith('/records/'):
            from urllib.parse import unquote
            iid = unquote(path.removeprefix('/records/')) if path.startswith('/records/') else None
            try:
                data = self.server.app.records(iid)
            except KeyError:
                self.send(404,dict(error='Record not found'))
                return
            if iid:
                title = html.escape(data['card']['title'])
                content = '<pre>'+html.escape(json.dumps(data,ensure_ascii=False,indent=2))+'</pre>'
            else:
                title = '调查记录'
                content = ''.join('<article><a href="/records/'+quote(c['id'])+'">'+html.escape(c['title'])+'</a><p>'+html.escape(c['reason'])+'</p></article>' for c in data['records'])
                if data['held']:
                    content += '<h2>尚待整理</h2><pre>'+html.escape(json.dumps(data['held'],ensure_ascii=False,indent=2))+'</pre>'
                content += '<h2>校验范围</h2><pre>'+html.escape(json.dumps({k:v for k,v in data.items() if k not in {'records','held'}},ensure_ascii=False,indent=2))+'</pre>'
            page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/review-app.css"><title>'+title+'</title><main class="records"><a href="/">返回复制页面</a><h1>'+title+'</h1>'+content+'</main></html>'
            self.send(200,page,'text/html; charset=utf-8')
        else:
            self.send(404,dict(error='Not found'))

    def do_POST(self):
        origin = self.headers.get('Origin')
        if not self.local_host() or (origin and origin not in {'http://'+self.headers['Host']}):
            self.send(403,dict(error='Use the same local review page.'))
            return
        if self.headers.get('Content-Type','').split(';')[0].strip() != 'application/json':
            self.send(415,dict(error='JSON required'))
            return
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 16384:
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(length))
            if self.path == '/api/copied':
                self.server.app.copied(data['issue_id'], data['key'])
            elif self.path == '/api/selection':
                self.server.app.select(data['issue_id'])
            else:
                self.send(404,dict(error='Not found'))
                return
            self.send(200,dict(ok=True))
        except (ValueError, KeyError, TypeError) as exc:
            self.send(409,dict(error=str(exc)))
        except sqlite3.Error:
            self.send(503,dict(error='进度暂未保存，请稍后重试。'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pack',type=Path)
    parser.add_argument('--state',type=Path,default=default_state_path())
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--ttl-seconds',type=service_ttl_seconds,default=DEFAULT_TTL_SECONDS,
                        help='Close the service after this many seconds from port creation (default: 10800, 3 hours)')
    parser.add_argument('--allow-pending',action='store_true')
    parser.add_argument('--allow-unbound-draft',action='store_true',help='Historical schema-2.0 compatibility only; normal jobs use compiled form bindings')
    parser.add_argument('--background',action='store_true',help='Detach the local service and print its actual URL/PID/log paths')
    args = parser.parse_args()
    if args.background:
        logs=args.state.resolve().parent/'logs'
        logs.mkdir(parents=True,exist_ok=True)
        token=uuid.uuid4().hex[:12]
        stdout_path,stderr_path=logs/(token+'.stdout.txt'),logs/(token+'.stderr.txt')
        command=[sys.executable,str(Path(__file__).resolve()),str(args.pack.resolve()),'--state',str(args.state.resolve()),'--port',str(args.port),
                 '--ttl-seconds',str(args.ttl_seconds)]
        if args.allow_pending:
            command.append('--allow-pending')
        if args.allow_unbound_draft:
            command.append('--allow-unbound-draft')
        detached = dict(start_new_session=True) if os.name != 'nt' else dict(creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        with stdout_path.open('xb') as stdout,stderr_path.open('xb') as stderr:
            child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,**detached)
        until=time.monotonic()+10
        while time.monotonic()<until:
            lines=stdout_path.read_text().splitlines()
            if lines:
                info=json.loads(lines[0])
                info.update(pid=child.pid,stdout_log=str(stdout_path),stderr_log=str(stderr_path))
                print(json.dumps(info,ensure_ascii=False),flush=True)
                return
            if child.poll() is not None:
                parser.exit(1,stderr_path.read_text()+'\n')
            time.sleep(.05)
        child.terminate()
        child.wait(timeout=3)
        parser.exit(1,'Local service did not finish startup; see '+str(stderr_path)+'\n')
    try:
        app = ReviewApplication(args.pack,args.state,args.allow_pending,args.allow_unbound_draft)
        server = ReviewServer(('127.0.0.1',args.port),app,ttl_seconds=args.ttl_seconds)
    except (OSError, ValueError) as exc:
        parser.exit(1,str(exc)+'\n')
    print(json.dumps(dict(url='http://localhost:'+str(server.server_port),state=str(args.state.resolve()),ready=len(app.model['items']),
                          ttl_seconds=server.ttl_seconds,started_at=server.started_at.isoformat(),expires_at=server.expires_at.isoformat()),ensure_ascii=False),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
