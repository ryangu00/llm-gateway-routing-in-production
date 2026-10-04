# Route evidence, permits, and the shared ledger

The gate turns the justification for a route change into machine-readable input. It never writes the production config itself. The [classification table](results.md#route-change-classification) describes the four change kinds; this page describes their checks and file contracts.

## Configure directories rather than deployment identities

All defaults below are public example locations. Configure them before a real use:

| Environment variable | Default / purpose |
|---|---|
| `ROUTE_DECISION_DIR` | `decisions`; unique `ADR-NNNN*.md` records |
| `ROUTE_STATE_DIR` | `state/route-change`; permits and `ledger.jsonl` |
| `ROUTE_EVALUATION_DIR` | `evaluation`; quality files under `runs/` |
| `ROUTE_MEASUREMENT_ROOT` | `workspace`; measurements under `research/`, also accepted from evaluation `runs/` |
| `ROUTE_HOST` | `gateway.example.invalid`; destination placeholder for printed staging commands |
| `ROUTE_REMOTE_CONFIG` | Protected gateway config path; deployment metadata |
| `ROUTE_STAGING_DIR` | `/usr/local/etc/litellm-gateway/staging` |
| `ROUTE_DRIFT_WATCH` | `examples/gateway.example.yaml`; catalog watch key to reconcile |
| `ROUTE_SERVICE_LABEL` | `example.litellm-gateway`; placeholder restart label |

The `adr` field and `ADR-NNNN` format are retained as generic decision-record schema names. The examples use synthetic identifiers; no internal decision records are distributed.

```bash
python gate/route_change.py --accept live-copy.yaml --adr ADR-0001
python gate/route_change.py --evidence evidence.json --adr ADR-0002 \
  --candidate candidate.yaml --base live-copy.yaml
```

These commands require fresh records with the corresponding file hash in the body. The first command registers a baseline only when the ledger is empty or the hash appeared previously. It cannot retroactively approve an unknown write.

## Primary evidence

Each `old` and `new` block carries `target`, `quality_runs`, `quality_src`, `prefill_tps`, `prefill_src`, and `decode_tps`; optional explicit `model` and `base_url` must agree with the measured results. `quality_metric` is `all`, `own`, or `pack`. `fallbacks` must be present, even when empty.

The public evaluation tool's recorded result fields include `bank_hash`, `grader_version`, `cats`, `rows[].runs`, `model`, `base_url`, `tier`, `thinking`, and `recipe_arm`. See the named related repository `ryanai-evalbank`. The gate also checks declared run count and complete finite per-category runs.

For each side, recompute per-run averages from the selected rows. Compare the supplied total list within 0.1 and require matching length. Require at least two quality runs per side, a single endpoint identity per side, and no duplicate results file. Both sides must use the same bank, categories, scored categories, and arm keys. Reusing one upstream under two aliases is not a cross-model comparison.

Prefill JSON must be under an allowed measurement directory, have the same `(model, base_url)` identity as the quality evidence, and include at least two positive `prefill_tps_runs`. The median must be within 2% of the reported value. Identical repeated values are refused as a possible cache-hit artifact. The historical cached-versus-uncached report is [R], not a new speed measurement.

The primary comparison binds the candidate's changed upstream model and base URL to `new`, and the live base's endpoint to `old`. Unrelated top-level changes, router changes, resilience changes, entry additions/removals, and changes to unrelated entries are refused. Only one provider's default endpoint has explicit resolution; provider-prefix identity handling remains partly closed. Treat that as a limit, not a completed general identity solution.

## Capacity and fallback evidence

[evidence-capacity.json](../examples/evidence-capacity.json) declares each cap as an entry, positive integer value, and reason. Caps are compared with the candidate; unrelated entry and top-level structures cannot change through this lighter path. A `cap_reason` is part of the declared capacity explanation.

[evidence-fallback.json](../examples/evidence-fallback.json) references generated per-target trigger files. A synthetic shape is:

```json
{
  "fallback": "cloud-tail",
  "triggered": 2,
  "covers": ["fallbacks:entry-a", "context_window_fallbacks:entry-a"],
  "candidate_sha256": "<candidate hash, 12 or 64 hex digits>",
  "method": "Temporary gateway with injected failures",
  "detail": []
}
```

This is a schema illustration, not a valid measurement to submit. Generate the detail and coverage from actual requests. The gate requires positive trigger count, the correct fallback name, and a candidate-bound hash. It rejects unrelated evidence.

For each target, compare its predecessor set in the ordered old and new chain. A newly inserted target needs evidence; so does an existing target whose predecessors changed. A middle deletion can therefore require both a removal declaration and evidence for the surviving tail. Deletions use `section:source` or `section:source:target` in `fallback_removals`; phantom declarations are refused.

Validate both chain maps, then check their union for cycles. Removing an entire window mapping additionally requires evidence for targets reachable through the surviving ordinary chain. This is a conservative gate policy; it is not a claim that every router version redirects window errors that way.

## Resilience evidence

[evidence-resilience.json](../examples/evidence-resilience.json) declares all three keys for each changed entry: `timeout`, `stream_timeout`, `num_retries`, plus a reason. The candidate must also contain all three explicitly. A retry policy that could override deployment retries makes the budget undefined and is refused.

The `matrix` file must carry the full candidate hash, `pass: true`, nonempty results, and `ok: true` on every row. All scenario names in `RES_SCENARIOS` must be present. Each changed entry must have non-streaming hang, streamed hang, and streamed stall evidence.

For a non-stream hang use `timeout`; for streamed hang/stall use `stream_timeout`. Allowed duration is:

```text
key * (num_retries + 1) + 60 seconds
```

Durations must be finite and nonnegative. A mocked planner output has no `pass` field and is not a matrix. Non-primary kinds may be combined with `+`; `primary` cannot be included in a combination.

## Freshness, snapshots, and output

A decision record must have a unique matching number, modification time within two hours, today's or yesterday's date in its body, and the first twelve candidate hash digits. Touching an old unrelated record does not satisfy the body checks.

The gate reads candidate, base, and evidence bytes once. Parsing operates on snapshots; the hash, frozen copies, and permit all derive from those bytes. Snapshots are checked again before granting. Frozen copies use exclusive creation and mode 0400, and readback must match.

A permit contains `id`, `adr`, `adr_file`, evidence source/frozen paths and hash, candidate path/hash, `base_sha256`, printed commands, `created`, and `expires`. The TTL is thirty minutes. Three staging/apply commands are printed; restart is separate. The synthetic tests verify the freeze, ledger, expiry interval, and command construction without executing SSH or applying production changes.

## Ledger contract shared with the catalog

One JSON object per line:

```json
{"ts": 1790000000.0, "kind": "permit", "id": "<16 hex>", "adr": "ADR-0001", "candidate_sha256": "<64 hex>"}
```

The timestamp and identifier above are synthetic schema values. An `accept` row also records `adr_file` and `source`. Both consumers require a valid object with a 64-hex `candidate_sha256`; corrupt JSON, malformed rows, unreadable files, and invalid text are failures. A missing or truly empty ledger is distinct from corruption.

The gate requires `--base` to match the last ledger hash. The catalog compares that same last hash with the watched live config. A signed but unapplied permit therefore produces a mismatch. A permit is not proof of application. Read [catalog check 4](catalog-format.md#the-five-drift-checks) before changing this schema; both readers have a shared newline/corruption regression.

## Root-owned applier

The installed directory is root-owned, mode 755, with the config mode 644. Staging is writable by the operator, while config, consumed markers, and the applied ledger are not. `install_applier.sh` supports an optional `EXPECTED_HOST` check and requires reader-supplied paths, Python, and service label. It is macOS-specific and needs sudo; `--check` is a no-write prerequisite summary.

The applier refuses a malformed, expired, swapped, replayed, wrong-base, or invalid-YAML candidate. Under an exclusive lock, it verifies current state, backs up, writes/fsyncs a same-directory temporary file, atomically renames, reads back the hash, appends `applied.jsonl`, and writes a consumed marker. A normal-user restart follows separately.

Imported tests inject a temporary root and effective-UID function. There is no command-line flag that bypasses the root check. The new automated tests cover the original manual acceptance/refusal cases; they do not establish deployed ownership, sudo policy, or service effectiveness.

The source once claimed a pre-tool enforcement hook that did not exist. That claim was withdrawn; it is not part of this design. The threat boundary is process mistakes and after-the-fact substitution, not malicious same-user code or root.
