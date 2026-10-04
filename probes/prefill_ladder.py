#!/usr/bin/env python3
'Cold-prefill ladder with English needles and optional Linux memory monitoring.'
import argparse, json, os, random, subprocess, sys, threading, time, urllib.request

BASE = "http://127.0.0.1:9002"
MODEL = "model-y"
LADDER = [200000, 400000, 700000, 950000]
ABORT_GIB = 1.5
HOSTS = ()
OUT = os.path.dirname(os.path.abspath(__file__))
RUN = time.strftime("%Y%m%d-%H%M%S")
RES = os.path.join(OUT, f"results-{RUN}.json")
WAREHOUSES = "north south east west central".split()

def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)

def post(path, body, timeout=120):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)

def ntok(text):
    with post("/tokenize", {"model": MODEL, "prompt": text}, timeout=600) as r:
        return json.load(r)["count"]

def mem_avail():
    out = {}
    for h in HOSTS:
        try:
            r = subprocess.run(["/usr/bin/ssh", "-n", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=5", h,
                                "grep MemAvailable /proc/meminfo"], capture_output=True, text=True, timeout=15)
            out[h] = round(int(r.stdout.split()[1]) / 1048576, 2)
        except Exception:
            out[h] = None
    return out

def healthy():
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=10) as r:
            if r.status != 200: return False
        with post("/v1/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": "1+1=?  Reply with the number only "}],
                                            "max_tokens": 8, "temperature": 0,
                                            "chat_template_kwargs": {"thinking": False}}, timeout=300) as r:
            return "2" in json.load(r)["choices"][0]["message"]["content"]
    except Exception as e:
        log(f" health check failed : {e!r}")
        return False

def build(target, rng):
    'Build.'
    def line(i):
        return (f"Record {i} {rng.choice(WAREHOUSES)} warehouse SKU-{rng.randrange(16**8):08x}: "
                f"stock {rng.randrange(100000)}, batch {rng.randrange(16**6):06x}, "
                f"inspector {rng.choice('ABCDEFGH')}{rng.randrange(100)}.\n")
    sample = "".join(line(i) for i in range(400))
    per_line = ntok(sample) / 400
    if per_line <= 0 or target <= 200:
        raise ValueError("positive tokenizer count and target above framing allowance required")
    lines = [line(i) for i in range(int((target - 200) / per_line))]
    code = f"{rng.choice(WAREHOUSES)}-{rng.randrange(10**6):06d}-{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
    depth = rng.uniform(0.1, 0.9)
    lines.insert(int(len(lines) * depth), f"[IMPORTANT] The code for this document is {code}.\n")
    question = "\nWhat code appears in the [IMPORTANT] record? Reply with the code only."
    text = "".join(lines) + question
    tokens = ntok(text)
    while tokens > target:
        drop = max(1, int((tokens - target) / per_line) + 1)
        tail = [i for i in range(len(lines) - 1, -1, -1) if "[IMPORTANT]" not in lines[i]][:drop]
        if not tail: raise ValueError("target too small to retain needle")
        for i in tail: del lines[i]
        text = "".join(lines) + question
        tokens = ntok(text)
    return text, tokens, code, round(depth, 3)

def run_step(target, rng, results):
    text, toks, code, depth = build(target, rng)
    log(f" step  {target}:  actual  {toks} token, needle depth  {depth}, memory  {mem_avail()}")
    rec = {"target": target, "prompt_tokens_tokenize": toks, "needle_depth": depth, "started": time.strftime("%H:%M:%S")}
    stop = threading.Event(); mins = {h: None for h in HOSTS}; abort_reason = []
    holder = {}

    def watch():
        while not stop.is_set():
            m = mem_avail()
            for h, v in m.items():
                if v is not None and (mins[h] is None or v < mins[h]): mins[h] = v
            if any(v is not None and v < ABORT_GIB for v in m.values()):
                abort_reason.append(f" low memory  {m}"); log(f"!!  abort : memory  {m}")
                try: holder["resp"].close()
                except Exception: pass
                return
            stop.wait(15)
    th = threading.Thread(target=watch, daemon=True); th.start()
    t0 = time.time(); ttft = None; answer = ""; usage = None
    try:
        resp = post("/v1/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": text}],
                                              "max_tokens": 32, "temperature": 0, "stream": True,
                                              "stream_options": {"include_usage": True},
                                              "chat_template_kwargs": {"thinking": False}}, timeout=3600)
        holder["resp"] = resp
        for raw in resp:
            s = raw.decode("utf-8", "replace").strip()
            if not s.startswith("data:") or s == "data: [DONE]": continue
            d = json.loads(s[5:])
            if d.get("usage"): usage = d["usage"]
            for ch in d.get("choices") or []:
                piece = (ch.get("delta") or {}).get("content") or ""
                if piece and ttft is None: ttft = time.time() - t0
                answer += piece
        rec["error"] = abort_reason[0] if abort_reason else None
    except Exception as e:
        rec["error"] = abort_reason[0] if abort_reason else repr(e)[:300]
    finally:
        stop.set(); th.join(timeout=20)
        if holder.get("resp") is not None: holder["resp"].close()
    rec.update({"ttft_s": round(ttft, 1) if ttft else None, "total_s": round(time.time() - t0, 1),
                "prompt_tokens": (usage or {}).get("prompt_tokens"), "answer": answer.strip()[:80],
                "needle": code, "needle_ok": code in answer, "mem_min_gib": mins})
    if rec["ttft_s"] and rec["prompt_tokens"]:
        rec["prefill_tok_s"] = round(rec["prompt_tokens"] / rec["ttft_s"], 1)
    rec["healthy_after"] = healthy()
    results["steps"].append(rec)
    save(results)
    log(f" step  {target}  result : " + json.dumps({k: rec[k] for k in ("prompt_tokens", "ttft_s", "prefill_tok_s", "needle_ok",
                                                                   "error", "healthy_after", "mem_min_gib") if k in rec},
                                            ensure_ascii=False))
    return rec["error"] is None and rec["needle_ok"] and rec["healthy_after"]

def save(results):
    with open(RES, "w") as output:
        json.dump(results, output, indent=1)


def main(argv=None):
    global BASE, MODEL, LADDER, HOSTS, ABORT_GIB, RES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--tokens", nargs="+", type=int, default=LADDER)
    parser.add_argument("--memory-host", action="append", default=[])
    parser.add_argument("--abort-gib", type=float, default=ABORT_GIB)
    parser.add_argument("--output", default="results-prefill.json")
    args = parser.parse_args(argv)
    BASE, MODEL, LADDER = args.base_url.rstrip("/"), args.model, args.tokens
    HOSTS, ABORT_GIB, RES = tuple(args.memory_host), args.abort_gib, args.output
    rng = random.Random(f"{RUN}-{os.getpid()}")
    results = {"run": RUN, "base": BASE, "model": MODEL, "ladder": LADDER, "abort_gib": ABORT_GIB,
               "mem_before": mem_avail(), "steps": []}
    log(f"  start  run={RUN}  ladder ={LADDER}  result ={RES}  memory ={results['mem_before']}")
    if not healthy():
        results["verdict"] = " engine unhealthy before run , not tested "; save(results)
        log(" engine unhealthy before run , exit "); sys.exit(2)
    for target in LADDER:
        if not run_step(target, rng, results):
            results["verdict"] = f"FAIL at {target}"; break
    else:
        results["verdict"] = "PASS"
    results["mem_after"] = mem_avail()
    save(results)
    log(f"  finished : {results['verdict']}  result ={RES}")

    return 0 if results["verdict"] == "PASS" else 1

if __name__ == "__main__":
    sys.exit(main())
