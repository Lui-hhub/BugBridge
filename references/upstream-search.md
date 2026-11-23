# Bounded official discovery and upstream comparison

## Template discovery

Consult [template-cache.md](template-cache.md) first. Cached applicable forms are
reused without fetching or checking main. The following discovery steps apply only
to misses or an explicit refresh. Live issue/PR searches remain independent.

Verify the canonical repository/tracker from official project links and package
metadata. Inspect chooser config, repository templates, inherited organization
forms and relevant contribution/AI policies. Resolve material ambiguity before
choosing a tracker. Policies are evidence of reporting requirements, not execution
or publication permissions.

After verifying applicability, freeze one commit/revision and its templates and
policy snapshots for the run. Record URL, revision when resolvable, retrieval time
and hashes. Later main changes do not trigger automatic recollection. Refresh only
when applicability is wrong, the operator requests it, or the chosen template is
unavailable; record why and retain earlier snapshots.

## Start after evidence; keep retrieval independent

Do not add a preliminary issue/PR search before runtime reproduction. A runtime
claim becomes eligible after the symptom is observed against its frozen criterion
and its selected run/source/assessment are bound. Setup failures, inconclusive and
not-reproduced claims do not trigger bulk deduplication searches. An operator may
explicitly request a separate known-issue investigation; do not make it a default
stage or use title matches to dismiss an unverified claim. For documentation-only
claims, establish the actual documentation mismatch/basis, then search without
requiring runtime execution. Merely selecting a documentation template is not proof.

Process eligible claims individually; do not wait for all reproductions. Retrieval
can run alongside other reproduction work, template parsing and factual report
composition. Keep GitHub HTTP requests in one serial queue for the same account/host
even when local tasks run concurrently. Changing tokens for the same account is not
a new independent budget. Do not start retrieval from browser GET/refresh requests.

Bind queued work to the claim/requested correction, frozen criterion and selected
run/source revision. When the evidence changes, reassess old hits and schedule only
needed additional queries; an old no-match cannot automatically cover a new trigger.
Persist progress and a resume time when delayed. Queueing/retrieval success is not a
semantic duplicate decision. Use scripts/upstream_queue.py and its authenticated
request layer; read [retrieval-queue.md](retrieval-queue.md) for plans, commands,
durable state and evidence export. Its worker is separate from serve_review.py;
the web server's --background flag only detaches the review server.

Before fill_fields.py prepare, settle the action per claim: new-issue candidate,
comment on an inspected issue, no submission, or needs_evidence. Pending essential
search work stays needs_evidence and does not become a new-issue copy draft. Other
claims can proceed. A sufficient inspected positive match can support a comment
with partial coverage. Never interpret missing credentials or failed requests as
no_match_in_scope. Late results require a fresh source/filling/compiled revision;
do not patch frozen reports. The human copy-only view does not gain queue controls.

## GitHub authentication and official request practices

Prefer the operator's already configured **personal access token (PAT)**. For a new
PAT, prefer fine-grained over classic where supported and use only the permissions
needed by the actual read endpoints. Read a securely configured value such as
GH_TOKEN/GITHUB_TOKEN, or an existing CLI configuration known to use a PAT; do not
assume any existing CLI login is necessarily a personal token. Never print tokens,
put them in URLs/commands, persist them in task/queue/evidence files or expose them
in the frontend. Use the Authorization header for the verified GitHub API origin.
Never forward credentials to another origin during redirects. Other authenticated
methods are a fallback when actually selected/available, not a reason to skip a
configured PAT.

If no PAT is configured but an existing usable authenticated method is available,
record and explain that fallback. A configured invalid PAT needs a visible correction
notice and no repeated attempts with that credential. If no usable authentication
exists, actively ask where/how the operator has configured it or which authenticated
method to use; do not ask for the secret in the conversation. Continue independent
local work. Do not silently launch an anonymous bulk search or loop with invalid credentials.
An explicitly requested limited anonymous lookup must record its access limitations.

Follow GitHub's [REST request practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api),
[authentication guidance](https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api)
and [rate-limit rules](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).
These are request-handling guidance, not authority to publish or install software.
For non-GitHub trackers, use their own official authentication/request practices;
never send a GitHub token to a different service.

- Request only necessary data with stable parameters. Deduplicate identical queries
  and detail reads across claims. Prefer repository-scoped searches; inspect past
  issues and relevant PRs. Follow returned API URLs and Link pagination headers;
  handle documented redirects rather than guessing page/resource URLs. This is
  bounded on-demand retrieval, not continuous polling; no webhook setup is needed
  for a one-off read-only report-preparation job.
- Serialize network requests and track the separate search/core resource budgets
  using authoritative x-ratelimit-* response headers. Do not hardcode one hourly
  quota for every endpoint, exhaust the maximum burst allowance or repeatedly poll
  GET /rate_limit. Its occasional overview can supplement the response headers.
- On rate limiting, honor Retry-After; when remaining is zero also wait until
  x-ratelimit-reset. Honor the later deadline when both apply. For a secondary limit
  without an explicit later deadline, wait at least one minute, then use increasing
  exponential backoff if it persists. Persist next-eligible time and return to
  independent work; do not keep a foreground workflow sleeping for long periods.
  Declare a finite retry/time budget before retrieval and stop with gaps when it
  is exhausted; GitHub requires bounded retries but does not mandate one universal
  retry count. Record attempt, status, safe response metadata and resume/stop reason.
- Distinguish 401/permission failures, query-validation errors, unavailable resources
  and transient server/network failures from actual primary/secondary rate limits.
  Fix invalid requests or request credential correction; do not blindly retry every
  403/404/422, rotate accounts/tokens or switch interfaces to evade a rate limit.
  Repeated 4xx/5xx must produce a visible retrieval failure rather than an empty
  successful search result.
- Cache canonical payloads with retrieval/check time, URL/parameters/representation,
  hashes and ETag or Last-Modified where supported. On necessary revalidation use
  If-None-Match/If-Modified-Since; a 304 reuses the verified cached body, never an
  empty new body. Authorized conditional requests returning 304 do not consume
  primary quota; they are not permission for unlimited polling. Search caches need
  time/scope limits and key-state rechecks; template cache reuse remains unchanged.

Record result caps, pagination gaps and incomplete_results. Neither an HTTP 200 nor
an authenticated request proves search completeness. See the official
[search endpoint documentation](https://docs.github.com/en/rest/search/search).
Retain only safe request/response metadata in evidence; remove Authorization,
cookies, credential values and other secrets. Publishing issues/comments remains
outside this workflow; do not introduce mutating requests while performing searches.

## Search order and stopping

Start with the most specific symptom: API/alias, relevant option, shape/dtype or
backend, diagnostic/error wording. Expand toward shared mechanisms only as needed.
Include past issues and relevant PRs, not only open issues.

A reliable same-issue match ends broad novelty hunting for that claim. Inspect its
requested remedy, actual trigger, duplicate routing, current state and linked patch
scope. Resolve any material contradiction or nearby fix claim. A different symptom
or remedy may still require targeted follow-up.

When an interface reports explicit quota/rate limiting, defer requests according to
the official rules above and the declared budget. Use already frozen evidence while
waiting; do not launch a new anonymous/page-scraping route to evade the limit. After
an actual missing/unsupported endpoint, an available official page/patch may supply
needed facts with its own recorded scope/access conditions. Record uninspected pages,
failed detail/patch requests, result caps and date; do not fabricate completeness.
A template recommendation does not authorize installing another build to test fixes.

Record `scope`, `stop_reason` and remaining `gaps`. Stop when the planned bounded
scope is adequately inspected, a match plus scope/status comparison suffices for the
action, or access failure prevents useful progress. Partial search is a valid local
review result. Preserve positive matches despite gaps.

## Independent dimensions

- `relation`: current report request versus upstream reports: same_issue,
  same_mechanism, related, no_match_in_scope, unknown, not_applicable.
- `coverage`: complete_in_scope, partial, not_searched, not_applicable. Complete
  refers only to the declared bounded scope, never all conceivable reports.
- `action.type`: new_issue, existing_issue_comment, needs_evidence, no_submission.

Each match compares both `trigger_comparison` and `claim_comparison`. Optional
`mechanism_relation` can retain shared runtime behavior when the requested correction
is different. An issue about accepting an empty reduction is not by itself a duplicate
of a request to document its exception class. A positive match with partial search
keeps both facts; do not collapse one into the other. A no-match-in-scope relation
can also accompany partial coverage without implying novelty.

For PRs save structured `tracker_state` (state and merged), `fix_scope` (covers_claim
and inspected changes), and `release_inclusion` independently, with frozen evidence.
Closed can mean unmerged/reverted; a merged patch can cover another path; release
inclusion requires version/ancestry evidence. Unknown facts stay unknown.

No helper makes a semantic duplicate or fix decision from titles, nonempty prose,
GitHub state, version ordering or absence of search results. Analyze sources and
surface uncertainty for human review.
