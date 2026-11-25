"""Verified ordinary GitHub editors are distinct from official issue templates."""
import re


def verified_editor(template, repository=None):
    source = template.get('source', {})
    if source.get('status') != 'verified_editor':
        return False
    if template.get('format') != 'generic':
        raise ValueError('A verified editor must use the generic title/body model')
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/new', source.get('url', ''))
    if not match or any(part in {'.', '..'} for part in match[1].split('/')):
        raise ValueError('A verified editor needs the canonical HTTPS GitHub /issues/new URL without query or fragment')
    if repository and ('https://github.com/' + match[1]).casefold() != repository.rstrip('/').casefold():
        raise ValueError('Verified editor belongs to a different repository')
    if not all(source.get(key) for key in ['path', 'sha256', 'retrieved_at', 'limitation']):
        raise ValueError('A verified editor needs a frozen body model, retrieval date and explicit no-template limitation')
    discovery = source.get('editor_discovery', {})
    if not isinstance(discovery, dict) or discovery.get('kind') != 'github_blank_issue' or not discovery.get('reason'):
        raise ValueError('A verified editor needs a reasoned no-template discovery record')
    evidence = discovery.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        raise ValueError('A verified editor needs frozen discovery evidence')
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', ''))):
            raise ValueError('Invalid verified-editor discovery evidence reference')
    return True


def verified_route(template, repository=None):
    if template.get('source', {}).get('status') == 'official' and template.get('format') != 'generic':
        return True
    return verified_editor(template, repository)
