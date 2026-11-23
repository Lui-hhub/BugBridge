# Template definitions, answers and the fixed view

The normal path is:

`official snapshot → normalized template → frozen field contract → answer values → reviewed compiled pack → fixed service view`

Changing materials or libraries changes data in this chain. It does not require
editing HTML, CSS, JavaScript or renderer code. The agent still investigates the
claims and writes report content; the program owns field structure and merging.

## Prepare the source pack

Reuse cached applicable templates offline; see [template-cache.md](template-cache.md).
For a new template, preserve its official raw snapshot and use the normalizer/cache
importer. Annotate field purposes from its own descriptions without changing parsed
labels/types/order/options. Do not invent structure based on another library.

Use [pack-contract.md](pack-contract.md) and assemble_pack.py for the investigation
source pack: materials ledger, selected templates, dispositions, environment records,
criteria/runs/assessments, upstream matches, actions and operator tasks. Set response
metadata such as verbatim evidence, content_tags and publication.reproducer bindings
here. Text may be empty until filling. Preparation validates evidence and definitions
but does not treat a draft as ready, execute code or search the network.

Upstream retrieval starts after successful runtime reproduction (or established
documentation evidence), without a preliminary search. It can overlap other local
work; see [upstream-search.md](upstream-search.md) for PAT-first authentication and
official request handling. Resolve each candidate's report action before preparation.
Pending essential searches stay needs_evidence; other candidates can proceed.
Late search results require a new source/filling revision rather than edits to a
frozen contract or compiled pack.

```bash
python scripts/layout_guard.py check
python scripts/fill_fields.py prepare --pack /fresh/run/pack.json --output-dir /fresh/run/filling
```

This creates four files without overwriting an existing directory:

| File | Owner and use |
| --- | --- |
| contract.json | Program-generated, readonly field definitions and official guidance, bound to the exact source pack |
| answers.schema.json | Program-generated JSON Schema with exact IDs/types/options and no additional properties |
| answers.json | Agent-editable answer values only |
| layout.lock.json | Program-frozen copy of the approved view checksums |

The contract preserves field source order and separates instructions/manual controls
from answer slots. Input/textarea/dropdown become typed values. Checkboxes, uploads
and fields identified as personal attestations remain manual on the official page;
the agent cannot manufacture those answers. Existing issue actions receive one
comment slot and a frozen target, without a new issue title or imitation form.
Markdown templates receive a title and one complete body slot with original guidance.

## Fill values and review that version

A native-form answer has this shape; real IDs and allowed selections come only from
the generated contract:

```json
{
  "schema_version": "1.0",
  "contract_sha256": "generated digest",
  "issues": {
    "case-1": {
      "title": "Concise report title",
      "responses": {
        "problem": "Concrete condition, expected behavior and observed result.",
        "backend": "one exact official option"
      }
    }
  }
}
```

Do not add labels, template text, page settings, options, response wrappers or
unknown keys. Do not delete optional slots: use null or an empty string for optional
text; use null or an allowed selection list for an optional multiple dropdown.
The renderer omits empty optional copy steps. A dropdown accepts only original
option labels; inputs and titles are single-line. Answer-object key order has no
effect on the presentation order. Duplicate JSON keys are rejected.

Write concise content following [publication.md](publication.md). Official render
fields receive raw code/log text, without extra fences. Required full tracebacks and
environment reports must retain their complete verbatim evidence. Code changes still
require a new execution in a matching existing environment before binding that code.

```bash
python scripts/fill_fields.py review-plan --pack /fresh/run/pack.json --contract /fresh/run/filling/contract.json --answers /fresh/run/filling/answers.json --output /fresh/run/filling/review.json
```

Perform the content review; complete notes and the fixed check outcomes in review.json.
Use reviewed/agent and reasoned pass, needs_work or not_applicable values. Never copy
a blanket pass without reviewing the text or claim human sign-off. The review file
is bound to the exact contract and answer bytes. Editing/reformatting answers requires
a new review file; preserve previous revisions. needs_work can remain in a compiled
pack, but that record stays out of the main copy list until resolved.

Source pack/evidence/template changes require a new filling directory and preparation;
changing only answers requires a new review. Never repair hashes or edit readonly
contracts to reuse stale material. Missing execution environments still require the
active operator question in SKILL.md, independently of local field preparation.

## Compile and serve

```bash
python scripts/fill_fields.py compile --pack /fresh/run/pack.json --contract /fresh/run/filling/contract.json --answers /fresh/run/filling/answers.json --review /fresh/run/filling/review.json --output-dir /fresh/run/compiled
python scripts/serve_review.py /fresh/run/compiled/pack.json --state /stable/state/review.sqlite3 --port 8765 --background
```

Compilation rederives the contract from the frozen source, checks exact IDs and
value types, then merges only title/text/selection values and the reviewed quality
record. Evidence metadata, field definitions, decisions and links cannot be changed
through answers. The full pack validator rechecks verbatim requirements and selected
executed-code bindings. It then copies verified artifacts and all six compilation
inputs into a fresh self-contained output. pack.json receives a form_binding and
field-validation.json records validation results. Originals are not overwritten;
later edits to working answers do not change this published local snapshot.

The service requires compiled bindings for new schema 2.0 packs. An invalid update
keeps the previous validated view. Explicit --allow-unbound-draft exists solely for
historical uncompiled 2.0 data, not to bypass this workflow. Legacy 1.0 is still
readable with its existing evidence limitations. --allow-pending supports an actual
pending environment interaction; it does not imply the operator answered.

Keep the stable SQLite path across revisions. Copy keys depend on report scope,
destination/field and exact payload, so unchanged fields retain copy progress. The
human page adds no local editing, template controls or required confirmations;
see [service.md](service.md).

## Protect the view

assets/view-manifest.json is the approved baseline. layout_guard.py check verifies
the exact packaged view file set. The prepare/compile steps and service startup/page
delivery also check it; per-task locks catch changes during preparation. Ordinary
jobs must not edit frontend files, helper code or this baseline. There is no command
to regenerate a baseline to clear a failed job. Only explicit UI maintenance may
update the approved assets and baseline after review and tests.

This guards against accidental workflow changes; it is not OS-level access control.
Compiled historical packs preserve their original view snapshot as provenance, while
the running service checks the currently approved installed assets. This lets an
explicitly reviewed future UI update still read older packs.

Unsupported official controls/constraints stop preparation with an explanation.
Preserve the source and request a separately scoped generic-tool enhancement instead
of rewriting a page for that library. Current tests cover PT and TF cached forms,
Markdown bodies, existing comments and a synthetic unrelated form through one renderer.
