"""Ordinary GitHub editors: frozen discovery, exact body model and copy delivery."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from fixtures import SCRIPTS, save
from test_service import service_pack
from fill_fields import compile_pack, prepare, prepare_review
from normalize_template import normalize
from review_pack import read_json, validate, write_json
from serve_review import ReviewApplication, ReviewServer


class EditorRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pack = service_pack(self.root)
        self.pack['library']['repository'] = 'https://github.com/synthetic/example'
        for key in ['issues', 'material_items', 'dispositions']:
            self.pack[key] = self.pack[key][:1]
        self.raw = save(self.root, 'editor/body.txt', '')
        self.proof = save(self.root, 'editor/discovery.json', json.dumps(dict(
            synthetic=True, reason='Local fixture only; not an actual tracker discovery.')))
        self.pack['sources'].extend([self.raw, self.proof])
        model = normalize('', 'generic', 'ordinary')
        model['source'] = dict(self.raw, status='verified_editor',
            url=self.pack['library']['repository'] + '/issues/new', retrieved_at='2026-11-25',
            limitation='Synthetic fixture for an ordinary editor with no applicable template.',
            editor_discovery=dict(kind='github_blank_issue', reason='Synthetic local discovery fixture.', evidence=[self.proof]))
        self.pack['templates'] = [model]
        self.pack['issues'][0].update(template_id='ordinary', responses=dict(body=dict(value='A complete synthetic issue body.')))

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *options):
        return subprocess.run([sys.executable, str(SCRIPTS/'normalize_template.py'),
            '--input', str(self.root/self.raw['path']), '--format', 'generic', '--id', 'ordinary',
            '--artifact-root', str(self.root), '--output', str(self.root/'normalized.json'),
            '--url', self.pack['library']['repository']+'/issues/new', '--retrieved-at', '2026-11-25', *options],
            capture_output=True, text=True)

    def test_cli_records_verified_editor_without_claiming_an_official_template(self):
        result = self.cli('--verified-editor', '--editor-evidence', str(self.root/self.proof['path']),
                          '--editor-reason', 'Synthetic directory and inherited-default discovery.')
        self.assertEqual(result.returncode, 0, result.stderr)
        model = read_json(self.root/'normalized.json')
        self.assertEqual(model['source']['status'], 'verified_editor')
        self.assertEqual(model['source']['editor_discovery']['evidence'], [self.proof])
        self.assertEqual([(f['id'], f['label']) for f in model['fields']], [('body', 'Issue body')])

    def test_cli_rejects_unbound_or_incompatible_editor_declarations(self):
        valid = ['--verified-editor', '--editor-evidence', str(self.root/self.proof['path']), '--editor-reason', 'Synthetic discovery.']
        cases = [['--official'], ['--format', 'markdown'], ['--url', 'https://example.invalid/issues/new'],
                 ['--url', self.pack['library']['repository']+'/issues/new?template=custom'],
                 ['--editor-reason', ''], ['--editor-evidence', str(self.root.parent/'missing-editor-proof.json')]]
        # The bare flag, evidence without the flag, and all incompatible variants must fail.
        for options in [['--verified-editor'], ['--editor-reason', 'Unbound reason.']] + [valid+c for c in cases]:
            with self.subTest(options=options):
                result = self.cli(*options)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root/'normalized.json').exists())

    def test_valid_editor_requires_matching_repository_and_intact_declared_discovery(self):
        report = validate(self.pack, self.root)
        self.assertTrue(report['ok'], report['errors'])
        self.assertFalse(report['blockers']['cli-case'], report['blockers'])
        mutations = [
            lambda p: p['library'].pop('repository'),
            lambda p: p['library'].update(repository='https://github.com/synthetic/other'),
            lambda p: p['sources'].remove(self.proof),
            lambda p: p['templates'][0]['source']['editor_discovery'].update(evidence=[]),
            lambda p: p['templates'][0]['source']['editor_discovery'].update(reason=''),
            lambda p: p['templates'][0]['source'].update(url='https://github.com/synthetic/example/issues/new#body'),
            lambda p: p['templates'][0]['source'].update(url='https://github.com/../example/issues/new'),
            lambda p: p['templates'][0].update(format='markdown'),
            lambda p: p['templates'][0]['fields'][0].update(label='Invented official field'),
            lambda p: p['templates'][0]['source']['editor_discovery']['evidence'][0].update(sha256='0'*64),
        ]
        for index, change in enumerate(mutations):
            with self.subTest(index=index):
                pack = copy.deepcopy(self.pack)
                change(pack)
                self.assertFalse(validate(pack, self.root)['ok'])
        (self.root/self.proof['path']).write_text('Changed discovery evidence.')
        self.assertFalse(validate(self.pack, self.root)['ok'])

    def test_unverified_and_falsely_official_generic_models_remain_held(self):
        for status in ['unverified', 'official']:
            with self.subTest(status=status):
                pack = copy.deepcopy(self.pack)
                pack['templates'][0]['source']['status'] = status
                report = validate(pack, self.root)
                self.assertTrue(report['ok'], report['errors'])
                self.assertTrue(report['blockers']['cli-case'])
                path = self.root/(status+'.json')
                write_json(path, pack)
                app = ReviewApplication(path, self.root/(status+'.sqlite3'), allow_unbound_draft=True)
                self.assertEqual(app.public()['items'], [])

    def compile(self):
        base = self.root/'pack.json'
        write_json(base, self.pack)
        filling = self.root/'filling'
        prepare(base, filling)
        review_path = filling/'review.json'
        prepare_review(base, filling/'contract.json', filling/'answers.json', review_path)
        review = read_json(review_path)
        for entry in review['reviews'].values():
            entry.update(status='reviewed', notes='Reviewed synthetic editor payload and discovery; no real defect claim.')
            for check in entry['checks']:
                check.update(status='not_applicable' if check['id']=='minimal_reproducer' else 'pass', reason='Synthetic content fixture.')
        write_json(review_path, review)
        result = compile_pack(base, filling/'contract.json', filling/'answers.json', review_path, self.root/'compiled')
        return Path(result['pack'])

    def test_compilation_preserves_discovery_and_delivers_two_exact_copy_fields(self):
        compiled = self.compile()
        shutil.rmtree(self.root/'editor')
        report = validate(read_json(compiled), compiled.parent)
        self.assertTrue(report['ok'], report['errors'])
        app = ReviewApplication(compiled, self.root/'progress.sqlite3')
        item = app.public()['items'][0]
        self.assertEqual(item['official_url'], 'https://github.com/synthetic/example/issues/new')
        self.assertEqual([f['id'] for f in item['fields']], ['title', 'body'])
        self.assertEqual([f['label'] for f in item['fields']], ['Title', 'Issue body'])
        self.assertEqual(item['fields'][1]['text'], 'A complete synthetic issue body.')
        proof = read_json(compiled)['templates'][0]['source']['editor_discovery']['evidence'][0]
        (compiled.parent/proof['path']).write_text('Changed compiled discovery.')
        self.assertFalse(validate(read_json(compiled), compiled.parent)['ok'])

    def test_editor_copy_and_selection_persist_through_real_http(self):
        compiled = self.compile()
        state = self.root/'http.sqlite3'
        app = ReviewApplication(compiled, state)
        server = ReviewServer(('127.0.0.1', 0), app)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
        thread.start()
        url = 'http://127.0.0.1:'+str(server.server_port)
        try:
            self.assertEqual(server.ttl_seconds, 10800)
            with urlopen(url+'/api/review', timeout=3) as response:
                item = json.load(response)['items'][0]
            for endpoint, payload in [('/api/copied', dict(issue_id=item['id'], key=item['fields'][1]['key'])),
                                      ('/api/selection', dict(issue_id=item['id']))]:
                request = Request(url+endpoint, json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
            restored = ReviewApplication(compiled, state).public()
            self.assertEqual(restored['selected_id'], item['id'])
            self.assertTrue(restored['items'][0]['fields'][1]['copied'])
            self.assertFalse(restored['items'][0]['fields'][0]['copied'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
