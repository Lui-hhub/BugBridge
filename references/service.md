# Local review service

The frontend is a copy list, not an investigation console. The service uses Python's
HTTP server and SQLite without installing dependencies or requiring a frontend build.

## Start and handoff

```bash
python scripts/serve_review.py /fresh/run/compiled/pack.json --state /stable/state/review.sqlite3 --port 8765 --background
```

Prepare and compile new reports through [filling.md](filling.md). Startup validates
the approved view checksums, frozen form_binding and all declared evidence. New 2.0
packs without compiled bindings are refused by default. --allow-unbound-draft is
explicit historical compatibility, not the new-report path; 1.0 remains readable.
The page assets are checked again on delivery; an accidental edit returns an error
instead of serving an unreviewed layout. Do not replace the approved baseline to
clear a mismatch during an ordinary preparation job.

New 2.0 reports must complete their content
review before entering the main list. Internal placeholders and missing required
copy content are held back; exclusions and held records remain under /records.
Personal confirmations/attachments are completed on the official form, not a second
local form. A concise operator_note can identify the actual remaining author step.

Use an existing interpreter with the YAML parser needed to recheck YAML snapshots.
--allow-pending permits an explicitly incomplete service during actual pending
questions; it does not authorize skipping environment interaction. Startup prints
the actual URL, state path and available draft count. Port 0 selects an available
port, useful for tests; return the actual printed port. --background detaches it from the launching terminal and returns its PID/log
paths, so completion of a tool command does not stop the service. Omit the flag
for a foreground development process. Keep the process alive for handoff. Binding is local loopback; no hosting/deployment/account setup is needed.

Default review state is .local/state/review.sqlite3 inside this checkout. It is
ignored by Git and independent of the invoking shell's working directory. A new
clone starts with fresh review progress. The GitHub retrieval queue separately uses
shared machine-level state to coordinate request limits across clones.
An explicit --state should be stable across rerenders and report output directories.
Restart with the same database to restore progress. Database state is separate from
frozen evidence and does not modify original materials.

## Human view

- A compact issue list, one selected report, and an official form/issue link.
- Title plus each nonempty official text field, in source order, each with Copy.
- Native GitHub dropdowns appear as prepared fixed values with a short instruction
  to select that value on GitHub. No local dropdown or confirmation duplicates.
- Markdown templates give Title and one complete body; existing-issue comments give
  one comment. No extra combined-body action for native multi-field forms.
- Render prose/code for reading, but copy the exact canonical payload. Field labels
  identify the destination. Optional empty fields and template instruction text do
  not create actions. Environment/log areas can scroll without changing copied text.
- No viewed checkboxes, field-selector menus, preview/source tabs, status dashboards
  or policy text. Full evidence, limitations and material dispositions are under the
  separate investigation-record link.

A successful clipboard operation is followed by a saved copy event. "Copied" is not
"submitted" or human approval. Navigation selection also persists automatically.
Progress keys include the material/project scope, issue, destination, official field
and exact payload. Reusing a new output run with unchanged fields retains progress;
a changed field receives a fresh key. Stale copy events are rejected.

The frontend retries unsaved events a bounded number of times and retains a small
browser-side pending queue if available. Server SQLite is authoritative across browser
refreshes/restarts; clearing browser storage does not clear server progress. When a
browser cannot confirm copying, do not claim it succeeded or mark progress.

A valid changed compiled pack is reloaded without discarding saved state. To revise
content, prepare/review/compile a fresh revision and restart against that pack using
the same state database. Do not edit compiled fields or copy pack.json alone
without its frozen relative artifacts. Invalid updates
retain the last validated draft and a concise notice; investigation records retain
the detailed error. No runs or upstream searches are triggered by web requests.

## WSL and Windows Edge

Serve inside WSL; open http://localhost:PORT in Windows Edge. An HTTP URL avoids
file://wsl$ encoding and Linux/Windows path confusion, and provides a normal clipboard
context. A file-only backup needs a Windows-readable path:

```bash
wslpath -w /home/user/project/review/REVIEW.html
```

Use the returned UNC path rather than C:\home\... or the unconverted Linux path.
Use actual observed browser windows/screenshots when testing desktop interactions;
path conversion alone is not proof that a browser opened. Do not bypass browser or
Windows permission/security prompts to complete testing.

## Checks

Tests cover the real local HTTP API, restart persistence, changed-field reset,
copy values, native-field ordering, hidden investigation details, partial failures
and offline cache reuse. DOM emulation validates mechanics only. Inspect at least
one ordinary bug, a documentation report and a Markdown/single-body form in an actual
browser when available, including a narrow viewport; state any missing coverage.
