# Review pack contract: schema_version 2.0

Artifact paths are relative to the directory containing `pack.json`. A reference
is `{path, sha256}`. Freeze material/template/evidence files under that root. Helpers
reject missing files, changed hashes, absolute artifact paths and escaping symlinks.
Original external paths are informational metadata. Source references in a native
run result are relative to that result's directory; criterion/assessment references
are relative to the pack root.

Safe IDs: letters/digits/underscore/hyphen followed by those characters or dots,
maximum 160 characters; `.` and `..` are excluded. IDs are unique in their scope.

## Root and coverage ledger

Required root keys:

| Key | Meaning |
| --- | --- |
| schema_version | `"2.0"` for new packs |
| run_id | Safe unique ID; also isolates browser viewed-state |
| workflow_status | in_progress, awaiting_environment, ready_for_review |
| materials_path | Operator's original material path, informational |
| library | name, repository, resolution_reason; optional verified official_repositories |
| sources | Nonempty list of frozen artifact references |
| material_items | Individual supplied claims, not just files |
| dispositions | Exactly one assignment per material ID |
| templates | Selected normalized templates; can be empty for archives/comments only |
| issues | Nonempty issue/mechanism groups |
| requests | Actually asked operator questions, or an empty list |
| review_limitations | Optional limitations displayed in HTML |
| form_binding | Program-produced frozen compilation references; required by default service delivery for new 2.0 packs |

Material item: `{id, description, refs:["materials/file.json", ...]}`. Every path
belongs to sources. One file can contain many claims; one claim can cite many files.
Use stable IDs and meaningful descriptions without depending on a particular fuzzer.

Disposition: `{material_id, issue_id, decision, reason, merged_into?}`. Decisions:
retained, excluded, needs_evidence, merged. Every input material ID occurs exactly
once. For merged material, merged_into is another material ID assigned to the same
canonical group, with no cycles. A merged issue group separately uses finding's
merged_into issue ID. Missing/duplicate/unknown IDs, invalid targets and cycles fail.

Each issue's `material_ids` exactly matches ledger assignments; `material_refs`
includes their source files. Excluded groups have excluded/merged dispositions;
deferred groups have needs_evidence/merged dispositions. Files alone cannot prove
that all claims within them were enumerated; the agent must inspect and enumerate.

## Templates and fields

`normalize_template.py` produces `{id,name,format,source,fields,...}`. Source has the
raw snapshot reference, URL, revision when available, retrieval date and status:
official, unverified or verified_editor. Official means a template's identity and applicability were verified.
A non-official/generic source needs an explicit limitation; never fabricate provenance.

`verified_editor` is reserved for `format: generic` after reasoned discovery finds
no applicable issue template. Its source URL is the canonical
`https://github.com/OWNER/REPO/issues/new` matching library.repository, without query
or fragment. Source also requires the frozen body-model reference, retrieval date,
limitation and `editor_discovery: {kind: "github_blank_issue", reason, evidence}`.
Evidence is a nonempty list of frozen `{path,sha256}` references declared in sources.
The validator checks those artifacts and the normalized single body field; compile
preserves them in the self-contained pack. The agent establishes semantic absence
and applicability from complete discovery responses. This status does not describe
an official template. Unverified generic models remain held; labeling a generic
model official does not make it an eligible template route. See template-fields.md.

Assign each non-markdown field a purpose from its actual label and description:
problem, reproducer, expected, observed, environment, documentation_fix, attestation,
attachments, body, other. The normalizer deliberately leaves semantics unassigned.
Annotate purpose/purpose_reason or pass --purpose-map; preserve all normalized IDs,
labels, descriptions, types, raw nodes, order/options/defaults/constraints.

The validator independently compares normalized fields to the frozen snapshot.
An absent existing YAML parser creates a visible manual-check blocker; no package
is installed. JSON snapshots need no parser dependency. Unknown controls/rules
remain visible blockers. Non-submitted markdown instructions remain preserved,
but appear outside the HTML input selector. Markdown templates have one body field.

## Issue and action

Required issue keys: id, title, kind, material_ids, material_refs, finding,
reproduction, upstream, action. New issue candidates also need template_id,
responses, submission_todos and publication. Optional operator_note is a concise
author instruction for the official page, not copied report text.

`finding: {status,reason,merged_into?}` uses needs_review, supported, excluded,
merged, deferred. Supported is not human confirmation or novelty. Issue merges
must resolve to a retained group without cycles.

`action: {type,reason,target_url?,comment?}`:

- new_issue: use a selected template and official response fields.
- existing_issue_comment: target_url must identify an inspected official issue;
  comment is `{value:"dedicated comment draft"}`. No new-issue responses.
- needs_evidence/no_submission: dedicated evidence/disposition cards; no template
  or fake responses required. Excluded/merged records use no_submission.

A known same_issue cannot use new_issue. Pending comment content is a visible form
blocker, not invalid evidence. Never silently turn an existing report into novelty.

## Reproduction, criteria and assessments

Reproduction: `{status,reason,environment_selection,criterion_ref?,runs?,selected_run_ids?,operator_decision?}`.
Statuses: reproduced, not_reproduced, inconclusive, awaiting_environment,
insufficient_material, not_applicable, skipped_by_operator.
Environment selection has status matched/missing/not_needed/not_checked/operator_skipped
and reason. A missing match must actively await an operator environment question;
do not execute dependent cases. Docs-only evidence can use not_needed/not_applicable.

Criterion is a frozen JSON artifact:

```json
{
  "defined_at": "2026-11-23T00:00:00+00:00",
  "symptom": "Concrete condition that distinguishes the claimed observation",
  "expected_behavior": "Expected result, distinct from the suspect implementation",
  "basis": [{"path":"criteria/reference.txt","sha256":"actual digest"}],
  "metrics": [{"name":"event_net_delta","unit":"bytes","definition":"Net memory change reported for one profiler event","scope":"Maximum event within the bounded CPU profile, excluding peak/total memory claims"}]
}
```

Dates above are illustrative. Record actual definition times before execution.
Metrics may be empty; when used, name/unit/definition/scope are all required.
Hashes and chronology detect inconsistencies, not independently prove actual events.

Run entry: `{id, result:{path,sha256}, assessment:{path,sha256}}`. Keep historical
attempts and select final IDs explicitly using selected_run_ids. Each result records
execution_performed, execution_status, started_at, command array, environment and
hashed source/stdout/stderr. Environment identifies a verified existing executable,
library name/version and selection reason. Record build/backend/device, cwd, exit
status and limits. `run_case.py` is optional; equivalent project-native records work.

Assessment is a separate JSON artifact:

```json
{
  "run_id":"local-v2",
  "result_sha256":"digest of this exact result JSON",
  "source_sha256":"digest of this run's source",
  "criterion_ref":{"path":"criteria/v2.json","sha256":"actual digest"},
  "assessed_at":"2026-11-23T00:02:00+00:00",
  "outcome":"observed",
  "reason":"Interpretation of cited output against the frozen criterion",
  "evidence":[{"path":"attempts/local-v2/stdout.txt","sha256":"actual digest"}]
}
```

Outcomes: observed, not_observed, inconclusive, not_assessed. Judged outcomes cite
that run's own stdout/stderr; setup failures cannot judge the symptom. Every run
assessment binds its exact result/source and prior criterion; selected assessments
use the issue's current criterion. Reproduced requires an observed selected run;
not_reproduced requires every selected outcome to be not_observed. Mixed builds
remain individually visible. Historical probes may use earlier frozen criteria.
The helper checks associations, not whether the agent's interpretation is correct.

`publication.reproducer: {source,run_ids,field_id,language?}` binds the submitted
program to selected runs with byte-identical source hashes. Its text must occur in
the specified response field, or action.comment when field_id is comment. Shortening
or editing code requires another execution, not recycled output.

## Upstream dimensions and PR evidence

Upstream has relation, coverage, reason; searched claims also have checked_at,
scope, stop_reason, gaps, queries and/or matches. Relations: unknown, same_issue,
same_mechanism, related, no_match_in_scope, not_applicable. Coverage:
complete_in_scope, partial, not_searched, not_applicable. Partial needs explicit
gaps; complete cannot contain incomplete queries. Positive matches survive partial
coverage. A negative within the inspected scope never proves novelty.

Query: `{query,checked_at,complete,gaps,evidence:{path,sha256}}`.
Match: `{url,title,kind,relation,reason,trigger_comparison,claim_comparison,evidence:[refs],mechanism_relation?}`.
URLs belong to the verified tracker list. Compare the requested correction separately
from a shared runtime mechanism. Overall same_issue/same_mechanism needs a corresponding
claim-level match. Avoid deduplicating a doc request solely from a runtime discussion.

PR matches also require:

- tracker_state: `{state:"open|closed|unknown",merged:true|false|null,evidence:[refs]}`.
- fix_scope: `{covers_claim:"yes|no|partial|unknown",summary,evidence:[refs]}`.
- release_inclusion: `{status:"included|not_included|unknown|not_applicable",version?,evidence:[refs]}`.

Known state/merge and fix-coverage facts need evidence; release inclusion/exclusion
needs version and evidence. Unknown is allowed. Closed, merged, effective coverage
and release containment are never inferred from one another.

The [retrieval queue](retrieval-queue.md) supplies hashed query/detail/patch
evidence after reproduction. Its succeeded/partial states describe retrieval only;
the agent still fills the relation, comparison and report action. Register imported
export files with root-relative paths. Queue output never edits this pack or its
compiled bindings; source revisions are explicit and preserve previous history.

## Responses, content review and submission tasks

Response: `{value,content_tags?,verbatim?,confirmed_by_operator?,attachments?}`.
Text controls use strings; input is one line. Single dropdown uses an allowed string,
multiple dropdown/checkboxes use allowed-label lists. Upload uses local attachment
names with matching hashed attachments (name plus artifact ref). Do not invent uploads.
Markdown instruction nodes have no response. Required empty values remain form
blockers; a structurally valid draft can still be rendered with them.

Nonempty personal confirmations require confirmed_by_operator true. Field render
language gets raw text; combined body adds code fences. Each verbatim artifact's
complete decoded text must appear in the field. Only declare verbatim where actual
submission requirements need it; raw evidence also remains independently accessible.
Content tags identify documentation_change, problem_description, environment_report,
execution_log, reproducer, upstream_inventory. Documentation fixes reject evidence dumps.

`publication.quality_review` has status pending/reviewed. Reviewed records have
reviewer agent/human, reviewed_at, notes and exactly these check IDs:
minimal_reproducer, concise_results, field_allocation, no_placeholders. Each check
has status pass/needs_work/not_applicable and reason. Human review requires actual
operator confirmation. A minimal-reproducer pass for a reproduced report requires
a bound publication program. Checks record semantic review; they are not proof of
minimality. Known internal placeholder patterns generate visible content work.

Submission todo: `{id,kind,label,state,basis?,confirmed_by_operator?}`. State is
pending/done/not_applicable. Policy/human_commentary tasks cite repository-specific
policy snapshot references in basis. Personal policy/commentary/attestation/attachment
tasks cannot be marked done without actual operator confirmation. Do not hardcode
one repository's author requirements. Pending todos do not block local completion.

## Operator environment requests

A real asked request has id, type environment, issue_ids, state pending, asked_at,
requirements, discovery_notes and actual question. issue_ids must exist. Answered
or skipped requests record operator_response; explicit execution skip also records
reproduction.operator_decision. Pending requests forbid ready_for_review and require
--allow-pending to render an incomplete draft. They cannot be replaced by a todo.

## Assembly and delivery

Use `assemble_pack.py --pack PATH COMMAND --help` to inspect a subcommand:

- init: fresh 2.0 skeleton with selected normalized templates and resolved identity.
- freeze --source FILE --as RELATIVE: copy one immutable artifact, hash/register it.
- material --id ID --source RELATIVE (repeatable) --description TEXT: enumerate claims.
- issue --id ID --title TEXT --kind TYPE --action TYPE --reason TEXT [--template-id ID].
- assign --material-id ID --issue-id ID --decision TYPE --reason TEXT [--merged-into MATERIAL].
- run --issue-id ID --id RUN --result RELATIVE --assessment RELATIVE [--select]: register
  existing records; never execute or infer an outcome. Preserve result-relative files.
- response --issue-id ID --field-id ID --file TEXTFILE: exact text response/comment.

The helper handles hashes/mappings/atomic pack updates. It intentionally leaves
criteria, assessment reasoning, upstream comparisons, quality review, tasks and final
status for analysis. Validate after editing these fields; a skeleton is not a report.

assemble_pack.py builds investigation source data. For normal publication, use
[filling.md](filling.md): fill_fields.py prepare generates readonly definitions and
value-only answers; review-plan binds an agent content review to that exact version;
compile merges and freezes a self-contained pack. Do not edit compiled packs or use
the source-pack response helper to bypass the generated answer contract.

`form_binding` has schema_version `"1.0"`, recorded_at, and six `{path,sha256}`
references: base, contract, schema, answers, review, layout. These snapshots are
also registered in sources. The validator reconstructs the field contract from base,
rechecks the generated schema and exact answer/review hashes, reapplies values and
review to base, and requires the complete resulting pack to match. It preserves
response metadata and investigation data. The bound layout is historical provenance;
the service independently checks the current approved installed view. Raw source
packs remain valid investigation data, but the service refuses unbound new 2.0
delivery unless historical compatibility is explicitly selected.

The default delivery is the local service in [service.md](service.md), started with
serve_review.py on the compiled pack and a stable SQLite state path. Its main page contains only prepared
official copy payloads, fixed selection values and navigation. Cached templates are
exported as frozen sources; cache_origin records their project/revision without
pretending a fresh download occurred.

`review_pack.py` on the compiled pack remains an optional static backup. It validates without executing
cases, then writes a fresh directory:
REVIEW.html, issue_cards.json, validation.json, and issues/<id>/{title.txt,body.md,<field-id>.txt}.
Archives have no fake response files. Report validity, form completeness, semantic
review and operator todo states are reported separately; copy buttons do not certify
bugs or human approval. Markdown preview is a safe subset; raw text is canonical.

Legacy 1.0 input remains read-only compatible with warnings. Its missing material
ledger/criterion bindings are not certified. Suggested actions are labelled inferred;
known-existing records need a dedicated comment before report copying. Legacy raw
prose is retained for review. Rerender into a new directory, never overwrite history.

An explicitly supplied legacy follow-up comment is accepted only for the inspected
issue target inferred from the saved comparisons. This does not certify legacy
criterion/coverage bindings as schema 2.0.
