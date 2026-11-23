# Offline template cache and examples

The cache is assets/template-cache/catalog.json, with immutable raw and normalized
files under each project/revision. PyTorch aliases: pt, torch, pytorch. TensorFlow
aliases: tf, tensorflow. Repository names also resolve. Cached source revision/date
is retained honestly; reuse is not an assertion that upstream has never changed.

## Hit: no fetching

```bash
python scripts/template_cache.py list --library tf
python scripts/template_cache.py export --library tf --template tensorflow_issue_template --pack-root /fresh/run
```

Export copies the exact cached raw snapshot to tracker/templates/<project>/<revision>/
and writes templates/<id>.normalized.json. It retains provenance and purposes.
Register these frozen files as pack sources. The helper validates cached hashes,
refuses output overwrites and has no network implementation. A matching cached
form must not be refetched just to repeat discovery or check for updates. Requests
for current duplicate/fix investigation still search official issues and PRs.

## Examples to learn from

Use the catalog's normalized paths to read relevant examples only:

- PyTorch bug-report: two submitted textareas plus the common GitHub title. Combine
  explanation/minimal code/actual result in the bug field; full diagnostics in Versions.
- PyTorch documentation: two textareas. Problem evidence and concrete proposed wording
  belong in different fields. Diagnostic collectors do not belong in the suggestion.
- PyTorch pt2-bug-report: separate bug, errors and Versions fields. Compiler-specific
  preparation instructions are agent responsibilities, not extra local controls.
- TensorFlow tensorflow_issue_template: 15 controls, including four dropdowns. Prepare
  exact selected values from evidence. Code/log fields have render=shell: copy raw
  content, not duplicate code fences. A Markdown template's headings are body sections,
  not separate native GitHub controls.
- TensorFlow tflite-other: raw .md is preserved but lacks proper template frontmatter
  and contains flattened YAML-like prose. Its entry is needs_review; export refuses.
  Do not reinterpret those strings as verified official controls. Resolve a valid
  official route for the actual claim instead.

Cached field-purpose annotations and cache_notes are examples of the reasoning;
never assign a new project's fields solely by similarity or library name.

## Miss: discover and add only missing files

Verify the tracker, chooser/defaults and applicability from official sources. Save
new raw files with source revision and actual retrieval time. Use an existing YAML
parser for import, or JSON equivalent inputs where applicable. An optional annotations
JSON maps filename to purposes keyed by normalized field ID, with purpose/reason and
notes. Local template IDs can be disambiguated with annotations[filename].id.

```bash
python scripts/template_cache.py import --library newlib --repository https://github.com/owner/repo --revision ACTUAL_REVISION --retrieved-at ACTUAL_TIME --directory /frozen/missing-templates --alias package-name --annotations /frozen/annotations.json --verified
```

--verified records the agent's prior identity/applicability verification, not merely
a successful download. Unannotated submitted purposes remain unassigned until the
agent reads the descriptions. Malformed files remain flagged raw examples rather
than silently dropped or presented as valid controls.

For a new template in an already cached project, provide only missing files and use
--add-missing. Existing entries, raw snapshots, aliases and per-template revisions
remain intact. Redundant incoming known filenames are ignored. A per-template source
revision governs export even when a project has cached files from multiple revisions.

Normal runs never refresh cached templates. An explicit operator refresh can use
--refresh with a new revision; earlier frozen files remain preserved and templates
not supplied in that update stay available. Do not overwrite an existing revision.
