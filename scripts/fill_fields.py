#!/usr/bin/env python3
"""Prepare immutable forms, accept value-only answers, compile a standalone pack."""
import argparse
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from field_contract import (add_sources, answer_schema, answer_skeleton, apply_answers,
    apply_review, make_contract, read_fields_json, review_skeleton, validate_answers, validate_review)
from layout_guard import ensure_layout, freeze_layout, MANIFEST, SKILL_ROOT
from review_pack import artifact, read_json, sha, validate, write_json


def require_report(report):
    if not report['ok']:
        raise ValueError('\n'.join(report['errors']))


def ref(root,path):
    return dict(path=path.relative_to(root).as_posix(),sha256=sha(path))


def frozen_inputs(pack_path):
    ensure_layout()
    pack_path=Path(pack_path).resolve()
    pack=read_json(pack_path)
    if pack.get('schema_version')!='2.0':
        raise ValueError('Prepare new reports with schema 2.0; do not invent missing legacy investigation history')
    # Publication text may still be empty; full code-to-field binding is checked at compilation.
    draft=copy.deepcopy(pack)
    draft.pop('form_binding',None)
    for issue in draft['issues']:
        if issue.get('action',{}).get('type') in {'new_issue','existing_issue_comment'}:
            publication=issue.setdefault('publication',{})
            publication.pop('reproducer',None)
            publication['quality_review']=dict(status='pending',checks=[])
    require_report(validate(draft,pack_path.parent,allow_pending=True))
    layout=read_json(SKILL_ROOT/MANIFEST)
    return pack,make_contract(pack,sha(pack_path),pack_path.name,layout)


def prepare(pack_path,output):
    pack_path,output=Path(pack_path).resolve(),Path(output).resolve()
    if output.exists():
        raise ValueError('Filling directory already exists; use a fresh revision')
    pack,contract=frozen_inputs(pack_path)
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix=output.name+'.tmp-',dir=output.parent))
    try:
        write_json(stage/'contract.json',contract)
        signature=sha(stage/'contract.json')
        write_json(stage/'answers.schema.json',answer_schema(contract,signature))
        write_json(stage/'answers.json',answer_skeleton(pack,contract,signature))
        freeze_layout(stage/'layout.lock.json')
        if output.exists():
            raise ValueError('Output appeared during preparation')
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    return dict(directory=str(output),contract_sha256=signature,cases=len(contract['cases']),
                fill_only=str(output/'answers.json'))


def load_answers(pack_path,contract_path,answers_path):
    pack,expected=frozen_inputs(pack_path)
    contract_path=Path(contract_path)
    contract=read_fields_json(contract_path)
    if contract!=expected:
        raise ValueError('Readonly contract or source pack changed; prepare a fresh filling revision')
    ensure_layout(baseline=contract_path.parent/'layout.lock.json')
    if read_json(contract_path.parent/'layout.lock.json')!=contract['layout']:
        raise ValueError('Task layout lock differs from the generated contract')
    contract_hash=sha(contract_path)
    schema_path=contract_path.parent/'answers.schema.json'
    if read_fields_json(schema_path)!=answer_schema(contract,contract_hash):
        raise ValueError('Generated answer schema was modified')
    answers=read_fields_json(answers_path)
    validate_answers(answers,contract,contract_hash)
    return pack,contract,answers


def prepare_review(pack_path,contract_path,answers_path,output):
    _,contract,_=load_answers(pack_path,contract_path,answers_path)
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:
        json.dump(review_skeleton(contract,sha(contract_path),sha(answers_path)),stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    return dict(fill_review=str(output),answers_sha256=sha(answers_path))


def compile_pack(pack_path,contract_path,answers_path,review_path,output,allow_pending=False):
    pack_path=Path(pack_path).resolve();contract_path=Path(contract_path).resolve()
    answers_path=Path(answers_path).resolve();review_path=Path(review_path).resolve();output=Path(output).resolve()
    if output.exists():
        raise ValueError('Compiled output already exists; preserve it and use another directory')
    if output==pack_path.parent or pack_path.is_relative_to(output):
        raise ValueError('Compiled output must not contain or replace the input pack')
    pack,contract,answers=load_answers(pack_path,contract_path,answers_path)
    review=read_fields_json(review_path)
    validate_review(review,contract,sha(contract_path),sha(answers_path))
    recorded_at=datetime.now(timezone.utc).isoformat()
    candidate=apply_answers(pack,contract,answers)
    apply_review(candidate,review,recorded_at)
    report=validate(candidate,pack_path.parent,allow_pending,collect_artifacts=True)
    require_report(report)
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix=output.name+'.tmp-',dir=output.parent))
    try:
        for entry in report['artifact_inventory']:
            source=artifact(pack_path.parent,entry)
            logical=pack_path.parent/entry['path']
            if logical.resolve()!=logical:
                raise ValueError('Freeze regular artifact copies before compiling a symlinked snapshot: '+entry['path'])
            target=stage/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:
                stream.write(source.read_bytes())
        proof=stage/'_publication'/uuid.uuid4().hex[:16]
        proof.mkdir(parents=True,exist_ok=False)
        files={'base':pack_path,'contract':contract_path,'schema':contract_path.parent/'answers.schema.json',
               'answers':answers_path,'review':review_path,'layout':contract_path.parent/'layout.lock.json'}
        binding=dict(schema_version='1.0',recorded_at=recorded_at)
        for name,source in files.items():
            target=proof/(name+'.json')
            target.write_bytes(source.read_bytes())
            binding[name]=ref(stage,target)
        candidate['form_binding']=binding
        add_sources(candidate,[binding[name] for name in files])
        # Frozen inputs are checked again here; edits during assembly cannot be published.
        final_report=validate(candidate,stage,allow_pending)
        require_report(final_report)
        ensure_layout(baseline=proof/'layout.json')
        write_json(stage/'pack.json',candidate)
        write_json(stage/'field-validation.json',{k:v for k,v in final_report.items() if k!='cards'})
        if output.exists():
            raise ValueError('Output appeared during compilation')
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    return dict(pack=str(output/'pack.json'),cases=len(contract['cases']),layout_unchanged=True,
                pending_questions=len(final_report['pending_requests']))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    prepare_cmd=sub.add_parser('prepare')
    prepare_cmd.add_argument('--pack',type=Path,required=True)
    prepare_cmd.add_argument('--output-dir',type=Path,required=True)
    for command in ['review-plan','compile']:
        child=sub.add_parser(command)
        for flag in ['pack','contract','answers']:
            child.add_argument('--'+flag,type=Path,required=True)
        if command=='review-plan':
            child.add_argument('--output',type=Path,required=True)
        else:
            child.add_argument('--review',type=Path,required=True)
            child.add_argument('--output-dir',type=Path,required=True)
            child.add_argument('--allow-pending',action='store_true')
    args=parser.parse_args()
    try:
        if args.command=='prepare':result=prepare(args.pack,args.output_dir)
        elif args.command=='review-plan':result=prepare_review(args.pack,args.contract,args.answers,args.output)
        else:result=compile_pack(args.pack,args.contract,args.answers,args.review,args.output_dir,args.allow_pending)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__':
    main()
