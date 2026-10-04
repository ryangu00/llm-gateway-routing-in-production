# Publication validation scope

The historical tables describe the source record. They are not results from the public adaptation. The commands below exercise synthetic fixtures and mocks only.

```bash
python3 -B tests/run_offline.py
node --test tests/body_timeout_probe.test.mjs
python3 -B fixtures/catalog_demo.py --mock-transport
```

The Python runner uses temporary state inside the repository, disables external pytest plugins and cache output, and runs the adapted gate, catalog, and harness suites plus new standard-library unittest cases. It preserves source-mutation controls. The three stale gate message assertions were updated; the rejection checks remain in place. Catalog tests that read the original deployment now use the synthetic fixture.

The additional cases cover applier refusal and readback, all-fake provider isolation, exception preservation, candidate-derived trigger predecessors, randomized English prompt construction, low-memory abort, routing/needle acceptance, the consumer acceptance adapter, pass-through idempotence and model-count refusal, and the complete catalog workflow.

The shell helper's snapshot/diff path is exercised with synthetic job and listener commands. The installer's no-write `--check` path is exercised separately. Shell syntax checks do not establish successful installation.

The catalog demo's `--mock-transport` mode uses explicitly mapped HTTP responses while retaining real disposable catalog files, hashes, timestamps, accepted fingerprints, and ledger reconciliation. Unexpected remote commands fail. Without this flag it starts loopback HTTP servers; that mode requires permission to bind local sockets.

The Node built-in tests replace the HTTP transport, SDK, clock waits, and process exit. They check flushed-versus-unflushed headers, delayed SSE construction, requested SDK settings, retained error cause, and cleanup. They do not measure a timeout and do not verify the real SDK or undici implementation.

## What was not executed

- The long LiteLLM matrix and real-tokenizer planning: the publication environment did not have the required LiteLLM Python package or tokenizer dependency, and its sandbox denied loopback listeners. The planner's explicit `--mock-counter` mode was exercised instead. No dependency installation was performed.
- Actual loopback catalog servers and inference probes: socket-free substitutes were used. Synthetic tokenizer counts are not model-token counts, and preassigned routing headers are not proof of a real router's behavior.
- The Node silence experiments: the local OpenAI SDK package was unavailable; only syntax and transport-mock tests were run. Historical timing rows remain historical.
- The authentication reproduction: **not re-run by us**. Its configuration was inspected by a test, without launching the fake backend or LiteLLM. Clean reproduction, the `prisma` workaround, wrong-key behavior, and newer releases remain unverified.
- The privileged applier installation, sudoers changes, actual service restarts, remote snapshots, real inference engines, and cloud endpoints. Temporary-root applier tests do not establish deployment permissions or crash recovery.

## Prerequisites the source does not provide

The consumer-specific, per-call fallback-counter integration must be supplied before using the acceptance adapter as a cutover check. The counter must correspond to that call; a manually created zero file does not establish acceptance.

The non-chat routes use placeholder provider targets. A working provider contract, decision-model identity, and credentials must be supplied for live use. Private client-wrapper and outbound-content-guard implementations were not supplied and were not invented.

The historical Node version and the exact offline tokenizer cache contents were not supplied; no network lookup was performed to fill these gaps.

LICENSE is copied without changes; .gitignore adds `.work/` for local run output. The banner source changes only displayed book text. All relative documentation links are checked against the shipped tree.
