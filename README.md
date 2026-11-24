# BugBridge

**Bridge local reproductions and upstream conversations.**

English · [简体中文](README.zh-CN.md)

BugBridge is an agent workspace for turning bug materials into traceable,
official-template issue drafts. Clone it, open the checkout in your agent, and
provide a **materials path** and a **target library/project name**.

Your agent investigates the evidence, reproduces claims in existing environments,
compares official issues and PRs, and prepares a local review page. You review the
drafts and copy each field into the official form yourself.

## Start with a checkout

```bash
git clone https://github.com/Lui-hhub/BugBridge.git
cd BugBridge
```

Open that directory in your coding agent and ask:

> My materials are at /path/to/materials, and the target library is pytorch.
> Follow this workspace's AGENTS.md, prepare the issue materials, reproduce the
> claims, compare existing issues/PRs, and give me the local review page.

[AGENTS.md](AGENTS.md) is the entry point. [SKILL.md](SKILL.md) contains the full
workflow; scripts, templates and the frontend are bundled in the checkout.
A global skill installation is not required.

## What BugBridge handles

- **Official forms:** cached PyTorch and TensorFlow examples, source-preserving
  imports for new forms, and typed field definitions that control presentation.
- **Reproduction evidence:** frozen criteria, selected executions, source hashes,
  actual output and existing-environment selection. Missing environments prompt
  the operator for their location instead of triggering framework installations.
- **Upstream retrieval:** an authenticated background GitHub queue after successful
  reproduction, with serial requests, rate-limit recovery, conditional caching
  and complete evidence exports. An offline reader lists metadata first, records
  candidate-selection reasons, and reads individual evidence items in bounded
  ranges. The agent compares triggers and actual fix scope.
- **Field-only composition:** the agent fills generated answer values. Validation
  rejects unknown fields/options, stale reviews and inconsistent evidence bindings.
- **Human review:** a fixed local frontend with official-form links and separately
  copyable fields. Selection and successful-copy progress persist automatically.

Retrieval results support analysis and human review. They do not automatically prove
novelty, a duplicate, fix coverage or inclusion in a release. BugBridge's reporting
workflow does not publish issues/comments or apply framework fixes.

## What stays local

| Location | Purpose |
| --- | --- |
| `runs/` | Fresh task directories, frozen materials, execution evidence and report packs |
| `.local/state/review.sqlite3` | This checkout's selection and copy progress |
| `assets/template-cache/` | Bundled upstream snapshots and normalized form models |
| Machine-level XDG state | Shared GitHub retrieval queue and request coordination across checkouts |

Task data, local state and credentials are excluded from Git. A fresh clone starts
with no task history or review progress. Material inputs can live outside the checkout;
used evidence is frozen into the task directory without modifying the originals.

Use a locally configured GitHub personal access token or an existing authenticated
CLI login. Keep credentials in local configuration, not in the repository or chat.

## Runtime and checks

Use an existing Python 3.10+ environment. The HTTP service, SQLite store and GitHub
retriever use the standard library. YAML snapshot validation requires an existing
interpreter with PyYAML. Node.js enables clipboard/DOM integration tests. The helper
interpreter and the target framework's reproduction environment are chosen separately.

```bash
python scripts/workspace.py paths
python scripts/layout_guard.py check
python -m unittest discover -s tests -v
```

Routine checks use synthetic records and local HTTP fixtures. Framework installations
and new defect reproductions are separate, explicit work. For WSL, open the returned
localhost URL in Windows Edge; use an available port when another checkout is running.
Each new review service closes its port automatically after 3 hours, including
background services. The handoff includes its expiry time; report files and saved
copy progress remain available for a later restart.

See the [retrieval queue](references/retrieval-queue.md),
[field composition](references/filling.md), [review service](references/service.md)
and [contribution guide](CONTRIBUTING.md) for details.

## License

BugBridge's original code and documentation are [MIT licensed](LICENSE).
Bundled upstream templates retain their own licenses and attribution; see
[third-party notices](THIRD_PARTY_NOTICES.md).
