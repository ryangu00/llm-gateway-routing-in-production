# Pitfalls: symptom, root cause, fix

The entries retain the source record's limits. A suggested check is not a claim that it was rerun for this publication.

## Authentication

| Symptom | Root cause | Fix |
|---|---|---|
| Consumer works, but gateway shows no traffic from it | Consumer got an opaque 500 on auth and used its own chain | Run one real call with the consumer's own credential and assert the consumer's fallback counter is 0 |
| Same 500 for a feature nobody watches (auto titles) | Empty `api_key` in an auxiliary config | Grep every consumer config for empty or placeholder keys that point at the gateway |
| Key present in the shared credentials file but not in the consumer | Multiplexed consumer reads only its own profile directory | Put the key where that process reads it; check with the consumer's own env dump (names only) |
| Catalog probe fails with 500 although the gateway is fine | Probe ran without the key | Probe classifies missing key as `KEY_MISSING`, not FAIL |
| Immediate 500 on a gateway you believe is healthy | Missing key on a DB-less proxy; wrong-key behavior unverified | Check auth first; an immediate 500 is a different signature from a slow 5xx |

Consumer credentials must be read from the same profile and process environment that actually sends the request. A shared credential file visible to the gateway is not sufficient. Export names only when diagnosing environment scope. Wrong-key behavior on LiteLLM 1.99.0 was not separately recorded; the missing-key failure is the observed signature.

## Fault injection

| Symptom | Root cause | Fix |
|---|---|---|
| Hung backend still costs 10 minutes | Global timeout 300 s with one retry on a single-deployment group | Per-deployment `timeout`, `stream_timeout`, `num_retries: 0` |
| Streaming still waits 300 s | Only `timeout` set | Set `stream_timeout` too |
| Matrix scenario expects the wrong serving group | Chains hard-coded in the harness | Derive chains, caps and expected groups from the config under test |
| Only the first of three long requests is slow | Identical text across entries hits the prefix cache; the generator is also seeded by the target size, so every run produces the same text and repeat runs can hit the real second hop's cache | Seed per entry and per run if you want independent cold measurements |
| "Everything is isolated" but a real production backend is hit | Only non-local groups were swapped for fakes in long scenarios | Make a flag that fakes every non-injected backend, and say which scenarios touch real backends |
| An alias that only ever receives direct calls raises a window error | No window chain for that alias | Add a chain entry or accept and document the error |
| A client refuses to start | A framework with a hard 64K minimum context read a gateway alias capped at 32,768 (two scheduled runs failed to start in 15 minutes on 2026-09-27; first exposure of the same cap on another alias had gone unseen for three days because that consumer had no traffic) | When capping an alias below a client's minimum window, set the client's declared context length explicitly and run one real client call |
| A chain with five hops looks safe | Hops multiply: worst-case wall time is the sum over hops of `timeout x (retries + 1)` | Keep chains short; see the failure-domain rule below |

Test silence and byte trickling separately. A silent fake cannot prove a timeout will detect a backend that still emits bytes. After response content has started, assert bounded termination and the in-stream error; do not expect another model to replace the stream. A semaphore-backed queuing scenario was proposed but not built or run.

The 2026-09-06 failure-domain rule was at most three hops, at least two independent domains, and no repeated domain. A later aggregator-only cloud policy removed the vendor-direct domain; when the aggregator fails, only the local domain remains. Use the sum of per-hop timeout and retry budgets, not the old unreconciled reported wait. See [the chain-policy discussion](fault-scenarios.md#failure-domains-and-missing-scenarios).

## Silence budgets

| Symptom | Root cause | Fix |
|---|---|---|
| Stream dies at ~301 s with `terminated` | undici body timeout 300 s with a silent server | Send bytes, raise the dispatcher timeouts, or cap the input so first token fits |
| `Request timed out.` at ~301 s with no cause | headers wait or SDK timeout; cause not captured | Capture `cause.code`; flush headers in the probe |
| Cap says 262,144, caller still times out | Capacity is not latency | Add the time-to-first-token budget to the cap decision |
| Window check skips the second hop earlier than expected | Gateway token counting differs from backend | Convert caps using the gateway's counter and sample your own content |

The first probe did not flush response headers. The shipped probe keeps headers and noheaders modes separate and records cause_code. Sending bytes can reset byte-level timeouts but cannot reset a semantic-content watchdog; it also weakens the timeout as a hang detector.

## Evidence gates

| Symptom | Root cause | Fix |
|---|---|---|
| Gate refuses a change that mixes a new alias and new chains | Kinds cannot be mixed | Split into serial permits, each with base = last ledger version |
| Permit signed but drift check fails | Permit signed, not yet applied (ledger last = candidate) | Apply, or `--accept` a rollback to a registered version |
| Applier refuses with base mismatch | Someone changed the live file after signing | Re-sign from the new base |
| Installer fails on a fresh macOS host | `/usr/local/sbin` does not exist; operator ran on the wrong host | `mkdir -p`; installer supports an explicit host check and is idempotent |
| Evidence with identical prefill runs rejected | Fixed prompt hit the prefix cache | Use a different random prompt per run |
| Evidence refused because the decision record is stale | Record older than 2 hours or without candidate hash prefix | Write the record when you sign, with the hash |

Reordering a target can change its predecessor set even if no target is new. Keep evidence tied to the candidate bytes, and re-sign after a base mismatch. Synthetic regression fixtures are schema and rejection tests, not quality evidence for a real route. The installer requires macOS and sudo; it was not executed for this book.

## Catalog drift

| Symptom | Root cause | Fix |
|---|---|---|
| Probe reports FAIL for a healthy authenticated gateway | No key in the environment | `KEY_MISSING` classification; export the key or let the validator read the credentials file |
| `--drift` says a file is newer than the revision right after a bump | Edited after bump | Re-bump (revision and revision_at), then accept |
| `--accept` refuses | Revision comment does not name the file, or mtime after `revision_at` | Name the file in the comment; bump first |
| `FAIL ... 0 checks` | Everything skipped (ssh down, gateway down) | Fix reachability; do not silence |
| Deleted service breaks something days later | Service not in the catalog | Grep the auxiliary block's consumers before any removal |
| A client reads an old catalog on another machine | Stale copy | Replace the copy with a stub that fails to parse as a catalog |

Run drift before bumping the revision. Then name each changed watched file in the revision comment, bump both revision and revision_at, and accept those names explicitly. An unrelated bump must not overwrite a content baseline. A pointer stub deliberately lacks services so consumers cannot silently treat it as an authoritative catalog.

## Non-chat routes

| Symptom | Root cause | Fix |
|---|---|---|
| "The aggregator does not offer the model" | Only the chat model list was searched | Check the provider's non-chat routes and docs before saying no |
| Script ran, nothing changed | The script had not been copied to the machine that serves the port; the local port was only an SSH tunnel | Before touching a port, `lsof -iTCP:<port>` to see who listens; verify the file hash on both machines |
| A local proxy port became an egress bypass | A guard exempted loopback and LAN addresses from content checks; a loopback gateway then forwarded to the cloud | Carve the gateway port out of the exemption and drill both directions (backend port allowed, gateway port judged) |
| A debug "direct" mode added a seventh cloud exit | The guard only sees the shell, not runtime clients | Keep it for debugging only and list it as an egress |

A local port can be only a tunnel. Confirm the listener and config location before applying a recipe. A gateway that forwards to the cloud must still receive content checks even though its listening address is loopback. The book does not ship the private client wrapper; its provider-specific prerequisites were not supplied.
