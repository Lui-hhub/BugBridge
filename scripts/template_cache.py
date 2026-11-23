#!/usr/bin/env python3
"""Offline template catalog. Discovery is agent work; cache hits never fetch."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlparse

from normalize_template import normalize
from review_pack import artifact, read_json, safe_id, sha

DEFAULT_CACHE = Path(__file__).resolve().parents[1] / 'assets' / 'template-cache'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.catalog-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reference(root, path):
    return dict(path=Path(path).relative_to(root).as_posix(), sha256=sha(path))


def yaml_data(text):
    import yaml
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError('Malformed YAML: ' + str(exc)) from exc


class TemplateCache:
    def __init__(self, root=DEFAULT_CACHE):
        self.root = Path(root).resolve()
        path = self.root / 'catalog.json'
        self.catalog = read_json(path) if path.exists() else dict(schema_version='1.0', libraries={})

    def library(self, name):
        query = name.casefold().rstrip('/')
        for key, value in self.catalog['libraries'].items():
            if query in {key.casefold(), value['repository'].casefold().rstrip('/'), *[a.casefold() for a in value.get('aliases', [])]}:
                return value
        raise KeyError('No cached project; discover official templates, then import the frozen files: ' + name)

    def import_library(self, library, repository, revision, retrieved_at, directory,
                       aliases=(), annotations=None, verified=False, refresh=False, add_missing=False):
        if not safe_id(library) or not safe_id(revision):
            raise ValueError('Library/revision IDs must be safe')
        url = urlparse(repository)
        if url.scheme != 'https' or url.username or url.password or url.query or url.fragment:
            raise ValueError('Use a verified credential-free HTTPS repository URL')
        previous = self.catalog['libraries'].get(library)
        names = {library.casefold(), *[a.casefold() for a in aliases]}
        for key, value in self.catalog['libraries'].items():
            if key != library and names & {key.casefold(), *[a.casefold() for a in value.get('aliases', [])]}:
                raise ValueError('A project alias is already assigned to another cached repository')
        if previous and not refresh and not add_missing:
            raise ValueError('Project is already cached; reuse it. Explicit refresh is required to add another revision')
        if previous and add_missing and previous['repository'].rstrip('/') != repository.rstrip('/'):
            raise ValueError('Adding templates cannot change the cached official repository')
        if not retrieved_at:
            raise ValueError('Record the actual snapshot retrieval time')
        directory = Path(directory).resolve()
        annotations = annotations or {}
        parent = self.root / library
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / revision
        if destination.exists():
            raise ValueError('Frozen cache revision already exists; refusing overwrite')
        stage = Path(tempfile.mkdtemp(prefix='.import-', dir=parent))
        entry = dict(id=library, repository=repository.rstrip('/'), revision=revision,
                     retrieved_at=retrieved_at, aliases=sorted(set(aliases) | set(previous.get('aliases', []) if previous else [])), templates=[])
        try:
            for source in sorted(directory.iterdir()):
                if not source.is_file() or source.suffix.lower() not in {'.md', '.yml', '.yaml', '.json'}:
                    continue
                if previous and add_missing and (source.name in {x['filename'] for x in previous['templates']} or source.name in {'config.yml','config.yaml'}):
                    continue
                if not source.resolve().is_relative_to(directory):
                    raise ValueError('Template symlink escapes the supplied snapshot directory')
                raw = stage / 'raw' / source.name
                raw.parent.mkdir(exist_ok=True)
                raw.write_bytes(source.read_bytes())
                final_raw = destination / 'raw' / source.name
                ref = dict(path=final_raw.relative_to(self.root).as_posix(), sha256=sha(raw))
                origin = repository.rstrip('/') + '/blob/' + revision + '/.github/ISSUE_TEMPLATE/' + source.name
                if source.name in {'config.yml', 'config.yaml'}:
                    entry['chooser'] = dict(ref, url=origin)
                    continue
                tid = annotations.get(source.name, {}).get('id', source.stem)
                if not safe_id(tid) or any(t['id'] == tid for t in entry['templates']):
                    raise ValueError('Template IDs must be unique; assign an explicit local ID in annotations')
                record = dict(id=tid, filename=source.name, raw=ref, url=origin, status='usable')
                try:
                    text = raw.read_text(encoding='utf-8')
                    if source.suffix == '.md':
                        if not text.startswith('---\n') and not text.startswith('---\r\n'):
                            raise ValueError('Markdown file lacks template frontmatter; do not infer form controls from embedded YAML-like prose')
                        model = normalize(text, 'markdown', tid)
                        meta = yaml_data(model.get('metadata', {}).get('raw_frontmatter', '')) or {}
                        if not isinstance(meta, dict) or not isinstance(meta.get('name'), str) or not meta.get('about'):
                            raise ValueError('Markdown template name/about metadata needs verification')
                        model['name'] = meta['name']
                        model['title_default'] = meta.get('title', '')
                    else:
                        if source.suffix == '.json':
                            data = json.loads(text)
                        else:
                            data = yaml_data(text)
                        model = normalize(data, 'github_form', tid)
                    purpose_map = annotations.get(source.name, {}).get('purposes', {})
                    for field in model['fields']:
                        if field['id'] in purpose_map:
                            spec = purpose_map[field['id']]
                            field.update(spec if isinstance(spec, dict) else dict(purpose=spec))
                    model['source'] = dict(ref, url=origin, revision=revision, retrieved_at=retrieved_at,
                        status='official' if verified else 'unverified',
                        limitation='' if verified else 'Tracker identity and template applicability need verification.')
                    model['cache_notes'] = annotations.get(source.name, {}).get('notes', '')
                    normalized = stage / 'normalized' / (tid + '.json')
                    normalized.parent.mkdir(exist_ok=True)
                    atomic_json(normalized, model)
                    record.update(name=model['name'], format=model['format'],
                        normalized=dict(path=(destination/'normalized'/(tid+'.json')).relative_to(self.root).as_posix(), sha256=sha(normalized)),
                        purposes_assigned=all(f.get('purpose') != 'unassigned' for f in model['fields']))
                except (ValueError, KeyError, TypeError, ImportError) as exc:
                    record.update(status='needs_review', reason=str(exc))
                entry['templates'].append(record)
            if not entry['templates']:
                raise ValueError('No new template files found; reuse the existing cache')
            if previous:
                replaced = {x['filename'] for x in entry['templates']}
                entry['templates'] = [x for x in previous['templates'] if x['filename'] not in replaced] + entry['templates']
                if 'chooser' not in entry and previous.get('chooser'):
                    entry['chooser'] = previous['chooser']
                if len({x['id'] for x in entry['templates']}) != len(entry['templates']):
                    raise ValueError('New template ID conflicts with an existing cached template')
            stage.rename(destination)
            self.catalog['libraries'][library] = entry
            atomic_json(self.root/'catalog.json', self.catalog)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return entry

    def export(self, library, template_id, pack_root, output=None):
        project = self.library(library)
        entry = next((x for x in project['templates'] if x['id'] == template_id), None)
        if not entry:
            raise KeyError('Template is not in the cached catalog: ' + template_id)
        if entry['status'] != 'usable':
            raise ValueError('Cached template needs source verification: ' + entry.get('reason', ''))
        model = read_json(artifact(self.root, entry['normalized']))
        raw = artifact(self.root, entry['raw'])
        if model['source']['sha256'] != entry['raw']['sha256']:
            raise ValueError('Normalized model and raw snapshot disagree')
        root = Path(pack_root).resolve()
        revision = model['source']['revision']
        target = root/'tracker'/'templates'/project['id']/revision/entry['filename']
        output = root / (output or ('templates/' + template_id + '.normalized.json'))
        if not output.resolve().is_relative_to(root) or output.exists():
            raise ValueError('Use a new normalized output path inside the pack root')
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha(target) != entry['raw']['sha256']:
                raise ValueError('Existing frozen snapshot differs')
        else:
            with target.open('xb') as stream:
                stream.write(raw.read_bytes())
        model['source'].update(reference(root, target))
        model['cache_origin'] = dict(library=project['id'], revision=revision)
        atomic_json(output, model)
        return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-root', type=Path, default=DEFAULT_CACHE)
    sub = parser.add_subparsers(dest='command', required=True)
    ls = sub.add_parser('list')
    ls.add_argument('--library')
    export = sub.add_parser('export')
    for name in ['library', 'template', 'pack-root']:
        export.add_argument('--' + name, required=True)
    export.add_argument('--output')
    add = sub.add_parser('import')
    for name in ['library', 'repository', 'revision', 'retrieved-at', 'directory']:
        add.add_argument('--' + name, required=True)
    add.add_argument('--alias', action='append', default=[])
    add.add_argument('--annotations', type=Path)
    add.add_argument('--verified', action='store_true')
    add.add_argument('--refresh', action='store_true')
    add.add_argument('--add-missing', action='store_true')
    args = parser.parse_args()
    try:
        cache = TemplateCache(args.cache_root)
        if args.command == 'list':
            result = cache.library(args.library) if args.library else cache.catalog
        elif args.command == 'export':
            model = cache.export(args.library, args.template, args.pack_root, args.output)
            result = dict(id=model['id'], name=model['name'], fields=[f['label'] for f in model['fields'] if f['type']!='markdown'],
                          output=str(Path(args.pack_root)/(args.output or ('templates/'+args.template+'.normalized.json'))))
        else:
            result = cache.import_library(args.library, args.repository, args.revision, args.retrieved_at,
                args.directory, args.alias, read_json(args.annotations) if args.annotations else {}, args.verified, args.refresh, args.add_missing)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    main()
