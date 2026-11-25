# Template discovery and field meaning

First consult [template-cache.md](template-cache.md). Reuse an applicable cached
snapshot offline. The discovery guidance below applies to missing templates or an
explicit operator refresh. Resolve an uncached tracker through authoritative project/package metadata. Inspect its
chooser, repository templates and applicable organization defaults; honor separate
documentation trackers. Capture chooser config and relevant report/AI policies.
Policy text does not grant installation, disclosure or publishing permission.
After applicability is verified, freeze one revision for the run instead of chasing
main changes; use the stopping guidance in [upstream-search.md](upstream-search.md).

## Normalization

Preserve every GitHub form body element, including non-submitted markdown,
source order, optional source ID, raw attributes/validations and default values.
Missing IDs get stable local IDs (`element-2`), not fabricated official IDs.
Duplicate IDs are errors. Keep raw nodes so new schema properties are not lost.

Support input, textarea, dropdown, checkboxes, upload and markdown. Preserve
single/multiple dropdown options/default index, textarea render language, minimum
length and per-checkbox required flags. Defaults/placeholders are guidance, not
evidence of supplied values. Unknown controls/constraints remain visible and
block form-complete claims until handled/reviewed.

Markdown templates have one canonical body. Fill it while preserving its headings
and requirements. Derived section-copy views, if added, are not official fields.
A no-template fallback has `format: generic` and explicit discovery limitations.

## Repositories without an issue template

After inspecting applicable repository template locations, chooser configuration
and inherited organization defaults, an agent can record the ordinary GitHub
editor as `source.status: verified_editor`. Preserve the complete discovery
responses and a reason explaining why no applicable official template exists.
This status verifies the title/body route; it does not claim an official template.
An unresolved `unverified` generic draft stays outside the main copy list.

Freeze an empty body-model file and the discovery evidence under the run root:

```bash
python scripts/normalize_template.py --input /fresh/run/tracker/body.txt --format generic --id ordinary --output /fresh/run/templates/ordinary.json --artifact-root /fresh/run --url https://github.com/OWNER/REPO/issues/new --retrieved-at ACTUAL_DISCOVERY_TIME --verified-editor --editor-evidence /fresh/run/tracker/repository-locations.json --editor-evidence /fresh/run/tracker/organization-defaults.json --editor-reason 'Explain the inspected locations and applicable absence.'
```

The command performs no discovery or network access. The agent must establish
applicability before using it. Register each evidence reference in pack.sources.
The validator checks their frozen hashes, independently rederives the single body
field, and requires the canonical HTTPS `/issues/new` URL to match library.repository
without a query or fragment. Title and Issue body use the normal filling/review
workflow and fixed view. No project-specific fields or page are invented. Preserve
reporting policies and manual author tasks separately; an ordinary editor does not
waive them. Do not cache a synthetic model as an official template snapshot.

## Semantics

Assign each non-instruction field a purpose using both label and description:
problem, reproducer, expected, observed, environment, documentation_fix,
attestation, attachments, body, or other. Retain a short rationale. The parser
deliberately does not guess semantics from the project name.

- A doc-problem field holds the URL, mismatch and supporting evidence; long
  environment reports can go in a Markdown details block there.
- A documentation-fix field holds proposed wording/rules/links/replacement
  examples. Code or the word environment may be legitimate proposed doc edits;
  measured environment reports, run logs and search inventories are misplaced.
- A textarea with `render: shell` gets raw log text in its copy payload. The
  combined body adds its fence, rather than copying double fences into the form.
- Personal policy/review attestations remain unchecked until operator confirmation.
- Uploads are local attachment manifests for manual attachment, never invented
  uploaded URLs. Preserve missing-required-attachment blockers.

One library can require multiple report templates and arbitrary field counts.
Bind each issue to a template ID/revision so updated upstream templates do not
silently reorder previously generated content.

## Primary references

Recheck when schema support matters; the schema may evolve:

- https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms
- https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema
- https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository

Instruction markdown is preserved in cached/raw evidence but omitted from the main
copy page; that page links to the official form instead.
Existing-issue follow-ups use dedicated comment drafts; archive/deferred cards do
not need a template or invented not-applicable responses. See
[publication.md](publication.md) for content review and operator tasks.
