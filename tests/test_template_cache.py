"""Offline cache behavior and the actual bundled PT/TF examples."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fixtures import SCRIPTS
from template_cache import DEFAULT_CACHE, TemplateCache
from review_pack import artifact


class TemplateCacheTests(unittest.TestCase):
    def test_bundled_templates_are_complete_and_hash_verified_offline(self):
        cache=TemplateCache()
        with patch('urllib.request.urlopen',side_effect=AssertionError('Cache hits must not fetch')):
            pt=cache.library('pt');tf=cache.library('tf')
            self.assertEqual(len(pt['templates']),10);self.assertEqual(len(tf['templates']),5)
            for project in [pt,tf]:
                artifact(cache.root,project['chooser'])
                for entry in project['templates']:
                    artifact(cache.root,entry['raw'])
                    if entry['status']=='usable':
                        model=json.loads(artifact(cache.root,entry['normalized']).read_text())
                        self.assertTrue(entry['purposes_assigned'])
                        self.assertTrue(all(f['purpose']!='unassigned' for f in model['fields']))
            bad=next(t for t in tf['templates'] if t['id']=='tflite-other')
            self.assertEqual(bad['status'],'needs_review')
            self.assertNotIn('normalized',bad)

    def test_export_uses_cached_raw_snapshot_and_correct_control_counts(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen',side_effect=AssertionError('No fetch')):
            root=Path(tmp);cache=TemplateCache()
            bug=cache.export('torch','bug-report',root)
            docs=cache.export('pt','documentation',root)
            tf=cache.export('tensorflow','tensorflow_issue_template',root)
            self.assertEqual(len([f for f in bug['fields'] if f['type']!='markdown']),2)
            self.assertEqual(len([f for f in docs['fields'] if f['type']!='markdown']),2)
            self.assertEqual(len([f for f in tf['fields'] if f['type']!='markdown']),15)
            self.assertEqual(sum(f['type']=='dropdown' for f in tf['fields']),4)
            self.assertEqual(next(f for f in tf['fields'] if f['id']=='code-to-reproduce')['render'],'shell')
            self.assertTrue(artifact(root,bug['source']).is_file())
            with self.assertRaises(ValueError):cache.export('pt','bug-report',root)
            with self.assertRaises(ValueError):cache.export('tf','tflite-other',root)

    def test_new_project_is_cached_once_and_unknown_semantics_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'downloaded';raw.mkdir()
            (raw/'bug.json').write_text(json.dumps(dict(name='New project form',body=[dict(type='textarea',id='problem',attributes=dict(label='Explain this observation'))])))
            cache=TemplateCache(root/'cache')
            result=cache.import_library('newlib','https://example.invalid/newlib','revision-1','2026-11-23',raw,aliases=['new'])
            self.assertFalse(result['templates'][0]['purposes_assigned'])
            self.assertEqual(cache.library('new')['id'],'newlib')
            with self.assertRaises(ValueError):cache.import_library('newlib','https://example.invalid/newlib','revision-2','2026-11-23',raw)
            with self.assertRaises(KeyError):cache.library('absent')

    def test_corrupt_cached_source_is_not_exported(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shutil.copytree(DEFAULT_CACHE,root/'cache')
            cache=TemplateCache(root/'cache');entry=cache.library('pt')['templates'][0]
            (cache.root/entry['raw']['path']).write_text('changed')
            with self.assertRaises(ValueError):cache.export('pt',entry['id'],root/'pack')

    def test_adding_a_new_template_preserves_existing_files_and_their_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'raw';raw.mkdir()
            shape=lambda label:json.dumps(dict(name=label,body=[dict(type='textarea',id='body',attributes=dict(label=label))]))
            (raw/'bug.json').write_text(shape('Original bug'))
            cache=TemplateCache(root/'cache')
            cache.import_library('newlib','https://example.invalid/newlib','revision-1','2026-11-23',raw,aliases=['new'])
            old=cache.library('new')['templates'][0]['raw'].copy()
            (raw/'bug.json').write_text('This redundant incoming file must not replace the cached original.')
            (raw/'docs.json').write_text(shape('New docs'))
            cache.import_library('newlib','https://example.invalid/newlib','revision-2','2026-11-23',raw,add_missing=True)
            self.assertEqual(len(cache.library('new')['templates']),2)
            original=cache.export('new','bug',root/'pack')
            self.assertEqual(original['source']['revision'],'revision-1')
            self.assertEqual(cache.library('new')['templates'][0]['raw'],old)
            self.assertTrue(artifact(cache.root,old).is_file())


if __name__=='__main__':unittest.main()
