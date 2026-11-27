"""Existing reviewed copy fields survive later same-issue evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from fixtures import save
from fill_fields import compile_pack, prepare, prepare_review
from review_pack import read_json, validate, write_json
from serve_review import ReviewApplication
from test_service import service_pack


class RetainedCopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pack = service_pack(self.root)
        self.original = self.compile(self.pack, 'original')
        self.state = self.root/'progress.sqlite3'
        app = ReviewApplication(self.original, self.state)
        self.before = app.public()['items'][1]
        for field in self.before['fields']:
            app.copied(self.before['id'], field['key'])
        self.issue = self.pack['issues'][1]
        evidence = save(self.root, 'upstream/match.json', '{"synthetic": true}')
        self.pack['sources'].append(evidence)
        self.issue['upstream'].update(relation='same_issue', coverage='partial', checked_at='2026-11-26',
            scope='Exact supplied issue inspected.', reason='Same trigger and request.',
            gaps=['Other issues not inspected.'], stop_reason='Positive same-issue evidence suffices.',
            matches=[dict(url='https://example.invalid/examplelib/issues/1', kind='issue', relation='same_issue',
                title='Existing synthetic issue', reason='Same requested change.',
                trigger_comparison='Same synthetic input.', claim_comparison='Same correction.', evidence=[evidence])])
        ref = dict(path=self.original.relative_to(self.root).as_posix())
        import hashlib
        ref['sha256'] = hashlib.sha256(self.original.read_bytes()).hexdigest()
        self.pack['sources'].append(ref)
        self.issue['publication']['retained_copy'] = dict(original_pack=ref, requested_by_operator=True,
            reason='Operator requests preserving existing copy fields, without a submission-tracking state.')

    def tearDown(self):
        self.tmp.cleanup()

    def compile(self, pack, name):
        source = self.root/(name+'.json')
        write_json(source, pack)
        filling = self.root/(name+'-filling')
        prepare(source, filling)
        review_path = filling/'review.json'
        prepare_review(source, filling/'contract.json', filling/'answers.json', review_path)
        review = read_json(review_path)
        for item in review['reviews'].values():
            item.update(status='reviewed', notes='Reviewed synthetic unchanged copy fields; no real bug or submission claim.')
            for check in item['checks']:
                check.update(status='not_applicable' if check['id']=='minimal_reproducer' else 'pass',
                    reason='Synthetic original field text and destinations are preserved.')
        write_json(review_path, review)
        output = compile_pack(source, filling/'contract.json', filling/'answers.json', review_path, self.root/name)
        return Path(output['pack'])

    def errors(self):
        return '\n'.join(validate(self.pack, self.root)['errors'])

    def test_recompiled_copy_retains_exact_keys_and_saved_progress(self):
        current = self.compile(self.pack, 'retained')
        after = ReviewApplication(current, self.state).public()['items'][1]
        self.assertEqual([f['key'] for f in after['fields']], [f['key'] for f in self.before['fields']])
        self.assertTrue(all(f['copied'] for f in after['fields']))
        self.assertEqual(after['official_url'], self.before['official_url'])

    def test_fresh_duplicate_remains_rejected(self):
        self.issue['publication'].pop('retained_copy')
        self.assertIn('another new-issue', self.errors())

    def test_implicit_or_unbound_retention_is_rejected(self):
        retained = self.issue['publication']['retained_copy']
        retained['requested_by_operator'] = False
        self.assertIn('explicit operator request', self.errors())
        retained['requested_by_operator'] = True
        retained['original_pack']['sha256'] = '0'*64
        self.assertIn('hash', self.errors().lower())

    def test_changed_fields_or_destinations_are_rejected(self):
        for change, message in [
            (lambda: self.issue.update(title='Changed title'), 'original title'),
            (lambda: self.issue['responses']['doc'].update(value='Changed body'), 'original field values'),
            (lambda: self.pack.update(materials_path='/different/materials'), 'material/project scope'),
            (lambda: self.pack['templates'][1]['fields'][0].update(label='Different destination'), 'form destination'),
        ]:
            original = copy.deepcopy(self.pack)
            change()
            self.assertIn(message, self.errors())
            self.pack = original
            self.issue = self.pack['issues'][1]

    def test_retention_does_not_override_exclusion_or_content_review(self):
        self.issue['finding']['status'] = 'excluded'
        self.pack['dispositions'][1]['decision'] = 'excluded'
        self.assertIn('excluded/merged', self.errors())
        self.issue['finding']['status'] = 'supported'
        self.pack['dispositions'][1]['decision'] = 'retained'
        self.issue['publication']['quality_review']['status'] = 'pending'
        source = self.root/'unreviewed.json'
        write_json(source, self.pack)
        app = ReviewApplication(source, self.state, allow_unbound_draft=True)
        self.assertNotIn(self.issue['id'], [i['id'] for i in app.public()['items']])


if __name__ == '__main__':
    unittest.main()
