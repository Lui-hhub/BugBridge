#!/usr/bin/env python3
"""Initialize task data in this checkout; no network, installs or reproduction."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import uuid

from layout_guard import SKILL_ROOT, ensure_layout
from review_pack import safe_id, write_json
from serve_review import default_state_path
from upstream_queue import default_state as queue_state


def paths():
    return dict(workspace_root=str(SKILL_ROOT),runs_root=str(SKILL_ROOT/'runs'),
                review_state=str(default_state_path()),queue_state=str(queue_state().resolve()),
                template_cache=str(SKILL_ROOT/'assets/template-cache'))


def start(materials,library,run_id=None):
    ensure_layout()
    source=Path(materials).expanduser().resolve()
    if not source.exists():raise ValueError('Materials path does not exist')
    if not isinstance(library,str) or not library.strip():raise ValueError('A target library/project name is required')
    now=datetime.now(timezone.utc)
    if run_id is None:
        label=re.sub(r'[^a-z0-9_-]+','-',library.casefold()).strip('-')[:40] or 'project'
        run_id=now.strftime('%Y%m%dT%H%M%SZ')+'-'+label+'-'+uuid.uuid4().hex[:8]
    if not safe_id(run_id):raise ValueError('run-id must be a safe unique identifier')
    result=dict(schema_version='1.0',run_id=run_id,created_at=now.isoformat(),
                materials_path=str(source),library_name=library.strip(),**paths())
    root=SKILL_ROOT/'runs'/run_id
    root.parent.mkdir(parents=True,exist_ok=True);root.mkdir(exist_ok=False)
    result['run_root']=str(root)
    try:write_json(root/'request.json',result)
    except BaseException:
        if not any(root.iterdir()):root.rmdir()
        raise
    return dict(result,request=str(root/'request.json'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True);sub.add_parser('paths')
    child=sub.add_parser('start');child.add_argument('--materials',type=Path,required=True)
    child.add_argument('--library',required=True);child.add_argument('--run-id')
    args=parser.parse_args()
    try:
        result=paths() if args.command=='paths' else start(args.materials,args.library,args.run_id)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (OSError,ValueError) as exc:parser.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
