# Fault scenarios and assertions

The [complete scenario and mode tables](results.md#fault-modes) distinguish inputs, injected behavior, and observed results. Scenario input counts in that table are historical. The English public prompts are re-counted; they are not assigned the original prompt's exact count.

## Plan from the candidate

`harness/trigger_matrix.py` reads and hashes the candidate bytes before injection. Its example has three entry aliases, a second hop, an isolation group, and cloud-tail roles. The entry names and backend addresses can be supplied with `--entries`, `--first-hop-base`, `--second-hop-base`, and `--isolation-group`. These are synthetic defaults, not retained deployment identifiers.

The topology check requires:

- One deployment per model group; duplicate groups are ambiguous and refused.
- Positive integer `max_input_tokens` values when present, excluding booleans.
- `enable_pre_call_checks: true`.
- Both chain sections as lists of single-key mappings, with known nonrepeating targets for each configured entry.
- Each entry on the configured first-hop base and an explicit isolation group.

For each scenario and entry, the plan selects the ordinary or window chain, prepends the entry, and walks it in order. It excludes failed targets and targets that cannot serve the token count. The selected target's index is the expected fallback count. A started-stream stall is the exception: the entry already sent content, so the expected serving group stays the entry with zero fallback.

Prompts consist of random `word<number>` terms plus an English suffix. The harness tries up to 12 resizes to put the router-counted prompt in `[150000,151000)` or `[40000,41000)`. It uses `litellm.token_counter(messages=...)`, not backend usage. The default deterministic long-prompt seed means repeated real runs can hit prefix cache; independent cold measurements need distinct prompts per entry and run.

## Isolation is an explicit mode

`--all-fake` adds a successful fake for every non-injected group, including groups with no original `api_base`. Injection also changes the provider to an OpenAI-compatible local request and supplies a synthetic key. No real backend or cloud key is needed for this mode.

Without `--all-fake`, the original behavior retains real local targets, always fakes the isolation group, and replaces nonlocal groups in long-input scenarios. Do not label that mode fully isolated. The historical runs included real second-hop and cloud traffic; current all-fake results cannot be compared as model-performance results.

`--mock-counter` is a public adaptation for dependency-free **planning only**. It counts whitespace, labels the plan accordingly, and is refused for runtime evidence. The offline harness tests inject a synthetic counter module. Neither validates the LiteLLM tokenizer nor produces resilience evidence.

## Own the temporary gateway

For each scenario, the harness writes an injected YAML copy, starts LiteLLM on loopback with `start_new_session`, waits for liveliness, and checks ownership. `lsof` must identify a listener in the process group created by this run. `/model/info` must show the correct injected base for each injected group; a matching base on a different group is not sufficient.

The client records `x-litellm-model-group`, `x-litellm-model-name`, `x-litellm-model-api-base`, `x-litellm-attempted-fallbacks`, HTTP status, prompt usage, and seconds. Stream records additionally retain chunk count, first-chunk time, content prefix, `[DONE]`, and whether the client timed out.

| Scenario class | Client deadline | Extra whole-chain allowance |
|---|---|---|
| Non-streaming hang | 3600 s | 30 s |
| Stream hang and stall | 1200 s | 30 s |
| Opt-in keepalive | 600 s | 30 s |
| Other scenarios | 900 s | 30 s |

These are harness settings [S], not service-level objectives. The whole-chain bound uses a thread because per-read socket timeouts can be reset indefinitely by bytes. A raised request exception is returned as that exception, not as "whole chain exceeded". The public suite adds coverage for that distinction.

Cleanup stops the owned process group, checks that its port stops listening, and closes fake servers and their stop events. Dead ports stay bound and non-listening so another fake server cannot reuse them. Results use exclusive-create output; an output collision cannot overwrite earlier evidence. A failed matrix retains diagnostic files.

## What the timeout keys mean in the pinned source

LiteLLM 1.99.0 source reading [S] gives non-stream timeout precedence as request `timeout`/`request_timeout`, deployment `timeout`/`request_timeout`, `litellm_settings.request_timeout`, then router `timeout`. Streaming precedence is request `stream_timeout`, deployment `stream_timeout`, router `stream_timeout`, then `request_timeout`.

Setting only deployment `timeout` leaves the streaming path at the global value. `_set_deployment_num_retries_on_exception` carries the deployment retry count onto the exception. A single-deployment group cannot obtain a different backend by retrying itself; the recorded policy used zero deployment retries before fallback.

The fake keepalive sends a space every 5 s for a lifetime of 1800 s. The silent fake also has a bounded lifetime of 1800 s. The reported 600 s keepalive-client deadline is not a retained measurement; the opt-in case exists to rerun it. Any byte can reset an httpx read timeout.

## Stream success and stream failure

For a hang before the first streamed content, assert serving group, fallback count, nonempty content, and `[DONE]`. For a stall after content, assert the original group, zero fallbacks, a `partial` prefix, and bounded termination. The historical stall ended with an error event and `done=false`, even though HTTP status remained 200.

A status-only client would call that a success. The matrix records both transport and stream facts so the caller can reject an incomplete answer. The anomaly in historical first-chunk timing remains unexplained.

## Target-level gate evidence

The merged emitter supports ordinary and window chains, repeated `--entry`, an explicit `--target`, and a candidate config. It disables eligible predecessors and counts only successful, correctly landed requests. Window tests additionally require an over-cap prompt and a short control that remains on the entry.

```bash
python harness/emit_trigger_test.py \
  --config examples/gateway.example.yaml --entry entry-a \
  --section context_window_fallbacks --target second-hop --tokens 40000 \
  --all-fake --output trigger-window.json
```

Its output includes `fallback`, `triggered`, `covers`, the candidate's full SHA-256, injection method, and detail rows. Schema examples are not measurements. A gate rejects the earliest historical trigger file because it lacked both `covers` and a candidate hash, even though it recorded successful landings.

## Failure domains and missing scenarios

The 2026-09-06 chain review set a policy of at most three hops, at least two independent failure domains, and no repeated domain. Domains were aggregator cloud, vendor-direct cloud, and local. A later aggregator-only cloud policy removed vendor-direct independence; if the aggregator fails, only local remains. The source does not establish that every later chain meets the original no-repeat-domain policy.

Worst-case wait is the sum over hops of `timeout x (retries + 1)`. An older reported multi-hop wait did not reconcile with that arithmetic and is not repeated as a measurement.

A server that queues instead of returning an error does not necessarily trigger fallback. A semaphore-queue fake was proposed but not implemented or run. No concurrency-throughput result is supplied here.
