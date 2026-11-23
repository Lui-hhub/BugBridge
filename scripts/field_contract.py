"""Immutable field definitions and value-only answers; no page generation by LLMs."""
import copy
import json
from pathlib import Path

from pack_v2 import PLACEHOLDER, QUALITY_CHECKS, timestamp

CONTRACT_VERSION = '1.0'
FILLABLE = {'input','textarea','dropdown'}
MANUAL = {'checkboxes','upload'}


def read_fields_json(path):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key in filling data: '+key)
            result[key]=value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=unique)


def exact_keys(value, expected, location):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(location+': unexpected or missing keys; expected '+', '.join(sorted(expected)))


def make_contract(pack, pack_hash, pack_name, layout):
    templates = {t['id']:t for t in pack.get('templates', [])}
    cases = {}
    for issue in pack.get('issues', []):
        action = issue.get('action', {}).get('type')
        if action not in {'new_issue','existing_issue_comment'}:
            continue
        case = dict(action=action, template_id=issue.get('template_id'), fields=[], manual_fields=[], instructions=[])
        if action == 'existing_issue_comment':
            case['target_url'] = issue['action'].get('target_url')
            case['fields'] = [dict(id='comment',type='textarea',label='Comment',description='Additional evidence for the inspected existing issue.',purpose='body',required=True,validations={'required':True},options=[],multiple=False,render=None)]
        else:
            template = templates[issue['template_id']]
            case['title'] = dict(label='Title', default=template.get('title_default',''), required=True)
            case['template_source'] = copy.deepcopy(template['source'])
            for field in template['fields']:
                kind = field['type']
                if kind == 'markdown':
                    case['instructions'].append(field.get('default',''))
                    continue
                if kind not in FILLABLE | MANUAL:
                    raise ValueError('Unsupported official control '+kind+'; add generic support during authorized maintenance, not a per-report frontend rewrite')
                if set(field.get('validations',{}))-{'required','min_length'}:
                    raise ValueError('Unreviewed official validation rule in '+field['label'])
                allowed = {'label','description','value','placeholder','render','options','default','multiple'}
                if set(field.get('raw',{}).get('attributes',{}))-allowed:
                    raise ValueError('Unreviewed official field attribute in '+field['label'])
                keys = ['id','source_id','type','label','description','purpose','required','validations','options','multiple','render']
                spec = {k:copy.deepcopy(field.get(k)) for k in keys}
                if kind in MANUAL or field.get('purpose')=='attestation':
                    case['manual_fields'].append(spec)
                else:
                    if template['format'] in {'markdown','generic'}:
                        spec['guidance'] = field.get('default','')
                    case['fields'].append(spec)
        cases[issue['id']] = case
    return dict(schema_version=CONTRACT_VERSION,source_pack_sha256=pack_hash,source_pack_name=pack_name,
                layout=copy.deepcopy(layout),cases=cases)


def answer_skeleton(pack, contract, contract_hash):
    originals = {i['id']:i for i in pack['issues']}
    result = dict(schema_version=CONTRACT_VERSION,contract_sha256=contract_hash,issues={})
    for iid, case in contract['cases'].items():
        original = originals[iid]
        if case['action']=='existing_issue_comment':
            result['issues'][iid] = dict(comment=original['action'].get('comment',{}).get('value',''))
        else:
            result['issues'][iid] = dict(title=original.get('title',''),responses={
                field['id']:copy.deepcopy(original.get('responses',{}).get(field['id'],{}).get('value')) for field in case['fields']})
    return result


def value_schema(field):
    if field['type']=='dropdown':
        if field.get('multiple'):
            shape = dict(type='array',items=dict(type='string',enum=field['options']),uniqueItems=True)
            if field['required']:
                shape['minItems']=1
        else:
            shape=dict(type='string',enum=field['options'])
    else:
        shape=dict(type='string')
        if field['type']=='input':
            shape['pattern']='^[^\\r\\n]*$'
        minimum=max(1 if field['required'] else 0,(field.get('validations') or {}).get('min_length',0))
        if minimum:
            shape['minLength']=minimum
    if field['required']:
        return shape
    options=[shape,dict(type='null')]
    if field['type']!='dropdown':
        options.append(dict(const=''))
    return dict(anyOf=options)


def answer_schema(contract, contract_hash):
    cases={}
    for iid,case in contract['cases'].items():
        if case['action']=='existing_issue_comment':
            properties=dict(comment=value_schema(case['fields'][0]))
        else:
            properties=dict(title=dict(type='string',minLength=1,pattern='^[^\\r\\n]*$'),responses=dict(type='object',additionalProperties=False,
                properties={f['id']:value_schema(f) for f in case['fields']},required=[f['id'] for f in case['fields']]))
        cases[iid]=dict(type='object',additionalProperties=False,properties=properties,required=list(properties))
    return {'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object','additionalProperties':False,
            'required':['schema_version','contract_sha256','issues'],
            'properties':dict(schema_version=dict(const=CONTRACT_VERSION),contract_sha256=dict(const=contract_hash),
                issues=dict(type='object',additionalProperties=False,properties=cases,required=list(cases)))}


def check_value(value, field, location):
    if value is None:
        if field['required']:
            raise ValueError(location+': required content missing')
        return
    kind = field['type']
    if kind=='dropdown':
        choices=value if field.get('multiple') else [value]
        if (field.get('multiple') and not isinstance(value,list)) or (not field.get('multiple') and not isinstance(value,str)):
            raise ValueError(location+': invalid selection type')
        if field['required'] and not choices:
            raise ValueError(location+': required content missing')
        if not all(isinstance(v,str) and v in field['options'] for v in choices) or len(set(choices))!=len(choices):
            raise ValueError(location+': unknown or duplicate official option')
    else:
        if value=='' and not field['required']:
            return
        if not isinstance(value,str) or not value.strip():
            raise ValueError(location+': expected nonblank text')
        if kind=='input' and ('\n' in value or '\r' in value):
            raise ValueError(location+': input is single-line')
        minimum=(field.get('validations') or {}).get('min_length',0)
        if len(value)<minimum:
            raise ValueError(location+': minimum length not met')
        if PLACEHOLDER.search(value):
            raise ValueError(location+': internal placeholder is not submission content')


def validate_answers(answers, contract, contract_hash):
    exact_keys(answers, {'schema_version','contract_sha256','issues'}, 'answers')
    if answers['schema_version']!=CONTRACT_VERSION or answers['contract_sha256']!=contract_hash:
        raise ValueError('Answers belong to another template contract')
    exact_keys(answers['issues'],contract['cases'],'answer issue IDs')
    for iid,case in contract['cases'].items():
        answer=answers['issues'][iid]
        if case['action']=='existing_issue_comment':
            exact_keys(answer,{'comment'},iid)
            check_value(answer['comment'],case['fields'][0],iid+':comment')
        else:
            exact_keys(answer,{'title','responses'},iid)
            check_value(answer['title'],dict(type='input',required=True),iid+':title')
            exact_keys(answer['responses'],[f['id'] for f in case['fields']],iid+' response IDs')
            for field in case['fields']:
                check_value(answer['responses'][field['id']],field,iid+':'+field['id'])


def apply_answers(base, contract, answers):
    pack=copy.deepcopy(base)
    pack.pop('form_binding',None)
    for issue in pack['issues']:
        iid=issue['id']
        if iid not in contract['cases']:
            continue
        answer=answers['issues'][iid]
        if contract['cases'][iid]['action']=='existing_issue_comment':
            issue['action']['comment']=dict(issue['action'].get('comment',{}),value=answer['comment'])
        else:
            issue['title']=answer['title']
            for fid,value in answer['responses'].items():
                response=issue.setdefault('responses',{}).setdefault(fid,{})
                response['value']=value
        issue.setdefault('publication',{})['quality_review']=dict(status='pending',checks=[])
    return pack


def review_skeleton(contract, contract_hash, answers_hash):
    return dict(schema_version=CONTRACT_VERSION,contract_sha256=contract_hash,answers_sha256=answers_hash,reviews={
        iid:dict(status='pending',reviewer='agent',notes='',checks=[dict(id=k,status='pending',reason='') for k in sorted(QUALITY_CHECKS)]) for iid in contract['cases']})


def validate_review(review, contract, contract_hash, answers_hash):
    exact_keys(review,{'schema_version','contract_sha256','answers_sha256','reviews'},'content review')
    if review['schema_version']!=CONTRACT_VERSION or review['contract_sha256']!=contract_hash or review['answers_sha256']!=answers_hash:
        raise ValueError('Content review belongs to another contract/answer version')
    exact_keys(review['reviews'],contract['cases'],'review issue IDs')
    for iid,entry in review['reviews'].items():
        exact_keys(entry,{'status','reviewer','notes','checks'},iid+' review')
        if entry['status']!='reviewed' or entry['reviewer']!='agent' or not isinstance(entry['notes'],str) or not entry['notes'].strip():
            raise ValueError(iid+': record an actual agent content review; never invent human sign-off')
        checks=entry['checks']
        if not isinstance(checks,list) or len(checks)!=len(QUALITY_CHECKS) or not all(isinstance(c,dict) for c in checks) or {c.get('id') for c in checks}!=QUALITY_CHECKS:
            raise ValueError(iid+': content review must cover the fixed quality checks')
        for check in checks:
            exact_keys(check,{'id','status','reason'},iid+' quality check')
            if check['status'] not in {'pass','needs_work','not_applicable'} or not isinstance(check['reason'],str) or not check['reason'].strip():
                raise ValueError(iid+': content review needs reasoned outcomes')


def apply_review(pack, review, recorded_at):
    if timestamp(recorded_at) is None:
        raise ValueError('Compiled review needs a timezone-aware recording time')
    for issue in pack['issues']:
        if issue['id'] in review['reviews']:
            issue.setdefault('publication',{})['quality_review']=dict(copy.deepcopy(review['reviews'][issue['id']]),reviewed_at=recorded_at)


def add_sources(pack, refs):
    by_path={ref['path']:ref for ref in pack['sources']}
    for ref in refs:
        if ref['path'] in by_path and by_path[ref['path']]!=ref:
            raise ValueError('Frozen source path collision: '+ref['path'])
        by_path[ref['path']]=ref
    pack['sources']=list(by_path.values())


def validate_binding(pack, root, verify, check, read_json):
    """Verify frozen compiler inputs and exact content without executing anything."""
    binding=pack.get('form_binding',{})
    try:
        exact_keys(binding,{'schema_version','base','contract','schema','answers','review','layout','recorded_at'},'form_binding')
        if binding['schema_version']!=CONTRACT_VERSION:
            raise ValueError('Unknown form-binding version')
        paths={name:verify(binding[name]) for name in ['base','contract','schema','answers','review','layout']}
        if not all(paths.values()):
            return
        base=read_json(paths['base']);contract=read_fields_json(paths['contract']);answers=read_fields_json(paths['answers']);review=read_fields_json(paths['review']);layout=read_fields_json(paths['layout'])
        expected=make_contract(base,binding['base']['sha256'],contract['source_pack_name'],layout)
        if contract!=expected:
            raise ValueError('Readonly field contract differs from the frozen source templates')
        if read_json(paths['schema'])!=answer_schema(contract,binding['contract']['sha256']):
            raise ValueError('Answer schema differs from the generated field contract')
        validate_answers(answers,contract,binding['contract']['sha256'])
        validate_review(review,contract,binding['contract']['sha256'],binding['answers']['sha256'])
        wanted=apply_answers(base,contract,answers)
        apply_review(wanted,review,binding['recorded_at'])
        if pack['templates']!=wanted['templates'] or pack['library']!=wanted['library'] or pack['materials_path']!=wanted['materials_path']:
            raise ValueError('Compiled pack changed frozen template/project definitions')
        originals={i['id']:i for i in wanted['issues']}
        if set(originals)!={i['id'] for i in pack['issues']}:
            raise ValueError('Compiled issue IDs differ from the filling contract')
        for issue in pack['issues']:
            original=originals[issue['id']]
            for name in ['title','template_id','action','responses','publication']:
                if issue.get(name)!=original.get(name):
                    raise ValueError(issue['id']+': compiled '+name+' differs from frozen answers/review')
        add_sources(wanted,[binding[n] for n in ['base','contract','schema','answers','review','layout']])
        wanted['form_binding']=copy.deepcopy(binding)
        if pack!=wanted:
            raise ValueError('Compiled pack changed data outside the value-only assembly contract')
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        check(False,'Form binding: '+str(exc))
