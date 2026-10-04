# Results and evidence conditions

These are the complete public result tables from the source record, plus mechanism tables needed to interpret them. Historical raw files are not distributed. Synthetic fixtures and local regression output are not additional production measurements.

**[M]** measured with a retained raw record when the fact sheet was written; **[R]** reported only, raw record not retained or found; **[S]** source/configuration reading; **[U]** upstream/vendor documentation read on 2026-10-04; **[E]** calculated from numbers in the source.

## Authentication incident

Deployment and discovery are reported only; log counts were measured over the whole 80.4 MB file on 2026-10-04, not an incident-only window.

| Item | Value | Source tag |
|---|---|---|
| Gateway live | 2026-09-02 | [R] decision record |
| Discovered | 2026-09-06 (four days later) | [R] |
| Probes on the deployment day | 4/4 green | [R] recorded in the decision record |
| Gateway log lines containing `No connected db` | 13,422 | [M] `grep -c` on the whole 80.4 MB log file on 2026-10-04 |
| Gateway log lines containing `ModuleNotFoundError` | 20,748 | [M] same file, same day |
| Entry aliases answering 200 with the real key | 6 of 6 | [R] recorded in the incident note |
| Consumer profiles fixed | 3 (plus 4 title-generation configs) | [R] |
| Requests silently diverted during the four days | not known | not recorded: the callback ledger (below) was dead |

The two error counts include later keyless probes. The number of silently diverted requests is unknown; the callback ledger was dead.

## The gateway audit

Decision-record audit on 2026-09-06; [M] applies to the successful-request ledger check, not to every claim in the table.

| Why the gateway was introduced | Result on 2026-09-06 |
|---|---|
| Converge all cloud egress on one port so an outbound-content guard can judge by content | Delivered, and the only reason we kept the gateway |
| One config file for every consumer | Half true: 4 consumer surfaces actually used it, not the 6 planned |
| Unified spend ledger via a custom callback | Dead: one successful request added 0 rows; the file held 32 rows, all `kind: fail`, first `2026-09-01T23:26:31`, last `2026-09-02T15:52:27` [M] |
| Content-aware routing via a pre-call hook | Dead: the custom-logger pre-call hook did not fire in 1.99 (we recorded this the same day; the record cites upstream reports of a "bypass", which we did not re-check) |

## Fault modes

Harness design [S], loopback fake backends; these lifetimes and intervals are test inputs, not backend performance.

| Mode | Behaviour | Used for |
|---|---|---|
| dead port | a socket that is bound but never listens (kept open so nobody else can take the port) | both hops down |
| `500` | returns HTTP 500 with body `{"error":{"message":"injected 500"}}` | fast failure |
| `hang` | accepts the connection, never answers (lifetime 1800 s, then closes) | silent backend |
| `stall` | sends HTTP 200 and one valid SSE chunk with content `partial`, then silence | stall after first chunk |
| `keepalive` (opt-in, `--only`) | sends HTTP 200 headers, then one space byte every 5 s for up to 1800 s | a backend that trickles bytes while "working" |
| `ok` | returns a valid completion, JSON or full SSE, flagged `fake_ok` in results | isolation: any group that must not be reached for real |

## Scenario plan

Historical planned input counts [S] for the original prompt language; translated prompts are re-counted and do not promise these exact counts.

| Scenario | Injection | Input (LiteLLM-counted tokens) | Expected serving group | Expected fallbacks |
|---|---|---|---|---|
| `both_local_dead` | first and second hop dead | 17 | cloud tail (entry-specific) | 2 |
| `input_150k` | none; input above first-hop cap (32,768) | 150,099 | second hop | 1 |
| `input_40k_second_hop_dead` | second hop dead; first hop skipped by window check | 40,100 | long-context alias (fake `ok`) | 2 |
| `first_hop_http500` | first hop returns 500 | 17 | second hop | 1 |
| `first_hop_hang` | first hop hangs, non-streaming | 17 | second hop | 1 |
| `first_hop_hang_stream` | first hop hangs, streaming | 17 | second hop | 1 |
| `first_hop_stall_stream` | first hop stalls after one chunk | 17 | the entry itself | 0 |

## Matrix run A

2026-09-25 [M], three entries times five scenarios: 15 of 15 pass; first-hop timeout 180 s, stream_timeout 90 s, num_retries 0; request-time sum 608.7 s [E].

| Scenario | Seconds (3 entries) | Serving group | Fallbacks |
|---|---|---|---|
| first_hop_http500 | 0.6, 0.4, 0.4 | second hop | 1 |
| first_hop_hang | 180.6, 180.4, 180.3 | second hop | 1 |
| both_local_dead | 2.9, 3.0, 3.8 | cloud tail (real cloud calls) | 2 |
| input_150k (cap 131,072 at that time) | 9.7, 15.6, 7.5 | cloud tail (real cloud calls) | 2 |
| input_40k_second_hop_dead | 6.8, 10.8, 5.9 | cloud tail (real cloud calls) | 2 |

## Matrix run B

2026-09-27 [M], three entries times seven scenarios: 21 of 21 pass; request-time sum 1,385.1 s, about 23 minutes [E]; shared prompts and cache state make entries dependent.

| Scenario | Seconds (3 entries) | Serving group | Fallbacks | Notes |
|---|---|---|---|---|
| first_hop_http500 | 0.4, 0.3, 0.3 | second hop | 1 | |
| first_hop_hang | 180.5, 180.3, 180.3 | second hop | 1 | non-streaming, timeout 180 |
| first_hop_hang_stream | 90.5, 90.3, 90.3 | second hop | 1 | first chunk at 90.4, 90.2, 90.2 s; stream ended with `[DONE]` and the answer `391` (17*23) |
| first_hop_stall_stream | 90.2, 90.0, 90.0 | the entry itself | 0 | HTTP 200 already sent; content `partial`; stream then ended with an in-stream error event (`APIConnectionError ... Timeout on reading data from socket`, code `500`); `done=false` |
| both_local_dead | 27.0, 25.7, 26.9 | cloud tail (real cloud calls) | 2 | |
| input_150k (cap 250,000) | 162.5, 3.0, 3.0 | second hop (real, cold prefill) | 1 | the three entries send identical text: only the first is a cold prefill, the next two hit the prefix cache |
| input_40k_second_hop_dead | 17.9, 17.7, 18.0 | long-context alias (fake `ok`) | 2 | |

The first `input_150k` request was a real cold prefill; the next two used the same text and hit the prefix cache. In stall rows, a first chunk appeared at 0.2 s for one entry and at 90.0 s for the others; this was not investigated.

Both historical matrix runs included real cloud calls. The source describes real cloud calls for the timing comparison while labeling the later long-input destination as fake; the records do not isolate this ambiguity. Dead-port technique also differed. The 2.9 to 3.8 s versus 25.7 to 27.0 s difference and 5.9 to 10.8 s versus 17.7 to 18.0 s difference are unexplained, not a latency model.

## Earlier trigger records and reported baselines

Each retained row is tagged [M]; default-hang and keepalive claims are [R], raw files not retained. The earliest unbound file is rejected by the current gate.

| Record | What it shows | Source tag |
|---|---|---|
| 2026-09-25 13:22, four entries whose own backend and first fallback were both set to dead ports (the second hop was last in those chains) | 4 of 4 landed on the real second hop, 4.2 to 5.9 s, `attempted-fallbacks` = 2 each; JSON carries `covers` and the candidate sha | [M] |
| 2026-09-25 13:22, window chain from a directly called alias | 329,589 LiteLLM-counted tokens landed on the cloud tail (a fake `ok` backend; 1,088,154 request bytes arrived at the fake; 1 fallback); a short control stayed on the alias with 0 fallbacks | [M] |
| 2026-09-27 16:16, three files, five scenarios for one entry | first-hop-down 3.5 s (1 fallback); window chain to the second hop with 54,462 tokens in 52.1 s (1 fallback); window chain to the long-context alias 364,904 tokens (fake, 0.3 s, 1 fallback); cloud tail via normal chain 5.1 s (2 fallbacks, fake) and via window chain 364,904 tokens (fake, 0.4 s, 2 fallbacks) | [M] for the five rows; a 6/6 claim includes a sixth "control stays on entry" row that is claimed in the decision record but has no JSON record |
| 2026-09-25 02:05, the first trigger test | first-hop-down 6 of 6 and window 2 of 2 landed on the second hop; no `covers`, no candidate sha in the file, so the current gate would reject it as evidence | [M] |
| Default behaviour before the timeout keys | a hung first hop gave the user the second hop's answer after 603.2 s = request_timeout 300 x (1 + num_retries 1) + 3.2 s | [R] the raw result file is not on disk; the arithmetic matches the source reading below |
| A backend that sends 200 and then trickles whitespace | with timeout 180 configured, the request ran to the test client's 600 s deadline and no fallback occurred (in the narrative of the incident) | [R] raw result not retained; the public harness keeps the mode as `--only first_hop_keepalive` so it can be re-run |

The 2026-09-27 16:16 claim is **reported as 6/6, but only five JSON records exist**. The sixth control has no record. Do not convert that claim into six measurements.

## Node silence probes

2026-09-25 [M], one run per row, OpenAI Node SDK 6.40; Node version not recorded; stream=true, timeout=600000, maxRetries=0.

| Probe | Silent time | Result | Time | Error and cause |
|---|---|---|---|---|
| v1 (headers not flushed) | 290 s | ok | 290.016 s | |
| v1 | 310 s | fail | 301.527 s | `Request timed out.` (cause empty) |
| v2 `headers` | 290 s | ok | header at 11 ms, first chunk at 290,019 ms | |
| v2 `headers` | 310 s | fail | 301,493 ms | `terminated`, cause code `UND_ERR_BODY_TIMEOUT` |
| v2 `noheaders` | 310 s | fail | 301,490 ms | `Request timed out.` (underlying cause code not captured) |

## Through-gateway needle requests

2026-09-25 [M], one non-streaming random-content request per row, first-hop cap 32,768, second-hop gateway cap 262,144, needle at random depth.

| Sent (second-hop tokenizer count) | Gateway-counted | Landed on | Time | Fallbacks | Needle |
|---|---|---|---|---|---|
| content sized to about 240K tokens by the second hop's tokenizer (response usage 239,887) | over the 262,144 cap by the gateway's count | cloud tail | 23.5 s | 2 | not answered (empty answer; proves routing only) |
| 199,855 | within the cap | second hop | 102.7 s | 1 | correct |

## Silence-limit stack

Node rows are measured/documented; client and gateway rows marked [S] are configuration or code readings, not independent timing measurements.

| Hop | Limit | Resets on | Tag |
|---|---|---|---|
| Node `fetch` (undici) headers wait | 300 s | n/a | [M] / [U] |
| Node `fetch` (undici) body wait | 300 s | any body byte | [M] / [U] |
| LiteLLM request timeout (default) | 300 s (`request_timeout`) | any byte (httpx read timeout) | [S] |
| LiteLLM per deployment | `timeout`, `stream_timeout` | any byte | [S] |
| A coding-agent client library's stream idle watchdog | 300 s default; per its source, response headers, SSE comments and keepalives do not reset it | semantic content only | [S] reviewer's code reading |
| Four other client-side limits in the client stack | 300 s, 300 s, 600 s, 900 s; plus an agent-job wall clock of 1,800 s | varies | [S] configuration reading |

## Route-change classification

Gate design [S]; non-primary kinds can be combined with +, but primary cannot be mixed with them.

| Kind | What may change | Evidence required |
|---|---|---|
| `primary` (first hop) | model and `api_base` of the changed entries only; nothing else in the file (no router settings, no resilience keys, no other top-level section, no added or removed entries) | Quality on both the old and new endpoint (>= 2 runs each), same bank hash, same categories, same arm parameters; prefill measurements (>= 2 runs, not all equal); identity of what was measured equals what is written |
| `capacity` | only the `max_input_tokens` of declared entries (plus a `cap_reason`) | `caps` list: entry, new integer value > 0, reason; all other entry structure byte-identical |
| `fallback` | only `fallbacks` and `context_window_fallbacks` | a trigger test per changed edge, covering it, bound to the candidate hash |
| `resilience` | only `timeout`, `stream_timeout`, `num_retries` of declared entries | a full matrix run (Chapter 2) for this exact candidate, every scenario present, all pass, per-entry durations within budget |

## Catalog evidence

Historical source inventory and tests read on 2026-10-04 unless a row gives another date; original file sizes and suite counts do not describe the adapted public files.

| Item | Value | Tag |
|---|---|---|
| Validator size | 536 lines; earliest saved backup about 2.7 KB, current about 30.6 KB; 12 saved revisions between 2026-09-24 and 2026-09-25 | [M] backups on disk |
| Test suite | 728 lines; 65 collected, 65 pass on 2026-10-04 | [M] |
| Three tests read the real catalog (one of them also stats the real watched files) | need a synthetic fixture before shipping | [S] |
| Catalog backups | 30 dated copies between 2026-09-01 and 2026-10-03 | [M] |
| Content baseline | 15 registered watch entries on 2026-10-04 | [M] |
| Registered deliberate caps | 7, each with a `review_by` (4 on 2026-10-09, 3 on 2026-12-31) | [M] |
| Route switch not mirrored in the catalog | inconsistent for 7 to 11 hours (2026-09-23) | [R] |
| Gateway caps for the second hop understated | five entries at 131,072 while the backend served 1,000,000; about three days (2026-09-20 to 2026-09-23); requests above 131K could be pushed to the cloud | [R] |
| Unregistered helper deleted as "unreferenced" | image retrieval silently broke for about 15 hours (2026-09-04) | [R] |
| Direct write to the production gateway config | caught by check 4 (live hash differs from ledger) | [R] drift output quoted in the decision record |
| A restart rewrote a watched settings file with identical bytes | content hash equal to baseline, only mtime newer; the correct handling is to bump and register, not to treat it as drift | [R] |

## Non-chat egress

2026-09-23; single-call latencies are [R], not an overhead comparison; only the aggregate input-token total is [M].

| Item | Value | Tag |
|---|---|---|
| Model count in `/v1/models` before and after | 27, unchanged | [R] decision record |
| Direct call to the aggregator (n=1) | HTTP 200, 972 ms, cost $0.0000169, 403 input tokens | [R] |
| Same call through the gateway route (n=1, from a laptop through a tunnel) | 708 ms, cost $0.000015 | [R] n=1, not an overhead measurement |
| Sample probe at deployment | HTTP 200, 284 tokens, cost $0.0000119 | [R] |
| Three runs of a 37-item set (111 requests) through the aggregator | 67,812 input tokens in total [M]; total cost $0.0028 [R] | mixed |

## Second-hop cap history

Configuration history [S]; these are gateway input caps, not measured latency guarantees. Hardware: Dell Pro Max with GB10; two machines in tensor parallel until 2026-09-26, one afterwards.

| Date or interval | Gateway cap | Condition |
|---|---|---|
| Until 2026-09-23 | 131,072 | Understated relative to the backend |
| 2026-09-23 | 1,000,000 | Raised in configuration |
| 2026-09-25 01:57 | 131,072 | Narrowed pending cold-prefill acceptance |
| 2026-09-25 12:29 | 262,144 | Selected after the ladder |
| 2026-09-26 15:10 | 250,000 | Backend max_model_len 256,000 minus about 6K output margin |

## Gate ledger and serial change

Ledger read on 2026-10-04 [M]; permits are not proof of application. The source covers one deployment and one operator group.

| Item | Value | Condition |
|---|---|---|
| Rows | 15 = 1 accept + 14 permits | Accept at 2026-09-25 01:10; permits from 2026-09-25 01:10 to 2026-09-27 20:05 |
| Decision-record grouping | 6 + 4 + 3 + 1 permits | Four decision records; identifiers withheld |
| Kind distribution | primary 4; capacity 3; capacity+fallback 2; resilience 2; fallback 3 | Frozen evidence classification |
| Permits with base_sha256 | 4 | Signed on 2026-09-27 after the applier existed |
| Earlier permits without base_sha256 | 10 | Earlier format |
| Serial change | primary 13:05; primary 14:26; fallback 15:05; capacity 15:10 | 2026-09-26 |
| Elapsed first to last permit | 2 h 5 min [E] | Operator estimate beforehand: 3 to 4 hours and three reloads |
| Tail-trigger traffic | Four real cloud calls, about 268K tokens each, 11.6 to 17.5 s [M] | Third step of that change; new predecessors required evidence |
| Applier installed | 2026-09-26 15:31 | Direct bypass earlier at 2026-09-26 01:42 was caught by hash drift [R] |
| Manual applier cases | Six [R] | No-op apply, replay, swapped candidate, base mismatch, expired, non-root; no original automated test |
| Original gate suite | 125 collected; 122 pass, 3 fail [M] | 2026-10-04; failures assert stale messages, rejection still correct |

## Calculations and reported comparisons

These are [E] calculations or [R] historical reports, never newly measured results.

| Item | Value | Condition and qualifier |
|---|---|---|
| Default silent hang | 603.2 s [R] | 300 x (1 + 1) + 3.2 s; raw result not retained |
| Selected cap first-token estimate | about 150 s [E] at 262,144 | Interpolated from the published second-hop cold ladder; see the related vision cookbook |
| Non-streaming output allowance | 8,192 tokens, about 250 s at 32.8 tokens/s [E] | Added to about 150 s prefill; capacity alone does not fit the timeout |
| Second-hop inherited retry | two attempts, about 600 s [E] | Silent 300 s timeout with one retry |
| Cached prefill report | 28,894 tokens/s versus 2,318 true [R] | 64K prompt on second-hop hardware; fixed repeated prompt hit prefix cache |
| Run-to-run latency ratios | roughly 7x to 9x; 1.6x to 3x | Unexplained both-local-dead and long-input/dead-second-hop differences, respectively |

The already-published cold ladder and tokenizer mismatch are not reproduced here. See [silence-budget.md](silence-budget.md) for the named related repository and how these results constrain the gateway.
