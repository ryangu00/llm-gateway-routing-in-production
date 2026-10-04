# Catalog format and drift acceptance

The synthetic [services.example.yaml](../catalog/services.example.yaml) contains two backends, one gateway, and an auxiliary service. It is a schema fixture, not a service discovery result. Its historical example dates must be replaced with the time of a reader's actual revision when using it against freshly created files.

## Fields and probe surfaces

| Field | Meaning |
|---|---|
| `schema_version`, `revision` | Schema and human-readable revision |
| `revision_at` | Full ISO timestamp of the revision bump |
| `drift_baseline: required` | Missing content-baseline file is a failure |
| `verified_at` | Recorded verification date; not a substitute for current probes |
| `host`, `endpoint`, `model`, `ssot` | Required per-service identity and authoritative path |
| `shape: gateway` | Selects gateway/backend capacity comparison |
| `probe_key_env` | Name of a credential variable, not its value |
| `drift_watch` | Local paths or `host:path` watch specifications |
| `local_patches` | Patched files that must be covered by watches |
| `intentional_caps` | Per-entry value, decision id, backend model, expiry, and review note |
| `consumers`, notes | Context for dependency review before removal |

The default reader preserves the original narrow main-block parser. `--drift` also uses strict `yaml.safe_load`, merges all blocks ending in `services`, and fails on duplicate names. `aux_services` entries participate in drift checks but are not automatically probed as `/v1/models` endpoints.

The default endpoint-port regex requires an IPv4 literal plus port. The example uses loopback. A DNS name in that field produces "endpoint not parseable" under this default; this is documented, not silently relaxed. Remote watch hostnames are a separate feature.

`--probe` appends `/models` to each main service endpoint and requires its declared model to equal or occur within a returned id. A key is read from the named environment variable, then the explicitly configured credential file. No key is printed. `KEY_MISSING` is neither service OK nor service FAIL; it means the credential-path prerequisite is absent.

## The five drift checks

1. **Whole-file structure.** Reject malformed YAML, missing fields, duplicate endpoint ports, and names that would overwrite each other across service blocks.
2. **Timestamp and patch coverage.** Require every watch to exist, compare mtime to `revision_at`, and require watched coverage for local patches. The revision and timestamp must share a calendar day. A future timestamp beyond catalog mtime plus 120 s is invalid.
3. **Capacity identity.** Fetch gateway model info, then backend model lists. Prefer exact backend id; accept a fuzzy match only when unique. Missing models or ambiguous matches fail. A smaller cap is exempt only with a matching integer registration, matching backend model, valid decision-id format, unexpired `review_by`, and nonempty entry `cap_reason`.
4. **Route ledger.** Compare watched gateway config bytes with the route gate's last candidate hash. Corrupt ledger is failure. No baseline is explicitly skipped and reported. See the [shared ledger contract](gate-evidence-schema.md#ledger-contract-shared-with-the-catalog).
5. **Content baseline.** Compare each watched file's hash, or directory's top-level name/size/mtime fingerprint, with the accepted baseline. This is independent of revision time and catches changes laundered by an unrelated bump.

The gateway model-info URL follows the supplied endpoint plus `/model/info`, as in the original validator. Verify the endpoint prefix in the intended deployment. Synthetic fixtures accept the example paths; a live prefix was not freshly tested here.

The public validator skips gateway-local loopback bases by default because a remote validator's loopback would be the wrong machine. `--local-backends` is an explicit adaptation for running the validator alongside the synthetic loopback services. Do not set it when gateway-local addresses refer to another machine.

Every family tracks targets versus completed checks. A family with targets and zero checks contributes a failure even if all individual targets were skipped. Obtaining no fingerprint is not a successful check. A remote shell's extra stdout cannot masquerade as a fingerprint: the hash protocol is parsed strictly.

## The order of operations

```bash
python catalog/infra_validate.py --catalog services.yaml --drift
# Name changed files in the revision comment; bump revision and revision_at.
python catalog/infra_validate.py --catalog services.yaml --drift --accept path/to/changed-file
python catalog/infra_validate.py --catalog services.yaml --probe --drift
```

Run drift **before** bumping so the timestamp check can expose unrecorded changes. Then bump and explicitly name the affected watches. `--accept all` only registers watches with no prior baseline. It does not reset changed existing fingerprints. An explicitly accepted changed file must be no newer than the revision and named in the revision comment. A failing item prevents the whole acceptance batch from being written.

The content file is named after the catalog with `.drift-baseline.json` replacing its YAML suffix. Baseline replacement is atomic. The original `services.yaml` convention therefore used `services.drift-baseline.json`.

An identical-byte rewrite changes mtime without changing content signature. Reconcile the timestamp/revision and registration; do not report different bytes that were never observed. Conversely, a revision bump cannot erase a genuine content difference.

## Paths and local demonstration

Use `--catalog`, `--credentials-file`, and `--ledger`, or `CATALOG_PATH`, `CATALOG_CREDENTIALS_FILE`, and `CATALOG_LEDGER`. `CATALOG_CONFIG_PATTERN` controls which watched paths count as gateway config for ledger reconciliation; its default recognizes the shipped gateway example and the generic protected-config layout.

```bash
python fixtures/catalog_demo.py
# With socket creation unavailable, exercise the same checks through a mock transport:
python fixtures/catalog_demo.py --mock-transport
```

The demo starts two loopback fake backends and a fake gateway model-info surface, writes a disposable catalog and ledger, records its initial fingerprints, and runs probe plus drift. All responses and capacities are synthetic. It tests the validator's HTTP/file integration, not LiteLLM. Socket-free tests replace HTTP and remote commands with explicit maps and fail if an unmocked call is attempted.

The three original tests that read a real catalog now read the example and construct a temporary baseline. The shared ledger test imports only the shipped validator. No real catalog, watch list, or baseline is required by the offline suites.

The final line remains machine-readable: `OK: X services, 0 failures`, `OK (skipped N): ...`, or `FAIL ...`. Exit status is the failure count capped at 250. The default/probe-only output preserves the original last-line contract; drift-mode skipped totals do not imply that a missing-key probe was verified.

## One authority, explicit pointers

The historical stale copy stopped at 2026-09-26 while the authority was at 2026-09-28; divergence was found on 2026-10-02. It was replaced with a pointer stub and the old copy/backups moved aside. The stub deliberately lacks `services` so a parser fails instead of using stale topology.

```yaml
authority: path-or-command-supplied-by-the-reader
note: This is a pointer, not a service catalog.
```

No remote authority location is inferred here. Before removing a service, inspect its consumer references, including auxiliary blocks. The recorded helper deletion broke image retrieval for about fifteen hours [R]; absence from the main probe block did not mean absence of dependencies.

`reboot_diff.sh snapshot|diff|mines` is a macOS-only companion. It records active jobs and listeners, compares snapshots, and lists dormant auto-start jobs. Set `REBOOT_STATE_DIR` for local snapshot storage. It does not change services. No actual machine inventory was collected for this publication.
