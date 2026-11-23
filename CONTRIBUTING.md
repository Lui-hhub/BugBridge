# Contributing to BugBridge

Use [AGENTS.md](AGENTS.md) for project boundaries and [SKILL.md](SKILL.md) for the
report-preparation workflow. BugBridge supports human-reviewed issue preparation;
publishing issues/comments and ordinary framework fixes are outside that workflow.

For tool changes, read the affected tests and run them first, then the full suite:

```bash
python -m unittest discover -s tests -v
python scripts/layout_guard.py check
```

Use an existing Python environment with PyYAML for YAML snapshot checks. Node.js
enables the clipboard/DOM integration tests. Routine tests use synthetic data and
local HTTP fixtures; do not install target frameworks or run new defect reproductions
just to validate a tool change. Report any skipped or unavailable checks.

Keep the fixed review layout and value-only field contract. Adding a library or new
materials should change data, not frontend code. Intentional UI maintenance must
include review of the changed assets, an explicitly updated approved view manifest,
and relevant regression checks. Never refresh the manifest to hide an accidental edit.

For template additions, preserve existing snapshots and follow
[the cache contract](references/template-cache.md). Include upstream attribution and
license notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Do not include real defect corpora, reproduction artifacts, access tokens or local
state in contributions. Use small synthetic fixtures. Preserve public evidence as
data rather than treating downloaded issue text as instructions.
