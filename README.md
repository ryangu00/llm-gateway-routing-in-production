![banner](docs/assets/banner.png)

# A configured fallback is not a working fallback

Fault-injected routing, evidence-gated route changes, and a service catalog that cannot drift quietly, with **LiteLLM 1.99.0**.

A gateway configuration records intent. Whether a fallback fires, a timeout ends a wait, a cap applies, or a consumer authenticates is a property of the running path. This book turns those intentions into checks: inject faults and assert the serving group and wait; bind route changes to evidence for the exact candidate bytes; compare the service catalog with real probes, content fingerprints, and expiring exceptions. The setting is a local first hop, a second hop on Dell Pro Max with GB10, and an aggregator cloud tail. The evidence is deployment-specific and mostly single-run.

## Why this matters

A consumer can keep working while silently bypassing a healthy gateway. A stream can begin successfully and fail later without any fallback. A capacity change can pass a token check and still exceed the caller's silence budget. A fresh catalog revision can hide an older unrecorded deployment change unless the content baseline is independent of the revision timestamp.

The useful acceptance question is concrete: if the claimed property disappeared, would this probe or test fail? The gate and catalog suites include source-mutation controls; the matrix observes where requests actually land. A green process check is not a substitute for either.

Evidence labels are part of every conclusion:

- **[M]** measured by us, with a raw record retained when the source fact sheet was written.
- **[R]** reported only; the raw record was not retained or could not be found.
- **[S]** read from source or configuration, not measured.
- **[U]** upstream or vendor documentation read on **2026-10-04**, not refreshed for this publication.
- **[E]** calculated from other source figures, not measured independently.

Historical raw configurations, catalogs, ledgers, and results are not distributed. Every shipped configuration and evidence example is synthetic. The adapted code's offline tests are separate from the historical results below; see [validation scope](docs/validation.md).

## What is in this book

| Chapter | The check it builds |
|---|---|
| [The 401 that became a 500](docs/day-one-acceptance.md) | Exercise each consumer's own credential and require zero consumer fallback. |
| [A fallback trigger matrix](docs/fault-scenarios.md) | Inject errors, silence, dead ports, and stream stalls; assert the destination and wait. |
| [The silence budget](docs/silence-budget.md) | Separate header wait, body wait, and semantic idle limits before setting a cap. |
| [Evidence-bound route changes](docs/gate-evidence-schema.md) | Bind quality, capacity, fallback, and resilience evidence to exact candidate bytes. |
| [A catalog that cannot drift quietly](docs/catalog-format.md) | Probe services, compare caps, reconcile the route ledger, and fingerprint watched content. |
| [One egress for non-chat endpoints](docs/non-chat-egress.md) | Add pass-through routes with gateway-side credentials, validation, probes, and rollback. |

## The setting

Version inventory was read on **2026-10-04 [S]**. This is a pinned historical deployment, not a claim about newer releases.

| Component | Recorded setting |
|---|---|
| Second-hop hardware | **Dell Pro Max with GB10**; two machines in tensor parallel until 2026-09-26, one afterwards |
| Second-hop serving stack | vLLM and the large model documented in `dell-pro-max-gb10-deepseek-v4-flash-vision-exp`; engine version not separately supplied here |
| Local first hop | Local OpenAI-compatible endpoint; gateway input cap 32,768 |
| Gateway | LiteLLM 1.99.0; `litellm-proxy-extras` 0.4.89; no database; optional `prisma` absent |
| Python dependencies | Python 3.14.6, httpx 0.28.1, OpenAI Python SDK 2.54.0, tiktoken 0.14.0 |
| API/configuration dependencies | FastAPI 0.141.1, uvicorn 0.52.4, PyYAML 6.0.3 |
| Client probe | OpenAI Node SDK 6.40; Node version not recorded |
| Reader prerequisites | Python 3.10 or newer; PyYAML; pytest for the original gate/catalog suites; Node 18 or newer and a local SDK for the Node probe |

The router used `num_retries: 1`, `allowed_fails: 2`, `cooldown_time: 60`, and `enable_pre_call_checks: true`, with both ordinary and context-window chains. The global `request_timeout` was 300 s. The first-hop resilience change made `timeout: 180`, `stream_timeout: 90`, and `num_retries: 0` explicit per deployment.

The second-hop gateway cap changed from 131,072 to 1,000,000 on 2026-09-23, back to 131,072 at 2026-09-25 01:57 pending the ladder, to 262,144 at 12:29 that day, and to 250,000 at 2026-09-26 15:10. The final cap followed a backend `max_model_len` of 256,000 with about 6K reserved for output [S]. These are configuration limits, not latency promises.

## Chapter 1: The 401 that became a 500

**Problem.** A class of consumers silently used its own fallback for four days after the gateway went live. The deployment-day probes were 4/4 green [R]. They tested availability, not the consumers' credential paths.

**Mechanism.** A consumer referenced `LITELLM_MASTER_KEY`, but its profile environment did not contain it. The shared credentials file was visible to the gateway launcher, not to that profile. The keyless request reached virtual-key/database handling and raised `No connected db.`. The error handler then imported the absent optional `prisma` package, raising `ModuleNotFoundError` while handling the first exception. The caller received generic HTTP 500 and interpreted it as provider failure [S/M].

The signature was `No api key passed in.` followed by authentication error handling, database-infrastructure classification, `import prisma`, and the second exception. Checking the backend first would have investigated the wrong failure.

**Procedure as run.** A consumer log exposed the fallback. A request with the real master key succeeded for all six entry aliases [R]. The operator backed up and patched three profile environment files, retained permissions 600, and ran a real-call regression that counted consumer fallback log lines; acceptance was zero. Four auxiliary title-generation configurations with empty keys were redirected to a keyless local endpoint [R].

| Item | Result and condition |
|---|---|
| Went live / discovered | 2026-09-02 / 2026-09-06, four days [R] |
| Deployment-day probes | 4/4 green [R], not consumer-credential acceptance |
| `No connected db` log lines | 13,422 [M], whole 80.4 MB log, counted 2026-10-04 |
| `ModuleNotFoundError` log lines | 20,748 [M], same file and date |
| Correct-key aliases | 6 of 6 returned 200 [R] |
| Diverted requests | Unknown; no usable callback ledger |

The log counts include later keyless probes and do **not** measure the four-day impact. A separate 2026-10-03 keyless observation returned 500; with the key, the model list and a model call succeeded. The wrong-key result on 1.99.0 has no separate recorded request and remains unverified.

**What did not work.** The audit found a dead spend callback: one successful request added zero rows; the file contained 32 failure rows [M]. The content-routing pre-call hook was also recorded as not firing in this version; the cited upstream bypass reports were not rechecked. `allow_requests_on_db_unavailable: true` did not prevent the later observed 500 and is not an established fix.

We did **not** reproduce the defect in a clean environment. [auth_401_as_500.py](probes/auth_401_as_500.py) is a self-contained reader reproduction candidate, **not re-run by us**. It starts a loopback fake backend and a database-less LiteLLM, then observes missing, valid, and wrong-key responses. The upstream `prisma` workaround and newer versions remain untested.

**Rule.** At cutover, make one real request per consumer using that consumer's own credential; require the requested serving group and zero consumer fallback. The [acceptance adapter](day-one/acceptance.sh) also checks HTTP 200 and zero gateway fallbacks. How to obtain a trustworthy per-call consumer fallback counter is consumer-specific and was not supplied.

## Chapter 2: A fallback trigger matrix built with fault injection

**Problem.** A configured chain does not establish which target answers when an earlier target fails, nor how long the caller waits.

**Mechanism.** The harness hashes the original candidate bytes, validates single-deployment groups and positive input caps, and derives ordinary and window chains from that candidate. Per scenario, it builds an injection map and computes the eligible serving target and its position in the chain. Long prompts use the same `litellm.token_counter(messages=...)` as the router, with bounded resizing into the target window.

A fresh temporary gateway runs in its own process group for each scenario. The harness checks listener ownership using `lsof` and `ps`, then checks the injected group-to-`api_base` mapping in `/model/info`. Each response records status, serving-group and attempted-fallback headers, duration, and stream state. A wall-clock-bounded thread preserves request exceptions instead of misreporting them as deadline overruns.

Fake modes are fast HTTP 500, a bound non-listening port, silent hang, one SSE chunk followed by a stall, full success, and opt-in whitespace keepalive. `--all-fake` rewrites **every** backend to loopback, including provider-default endpoints. The configurable isolation group is always fake. Without that flag, some scenarios can contact the configured real backends.

**Procedure as run.** Historical runs used the full matrix or selected scenarios on candidate copies. The public equivalent is:

```bash
python harness/trigger_matrix.py --config examples/gateway.example.yaml --plan-only
python harness/trigger_matrix.py --config examples/gateway.example.yaml --all-fake
python harness/trigger_matrix.py --config examples/gateway.example.yaml --all-fake --only first_hop_hang
```

`--plan-only` does not load credentials, allocate ports, launch processes, or emit a `pass` field. Full results use exclusive creation and a unique name. Diagnostics are removed only on success. The separate [trigger emitter](harness/emit_trigger_test.py) produces target-level `covers` evidence for the route gate.

Run A: **2026-09-25**, three entries per scenario, **15 of 15 pass [M]**; explicit 180/90 s timeouts and no deployment retry; summed request time **608.7 s [E]**.

| Scenario | Seconds across entries | Serving group | Fallbacks |
|---|---|---|---|
| First-hop HTTP 500 | 0.6, 0.4, 0.4 | Second hop | 1 |
| First-hop silent hang | 180.6, 180.4, 180.3 | Second hop | 1 |
| Both local hops dead | 2.9, 3.0, 3.8 | Real cloud tail | 2 |
| Input 150K; second-hop cap 131,072 | 9.7, 15.6, 7.5 | Real cloud tail | 2 |
| Input 40K; second hop dead | 6.8, 10.8, 5.9 | Real cloud tail | 2 |

Run B: **2026-09-27**, three entries per scenario, **21 of 21 pass [M]**; summed request time **1,385.1 s, about 23 minutes [E]**.

| Scenario | Seconds across entries | Serving group | Fallbacks |
|---|---|---|---|
| First-hop HTTP 500 | 0.4, 0.3, 0.3 | Second hop | 1 |
| First-hop silent hang | 180.5, 180.3, 180.3 | Second hop | 1 |
| Hang before first streamed chunk | 90.5, 90.3, 90.3 | Second hop | 1 |
| Stall after first streamed chunk | 90.2, 90.0, 90.0 | Entry itself | 0 |
| Both local hops dead | 27.0, 25.7, 26.9 | Real cloud tail | 2 |
| Input 150K; second-hop cap 250,000 | 162.5, 3.0, 3.0 | Real second hop | 1 |
| Input 40K; second hop dead | 17.9, 17.7, 18.0 | Fake isolation group | 2 |

The streamed hang completed with `[DONE]`; first chunks arrived at 90.4, 90.2, 90.2 s and answered `391`. The stall returned HTTP 200 and `partial`, then an in-stream socket-read timeout error with code 500 and `done=false`. Its first chunk appeared at 0.2 s for one entry and 90.0 s for the other two; this was not investigated.

Only the first long request in run B was a cold prefill: identical text let the next entries hit prefix cache. The run-to-run dead-hop timing changes were not isolated; cloud latency and dead-port technique differed. They are not a chain latency model. [Complete records and qualifications](docs/results.md) include the reported **6/6** trigger claim for which only **five JSON records** exist; the sixth control has **no record**.

**What did not work.** Default silent-hang behavior cost **603.2 s [R]**, with no retained raw file: `300 x (1 + 1) + 3.2`. A whitespace-trickling backend reached the client's **600 s** deadline with no fallback despite a configured **180 s** timeout [R], also without retained raw output. Per-read timeouts reset on bytes. The queued-overflow assumption was refuted in notes, but no publishable concurrency record or semaphore-queue scenario is shipped.

Source reading [S] explains why both `timeout` and `stream_timeout` matter and why deployment `num_retries: 0` prevents retrying the same single hung deployment. A direct call to a fallback alias also needs that alias's own window chain; it does not inherit the entry's chain.

**Rule.** For every relied-on edge, cap, and timeout, inject its failure and assert destination, fallback count, and wait against the candidate hash. A silent fake cannot establish behavior under byte trickling; a started stream cannot be repaired by stitching in another model.

## Chapter 3: The silence budget

**Problem.** The engine can accept an input whose cold prefill takes longer than a client or gateway will wait without data. Input capacity and path latency are different constraints.

**Mechanism.** The Node probe's `headers` mode flushes headers immediately and delays the body. `noheaders` delays both. A traced `fetch` records header and first-chunk times plus `cause.code`; requests use `stream: true`, `timeout: 600000`, and `maxRetries: 0`.

**Procedure as run.** On 2026-09-25, run each mode at the delays below with OpenAI Node SDK 6.40. Node version was not recorded. Each row is one run [M]. The first probe is historical only; the shipped script is the corrected second probe.

| Probe | Silence | Result | Recorded time / cause |
|---|---|---|---|
| First probe, headers not flushed | 290 s | OK | 290.016 s |
| First probe | 310 s | Fail | 301.527 s; `Request timed out.`, empty cause |
| Corrected `headers` | 290 s | OK | Header 11 ms; first chunk 290,019 ms |
| Corrected `headers` | 310 s | Fail | 301,493 ms; `terminated`, `UND_ERR_BODY_TIMEOUT` |
| Corrected `noheaders` | 310 s | Fail | 301,490 ms; `Request timed out.`, underlying cause not captured |

Undici documented header and body defaults of **300,000 ms [U]**; additional body bytes reset the latter. The source-read client stack also included a **300 s** semantic-content watchdog that ignored headers, SSE comments, and keepalives, other limits of **300 s, 300 s, 600 s, 900 s**, and a job wall clock of **1,800 s [S]**. Those client limits were not independently timed here.

The cap chosen on 2026-09-25 was **262,144**, with an interpolated first-token estimate of **about 150 s [E]** from the already-published cold ladder. For a non-streaming request, **8,192** output tokens need **about 250 s at 32.8 tokens/s [E]** in addition to that prefill. A global retry can then cost **about 600 s [E]** over two silent attempts. The cap is not a completion-latency guarantee.

Through-gateway tests on **2026-09-25 [M]**, one random-content non-streaming request per row, first-hop cap **32,768**:

| Backend-sized input | Gateway decision | Seconds | Fallbacks | Needle |
|---|---|---|---|---|
| About 240K; response usage 239,887 | Gateway count exceeded 262,144; cloud tail | 23.5 | 2 | Empty answer; routing only |
| 199,855 | Within cap; second hop | 102.7 | 1 | Correct |

**What did not work.** The original probe conflated header and body waits. Raising a cap to engine capacity ignored first-token silence. Raising the timeout to fit a very long prefill also lengthened the wait before fallback on a real hang. A byte heartbeat helps byte-level limits but not semantic idle limits and can defeat hang detection.

**Rule.** List every hop's silence limit and what resets it. Choose the cap against the smallest relevant budget with margin, then check generation time too. [The detailed page](docs/silence-budget.md) links the previously published ladder and tokenizer comparison without duplicating those measurements.

## Chapter 4: Route changes bound to machine evidence

**Problem.** On 2026-09-23 a primary change was made from a decode-throughput number before quality and prefill evidence existed; it was reverted about **6.5 hours** later, from **16:49 to 23:25**. Another edge was added for an overflow behavior it did not trigger. Neither justification was adequate evidence.

**Mechanism.** The gate classifies changes and limits what each class can alter. A primary change cannot be combined with the lighter classes.

| Kind | Allowed change | Required evidence |
|---|---|---|
| `primary` | Changed upstream model/endpoint, no unrelated structures | At least 2 quality runs per side, comparable bank/categories/arms, at least 2 nonidentical prefill runs, measured endpoint identity |
| `capacity` | Declared positive input caps and reasons | Matching `caps` declarations; other structure unchanged |
| `fallback` | Ordinary/window chain changes | Candidate-bound triggers for targets with changed predecessor sets; explicit removals |
| `resilience` | Explicit timeout, stream timeout, retries | Complete passing matrix for the exact candidate and bounded per-entry hang durations |

Quality totals are recomputed from `rows[].runs` and matched within **0.1**; run counts must agree. Prefill median must match within **2%**, with nonidentical positive runs. Both sides must agree on `bank_hash`, `cats`, scored categories, and arm keys `tier`, `grader_version`, `recipe_arm`, `thinking`. The evidence must identify the actual upstream, not merely the alias requested.

Fallback triggers carry `fallback`, positive `triggered`, `covers`, and a candidate hash prefix of **12 or 64 hex digits**. Reordering, inserting predecessors, or deleting intermediate targets can require new evidence. Both chain types form one cycle check. Resilience evidence uses the full hash, all scenario names, all passing rows, and hang budgets of `timeout_key x (num_retries + 1) + 60` seconds; overriding retry policies are refused.

Candidate, base, and evidence are read once as bytes, snapshotted, checked, and reverified. The base must match the last ledger version. A unique decision record must be within **2 hours**, name the candidate's first **12** hash digits, and contain today's or yesterday's date. Successful permits last **30 minutes**; frozen files use mode **0400**.

**Procedure as run.** Edit candidate, gather kind-specific evidence, write the fresh decision record, run the gate, stage its frozen candidate and permit, and invoke the root-owned applier. Restart separately, smoke-test consumer aliases, then run catalog drift before revising and accepting the new baseline. A combined change was split into four serial permits; do not retrospectively sign a bypass.

| Gate record | Result and condition |
|---|---|
| Ledger read 2026-10-04 [M] | 15 rows: 1 accept + 14 permits; one deployment, about three days |
| Permit kinds [M] | Primary 4; capacity 3; capacity+fallback 2; resilience 2; fallback 3 |
| Serial permits on 2026-09-26 | Primary 13:05, primary 14:26, fallback 15:05, capacity 15:10; 2 h 5 min elapsed [E] |
| Original suite, 2026-10-04 [M] | 125 collected; 122 pass, 3 stale-message failures; gate rejection still correct |
| Original applier checks [R] | Six manual cases; no original automated tests |

The applier checks permit shape, expiry, candidate hash, replay state under an exclusive lock, live base hash, and nonempty YAML model list. It backs up, writes and fsyncs a temporary file in the same directory, atomically renames, verifies the readback hash, appends the applied ledger, and marks the permit consumed. Restarting is outside this program.

**What did not work.** Early checks accepted fabricated evidence paths and touched old decision records. Alias-based identity could credit a fallback answer to a requested primary. A claimed enforcement hook did not exist; that claim was corrected. The boundary became a root-owned config and applier, coupled to hash drift detection. Provider-prefix identity handling remains only partly closed.

**Threat model.** The gate defends against operator self-deception and after-the-fact substitution. It does not defend against a malicious process under the same user, which can edit the ledger and evidence, nor against root.

**Rule.** Use one write path, a fresh hash-naming decision record, and kind-specific evidence. Keep negative controls that fail when a gate is removed. The [schema page](docs/gate-evidence-schema.md) documents the ledger dependency shared with the catalog.

## Chapter 5: A service catalog that cannot drift quietly

**Problem.** A route change stayed out of the catalog, gateway caps understated the backend, and an unregistered helper was removed as unreferenced. A catalog is useful only if stale entries cause a visible failure.

**Mechanism.** The synthetic catalog records revision and full `revision_at`, endpoint/model identity, authoritative paths, watched files, local patches, and intentional caps. All `*services` blocks participate in drift validation; only the main `services` block is probed as a model-list surface.

The five checks are:

1. Strict YAML, required fields, endpoint-port uniqueness, and duplicate service names across blocks.
2. Watched-file existence and mtime versus `revision_at`, including coverage of local patches; the revision day must match and cannot be later than catalog mtime plus **120 s**.
3. Gateway `max_input_tokens` versus backend `max_model_len`; intentional smaller caps require the same registered value, decision id, matching backend model, unexpired `review_by`, and nonempty `cap_reason`.
4. Live gateway config hash versus the route gate's last ledger row; a corrupt ledger fails.
5. Content fingerprints versus a separately accepted baseline, independent of revision timestamps.

A target family with zero completed checks fails rather than turning all skips into green. Missing credentials are `KEY_MISSING`, not evidence that the service is down. Remote loopback backends are skipped unless the validator is explicitly run in local-backend mode. File fingerprints hash bytes; directory fingerprints hash top-level names, sizes, and mtimes.

**Procedure as run.** Change the deployment; run `--drift` before bumping; name changed files in the revision comment; bump `revision` and `revision_at`; `--drift --accept` those exact watches; then require `--probe --drift` to pass. `--accept all` registers only new watches and cannot overwrite an already changed baseline.

| Catalog record | Result and condition |
|---|---|
| Original suite, 2026-10-04 [M] | 65 collected, 65 pass; three tests needed synthetic catalog replacement |
| Baseline / intentional caps, 2026-10-04 [M] | 15 watches; 7 deliberate caps; 4 review dates 2026-10-09 and 3 dates 2026-12-31 |
| Route not mirrored, 2026-09-23 [R] | Inconsistent for 7 to 11 hours |
| Understated second-hop caps [R] | Five entries at 131,072 versus backend 1,000,000, about three days, 2026-09-20 to 2026-09-23 |
| Removed helper, 2026-09-04 [R] | Image retrieval silently broken for about 15 hours |

**What did not work.** Day-only revisions missed same-day edits. Unrelated revision bumps hid previous drift. A permissive parser accepted invalid YAML; merging blocks could overwrite duplicate names; all-unreachable checks could appear successful; exceptions without expiry could exempt mistaken caps indefinitely. Each became a check.

A stale catalog copy found on 2026-10-02 was replaced with a pointer stub without a `services` key. The old copy and backups were moved aside rather than left available as apparently authoritative alternatives. An identical-content rewrite still needs timestamp reconciliation, but does not imply a changed content fingerprint.

**Rule.** Keep one authority, real probes, independent content baselines, and expiring exemptions. [Catalog format and workflow](docs/catalog-format.md) explain the endpoint regex, local-only synthetic demo, and pointer stub.

## Chapter 6: One cloud egress for non-chat endpoints

**Problem.** Searching only the chat-model list missed an aggregator's non-chat decision routes. Calling those routes directly would create another cloud exit outside the intended gateway path.

**Mechanism.** Add `/decide/decisions` and `/decide/sdk` to `general_settings.pass_through_endpoints`. Each POST forwards to a provider non-chat endpoint with `Authorization: Bearer os.environ/AGG_API_KEY` injected by the gateway. The pass-through timeout is **60 s**. The shipped targets use `aggregator.example`; live provider prerequisites are intentionally unspecified.

**Procedure as run.** Back up the config, detect an existing block, assert the insertion anchor, validate YAML with the gateway's Python, restart, poll liveliness for up to **40 s**, compare the authenticated model count, send a public sample, and print rollback. The public recipe defaults to a candidate-only edit; restart and probes are explicit, with offline mocks covering that branch.

| Result on 2026-09-23 | Condition and evidence |
|---|---|
| 27 models before and after | Unchanged [R] |
| Direct call: HTTP 200, 972 ms, $0.0000169, 403 input tokens | n=1, reported only [R] |
| Gateway call: 708 ms, $0.000015 | n=1 through a tunnel [R]; not an overhead measurement |
| Deployment sample: HTTP 200, 284 tokens, $0.0000119 | Reported only [R] |
| Three 37-item runs, 111 requests | 67,812 input tokens [M]; total cost $0.0028 [R] |

**What did not work.** A local port was a tunnel rather than the serving process. A content guard exempted local addresses even though the gateway forwarded onward. A debugging direct mode created another cloud exit. Provider-specific private wrapper code is not included; the source did not supply a public implementation or a verified public payload contract.

**Rule.** Include every used endpoint in the egress policy, not just chat. Validate the change and print rollback. The existing gate does not authorize arbitrary top-level pass-through changes; the [recipe page](docs/non-chat-egress.md) keeps that limitation explicit.

## Pitfalls

| Symptom | Root cause | Fix |
|---|---|---|
| Consumer succeeds but gateway sees no traffic | Missing credential causes opaque failure and client fallback | Test the consumer's own credential and fallback counter |
| Streaming hang outlasts deployment timeout | `stream_timeout` was not set | Declare both timeout keys and retry behavior |
| Partial answer followed by an error | Stream already started; fallback cannot replace it | Check error events and `[DONE]`, not HTTP status alone |
| Cap passes but request times out | Capacity counted without silence/generation budget | Test the complete path and both token counters |
| Bump appears to clear unrelated drift | Timestamp-only comparison | Keep an independent content baseline |
| Permit refuses the live base | Live config changed after signing | Re-sign from the verified base |
| Non-chat request bypasses content checks | Local gateway port treated like a local backend | Check gateway-bound content even on loopback |

[Expanded pitfalls](docs/pitfalls.md) retain all chapter-specific symptoms, causes, fixes, and unresolved cases.

## Reproduce it without our infrastructure

Prepare dependencies in advance. No GPU or cloud credential is needed for all-fake runs. The full LiteLLM matrix also needs `lsof`, `ps`, and a populated tokenizer cache; a fresh tokenizer installation may require provisioning before going offline. The exact cache contents were not supplied.

```bash
python3 -m venv .venv
source .venv/bin/activate
.venv/bin/python -m pip install 'litellm[proxy]==1.99.0' 'PyYAML==6.0.3' pytest
npm install openai@6.40
```

These are reader setup commands, not commands executed for this publication. Then, from the repository root:

1. Run `.venv/bin/python -B tests/run_offline.py`. Original gate/catalog suites use temporary synthetic evidence; additional unittest cases exercise applier refusal, all-fake injection, probe logic, and pass-through validation without a server.
2. Run `.venv/bin/python harness/trigger_matrix.py --config examples/gateway.example.yaml --plan-only`. Without LiteLLM, add `--mock-counter` for a clearly labeled synthetic plan; that mode cannot produce measurement evidence.
3. Run the same harness with `--all-fake --litellm .venv/bin/litellm`. A full run is expected to take about **20 minutes**, dominated by three entries times **(180 + 90 + 90) s** of deliberate waiting; this is an expectation from the source, not a new timing result.
4. Generate target-level evidence with `.venv/bin/python harness/emit_trigger_test.py --config examples/gateway.example.yaml --entry entry-a --target second-hop --all-fake --output trigger-example.json`. Copy evidence into a configured measurement directory before giving it to the gate.
5. Run `.venv/bin/python fixtures/catalog_demo.py` for loopback model-list probes and all five catalog checks against disposable synthetic state. Add `--mock-transport` when socket creation is unavailable.
6. Run `node probes/body_timeout_probe.mjs headers 290`, then `headers 310` and `noheaders 310`. Record your Node version and actual result; do not treat historical outcomes as expected pass assertions for a different runtime.
7. Use the fake service and probe commands in [silence-budget.md](docs/silence-budget.md) to test tokenizer/needle plumbing. Mock tokenizer counts do not measure model capacity or speed.
8. Copy the gateway example to a candidate and run `bash recipes/add_passthrough.sh --config candidate.yaml`. The default edits and validates only that copy; no service restart or provider call occurs.

For the authentication incident, follow [day-one-acceptance.md](docs/day-one-acceptance.md) in an isolated, already provisioned environment. The reproduction script has **not been re-run by us**. Do not install the privileged applier merely to run the offline tests.

## Limits and open questions

- Most observations are single runs. Matrix entries share configuration and fake servers; identical long prompts also share cache effects.
- Default-hang and keepalive baselines are [R]; their raw records are absent. The sixth claimed trigger control has no JSON record.
- Run-to-run fallback latency differences and the stall first-chunk discrepancy remain unexplained.
- Clean authentication reproduction, the `prisma` workaround, wrong-key behavior on 1.99.0, and newer releases remain unchecked.
- Diverted-request impact is unknown. The callback ledger was dead; callback/pre-call-hook behavior needs its own negative control before reuse.
- The queued-overflow scenario was not built or run. Several client silence limits are only source readings; the historical Node version is unknown.
- Gate volume was 14 permits in about three days in one deployment. Larger-volume friction and complete provider-prefix identity binding are unresolved.
- New offline applier tests replace the original absence of automated coverage; they do not validate installed ownership, sudoers, restart, or crash recovery.
- This publication does not rerun the long matrix, Node timing experiments, real backends, cloud calls, or privileged deployment. See [validation scope](docs/validation.md).
- No behavior claim extends beyond LiteLLM 1.99.0. Exact provider endpoint contracts and consumer fallback-counter integration remain reader prerequisites.

## Files

| Path | Purpose |
|---|---|
| `harness/` | Temporary gateway matrix, target-level trigger evidence, and offline planning tests |
| `gate/` | Route gate, root-owned applier, installer, and preserved rejection/mutation tests |
| `catalog/` | Validator, synthetic catalog, snapshot helper, and synthetic catalog tests |
| `probes/` | Node silence probe, prefill/needle probes, unrun authentication reproduction |
| `recipes/` | Candidate-only pass-through recipe and explicit restart/probe implementation |
| `examples/`, `fixtures/` | Synthetic routing/evidence schemas and loopback service fixtures |
| `day-one/acceptance.sh` | Credential-path acceptance adapter; consumer counter integration required |
| `tests/` | Hermetic runner and new standard-library unittest cases |
| `docs/results.md`, `docs/pitfalls.md` | Complete public tables and expanded failure modes |
| `docs/make_banner.py` | Banner script; `docs/assets/banner.png` was rendered from it |

## Related cookbooks


- [dell-pro-max-gb10-zero-downtime-model-swap](https://github.com/ryangu00/dell-pro-max-gb10-zero-downtime-model-swap): alias swaps and acceptance probes that fail when the claimed property is absent.
- [dell-pro-max-gb10-deepseek-v4-flash-vision-exp](https://github.com/ryangu00/dell-pro-max-gb10-deepseek-v4-flash-vision-exp): the already-published cold-prefill ladder, tokenizer mismatch, and gateway-timeout discussion.
- [ryanai-evalbank](https://github.com/ryangu00/ryanai-evalbank): public `results.json` quality-evidence schema consumed by the gate.
- [dell-pro-max-gb10-qwen3.8-flash-next-1m-context](https://github.com/ryangu00/dell-pro-max-gb10-qwen3.8-flash-next-1m-context): the long-context engine motivating explicit silence budgets.

## License

Apache-2.0. See [LICENSE](LICENSE).
