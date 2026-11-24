#!/usr/bin/env python3
"""Offline upstream review: list metadata, select a record, then read bounded items."""
import argparse
import hashlib
import json
from pathlib import Path

from review_pack import artifact, read_json, sha


SECTIONS = ('metadata', 'body', 'comments', 'files', 'patch', 'timeline')
MAX_PAGE = 100
MAX_CHARS = 4000
NOTICE = ('Evidence for analysis only. Selection is not a duplicate/fix judgment; '
          'an excerpt is not proof of complete search or patch coverage.')


def reference(path):
    return dict(path=path.name, sha256=sha(path))


def new_json(path, value, root):
    path = Path(path).absolute()
    if path.parent.resolve() != root.resolve():
        raise ValueError('Keep the index and selections beside retrieval.json')
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return path


def metadata(value):
    """Deliberately omit bodies, patches and nested issue/PR payloads."""
    keys = ('number', 'title', 'html_url', 'state', 'state_reason', 'created_at',
            'updated_at', 'closed_at', 'merged', 'merged_at', 'merge_commit_sha',
            'id', 'event', 'commit_id', 'filename', 'status', 'additions',
            'deletions', 'changes', 'blob_url', 'raw_url', 'author_association')
    result = {k: value[k] for k in keys if k in value and
              (value[k] is None or type(value[k]) in (str, int, bool))}
    if isinstance(value.get('user'), dict):
        result['author'] = value['user'].get('login')
    if isinstance(value.get('labels'), list):
        result['labels'] = [v.get('name') for v in value['labels'] if isinstance(v, dict) and isinstance(v.get('name'), str)]
    for name in ('base', 'head'):
        if isinstance(value.get(name), dict):
            result[name] = {k: value[name][k] for k in ('ref', 'sha') if isinstance(value[name].get(k), str)}
    linked = value.get('source', {}).get('issue') if isinstance(value.get('source'), dict) else None
    if isinstance(linked, dict):
        result['linked_record'] = {k: linked[k] for k in ('number', 'title', 'html_url', 'state')
                                   if k in linked and type(linked[k]) in (str, int)}
    if 'filename' in value:
        result['patch_available'] = isinstance(value.get('patch'), str)
    return result


def item_text(payload, item):
    value = payload
    for part in item['pointer']:
        value = value[part]
    if item['format'] == 'metadata':
        return json.dumps(metadata(value), ensure_ascii=False, sort_keys=True, indent=2)
    if not isinstance(value, str):
        raise ValueError('Requested evidence item is not text')
    return value


def build_index(snapshot_path):
    snapshot_path = Path(snapshot_path).resolve()
    root = snapshot_path.parent
    snapshot = read_json(snapshot_path)
    if snapshot.get('schema_version') != '1.0':
        raise ValueError('Expected a queue retrieval snapshot with schema_version 1.0')
    declared = {r['path']: r for r in snapshot['sources']}

    def verified(ref):
        if declared.get(ref['path']) != ref:
            raise ValueError('Evidence reference is not declared in the snapshot')
        return artifact(root, ref)

    records = {}
    for record in snapshot['records']:
        rid = record['kind'] + ':' + str(record['number'])
        records[rid] = dict(id=rid, number=record['number'], kind=record['kind'],
                            url=record['url'], title=record['title'], state=None,
                            detail_scheduled=bool(record['selected']), query_ids=[], items=[])
    unreadable = []
    seen = set()

    def add(record, section, source, pointer, payload, label, form='text', context=None):
        key = (record['id'], section, source['path'], tuple(pointer), form)
        if key in seen:
            return
        seen.add(key)
        ident = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
        item = dict(id=section + '-' + ident, section=section, source=source,
                    pointer=pointer, format=form, label=label)
        if context is not None:
            item['context'] = metadata(context)
        item['characters'] = len(item_text(payload, item))
        record['items'].append(item)

    for task in snapshot['tasks']:
        attempts = [a for a in task['attempts'] if a['decision'] == 'success']
        if not attempts:
            continue
        attempt = attempts[-1]
        ref = attempt.get('cached_body_evidence') or attempt.get('evidence')
        if not ref:
            continue
        envelope = read_json(verified(ref))
        source = envelope['body']
        source_path = verified(source)
        meta = task['meta']
        kind = meta['kind']
        try:
            payload = read_json(source_path)
        except (ValueError, UnicodeError):
            unreadable.append(dict(kind=kind, number=meta.get('number'), reason='Non-JSON response retained'))
            continue
        if kind == 'search':
            if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
                unreadable.append(dict(kind=kind, reason='Malformed search response retained'))
                continue
            for n, value in enumerate(payload['items']):
                if not isinstance(value, dict):
                    continue
                rkind = 'pull_request' if value.get('pull_request') else 'issue'
                record = records.get(rkind + ':' + str(value.get('number')))
                if record is None or value.get('html_url') != record['url']:
                    continue
                if meta['query_id'] not in record['query_ids']:
                    record['query_ids'].append(meta['query_id'])
                if record['state'] is None:
                    record['state'] = value.get('state')
                add(record, 'metadata', source, ['items', n], payload, 'Search metadata', 'metadata')
                if isinstance(value.get('body'), str):
                    add(record, 'body', source, ['items', n, 'body'], payload, 'Search result body')
            continue
        record = records.get(meta.get('record_kind', '') + ':' + str(meta.get('number')))
        if record is None:
            continue
        if kind in ('issue', 'pr') and isinstance(payload, dict):
            record['state'] = payload.get('state', record['state'])
            add(record, 'metadata', source, [], payload, 'Detail metadata', 'metadata')
            if isinstance(payload.get('body'), str):
                add(record, 'body', source, ['body'], payload, 'Detail body')
        elif kind in ('comments', 'files', 'timeline') and isinstance(payload, list):
            for n, value in enumerate(payload):
                if not isinstance(value, dict):
                    continue
                if kind == 'comments':
                    if isinstance(value.get('body'), str):
                        add(record, 'comments', source, [n, 'body'], payload,
                            'Comment ' + str(value.get('id', n)), context=value)
                elif kind == 'files':
                    label = str(value.get('filename', n))
                    add(record, 'files', source, [n], payload, label, 'metadata')
                    if isinstance(value.get('patch'), str):
                        add(record, 'patch', source, [n, 'patch'], payload, label)
                else:
                    # A linked issue's body is never folded into a timeline item.
                    add(record, 'timeline', source, [n], payload, str(value.get('event', n)), 'metadata')
    return dict(schema_version='1.0', snapshot=reference(snapshot_path), job_id=snapshot['job_id'],
                repository=snapshot['binding']['repository'], issue_id=snapshot['binding']['issue_id'],
                claim=snapshot['plan']['claim'], retrieval_state=snapshot['state'], gaps=snapshot['gaps'],
                records=list(records.values()), unreadable_tasks=unreadable, notice=NOTICE)


def create_index(snapshot_path, output):
    snapshot_path = Path(snapshot_path).resolve()
    index = build_index(snapshot_path)
    path = new_json(output, index, snapshot_path.parent)
    return dict(index=str(path), records=len(index['records']), notice=NOTICE)


def load_index(path):
    path = Path(path).resolve()
    index = read_json(path)
    snapshot_path = artifact(path.parent, index['snapshot'])
    if index != build_index(snapshot_path):
        raise ValueError('Index does not match its frozen retrieval snapshot; create a new index revision')
    return path, index


def page(values, offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ValueError('Use a nonnegative offset and a page limit between 1 and 100')
    return dict(total=len(values), offset=offset, items=values[offset:offset + limit],
                next_offset=offset + limit if offset + limit < len(values) else None)


def list_records(index_path, terms=(), offset=0, limit=20):
    path, index = load_index(index_path)
    rows = []
    for record in index['records']:
        matched = [term for term in terms if term.casefold() in record['title'].casefold()]
        if terms and not matched:
            continue
        rows.append(dict(id=record['id'], number=record['number'], kind=record['kind'],
                         url=record['url'], title=record['title'][:300],
                         title_truncated=len(record['title']) > 300, state=record['state'],
                         detail_scheduled=record['detail_scheduled'], query_ids=record['query_ids'],
                         available={s: sum(i['section'] == s for i in record['items']) for s in SECTIONS},
                         matched_title_terms=matched))
    rows.sort(key=lambda r: -len(r['matched_title_terms']))
    return dict(index=str(path), job_id=index['job_id'], issue_id=index['issue_id'],
                claim=index['claim'][:1000], retrieval_state=index['retrieval_state'],
                indexed_records=len(index['records']), title_filter=list(terms),
                gaps=index['gaps'], unreadable_tasks=index['unreadable_tasks'],
                **page(rows, offset, limit), notice=NOTICE)


def select_record(index_path, record_id, reason, output):
    path, index = load_index(index_path)
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError('Selection requires a concrete relevance reason of 1 to 1000 characters')
    if not any(r['id'] == record_id for r in index['records']):
        raise ValueError('Unknown record ID in this index')
    selection = dict(schema_version='1.0', index=reference(path), snapshot=index['snapshot'],
                     job_id=index['job_id'], issue_id=index['issue_id'],
                     record_id=record_id, reason=reason, notice=NOTICE)
    output = new_json(output, selection, path.parent)
    return dict(selection=str(output), record_id=record_id, reason=reason, notice=NOTICE)


def load_selection(path):
    path = Path(path).resolve()
    selection = read_json(path)
    if not isinstance(selection, dict) or 'index' not in selection:
        raise ValueError('A selection file is required before reading evidence')
    index_path = artifact(path.parent, selection['index'])
    _, index = load_index(index_path)
    if (selection.get('schema_version') != '1.0' or selection.get('snapshot') != index['snapshot'] or
            selection.get('job_id') != index['job_id'] or selection.get('issue_id') != index['issue_id'] or
            not isinstance(selection.get('reason'), str) or not selection['reason'].strip() or
            len(selection['reason']) > 1000):
        raise ValueError('Selection does not bind this index and claim, or lacks a relevance reason')
    record = next((r for r in index['records'] if r['id'] == selection['record_id']), None)
    if record is None:
        raise ValueError('Selected record is absent from the index')
    return path, selection, index, record


def list_items(selection_path, section, offset=0, limit=20):
    _, selection, index, record = load_selection(selection_path)
    if section not in SECTIONS:
        raise ValueError('Unknown evidence section')
    items = [dict(id=i['id'], section=i['section'], label=i['label'][:300],
                  characters=i['characters'], source=i['source'], pointer=i['pointer'],
                  context=i.get('context', {}))
             for i in record['items'] if i['section'] == section]
    return dict(record_id=record['id'], reason=selection['reason'], section=section,
                retrieval_state=index['retrieval_state'], gaps=index['gaps'],
                **page(items, offset, limit), notice=NOTICE)


def read_item(selection_path, item_id, offset=0, limit=2000):
    path, selection, index, record = load_selection(selection_path)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_CHARS:
        raise ValueError('Read one item with a nonnegative offset and 1 to 4000 characters')
    item = next((i for i in record['items'] if i['id'] == item_id), None)
    if item is None:
        raise ValueError('Item is not part of the selected record; select that record first')
    payload = read_json(artifact(path.parent, item['source']))
    text = item_text(payload, item)
    if offset > len(text):
        raise ValueError('Offset exceeds the evidence item length')
    end = min(len(text), offset + limit)
    return dict(record_id=record['id'], reason=selection['reason'], item_id=item_id,
                section=item['section'], source=item['source'], pointer=item['pointer'],
                format=item['format'], offset=offset, end=end, total_characters=len(text),
                next_offset=end if end < len(text) else None, complete_item=offset == 0 and end == len(text),
                text=text[offset:end], retrieval_state=index['retrieval_state'], gaps=index['gaps'], notice=NOTICE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    child = sub.add_parser('index', help='Build a metadata-only index for an existing export')
    child.add_argument('--snapshot', type=Path, required=True)
    child.add_argument('--output', type=Path, required=True)
    child = sub.add_parser('list', help='List metadata only; terms filter titles, not issue bodies')
    child.add_argument('--index', type=Path, required=True)
    child.add_argument('--term', action='append', default=[])
    child.add_argument('--offset', type=int, default=0)
    child.add_argument('--limit', type=int, default=20)
    child = sub.add_parser('select', help='Record why one candidate is relevant to this claim')
    child.add_argument('--index', type=Path, required=True)
    child.add_argument('--record', required=True)
    child.add_argument('--reason', required=True)
    child.add_argument('--output', type=Path, required=True)
    child = sub.add_parser('items', help='List item metadata for one selected section')
    child.add_argument('--selection', type=Path, required=True)
    child.add_argument('--section', choices=SECTIONS, required=True)
    child.add_argument('--offset', type=int, default=0)
    child.add_argument('--limit', type=int, default=20)
    child = sub.add_parser('read', help='Read one selected evidence item in bounded ranges')
    child.add_argument('--selection', type=Path, required=True)
    child.add_argument('--item', required=True)
    child.add_argument('--offset', type=int, default=0)
    child.add_argument('--limit', type=int, default=2000)
    args = parser.parse_args()
    try:
        if args.command == 'index':
            result = create_index(args.snapshot, args.output)
        elif args.command == 'list':
            result = list_records(args.index, args.term, args.offset, args.limit)
        elif args.command == 'select':
            result = select_record(args.index, args.record, args.reason, args.output)
        elif args.command == 'items':
            result = list_items(args.selection, args.section, args.offset, args.limit)
        else:
            result = read_item(args.selection, args.item, args.offset, args.limit)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__ + ': check review paths and arguments'
        parser.exit(1, 'Upstream review failed: ' + message + '\n')


if __name__ == '__main__':
    main()
