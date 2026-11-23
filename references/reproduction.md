# Existing environments and evidence

Inspect project setup and bounded known local environments through metadata/small
read-only probes. Do not scan the whole machine or run all discovered programs.
Record executable/toolchain, library version/build, runtime, device/backend,
relevant dependencies and selection reason. Use project-native commands across
languages, without a dependency on conda or Python for the target case.

An absent explicit version/backend is a mismatch, even if newer packages exist.
Ask before substituting. With no specified version, prefer project fit. Do not
install/upgrade, including in response to a template's nightly recommendation.

When no environment matches, actively ask for its location/name/entry point and
explain requirements, candidates and mismatch reasons. Record the question with
`asked_at`, affected IDs, type environment and state pending. Independent work
can continue; dependent execution/final completion wait. Check the supplied
environment, or record an explicit operator skip. Silence is neither an answer
nor permission. An unanswered request cannot silently become a terminal result.

## Criteria and independent states

Define success from the supplied symptom and a documented/reference expectation
before running. Crashes, wrong values/shapes, intended exceptions and doc
discrepancies need different criteria. Do not compute the expected result with the
same suspect operation. Consider warning/deprecation contracts, inactive options,
argument overrides, numerical/quantization conventions and confounded invalid
inputs when they affect interpretation.

- execution: returned, nonzero_exit, process_signal, timeout, memory_limit,
  setup_error;
- reproduction: reproduced, not_reproduced, inconclusive, awaiting_environment,
  insufficient_material, not_applicable, skipped_by_operator;
- finding: needs_review, supported, excluded, merged, deferred;
- upstream relationship and search coverage are independent; see
  [upstream-search.md](upstream-search.md).

Supported means evidence support, not new human sign-off. Docs-only claims can be
not_applicable for execution but still searched. A different local version failing
to show the symptom does not invalidate historical evidence by itself.

Freeze criterion JSON with definition time, independent expectation/basis and any
metric's name/unit/definition/scope. Save a separate assessment for every run with
exact result/source hashes, criterion reference, cited run output and explicit
observed/not_observed/inconclusive/not_assessed outcome. Preserve early attempts;
select the final run IDs explicitly. Selected runs use the final frozen criterion.
See [pack-contract.md](pack-contract.md) for the checked bindings.

A publication example is a distinct artifact from a diagnostic collector. If it is
shortened or otherwise edited, run that exact source again and bind its actual output.
Do not present maximum per-event net memory change as peak or total memory: retain
the measurement scope in both criteria and prose.

## Runtime records

Use a fresh attempt with immutable source, command array/cwd, environment,
versions, full stdout/stderr, timestamps, exit status, limits and hashes. Preserve
output before native failure. External timeout/RSS kills are not native API
signals. Re-execute changed inputs or code; do not transplant old tracebacks or
rewrite line numbers. Compare builds/devices as well as versions.

Finite resource budgets, process-tree cleanup and core-dump suppression remain
necessary. They do not isolate filesystem/network access. Use a real sandbox for
untrusted code or request a usable isolated environment.

The optional POSIX helper runs one inspected standalone file:

```bash
python scripts/run_case.py --environment environment.json --source case.js \
  --output-dir attempts/local-001 --reviewed-local-code -- node-path '{source}'
```

Environment JSON identifies a verified existing environment, absolute executable,
library name/version and selection reason. The command must begin with that exact
executable; `{source}` becomes an immutable copy. No shell is added. Multifile or
project-native runners can instead record equivalent evidence. The helper does
not discover environments, install, judge symptoms or provide a security sandbox.
Use equivalent native isolation on non-POSIX hosts, without silently dropping guards.

Capture official diagnostics in the actual selected environment when present.
Missing diagnostics remain explicitly missing. Retain raw evidence locally and
make submission redactions explicit, preserving the raw counterpart.
