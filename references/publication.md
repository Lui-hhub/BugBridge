# Publication content and operator review

The pack retains investigation evidence, a publication draft, and the operator's
remaining tasks. Their contents and completion states are different.

The agent writes the values in the program-generated answers.json from
[filling.md](filling.md), then reviews that exact version and compiles it. Template
definitions and the frontend remain unchanged. Evidence metadata and action/target
decisions belong in the investigation source pack before preparation.

## Small examples with attributable results

Prepare a self-contained publication program that retains the necessary imports,
inputs, options and controls for the claimed behavior. Diagnostic matrices, repeated
sampling, JSON serialization, complete docstring dumps and machine-local launch
commands belong in evidence unless necessary to demonstrate this particular claim.
Do not impose a fixed line cap or delete controls simply to shorten code.

If a collector was the only executed program, shortening it creates another source
version. Execute that version in an already matching local environment, freeze it,
assess its actual output and select its run IDs. Bind `publication.reproducer` to the
executed source and the exact field/comment containing it. Do not attach collector
output to code never executed. If execution is explicitly skipped or unavailable,
label proposed code as unverified and leave the publication quality check unfinished.

For runtime claims, the main text normally establishes the concrete condition,
expected behavior and basis, small example, and succinct observed result. Retain the
full relevant exception traceback when requested. Full diagnostic/raw outputs remain
in the evidence area; include them in submission fields when required or useful,
with long supporting material folded where the target form permits it.

Keep full requested collect_env/equivalent reports in environment fields. A doc form
without that field can place necessary diagnostics in a supporting details block in
the problem field. The proposed-doc-change field contains wording/examples/links,
without a dump of collection logs. Internal audit narration and absolute workspace
commands should not crowd the initial explanation.

## Content review and tasks

Before claiming content was reviewed, complete the generated review.json, which
compilation maps to `publication.quality_review`: minimal
reproducer, concise results, field allocation, no internal placeholders. Each has a
reasoned pass/needs_work/not_applicable outcome. An agent review is labelled agent;
it never supplies human sign-off. A pass records a review, not machine proof that the
program is minimal or the report correct. The helper checks bindings and explicit
states; meaning still requires analysis and human review.

Do not write unfilled internal author instructions into copyable prose. Store them
as `submission_todos`. Extract target-repository requirements from its frozen policies
and cite the basis; one repository's human-commentary requirement is not universal.
An operator-confirmed policy/attestation/attachment task may be marked done, while an
agent must not invent the human's explanation, responsibility statement or uploaded
file. Required form declarations remain unchecked without actual confirmation.

Unfinished content-quality work stays in investigation records until the draft is
ready to copy. Pending personal submission tasks can become a concise operator_note
for completion on the official page; do not create a local task board.
They do not require permission to finish local preparation and do not need a blocking
operator question. Missing execution environments still follow the mandatory question
and waiting rule in SKILL.md.

## Actions and copying

- new_issue: actual selected official fields, individually copyable as drafts.
- existing_issue_comment: inspected target issue and a concise dedicated comment;
  no imitation of a new issue's required fields.
- needs_evidence/no_submission: dedicated cards with reasoning, material mapping
  and evidence; no fake not-applicable form answers or report-copy controls.

Copying original evidence source remains available even for excluded/archived records.
The UI labels this separately from copying a report. Full body and field copying use
the exact exported strings; switching to rendered preview never changes the payload.
The main service page links to the official form instead of displaying instruction
blocks. It has no input-field selector or viewed checkbox. Successful-copy progress
and report selection save automatically; see [service.md](service.md).

The standalone preview supports a safe Markdown subset (headings, lists, links,
fenced code, simple tables and details). It creates DOM nodes without interpreting
arbitrary HTML. Unsupported syntax remains readable literal text. Raw text is the
canonical payload for official forms; final rendering can be checked in that form.
