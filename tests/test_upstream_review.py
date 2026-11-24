"""Metadata-first review of synthetic exports; no real tracker or framework calls."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from fixtures import SCRIPTS, add_run, make_v2_pack
from github_http import Credential, Response
from review_pack import artifact, read_json, write_json
from test_upstream_queue import Clock, FakeClient, hit
from upstream_queue import Queue
from upstream_review import (create_index, list_items, list_records, read_item,
                             select_record)


BODY = 'SELECTED_BODY_ONLY\n' + '示例 αβ🙂\n' * 900
UNRELATED = 'UNRELATED_BODY_MUST_NOT_APPEAR_IN_METADATA'
COMMENT_A = 'COMMENT_A_ONLY'
COMMENT_B = 'COMMENT_B_ONLY'
PATCH = '@@ local synthetic change @@\n-' + 'old\n' * 2000 + '+new\n'
NESTED = 'LINKED_BODY_MUST_NOT_APPEAR_IN_TIMELINE'


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pack = make_v2_pack(self.root)
        self.pack['library']['repository'] = 'https://github.com/example/engine'
        add_run(self.root, self.pack['issues'][0])
        self.pack_path = self.root / 'pack.json'
        write_json(self.pack_path, self.pack)
        self.plan = dict(schema_version='1.0', claim='Synthetic shape correction.',
                         queries=['specific synthetic symptom'], limits=dict(max_pages=1, max_records=5))
        self.clock = Clock()
        self.queue = Queue(self.root / 'state/upstream.sqlite3', self.clock)
        self.auth = Credential('synthetic_test_credential', 'fixture', 'configured_token')
        self.job = self.queue.enqueue(self.pack_path, 'cli-case', self.plan)['job_id']
        self.client = FakeClient(self.response)
        for _ in range(40):
            if self.queue.step(self.auth, self.client)['state'] == 'idle':
                break
        else:
            self.fail('Synthetic retrieval failed to terminate')
        self.export = self.root / 'export'
        self.info = self.queue.export(self.job, self.export)
        self.snapshot_path = Path(self.info['evidence'])
        self.index_path = Path(self.info['review_index'])

    def response(self, url, cached):
        parts = urlsplit(url)
        if parts.path == '/search/issues':
            if 'is:pr' in parse_qs(parts.query)['q'][0]:
                items = [dict(hit(2, True), body='PR_BODY_ONLY')]
            else:
                items = [dict(hit(), body=BODY), dict(hit(3), body=UNRELATED)]
            body = dict(total_count=len(items), incomplete_results=False, items=items)
        elif parts.path.endswith('/comments'):
            body = [dict(id=11, body=COMMENT_A, user=dict(login='fixture-maintainer'), author_association='MEMBER'),
                    dict(id=12, body=COMMENT_B)]
        elif parts.path.endswith('/files'):
            body = [dict(filename='engine.py', status='modified', patch=PATCH),
                    dict(filename='generated.py', status='modified')]
        elif parts.path.endswith('/timeline'):
            body = [dict(id=7, event='cross-referenced',
                         source=dict(issue=dict(hit(2, True), body=NESTED)))]
        else:
            n = int(parts.path.rsplit('/', 1)[-1])
            body = dict(number=n, state='closed', title='Synthetic detail',
                        body=BODY if n == 1 else UNRELATED if n == 3 else 'PR_BODY_ONLY')
            if '/pulls/' in parts.path:
                body.update(merged=True, merged_at='2026-01-01T00:00:00Z', merge_commit_sha='abc123')
        return Response(200, {}, json.dumps(body).encode(), url)

    def select(self, record='issue:1', output='selection.json'):
        return Path(select_record(self.index_path, record,
                                  'The title matches the API and shape correction under investigation.',
                                  self.export / output)['selection'])

    def item(self, selection, section, label=None):
        items = list_items(selection, section)['items']
        return next(i for i in items if label is None or i['label'] == label)

    def test_export_preserves_full_evidence_but_index_and_lists_omit_all_bodies(self):
        snapshot = read_json(self.snapshot_path)
        contents = [artifact(self.export, ref).read_text() for ref in snapshot['sources']]
        self.assertTrue(any(UNRELATED in text for text in contents))
        self.assertTrue(any(any(isinstance(value, dict) and value.get('patch') == PATCH for value in payload)
                            for payload in map(json.loads, contents) if isinstance(payload, list)))
        raw = self.index_path.read_text() + json.dumps(list_records(self.index_path))
        for text in (BODY[:20], UNRELATED, COMMENT_A, COMMENT_B, PATCH[:20], NESTED):
            self.assertNotIn(text, raw)
        self.assertEqual(list_records(self.index_path)['indexed_records'], 3)
        self.assertTrue(all('sources' not in r for r in list_records(self.index_path)['items']))

    def test_title_filter_and_metadata_pagination_do_not_claim_exhaustive_review(self):
        first = list_records(self.index_path, limit=1)
        self.assertEqual((first['total'], first['next_offset']), (3, 1))
        self.assertNotEqual(first['items'][0]['id'], list_records(self.index_path, offset=1, limit=1)['items'][0]['id'])
        # A body-only term does not silently turn into a body excerpt.
        filtered = list_records(self.index_path, terms=[UNRELATED])
        self.assertEqual(filtered['total'], 0)
        self.assertEqual(filtered['indexed_records'], 3)
        self.assertEqual(list_records(self.index_path, terms=['report 3'])['items'][0]['id'], 'issue:3')
        self.assertIn('not proof', first['notice'])
        for offset, limit in [(-1, 20), (0, 101), (0, 0)]:
            with self.assertRaises(ValueError):
                list_records(self.index_path, offset=offset, limit=limit)

    def test_selection_requires_reason_known_id_and_a_fresh_file_beside_snapshot(self):
        for rid, reason in [('issue:1', '  '), ('issue:999', 'Related API'), ('issue:1', 'x' * 1001)]:
            with self.assertRaises(ValueError):
                select_record(self.index_path, rid, reason, self.export / 'bad.json')
        with self.assertRaises(ValueError):
            select_record(self.index_path, 'issue:1', 'Related API', self.root / 'outside.json')
        selection = self.select()
        before = selection.read_bytes()
        with self.assertRaises(FileExistsError):
            self.select()
        self.assertEqual(selection.read_bytes(), before)

    def test_reader_refuses_items_from_unselected_records(self):
        selection = self.select()
        foreign = next(r for r in read_json(self.index_path)['records'] if r['id'] == 'issue:3')['items'][0]
        with self.assertRaisesRegex(ValueError, 'selected record'):
            read_item(selection, foreign['id'])
        with self.assertRaisesRegex(ValueError, 'selection file'):
            read_item(self.index_path, foreign['id'])

    def test_body_ranges_are_exact_bounded_unicode_and_can_recover_the_entire_item(self):
        selection = self.select()
        item = self.item(selection, 'body', 'Detail body')
        first = read_item(selection, item['id'])
        self.assertEqual(first['text'], BODY[:2000])
        self.assertEqual(first['next_offset'], 2000)
        self.assertFalse(first['complete_item'])
        self.assertEqual(first['source'], item['source'])
        self.assertEqual(first['pointer'], ['body'])
        pieces = []
        offset = 0
        while True:
            part = read_item(selection, item['id'], offset=offset)
            pieces.append(part['text'])
            if part['next_offset'] is None:
                break
            offset = part['next_offset']
        self.assertEqual(''.join(pieces), BODY)
        for offset, limit in [(-1, 20), (len(BODY) + 1, 20), (0, 4001), (0, 0)]:
            with self.assertRaises(ValueError):
                read_item(selection, item['id'], offset, limit)

    def test_comments_and_patches_are_read_one_item_at_a_time(self):
        selection = self.select()
        comments = list_items(selection, 'comments', limit=1)
        self.assertEqual(comments['total'], 2)
        self.assertEqual(comments['next_offset'], 1)
        self.assertEqual(comments['items'][0]['context']['author'], 'fixture-maintainer')
        self.assertEqual(comments['items'][0]['context']['author_association'], 'MEMBER')
        excerpt = read_item(selection, comments['items'][0]['id'])
        self.assertEqual(excerpt['text'], COMMENT_A)
        self.assertTrue(excerpt['complete_item'])
        self.assertNotIn(COMMENT_B, json.dumps(comments) + excerpt['text'])
        pr = self.select('pull_request:2', 'selection-pr.json')
        files = list_items(pr, 'files')['items']
        generated = next(i for i in files if i['label'] == 'generated.py')
        self.assertFalse(json.loads(read_item(pr, generated['id'])['text'])['patch_available'])
        patch = self.item(pr, 'patch')
        self.assertEqual(read_item(pr, patch['id'], offset=100, limit=300)['text'], PATCH[100:400])
        self.assertNotIn('@@', json.dumps(files))

    def test_metadata_retains_pr_state_without_expanding_bodies_or_linked_records(self):
        selection = self.select()
        timeline = self.item(selection, 'timeline')
        text = read_item(selection, timeline['id'])['text']
        self.assertEqual(json.loads(text)['linked_record']['number'], 2)
        self.assertNotIn(NESTED, text)
        pr = self.select('pull_request:2', 'selection-pr.json')
        detail = self.item(pr, 'metadata', 'Detail metadata')
        value = json.loads(read_item(pr, detail['id'])['text'])
        self.assertTrue(value['merged'])
        self.assertEqual(value['merge_commit_sha'], 'abc123')
        self.assertNotIn('body', value)

    def test_index_snapshot_and_raw_evidence_tampering_are_detected(self):
        selection = self.select()
        item = self.item(selection, 'body')
        paths = [self.index_path, self.snapshot_path, artifact(self.export, item['source'])]
        for path in paths:
            original = path.read_bytes()
            try:
                path.write_bytes(original + b' ')
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    read_item(selection, item['id'])
            finally:
                path.write_bytes(original)
        value = read_json(self.index_path)
        value['records'][0]['title'] = 'Manually changed index'
        write_json(self.index_path, value)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            list_records(self.index_path)

    def test_selection_cannot_be_reused_with_a_different_export_revision(self):
        selection = self.select()
        self.clock.value += 1
        other = self.root / 'other-export'
        self.queue.export(self.job, other)
        moved = other / selection.name
        moved.write_bytes(selection.read_bytes())
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            list_items(moved, 'body')

    def test_existing_exports_can_be_indexed_offline_without_changing_raw_files(self):
        snapshot = read_json(self.snapshot_path)
        before = {ref['path']: artifact(self.export, ref).read_bytes() for ref in snapshot['sources']}
        snapshot_bytes = self.snapshot_path.read_bytes()
        self.index_path.unlink()  # Simulate a historical export without the derived sidecar.
        index = self.export / 'review-index-v1.json'
        calls = len(self.client.calls)
        create_index(self.snapshot_path, index)
        self.index_path = index
        selection = self.select()
        read_item(selection, self.item(selection, 'body')['id'])
        self.assertEqual(len(self.client.calls), calls)
        self.assertEqual(self.snapshot_path.read_bytes(), snapshot_bytes)
        self.assertTrue(all((self.export / name).read_bytes() == value for name, value in before.items()))

    def test_cancelled_partial_export_keeps_search_bodies_without_hiding_gaps(self):
        plan = copy.deepcopy(self.plan)
        plan['limits']['max_records'] = 1
        job = self.queue.enqueue(self.pack_path, 'cli-case', plan, refresh=True)['job_id']
        self.queue.step(self.auth, self.client)
        self.queue.cancel(job, 'Synthetic review has enough evidence.')
        info = self.queue.export(job, self.root / 'partial-export')
        index = Path(info['review_index'])
        value = list_records(index)
        self.assertEqual(value['retrieval_state'], 'cancelled')
        self.assertEqual(value['gaps'], read_json(Path(info['evidence']))['gaps'])
        self.assertTrue(value['gaps'])
        selected = select_record(index, 'issue:1', 'Related API', index.parent / 'selection.json')['selection']
        self.assertTrue(list_items(selected, 'body')['items'])
        # Detail scheduling is independent of a reasoned reading selection.
        other = next(r for r in value['items'] if r['id'] == 'issue:3')
        self.assertFalse(other['detail_scheduled'])
        choice = select_record(index, 'issue:3', 'Inspect this specific nearby API claim.',
                               index.parent / 'selection-other.json')['selection']
        item = self.item(choice, 'body')
        self.assertEqual(read_item(choice, item['id'])['text'], UNRELATED)

    def test_cli_review_roundtrip_is_offline_and_requires_explicit_selection(self):
        def command(args, ok=True):
            result = subprocess.run([sys.executable, str(SCRIPTS / 'upstream_review.py')] + args,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode == 0, ok, result.stderr)
            return json.loads(result.stdout) if ok else result.stderr
        listing = command(['list', '--index', str(self.index_path), '--limit', '1'])
        self.assertEqual(listing['total'], 3)
        self.assertNotIn(BODY[:20], json.dumps(listing))
        command(['read', '--item', 'anything'], ok=False)
        selection = self.export / 'cli-selection.json'
        command(['select', '--index', str(self.index_path), '--record', 'issue:1',
                 '--reason', 'Matches the current API shape correction.', '--output', str(selection)])
        items = command(['items', '--selection', str(selection), '--section', 'body'])
        text = command(['read', '--selection', str(selection), '--item', items['items'][0]['id'], '--limit', '50'])
        self.assertEqual(text['text'], BODY[:50])


if __name__ == '__main__':
    unittest.main()
