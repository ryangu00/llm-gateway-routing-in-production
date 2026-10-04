#!/usr/bin/env python3
'Fault-injected temporary LiteLLM matrix. --all-fake isolates every backend on loopback.'
import argparse, hashlib, http.server, json, os, random, signal, socket, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from contextlib import ExitStack
import yaml

LIVE = os.path.expanduser("examples/gateway.example.yaml")
LITELLM = os.path.expanduser("litellm")
CRED = os.path.expanduser("credentials.env")
OUT_DIR = os.path.expanduser("evaluation/runs")
FIRST_HOP = "http://127.0.0.1:9001/v1"
SECOND_HOP = ("http://127.0.0.1:9002/v1", "http://127.0.0.1:9003/v1")
ENTRIES = ("entry-a", "entry-b", "entry-c")
SCENARIOS = ("both_local_dead", "input_150k", "input_40k_second_hop_dead", "first_hop_http500", "first_hop_hang", "first_hop_hang_stream", "first_hop_stall_stream")
TIMEOUTS = {"first_hop_hang": 3600, "first_hop_hang_stream": 1200, "first_hop_stall_stream": 1200, "first_hop_keepalive": 600}
SHORT = "17*23=? Reply with the number only."
HANG_S = 1800
ISOLATION_GROUP = "isolation-group"
MOCK_COUNTER = False

def cred_env(path=None, all_fake=False):
    'Cred env.'
    env = dict(os.environ)
    if all_fake:
        env["LITELLM_MASTER_KEY"] = "fixture-key"
        env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
        return env
    if path:
        with open(os.path.expanduser(path)) as source:
            for line in source:
                line = line.strip().removeprefix("export ")
                if not line or line.startswith("#") or "=" not in line: continue
                key, value = line.split("=", 1)
                if key.strip().isidentifier(): env[key.strip()] = value.strip().strip("\"'")
    if not env.get("LITELLM_MASTER_KEY"):
        raise ValueError("LITELLM_MASTER_KEY missing; use --all-fake for offline work")
    return env

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def port_open(p):
    with socket.socket() as s:
        s.settimeout(0.5); return s.connect_ex(("127.0.0.1", p)) == 0

class Fake(http.server.BaseHTTPRequestHandler):
    mode = "500"; stop = threading.Event()
    def do_POST(self):
        if self.mode == "ok":
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            common = {"id": "fake-ok", "created": int(time.time()), "model": request.get("model", "fake")}
            if request.get("stream"):
                chunks = [dict(common, object="chat.completion.chunk", choices=[{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]),
                          dict(common, object="chat.completion.chunk", choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])]
                b = ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                b = json.dumps(dict(common, object="chat.completion", choices=[{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                                    usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})).encode()
                content_type = "application/json"
            self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b); return
        if self.mode == "hang":
            self.stop.wait(HANG_S); return
        if self.mode == "keepalive":
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            end = time.monotonic() + HANG_S
            while not self.stop.wait(5) and time.monotonic() < end:
                try: self.wfile.write(b" "); self.wfile.flush()
                except OSError: return
            return
        if self.mode == "stall":
            chunk = {"id": "stall", "object": "chat.completion.chunk", "created": int(time.time()), "model": "m",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": "partial"}, "finish_reason": None}]}
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode()); self.wfile.flush()
            self.stop.wait(HANG_S); return
        b = b'{"error":{"message":"injected 500"}}'
        self.send_response(500); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    do_GET = do_POST
    def log_message(self, *a): pass

def fake_backend(mode):
    h = type(f"Fake_{mode}", (Fake,), {"mode": mode, "stop": threading.Event()})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"



class Gateway:
    'Own, verify, and clean up a temporary LiteLLM process group.'
    def __init__(self, rundir, name, cfg, env, expect_bases):
        self.port = free_port()
        if port_open(self.port): raise RuntimeError(f" port  {self.port}  already in use ")
        self.cfgp = os.path.join(rundir, f"{name}.yaml")
        with open(self.cfgp, "w") as f: f.write(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
        self.log = open(os.path.join(rundir, f"{name}.log"), "w")
        self.p = subprocess.Popen([LITELLM, "--config", self.cfgp, "--port", str(self.port), "--host", "127.0.0.1"],
                                  cwd=rundir, env=env, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        self.k = env["LITELLM_MASTER_KEY"]
        try:
            self._verify(name, expect_bases)
        except BaseException:
            self.close(); raise
    def _verify(self, name, expect_bases):
        t0 = time.time()
        while time.time() - t0 < 180:
            if self.p.poll() is not None: raise RuntimeError(f" temporary  LiteLLM  exit  rc={self.p.returncode}( see  {name}.log)")
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health/liveliness", timeout=2).status == 200: break
            except Exception: time.sleep(1)
        else:
            raise RuntimeError(" temporary  LiteLLM 180 s  not ready ")
        pids = subprocess.run(["/usr/sbin/lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout.split()
        pg = {subprocess.run(["/bin/ps", "-o", "pgid=", "-p", x], capture_output=True, text=True).stdout.strip() for x in pids}
        if not pids or pg != {str(self.p.pid)}: raise RuntimeError(f" port  {self.port}  listeners  {pids}( group  {pg}) does not belong to this process group  {self.p.pid}")
        info = self.get("/model/info")
        if isinstance(expect_bases, dict):
            for group_name, base in expect_bases.items():
                loaded = [d for d in info.get("data", []) if d.get("model_name") == group_name]
                if not loaded or any((d.get("litellm_params") or {}).get("api_base") != base for d in loaded):
                    raise RuntimeError(f"/model/info  of  {group_name}  did not load injected  api_base")
            expect_bases = expect_bases.values()
        bases = {str((d.get("litellm_params") or {}).get("api_base")) for d in info.get("data", [])}
        miss = set(expect_bases) - bases
        if miss: raise RuntimeError(f"/model/info  missing injected  api_base {sorted(miss)}: wrong configuration loaded ")
    def get(self, path):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers={"Authorization": f"Bearer {self.k}"})
        with urllib.request.urlopen(r, timeout=30) as response:
            return json.load(response)
    def chat(self, entry, text, timeout):
        body = json.dumps({"model": entry, "messages": [{"role": "user", "content": text}], "max_tokens": 20}).encode()
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", data=body,
                                   headers={"Authorization": f"Bearer {self.k}", "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                h = resp.headers; d = json.load(resp); code = resp.status; err = None
        except urllib.error.HTTPError as e:
            h, d, code, err = e.headers, {}, e.code, e.read()[:300].decode(errors="replace")
        except Exception as e:
            h, d, code, err = {}, {}, None, repr(e)[:300]
        return {"http": code, "seconds": round(time.time() - t0, 1), "group": h.get("x-litellm-model-group") if h else None,
                "model_name": h.get("x-litellm-model-name") if h else None, "api_base": h.get("x-litellm-model-api-base") if h else None,
                "attempted_fallbacks": h.get("x-litellm-attempted-fallbacks") if h else None,
                "prompt_tokens": (d.get("usage") or {}).get("prompt_tokens"), "error": err}
    def chat_stream(self, entry, text, timeout):
        'Read SSE incrementally; distinguish completion, error events, and client timeouts.'
        body = json.dumps({"model": entry, "messages": [{"role": "user", "content": text}], "max_tokens": 20, "stream": True}).encode()
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", data=body,
                                   headers={"Authorization": f"Bearer {self.k}", "Content-Type": "application/json"})
        t0 = time.time(); h, code, err, chunks, done, content, first, hung = {}, None, None, 0, False, "", None, False
        resp = None
        try:
            resp = urllib.request.urlopen(r, timeout=timeout); h = resp.headers; code = resp.status
            while True:
                line = resp.readline()
                if not line: break
                line = line.decode(errors="replace").strip()
                if line == "data: [DONE]": done = True; break
                if line.startswith("data: "):
                    chunks += 1; first = first or round(time.time() - t0, 1)
                    try:
                        d = json.loads(line[6:])
                        if d.get("error"): err = str(d["error"])[:300]
                        for c in d.get("choices") or []: content += (c.get("delta") or {}).get("content") or ""
                    except ValueError: pass
        except urllib.error.HTTPError as e:
            h, code, err = e.headers, e.code, e.read()[:300].decode(errors="replace")
        except (socket.timeout, TimeoutError) as e:
            hung, err = True, f" client read timeout  {timeout}s"
        except Exception as e:
            err = repr(e)[:300]
        finally:
            if resp is not None: resp.close()
        return {"http": code, "seconds": round(time.time() - t0, 1), "group": h.get("x-litellm-model-group") if h else None,
                "model_name": h.get("x-litellm-model-name") if h else None, "api_base": h.get("x-litellm-model-api-base") if h else None,
                "attempted_fallbacks": h.get("x-litellm-attempted-fallbacks") if h else None, "prompt_tokens": None,
                "stream": {"chunks": chunks, "first_chunk_s": first, "done": done, "content": content[:80], "hung": hung}, "error": err}
    def close(self):
        try: os.killpg(self.p.pid, signal.SIGTERM)
        except OSError: pass
        try: self.p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try: os.killpg(self.p.pid, signal.SIGKILL)
            except OSError: pass
            self.p.wait(timeout=10)
        for _ in range(20):
            if not port_open(self.port): break
            time.sleep(0.5)
        self.log.close()
        if port_open(self.port): raise RuntimeError(f" temporary  LiteLLM  after shutdown port  {self.port}  still listening ")

def bounded(fn, *a, deadline):
    'Bound whole-chain wait while preserving request exceptions as exceptions.'
    box = {}
    def run():
        try: box["r"] = fn(*a)
        except Exception as e: box["e"] = e
    th = threading.Thread(target=run, daemon=True); th.start(); th.join(deadline)
    if "r" in box: return box["r"]
    if "e" in box:
        return {"http": None, "seconds": None, "group": None, "model_name": None, "api_base": None, "attempted_fallbacks": None,
                "prompt_tokens": None, "stream": {"chunks": 0, "first_chunk_s": None, "done": False, "content": "", "hung": False},
                "error": f" request exception  {type(box['e']).__name__}: {str(box['e'])[:200]}"}
    return {"http": None, "seconds": float(deadline), "group": None, "model_name": None, "api_base": None, "attempted_fallbacks": None,
            "prompt_tokens": None, "stream": {"chunks": 0, "first_chunk_s": None, "done": False, "content": "", "hung": True},
            "error": f" whole chain exceeded wall-clock limit  {deadline}s"}

def words(n, rng=None):
    rng = rng or random
    return " ".join(f"word{rng.randint(0, 99999)}" for _ in range(n)) + "  Reply only  ok"


def count_prompt(text):

    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    if MOCK_COUNTER:
        return len(text.split())
    from litellm import token_counter
    return token_counter(messages=[{"role": "user", "content": text}])


def make_prompts():
    prompts = {"short": (SHORT, count_prompt(SHORT))}
    for name, target in (("input_150k", 150000), ("input_40k_second_hop_dead", 40000)):
        n = target // 3
        for _ in range(12):
            text = words(n, random.Random(target)); tokens = count_prompt(text)
            if target <= tokens < target + 1000:
                prompts[name] = (text, tokens); break
            n = max(1, int(n * (target + 100) / max(tokens, 1)))
        else:
            raise ValueError(f"{name}: cannot generate target within bounded attempts  LiteLLM token  count ")
    return prompts


def topology(cfg):
    models = {}
    for m in cfg["model_list"]:
        name = m["model_name"]
        if name in models: raise ValueError(f" duplicate model group  {name}: matrix requires one deployment per group ")
        models[name] = m
        cap = (m.get("model_info") or {}).get("max_input_tokens")
        if cap is not None and (type(cap) is not int or cap <= 0):
            raise ValueError(f"{name}.max_input_tokens  must be a positive integer ")
    for entry in ENTRIES:
        if entry not in models or models[entry]["litellm_params"].get("api_base") != FIRST_HOP:
            raise ValueError(f"{entry}  must use  first-hop {FIRST_HOP}")
    if ISOLATION_GROUP not in models: raise ValueError(" missing  isolation-group, cannot verify isolation ")
    rs = cfg.get("router_settings") or {}
    if rs.get("enable_pre_call_checks") is not True: raise ValueError(" requires  enable_pre_call_checks=true")
    chains = {}
    for section in ("fallbacks", "context_window_fallbacks"):
        chains[section] = {}
        for item in rs.get(section) or []:
            if not isinstance(item, dict) or len(item) != 1: raise ValueError(f"{section}  must be a list of single-key mappings ")
            entry, targets = next(iter(item.items()))
            if entry not in ENTRIES: continue
            if entry in chains[section]: raise ValueError(f"{section}.{entry}  duplicate ")
            if not isinstance(targets, list) or not targets or any(not isinstance(t, str) or t not in models or t == entry for t in targets):
                raise ValueError(f"{section}.{entry}  invalid targets ")
            if len(set(targets)) != len(targets): raise ValueError(f"{section}.{entry}  duplicate targets ")
            chains[section][entry] = targets
        if set(chains[section]) != set(ENTRIES): raise ValueError(f"{section}  missing entry chains ")
    return models, chains


def make_plan(cfg, prompts, scenarios=SCENARIOS, all_fake=False):
    models, chains = topology(cfg)
    plan = []
    for scenario in scenarios:
        long_input = scenario in ("input_150k", "input_40k_second_hop_dead")
        _, tokens = prompts[scenario if long_input else "short"]
        replacements = {}
        if scenario == "both_local_dead": replacements = {FIRST_HOP: "dead-first-hop", **{b: "dead-second-hop" for b in SECOND_HOP}}
        elif scenario == "input_40k_second_hop_dead": replacements = {b: "dead-second-hop" for b in SECOND_HOP}
        elif scenario.startswith("first_hop_"):
            replacements = {FIRST_HOP: {"first_hop_http500": "500", "first_hop_hang": "hang", "first_hop_hang_stream": "hang",
                                 "first_hop_stall_stream": "stall", "first_hop_keepalive": "keepalive"}[scenario]}
        injections = {}
        for name, m in models.items():
            base = m["litellm_params"].get("api_base")
            if base in replacements: injections[name] = replacements[base]


            if name == ISOLATION_GROUP or (long_input and base not in (FIRST_HOP, *SECOND_HOP)):
                injections[name] = "ok"
        if all_fake:
            for group in models:
                injections.setdefault(group, "ok")
        for entry in ENTRIES:
            cap = (models[entry].get("model_info") or {}).get("max_input_tokens")
            if cap is None or (tokens > cap) != long_input:
                raise ValueError(f"{scenario}/{entry}: input  {tokens}  versus entry cap  {cap}  violates scenario preconditions ")
            section = "context_window_fallbacks" if long_input else "fallbacks"
            chain = [entry] + chains[section][entry]
            if scenario == "first_hop_stall_stream":
                expect, nfb = entry, 0
            else:
                for nfb, target in enumerate(chain):
                    if injections.get(target) not in (None, "ok"): continue
                    limit = (models[target].get("model_info") or {}).get("max_input_tokens")
                    if long_input and limit is None:
                        raise ValueError(f"{scenario}/{entry}: target  {target}  missing  max_input_tokens, cannot derive window expectation ")
                    if limit is None or tokens <= limit:
                        expect = target; break
                else:
                    raise ValueError(f"{scenario}/{entry}: no eligible target in chain ")
            plan.append({"scenario": scenario, "entry": entry, "litellm_tokens": tokens,
                         "chain": chain, "injections": injections, "expect_group": expect,
                         "expect_fallbacks": nfb, "timeout": TIMEOUTS.get(scenario, 900),
                         "fake_ok": injections.get(expect) == "ok"})
    return chains, plan


def inject_config(cfg, injections, endpoints):
    c = json.loads(json.dumps(cfg))
    for m in c["model_list"]:
        mode = injections.get(m["model_name"])
        if mode is None: continue
        params = m["litellm_params"]
        params["api_base"] = endpoints[mode]
        params["api_key"] = "fake-local"
        if mode is not None:
            params["model"] = "openai/" + params["model"].split("/", 1)[-1]
            if "custom_llm_provider" in params: params["custom_llm_provider"] = "openai"
    return c


def close_fake(server):
    server.RequestHandlerClass.stop.set(); server.shutdown(); server.server_close()


def main(argv=None):
    global ENTRIES, FIRST_HOP, SECOND_HOP, ISOLATION_GROUP, LITELLM, MOCK_COUNTER
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=LIVE)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--skip-hang", action="store_true")
    parser.add_argument("--only", choices=SCENARIOS + ("first_hop_keepalive",))
    parser.add_argument("--entries", nargs="+", default=ENTRIES)
    parser.add_argument("--first-hop-base", default=FIRST_HOP)
    parser.add_argument("--second-hop-base", action="append")
    parser.add_argument("--isolation-group", default=ISOLATION_GROUP)
    parser.add_argument("--credentials-file")
    parser.add_argument("--litellm", default=LITELLM)
    parser.add_argument("--output-dir", default=OUT_DIR)
    parser.add_argument("--all-fake", action="store_true")
    parser.add_argument("--mock-counter", action="store_true", help="plan-only whitespace counter; never measurement evidence")
    args = parser.parse_args(argv)
    if args.mock_counter and not args.plan_only:
        parser.error("--mock-counter is restricted to --plan-only")
    ENTRIES, FIRST_HOP = tuple(args.entries), args.first_hop_base
    SECOND_HOP = tuple(args.second_hop_base or SECOND_HOP)
    ISOLATION_GROUP, LITELLM, MOCK_COUNTER = args.isolation_group, args.litellm, args.mock_counter
    cfg_path = os.path.abspath(os.path.expanduser(args.config))
    with open(cfg_path, "rb") as f: raw = f.read()
    cfg = yaml.safe_load(raw); sha = hashlib.sha256(raw).hexdigest()
    scenarios = [s for s in SCENARIOS if not (args.skip_hang and s in TIMEOUTS)]
    if args.only:
        scenarios = [args.only] if args.only in scenarios or args.only == "first_hop_keepalive" else []
    if not scenarios: parser.error(" selected scenario was  --skip-hang  excluded ")
    topology(cfg)
    prompts = make_prompts()
    chains, plan = make_plan(cfg, prompts, scenarios, all_fake=args.all_fake)
    if args.plan_only:
        print(json.dumps({"plan_only": True, "counter": "mock-whitespace" if MOCK_COUNTER else "litellm", "config": cfg_path, "config_sha256": sha,
                          "chains": chains, "plan": plan}, ensure_ascii=False, indent=2))
        return 0
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))
    env = cred_env(args.credentials_file, args.all_fake); results = []
    rundir = tempfile.mkdtemp(prefix="trigger-matrix-", dir=os.getcwd())
    with ExitStack() as cleanup:
        endpoints = {}
        modes = {mode for row in plan for mode in row["injections"].values()}
        for mode in sorted(modes):
            if mode.startswith("dead-"):

                sock = cleanup.enter_context(socket.socket()); sock.bind(("127.0.0.1", 0))
                endpoints[mode] = f"http://127.0.0.1:{sock.getsockname()[1]}/v1"
            else:
                server, endpoints[mode] = fake_backend(mode); cleanup.callback(close_fake, server)
        for scenario in scenarios:
            rows = [r for r in plan if r["scenario"] == scenario]
            injections = rows[0]["injections"]
            expected_bases = {name: endpoints[mode] for name, mode in injections.items()}
            gw = Gateway(rundir, scenario, inject_config(cfg, injections, endpoints), env, expected_bases)
            try:
                for row in rows:
                    e, exp, nfb, tmo = (row[k] for k in ("entry", "expect_group", "expect_fallbacks", "timeout"))
                    text, tokens = prompts.get(scenario, prompts["short"])
                    stream = scenario.endswith("_stream")
                    r = bounded(gw.chat_stream if stream else gw.chat, e, text, tmo, deadline=tmo + 30)
                    fb = r["attempted_fallbacks"]
                    fb_ok = isinstance(fb, str) and fb.isdigit() and int(fb) == nfb
                    ok = r["http"] == 200 and r["group"] == exp and fb_ok
                    if scenario == "first_hop_hang_stream":
                        ok = ok and r["stream"]["done"] and bool(r["stream"]["content"])
                    elif scenario == "first_hop_stall_stream":
                        ok = ok and r["stream"]["content"].startswith("partial") and not r["stream"]["hung"]
                    elif scenario == "input_150k":
                        ok = ok and 150000 <= tokens < 151000
                    elif scenario == "input_40k_second_hop_dead":
                        ok = ok and 40000 <= tokens < 41000
                    r.update(scenario=scenario, entry=e, expect_group=exp, expect_fallbacks=nfb,
                             litellm_tokens=tokens, fake_ok=row["fake_ok"], ok=bool(ok))
                    results.append(r); print(json.dumps(r, ensure_ascii=False), flush=True)
            finally:
                gw.close()
    ok = all(r["ok"] for r in results)
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{random.randrange(16**6):06x}"
    out = {"ts": time.strftime("%FT%T"), "run_id": run_id, "argv": list(argv) if argv is not None else sys.argv[1:], "config": os.path.abspath(os.path.expanduser(cfg_path)), "config_sha256": sha, "chains": chains, "pass": ok,
           "n": len(results), "failed": [f"{r['scenario']}/{r['entry']}" for r in results if not r["ok"]], "results": results,
           "note": "Group is the serving group; seconds is whole-chain client wait. Synthetic backends are flagged fake_ok."}
    os.makedirs(args.output_dir, exist_ok=True)
    p = os.path.join(args.output_dir, f"trigger-matrix-{run_id}.json")
    with open(p, "x") as f: json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"{'PASS' if ok else 'FAIL'} {len(results)}  requests , failed  {out['failed']} -> {p}")
    if ok:
        import shutil; shutil.rmtree(rundir, ignore_errors=True)
    else:
        print(f" diagnostics retained at  {rundir}")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
