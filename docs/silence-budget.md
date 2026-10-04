# Silence budgets and input caps

The [probe results and hop-by-hop limits](results.md#node-silence-probes) preserve measurement versus source-reading tags. The underlying question is which event resets each clock, not just how large each timeout is.

## Three different kinds of silence

Waiting for headers, waiting for any body byte, and waiting for semantic content are different states. A server can send headers immediately and then spend a long cold prefill producing no content. A heartbeat can satisfy a byte-level read timer without satisfying a semantic-content watchdog.

The shipped Node probe separates these cases. It runs a loopback HTTP server and delays its SSE body. In `headers` mode, `flushHeaders()` runs before the delay; in `noheaders`, it does not. The client uses the OpenAI Node SDK, traced `fetch`, and the same stream/SDK timeout/retry parameters as the recorded experiment.

```bash
node probes/body_timeout_probe.mjs headers 290
node probes/body_timeout_probe.mjs headers 310
node probes/body_timeout_probe.mjs noheaders 310
```

Read `header_ms` and `first_chunk_ms` separately. On failure retain `fail_ms`, message, and `cause_code`. The original no-header probe returned only a generic timeout, while the corrected body-wait case exposed `UND_ERR_BODY_TIMEOUT` [M]. The roughly 301.5 s observed termination was consistent with a documented 300 s limit checked at about 1.5 s granularity; this is an interpretation, not an independent timer-resolution measurement.

The source records OpenAI Node SDK 6.40 but not the Node version. A different Node/SDK combination is a new experiment. The public script loads `openai` from local `node_modules`; no private module path is used.

## A capacity decision also needs a time budget

The 2026-09-25 decision chose a second-hop cap of 262,144 rather than 1,000,000. Its first-token estimate was about 150 s [E], interpolated from the existing cold-prefill ladder. The recorded reasoning was that above about 450K tokens the first token exceeds a 300 s gateway budget, followed by a retry and eventual cloud fallback; a much longer timeout would also prolong every silent engine hang.

A review corrected the latency claim: a non-streaming completion includes generation time as well. The arithmetic for 8,192 output tokens at 32.8 tokens/s is about 250 s, added to about 150 s of prefill [E]. The second hop still inherited the global retry, so two silent attempts cost about 600 s [E]. The earlier matrix injected first-hop faults; it had not established all second-hop latency behavior.

The already-published ladder and one-sample tokenizer comparison belong in [dell-pro-max-gb10-deepseek-v4-flash-vision-exp](https://github.com/ryangu00/dell-pro-max-gb10-deepseek-v4-flash-vision-exp). Their detailed numeric rows are intentionally not republished here.

The new end-to-end evidence in this book is narrower: the larger backend-sized prompt was rejected by the gateway's count and reached the cloud with an empty answer; the smaller prompt reached the second hop and answered its needle. Both are single runs. A one-sample tokenizer mismatch does not justify a universal conversion ratio.

## Randomized prefill and a retained needle

`prefill_ladder.py` generates fresh English inventory records for each step. It calibrates a sample with the backend's `/tokenize`, constructs content near the requested target, inserts a code at a random depth, and re-counts. Trimming removes tail records while preserving the needle. The adaptation changes the original prompt language; it cannot reproduce the original content's token counts exactly.

Each streamed step records first content time, total time, response usage, answer correctness, and post-request health. A health check requires both `/health` and a small correct completion. Failure, wrong answer, unhealthy backend, or low memory stops the ladder.

Memory monitoring is opt-in with repeatable `--memory-host`; it reads Linux `MemAvailable` over SSH. It retains the original abort-on-low-memory mechanism. No deployment host is supplied by default. If monitoring is omitted, the empty memory record is not a memory-safety result. Remote OS and SSH prerequisites must be supplied by the reader.

The gateway probe imports the same builder, so its `--backend` must expose `/tokenize`. It checks expected serving group, fallback count, planted answer, and maximum elapsed time. Credentials come from the environment or an explicitly named file and are never printed.

## Exercise plumbing without an inference engine

In a terminal with loopback permission:

```bash
python fixtures/fake_services.py --port 9002 --model model-y --group second-hop
```

In another terminal:

```bash
python probes/prefill_ladder.py --base-url http://127.0.0.1:9002 \
  --model model-y --tokens 40000 --output synthetic-prefill.json
export LITELLM_MASTER_KEY=fixture-key
python probes/e2e_through_gateway.py --gateway http://127.0.0.1:9002 \
  --backend http://127.0.0.1:9002 --tokens 40000 --output synthetic-e2e.json
```

The fake returns a planted code and synthetic route headers. Its tokenizer is whitespace counting, and it does not implement LiteLLM routing. These commands validate request/response plumbing, not cold prefill, gateway routing, latency, or token capacity. The test suite exercises the same logic with in-memory transport when loopback sockets are unavailable.

For real capacity work, use the actual backend tokenizer and the actual caller path, randomize prompts to avoid cache reuse, and retain both correctness and timing. This book does not add a fresh hardware ladder or a new cap recommendation.
