"""Read-only GitHub transport, credential resolution and bounded retry decisions."""
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
import json
import os
import re
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_VERSION = '2026-03-10'
SAFE_HEADERS = {'etag', 'last-modified', 'link', 'retry-after', 'x-ratelimit-limit',
                'x-ratelimit-remaining', 'x-ratelimit-reset', 'x-ratelimit-resource',
                'x-github-request-id', 'content-type', 'location'}


@dataclass
class Credential:
    token: str = field(repr=False)
    source: str = 'configured'
    kind: str = 'configured_token'


def resolve_credential(environ=None, allow_cli=True):
    env = os.environ if environ is None else environ
    candidates = []
    for name in ['ISSUE_REVIEW_GITHUB_PAT', 'GH_TOKEN', 'GITHUB_TOKEN']:
        value = env.get(name, '').strip()
        if value:
            if any(c.isspace() for c in value):
                raise ValueError('Invalid credential format in '+name+'; correct local configuration without pasting secrets into chat')
            kind = 'personal_access_token' if name=='ISSUE_REVIEW_GITHUB_PAT' or value.startswith(('github_pat_', 'ghp_')) else 'configured_token'
            candidates.append(Credential(value, name, kind))
    if candidates:
        return next((c for c in candidates if c.source=='ISSUE_REVIEW_GITHUB_PAT'),
                    next((c for c in candidates if c.kind=='personal_access_token'), candidates[0]))
    if allow_cli and shutil.which('gh'):
        try:
            result = subprocess.run(['gh','auth','token','--hostname','github.com'],
                capture_output=True, text=True, timeout=5, env=dict(env))
            value = result.stdout.strip()
            if result.returncode==0 and value and not any(c.isspace() for c in value):
                return Credential(value, 'existing_gh_login',
                    'personal_access_token' if value.startswith(('github_pat_', 'ghp_')) else 'existing_authenticated_login')
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def validate_url(url):
    if not isinstance(url,str) or any(ord(c)<33 for c in url):
        raise ValueError('Invalid control characters or whitespace in API URL')
    parts = urlsplit(url)
    if parts.scheme!='https' or parts.netloc!='api.github.com' or parts.username or parts.password or parts.fragment:
        raise ValueError('Only HTTPS URLs on api.github.com are accepted')
    if not (parts.path=='/search/issues' or re.fullmatch(r'/(?:repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|repositories/[0-9]+)/(?:issues|pulls)(?:/[0-9]+(?:/(?:comments|timeline|files))?)?', parts.path)):
        raise ValueError('URL is outside the read-only issue/PR endpoints')
    if set(parse_qs(parts.query)) & {'access_token','token','client_secret'}:
        raise ValueError('Credentials cannot appear in URLs')
    return url


def safe_text(value, credential):
    return value.replace(credential.token, '[REDACTED]') if credential else value


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes
    url: str
    redacted: bool = False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


class GitHubClient:
    def __init__(self, credential, timeout=15, max_bytes=4*1024*1024, opener=None, before_request=None):
        if not credential:
            raise ValueError('GitHub authentication is required; anonymous bulk retrieval is disabled')
        self.credential, self.timeout, self.max_bytes = credential, timeout, max_bytes
        self.opener = opener or build_opener(NoRedirect())
        self.before_request=before_request

    def get(self, url, accept='application/vnd.github+json', cached=None):
        validate_url(url)
        original = url
        for redirect in range(6):
            headers = {'Authorization':'Bearer '+self.credential.token,
                       'Accept':accept, 'X-GitHub-Api-Version':API_VERSION,
                       'User-Agent':'issue-materials-review/1.0'}
            if cached and url==original:
                if cached.get('etag'):
                    headers['If-None-Match']=cached['etag']
                elif cached.get('last_modified'):
                    headers['If-Modified-Since']=cached['last_modified']
            request = Request(url, headers=headers, method='GET')
            if self.before_request:self.before_request(url)
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except HTTPError as exc:
                response = exc
            with response:
                status = response.status if hasattr(response,'status') else response.code
                safe_headers = {key.lower():safe_text(str(value),self.credential) for key,value in response.headers.items() if key.lower() in SAFE_HEADERS}
                body = response.read(self.max_bytes+1)
                if len(body)>self.max_bytes:
                    raise ValueError('Response exceeds the declared byte limit')
            if status in {301,302,303,307,308}:
                # Validate before another request; no credentials cross origins.
                from urllib.parse import urljoin
                url = validate_url(urljoin(url, safe_headers.get('location','')))
                continue
            sanitized=body.replace(self.credential.token.encode(), b'[REDACTED]')
            return Response(status, safe_headers, sanitized, url, sanitized!=body)
        raise ValueError('Redirect limit exceeded')


def number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def retry_decision(response, attempt, now):
    """No sleeps: return an absolute resume time and a classified outcome."""
    status, headers = response.status, response.headers
    try:
        message = json.loads(response.body).get('message','').lower()
    except (ValueError, AttributeError):
        message = ''
    if status==401:
        return dict(kind='needs_auth', reason='Configured GitHub credential was rejected; correct it and resume explicitly')
    limited = status==429 or (status==403 and (
        headers.get('x-ratelimit-remaining')=='0' or 'retry-after' in headers or
        'rate limit' in message or 'abuse' in message))
    if limited:
        deadlines=[]
        if 'retry-after' in headers:
            delay=number(headers['retry-after'], -1)
            if delay>=0:
                deadlines.append(now+delay)
            else:
                try:deadlines.append(parsedate_to_datetime(headers['retry-after']).timestamp())
                except (ValueError, TypeError, OverflowError):pass
        if headers.get('x-ratelimit-remaining')=='0':
            deadlines.append(number(headers.get('x-ratelimit-reset'),now+60))
        delay = max(deadlines,default=now+60*(2**max(0,attempt-1)))
        secondary = headers.get('x-ratelimit-remaining')!='0' or 'secondary' in message
        return dict(kind='rate_limit', resume_at=max(now+1,delay), global_pause=secondary,
                    reason='GitHub rate limit; saved for deferred retry')
    if status in {408,500,502,503,504}:
        return dict(kind='transient',resume_at=now+min(60,2**attempt),reason='Transient GitHub response '+str(status))
    if status in {200,304}:
        return dict(kind='success')
    return dict(kind='permanent',reason='GitHub HTTP '+str(status)+'; inspect permissions, resource or query instead of blindly retrying')
