"""A relocated checkout works without a global skill or the original cwd."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from fixtures import SCRIPTS


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.checkout=self.root/'移动后的 checkout'
        shutil.copytree(SCRIPTS.parent,self.checkout,ignore=shutil.ignore_patterns('.git','.local','runs','__pycache__','*.pyc'))
        self.cwd=self.root/'unrelated-cwd';self.cwd.mkdir()
        self.env=dict(os.environ,XDG_STATE_HOME=str(self.root/'shared-state'))
        self.env.pop('PYTHONPATH',None)

    def tearDown(self):self.tmp.cleanup()

    def command(self,script,*args,ok=True):
        result=subprocess.run([sys.executable,str(self.checkout/'scripts'/script),*map(str,args)],
            cwd=self.cwd,env=self.env,capture_output=True,text=True,timeout=15)
        if ok:self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
        self.assertNotEqual(result.returncode,0);return result

    def test_paths_are_checkout_local_and_github_coordination_is_shared(self):
        result=self.command('workspace.py','paths')
        self.assertEqual(result['workspace_root'],str(self.checkout))
        self.assertEqual(result['review_state'],str(self.checkout/'.local/state/review.sqlite3'))
        self.assertEqual(result['queue_state'],str(self.root/'shared-state/issue-materials-to-html/upstream.sqlite3'))
        self.assertFalse((self.checkout/'runs').exists());self.assertFalse((self.checkout/'.local').exists())

    def test_fresh_tasks_preserve_materials_and_use_one_review_state(self):
        material=self.root/'external material.json';material.write_text('{"claim":"synthetic setup only"}')
        first=self.command('workspace.py','start','--materials',material,'--library','PyTorch')
        second=self.command('workspace.py','start','--materials',material,'--library','PyTorch')
        self.assertNotEqual(first['run_id'],second['run_id'])
        self.assertEqual(first['review_state'],second['review_state'])
        self.assertEqual(first['materials_path'],str(material))
        self.assertEqual(json.loads(Path(first['request']).read_text())['library_name'],'PyTorch')
        self.assertTrue(Path(first['run_root']).is_relative_to(self.checkout/'runs'))
        self.assertEqual(material.read_text(),'{"claim":"synthetic setup only"}')

    def test_overwrite_escape_and_missing_materials_are_rejected(self):
        material=self.root/'materials';material.mkdir()
        first=self.command('workspace.py','start','--materials',material,'--library','tf','--run-id','fixed')
        before=Path(first['request']).read_bytes()
        self.command('workspace.py','start','--materials',material,'--library','tf','--run-id','fixed',ok=False)
        self.assertEqual(Path(first['request']).read_bytes(),before)
        self.command('workspace.py','start','--materials',material,'--library','tf','--run-id','../escape',ok=False)
        self.command('workspace.py','start','--materials',self.root/'absent','--library','tf',ok=False)
        self.assertFalse((self.checkout/'escape').exists())

    def test_layout_and_cached_templates_resolve_from_the_relocated_checkout(self):
        layout=self.command('layout_guard.py','check');self.assertTrue(layout['ok'])
        output=self.root/'task-output'
        self.command('template_cache.py','export','--library','tf','--template','tensorflow_issue_template','--pack-root',output)
        model=json.loads((output/'templates/tensorflow_issue_template.normalized.json').read_text())
        self.assertTrue((output/model['source']['path']).is_file())
        self.assertEqual(sum(f['type']=='dropdown' for f in model['fields']),4)


if __name__=='__main__':unittest.main()
