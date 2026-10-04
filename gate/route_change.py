#!/usr/bin/env python3
'Evidence gate: hash-bound, kind-specific route-change permits. See docs/gate-evidence-schema.md.'
import argparse, glob, hashlib, json, os, re, shlex, statistics, sys, time
from datetime import date, timedelta

ADR_DIR = os.path.expanduser(os.environ.get("ROUTE_DECISION_DIR", "decisions"))
STATE_DIR = os.path.expanduser(os.environ.get("ROUTE_STATE_DIR", "state/route-change"))
PERMIT_DIR = os.path.join(STATE_DIR, "permits")
LEDGER = os.path.join(STATE_DIR, "ledger.jsonl")
EVALBANK = os.path.expanduser(os.environ.get("ROUTE_EVALUATION_DIR", "evaluation"))
TRIGGER_ROOT = os.path.expanduser(os.environ.get("ROUTE_MEASUREMENT_ROOT", "workspace"))

HOST = os.environ.get("ROUTE_HOST", "gateway.example.invalid")
REMOTE_CFG = os.environ.get("ROUTE_REMOTE_CONFIG", "/usr/local/etc/litellm-gateway/config.yaml")
STAGING = os.environ.get("ROUTE_STAGING_DIR", "/usr/local/etc/litellm-gateway/staging")
DRIFT_KEY = os.environ.get("ROUTE_DRIFT_WATCH", "examples/gateway.example.yaml")
LABEL = os.environ.get("ROUTE_SERVICE_LABEL", "example.litellm-gateway")
ADR_MAX_AGE = 2 * 3600
PERMIT_TTL = 30 * 60
SHA_PREFIX = 12

def num(x):
    import math
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

def _no_nonfinite(c):
    raise ValueError(f"JSON  contains nonfinite constant  {c}")

def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()

def bsha(b):
    return hashlib.sha256(b).hexdigest()

CAND_SHA_RE = re.compile(r"[0-9a-f]{12}|[0-9a-f]{64}")

def inside(path, root):
    return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)

def measure_dirs():
    return [os.path.join(TRIGGER_ROOT, "research"), os.path.join(EVALBANK, "runs")]

def load_measured(src):
    'Read a JSON measurement under an approved evidence directory.'
    t = os.path.expanduser(str(src or ""))
    if not t or not os.path.isfile(t):
        raise ValueError(f"{src!r}  missing file ")
    if not any(inside(t, r) for r in measure_dirs()):
        raise ValueError(f"{t}  outside  {'  or  '.join(r + '/' for r in measure_dirs())}  under (decision records/catalog files  decision records are not measurements )")
    try:
        d = json.load(open(t), parse_constant=_no_nonfinite)
    except ValueError:
        d = None
    if not isinstance(d, dict):
        raise ValueError(f"{t}  is not  JSON object")
    return t, d

def load_results(src):
    'Read evaluation results.json under the configured runs directory.'
    p = os.path.expanduser(str(src))
    if not os.path.isabs(p): p = os.path.join(EVALBANK, p)
    if os.path.isdir(p): p = os.path.join(p, "results.json")
    if not inside(p, os.path.join(EVALBANK, "runs")):
        raise ValueError(f"{src}  outside  {EVALBANK}/runs/  under ")
    if not os.path.isfile(p):
        raise ValueError(f"{src}  cannot find  results.json")
    d = json.load(open(p))
    if not isinstance(d.get("rows"), list) or not d["rows"]:
        raise ValueError(f"{src}  of  results.json  missing  rows")
    return p, d

def per_run_scores(d, metric):
    rows = [r for r in d["rows"] if metric == "all" or str(r.get("kind", "")).startswith(metric)]
    n = max((len(r.get("runs") or []) for r in rows), default=0)
    out = []
    for i in range(n):
        v = [r["runs"][i] for r in rows if len(r.get("runs") or []) > i and num(r["runs"][i])]
        if v: out.append(statistics.mean(v))
    return out

def check_side(ev, side, metric):
    'Validate scores, complete runs, comparable arms, and measured endpoint identity.'
    s = ev.get(side)
    if not isinstance(s, dict): return [f"evidence  missing  {side}  side ( both sides required )"], set(), set(), set()
    p, keys, ids, paths = [], set(), set(), set()
    runs = s.get("quality_runs")
    if not isinstance(runs, list) or not runs or not all(num(r) and 0 <= r <= 100 for r in runs):
        p.append(f"{side}.quality_runs  missing or not  0-100  numeric list (evaluation  quality )")
        runs = None
    elif len(runs) < 2:
        p.append(f"{side}.quality_runs  only  {len(runs)}  runs , at least  2  runs for median ")
    srcs = s.get("quality_src")
    srcs = [srcs] if isinstance(srcs, str) else srcs
    if not srcs or not all(str(x).strip() for x in srcs):
        p.append(f"{side}.quality_src  missing (evaluation runs/<id>  path )")
    else:
        computed = []
        for x in srcs:
            try:
                path, d = load_results(x)
            except Exception as e:
                p.append(f"{side}.quality_src {e}"); continue
            if os.path.realpath(path) in paths:
                p.append(f"{side}.quality_src  duplicate reference to same  results.json {os.path.realpath(path)}"); continue
            sel = [r for r in d["rows"] if metric == "all" or str(r.get("kind", "")).startswith(metric)]
            nr = d.get("runs")
            if not (type(nr) is int and nr >= 1 and sel and all(isinstance(r.get("runs"), list) and len(r["runs"]) == nr
                                                             and all(num(v) for v in r["runs"]) for r in sel)):
                p.append(f"{side}.quality_src {os.path.realpath(path)}  incomplete scores :quality_metric={metric}  each category requires exactly  runs={nr!r}  finite scores ")
                paths.add(os.path.realpath(path)); continue
            bh, cats, rc = d.get("bank_hash"), d.get("cats"), [r.get("cat") for r in sel]
            if not (isinstance(bh, str) and bh.strip() and isinstance(cats, list) and cats
                    and all(isinstance(c, str) and c.strip() for c in cats) and len(set(cats)) == len(cats)
                    and all(isinstance(c, str) and c in cats for c in rc) and len(set(rc)) == len(rc)
                    and (metric != "all" or set(rc) == set(cats))):
                p.append(f"{side}.quality_src {os.path.realpath(path)}  incomplete metadata :bank_hash  must be a nonempty string ,cats  must be nonempty without duplicates ,"
                         f"rows  of  cat  must belong to  cats  without duplicates " + (",quality_metric=all  when  cats  every category needs a row " if metric == "all" else ""))
                paths.add(os.path.realpath(path)); continue
            computed += per_run_scores(d, metric)
            arm = tuple(str(d.get(k)) for k in ARM_KEYS)
            keys.add((bh, tuple(sorted(cats)), tuple(sorted(rc)), arm))
            ids.add((str(d.get("model") or ""), str(d.get("base_url") or "").rstrip("/")))
            paths.add(os.path.realpath(path))
        if any(not m or not b for m, b in ids):
            p.append(f"{side}.quality_src  of  results.json  missing  model/base_url( endpoint identity unavailable )")
        elif len(ids) > 1:
            p.append(f"{side}.quality_src  mixed endpoints / models  run:{sorted(ids)}")
        for k, i in (("model", 0), ("base_url", 1)):
            if s.get(k) and ids and {str(s[k]).rstrip("/")} != {x[i] for x in ids}:
                p.append(f"{side}.{k} {s[k]!r}  and  results.json  of  {sorted(x[i] for x in ids)}  does not match ")
        if runs and not computed:
            p.append(f"{side}.quality_src  of  results.json  using  quality_metric={metric}  cannot compute per-run totals (rows[].runs  empty or nonnumeric )")
        elif runs and (len(computed) != len(runs) or any(
                abs(a - b) > 0.1 for a, b in zip(sorted(runs), sorted(computed)))):
            p.append(f"{side}.quality_runs {runs}  and  results.json  computed per-run totals  "
                     f"{[round(c, 1) for c in computed]}  does not match (quality_metric={metric})")
    for k, what in (("prefill_tps", "prefill"), ("decode_tps", "decode")):
        if not (num(s.get(k)) and s[k] > 0): p.append(f"{side}.{k}  missing or nonpositive ({what})")
    if num(s.get("prefill_tps")) and s["prefill_tps"] > 0:
        try:
            _, d = load_measured(s.get("prefill_src"))
            pid_ = (str(d.get("model") or ""), str(d.get("base_url") or "").rstrip("/"))
            if not all(pid_):
                p.append(f"{side}.prefill_src  missing  model/base_url( endpoint identity unavailable )")
            elif ids and pid_ not in ids:
                p.append(f"{side}.prefill_src  measured identity  {pid_}, does not match quality evidence identity  {sorted(ids)}")
            runs = d.get("prefill_tps_runs")
            if not (isinstance(runs, list) and len(runs) >= 2 and all(num(r) and r > 0 for r in runs)):
                p.append(f"{side}.prefill_src  missing  prefill_tps_runs(≥2  positive runs )")
            elif len(set(runs)) == 1:
                p.append(f"{side}.prefill_src  all runs identical  {runs}: possible fixed  prompt  repeated cache hits  prefix cache(possible prefix-cache reuse)")
            elif abs(statistics.median(runs) - s["prefill_tps"]) > 0.02 * s["prefill_tps"]:
                p.append(f"{side}.prefill_tps {s['prefill_tps']}  and  prefill_src  median  {statistics.median(runs)}  difference  >2%")
        except ValueError as e:
            p.append(f"{side}.prefill_src {e}")
    return p, keys, ids, paths

def check_adr(adr, cand_sha):
    'Require a unique fresh decision record naming the candidate hash and date.'
    if not re.fullmatch(r"ADR-\d{4}", adr or ""):
        return [f"--adr  format must be  ADR-NNNN, received  {adr!r}"], None
    hits = sorted(glob.glob(os.path.join(ADR_DIR, adr + "*.md")))
    if not hits: return [f"{ADR_DIR}  has no  {adr}*.md( write first  ADR  before changing routes )"], None
    if len(hits) > 1: return [f"{adr}  duplicate decision number :{[os.path.basename(h) for h in hits]}"], None
    f, p = hits[0], []
    age = time.time() - os.path.getmtime(f)
    if age > ADR_MAX_AGE:
        p.append(f"{os.path.basename(f)} mtime  already  {age/3600:.1f}  hours  > 2  hours (ADR  must be written at signing ,)")
    body = open(f, errors="ignore").read()
    if cand_sha and cand_sha[:SHA_PREFIX] not in body:
        p.append(f"{os.path.basename(f)}  body lacks candidate  config  of  sha256  prefix  {SHA_PREFIX}  digits  {cand_sha[:SHA_PREFIX]}"
                 "(ADR  must identify the exact candidate  config;touch  old  ADR  will not pass )")
    ok_days = {date.today().isoformat(), (date.today() - timedelta(days=1)).isoformat()}
    if not ok_days & set(re.findall(r"\d{4}-\d{2}-\d{2}", body)):
        p.append(f"{os.path.basename(f)}  body lacks today or yesterday date ( freshness checked in body , not from  mtime)")
    return p, f

def check_candidate(candidate):
    try:
        import yaml
        doc = yaml.safe_load(open(candidate))
    except Exception as e:
        return [f" candidate  config {candidate}  unreadable or invalid  YAML:{str(e).splitlines()[0]}"]
    ml = doc.get("model_list") if isinstance(doc, dict) else None
    if not (isinstance(ml, list) and ml and all(isinstance(m, dict) and m.get("model_name") for m in ml)):
        return [f" candidate  config {candidate}  missing nonempty  model_list( every entry needs  model_name)"]
    p = []
    for m in ml:
        mi = m.get("model_info")
        if isinstance(mi, dict) and "max_input_tokens" in mi and not (type(mi["max_input_tokens"]) is int and mi["max_input_tokens"] > 0):
            p.append(f"{m.get('model_name')}  candidate  max_input_tokens  must be a positive integer , received  {mi['max_input_tokens']!r}")
        lp = m.get("litellm_params")
        if isinstance(lp, dict):
            for k in ("timeout", "stream_timeout"):
                if k in lp and not (num(lp[k]) and lp[k] > 0):
                    p.append(f"{m.get('model_name')}  candidate  litellm_params.{k}  must be finite positive , received  {lp[k]!r}")
            if "num_retries" in lp and not (type(lp["num_retries"]) is int and lp["num_retries"] >= 0):
                p.append(f"{m.get('model_name')}  candidate  litellm_params.num_retries  must be a nonnegative integer , received  {lp['num_retries']!r}")
    return p

def check_fallbacks(fb):
    'Check fallbacks.'
    p = []
    if not isinstance(fb, list):
        p.append("evidence  missing  fallbacks( unchanged for this operation  fallback  write  [];:fallback  unmeasured justification )")
    else:
        for i, f in enumerate(fb):
            name = str((f or {}).get("name") or "")
            t = os.path.expanduser(str((f or {}).get("trigger_test") or ""))
            if not name or not t or not os.path.isfile(t):
                p.append(f"fallbacks[{i}] {name}  missing trigger evidence file  trigger_test( or missing file )")
                continue
            try:
                _, d = load_measured(t)
            except ValueError as e:
                p.append(f"fallbacks[{i}] {name}  of  trigger_test {e}()"); continue
            n = d.get("triggered")
            if name not in open(t, errors="ignore").read():
                p.append(f"fallbacks[{i}] trigger_test {t}  body does not mention  {name}( not this  fallback  measurement )")
            elif d.get("fallback") != name or not (num(n) and n > 0):
                p.append(f"fallbacks[{i}] trigger_test {t}  is not verifiable trigger evidence : requires  \"fallback\": \"{name}\"  and  "
                         f"\"triggered\": N>0( requests served by this  fallback  request count ), actual  fallback={d.get('fallback')!r} triggered={n!r}")
    return p

def read_ledger():
    'Return valid ledger rows; a corrupt ledger is never an empty ledger.'
    try: lines = [l for l in open(LEDGER, encoding="utf-8") if l.strip()]
    except FileNotFoundError: return []
    except OSError as e: raise ValueError(f" unreadable :{e}")
    out = []
    for i, l in enumerate(lines, 1):
        try: e = json.loads(l)
        except ValueError as x: raise ValueError(f" row  {i}  row is invalid  JSON({x})")
        if not isinstance(e, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(e.get("candidate_sha256"))):
            raise ValueError(f" row  {i}  row must contain  64  digits  candidate_sha256  of  JSON object")
        out.append(e)
    return out

def base_vs_ledger(base):
    'Base vs ledger.'
    try: L = read_ledger()
    except ValueError as e: return [f" ledger  {LEDGER}  corrupt ({e}), cannot identify live version ; repair the ledger first "]
    last = L[-1]["candidate_sha256"] if L else None
    return [] if sha256(base) == last else [f"--base {sha256(base)[:SHA_PREFIX]}  not the last registered ledger version  {str(last)[:SHA_PREFIX]}"]

def _norm_entry(m):
    'Normalize only the fields handled by capacity and resilience checks.'
    import copy
    e = copy.deepcopy(m)
    if isinstance(e.get("model_info"), dict):
        e["model_info"].pop("max_input_tokens", None); e["model_info"].pop("cap_reason", None)
    if isinstance(e.get("litellm_params"), dict):
        for k in RES_KEYS: e["litellm_params"].pop(k, None)
    return json.dumps(e, sort_keys=True, ensure_ascii=False, default=str)

RES_KEYS = ("timeout", "stream_timeout", "num_retries")
ARM_KEYS = ("tier", "grader_version", "recipe_arm", "thinking")
DEFAULT_API_BASE = {"openrouter": "https://openrouter.ai/api/v1"}
RES_SCENARIOS = ("both_local_dead", "input_150k", "input_40k_second_hop_dead", "first_hop_http500", "first_hop_hang", "first_hop_hang_stream", "first_hop_stall_stream")
HANG_BUDGET = {"first_hop_hang": "timeout", "first_hop_hang_stream": "stream_timeout", "first_hop_stall_stream": "stream_timeout"}

def check_resilience(ev, kinds, b, c, cand_sha, rs=None):
    'Require explicit timeout keys and a passing candidate-bound matrix within budgets.'
    p = []
    lp = lambda m: (m.get("litellm_params") or {}) if isinstance(m.get("litellm_params"), dict) else {}
    changed = {}
    for n in sorted(set(b) & set(c), key=str):
        diff = {k: lp(c[n]).get(k) for k in RES_KEYS if json.dumps(lp(b[n]).get(k)) != json.dumps(lp(c[n]).get(k))}
        if diff: changed[n] = diff
    for n in sorted(c, key=str):
        cp = lp(c[n])
        for k in ("timeout", "stream_timeout"):
            if k in cp and not (num(cp[k]) and cp[k] > 0): p.append(f"{n} litellm_params.{k}  must be positive , received  {cp[k]!r}")
        if "num_retries" in cp and not (type(cp["num_retries"]) is int and cp["num_retries"] >= 0):
            p.append(f"{n} litellm_params.num_retries  must be a nonnegative integer , received  {cp['num_retries']!r}")
    if changed and "resilience" not in kinds:
        p.append(f" resilience settings changed ({sorted(changed)}) but  kind  does not include  resilience")
    if "resilience" not in kinds: return p
    decl = ev.get("resilience")
    if not (isinstance(decl, list) and decl and all(isinstance(x, dict) and x.get("entry") for x in decl)):
        return p + ["kind  contains  resilience  but  evidence.resilience  must be nonempty  [{entry, …}]  list "]
    dmap = {x["entry"]: x for x in decl}
    rs = rs or {}
    for k in ("retry_policy", "model_group_retry_policy"):
        if rs.get(k): p.append(f" candidate  router_settings.{k}  exists : overrides deployment  num_retries,resilience  budget undefined ")
    for n, x in dmap.items():
        cp = lp(c[n]) if n in c else {}
        for k in RES_KEYS:
            if k not in x: p.append(f"resilience[{n}]  must explicitly declare  {k}( do not infer budgets from defaults )")
            if k not in cp: p.append(f"{n}  candidate  litellm_params  must explicitly configure  {k}")
        if not (num(x.get("timeout")) and x["timeout"] > 0 and num(x.get("stream_timeout")) and x["stream_timeout"] > 0
                and type(x.get("num_retries")) is int and x["num_retries"] >= 0):
            p.append(f"resilience[{n}]  of  timeout/stream_timeout  must be finite positive , num_retries  must be a nonnegative integer ")
    for n, diff in changed.items():
        x = dmap.get(n)
        if x is None: p.append(f"{n}  resilience settings changed  {diff}  but  evidence.resilience  undeclared "); continue
        for k in RES_KEYS:
            if json.dumps(lp(c[n]).get(k)) != json.dumps(x.get(k)):
                p.append(f"{n}  of  {k}  candidate  {lp(c[n]).get(k)!r}  versus declaration  {x.get(k)!r}  mismatch ")
        if not (isinstance(x.get("reason"), str) and x["reason"].strip()): p.append(f"resilience[{n}]  missing  reason")
    for n in sorted(set(dmap) - set(changed)): p.append(f"resilience  declared  {n}  but resilience settings did not change ")
    try:
        _, d = load_measured(ev.get("matrix"))
    except ValueError as e:
        return p + [f"evidence.matrix {e}( must be  trigger_matrix.py  results for this candidate )"]
    if not (cand_sha and d.get("config_sha256") == cand_sha):
        p.append(f"matrix  of  config_sha256 {str(d.get('config_sha256'))[:SHA_PREFIX]}  not this candidate full  sha {str(cand_sha)[:SHA_PREFIX]}")
    res = d.get("results") if isinstance(d.get("results"), list) else []
    if d.get("pass") is not True or not res or not all(isinstance(r, dict) and r.get("ok") is True for r in res):
        p.append("matrix  not all passed (pass  must be  true  and every row  ok)")
    seen = {r.get("scenario") for r in res if isinstance(r, dict)}
    if set(RES_SCENARIOS) - seen: p.append(f"matrix  missing scenarios  {sorted(set(RES_SCENARIOS) - seen)}")
    for n in changed:
        x = dmap.get(n) or {}
        if not (num(x.get("timeout")) and num(x.get("stream_timeout")) and type(x.get("num_retries")) is int): continue
        for sc, key in HANG_BUDGET.items():
            budget = x[key] * (x["num_retries"] + 1) + 60
            rows = [r for r in res if isinstance(r, dict) and r.get("scenario") == sc and r.get("entry") == n]
            if not rows: p.append(f"matrix  missing  {n}  of  {sc}  measurement ")
            elif not all(num(r.get("seconds")) and 0 <= r["seconds"] <= budget for r in rows):
                p.append(f"{n}  of  {sc}  duration  {[r.get('seconds') for r in rows]}  exceeds budget  {budget} s({key}*( retries +1)+60)")
    return p

def _fb_map(doc, sec, names, p, label):
    ' fb map.'
    v = (doc.get("router_settings") or {}).get(sec)
    if v is None: return {}
    out = {}
    if not isinstance(v, list):
        if p is not None: p.append(f"{label} {sec}  must be a list ")
        return {}
    for i, item in enumerate(v):
        if not (isinstance(item, dict) and len(item) == 1):
            if p is not None: p.append(f"{label} {sec}[{i}]  must be a single-key mapping ")
            continue
        (src, tg), = item.items()
        bad = []
        if src not in names: bad.append(f" source  {src!r}  does not reference existing  model_name")
        if src in out: bad.append(f" source  {src!r}  duplicate definition ")
        if not (isinstance(tg, list) and tg and all(isinstance(t, str) and t for t in tg)):
            bad.append(" targets must be a nonempty string list ( parameter dictionaries forbidden )"); tg = []
        for t in tg:
            if t not in names: bad.append(f" target  {t!r}  does not reference existing  model_name")
            if t == src: bad.append(" self-cycle ")
        if len(set(tg)) != len(tg): bad.append(" duplicate target ")
        if bad and p is not None: p += [f"{label} {sec}[{src}] {x}" for x in bad]
        out[src] = list(tg)
    return out

def _cycle(m):
    ' cycle.'
    state = {}
    def dfs(n, path):
        if state.get(n) == 1: return path[path.index(n):] + [n]
        if state.get(n) == 2: return None
        state[n] = 1
        for t in m.get(n, []):
            r = dfs(t, path + [n])
            if r: return r
        state[n] = 2
        return None
    for n in list(m):
        r = dfs(n, [])
        if r: return r
    return None

def check_nonprimary(ev, adr, candidate, base):
    'Whitelist non-primary edits and require evidence for changed predecessor sets.'
    kind = ev.get("kind"); kinds = set(str(kind).split("+")); p = []
    if not kinds or not kinds <= {"capacity", "fallback", "resilience"}:
        return [f"kind  must be  primary  or  capacity/fallback/resilience  of  +  combination , received  {kind!r}"], ev, None
    pc = check_candidate(candidate); p += pc
    if not base or not os.path.isfile(base):
        p.append(" non-primary change requires  --base < live  config  local copy >( identify starting version )")
    else:
        p += base_vs_ledger(base)
    cand_sha = None if pc else sha256(candidate)
    if not p:
        import yaml
        bd, cd = yaml.safe_load(open(base)), yaml.safe_load(open(candidate))
        if not isinstance(bd, dict) or not isinstance(bd.get("model_list"), list):
            p.append("--base  invalid  LiteLLM config"); bd = {"model_list": []}
        bl, cl = bd["model_list"], cd["model_list"]
        if sorted(map(_norm_entry, bl)) != sorted(map(_norm_entry, cl)):
            p.append("model_list  entry structure changed ( except  model_info.max_input_tokens/cap_reason  and  litellm_params  excluding resilience keys , contains  rpm/tpm/weight  sibling keys ,  entry additions or removals ,  duplicate deployments ): requires  primary  evidence ")
        b = {m.get("model_name"): m for m in bl}; c = {m.get("model_name"): m for m in cl}
        if len(bl) != len(b) or len(cl) != len(c):
            p.append(" duplicate deployments exist : unsupported by non-primary path , requires  primary  evidence ")
        caps = {}
        if "capacity" in kinds:
            for x in ev.get("caps") or []:
                v, r = x.get("max_input_tokens"), x.get("reason")
                if not (type(v) is int and v > 0): p.append(f"caps[{x.get('entry')}] max_input_tokens  must be a positive integer , received  {v!r}")
                if not (isinstance(r, str) and r.strip()): p.append(f"caps[{x.get('entry')}]  missing  reason( must be a nonempty string )")
                caps[x.get("entry")] = x

        tsig = lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
        for n in sorted(set(b) & set(c), key=str):
            bi, ci = b[n].get("model_info") or {}, c[n].get("model_info") or {}
            if tsig(bi) == tsig(ci): continue
            x = caps.get(n)
            if x is None:
                p.append(f"{n}  of  model_info  changed but  evidence.caps  undeclared "); continue
            cv = ci.get("max_input_tokens")
            if "max_input_tokens" not in ci:
                p.append(f"{n}  candidate removed  max_input_tokens, and  caps  declares  {x.get('max_input_tokens')!r}  mismatch ( removing a cap requires  primary  evidence )")
            elif not (type(cv) is int and cv > 0):
                pass
            elif cv != x.get("max_input_tokens"):
                p.append(f"{n}  of  max_input_tokens {ci.get('max_input_tokens')!r}  and  caps  declares  {x.get('max_input_tokens')!r}  mismatch ")
            cr = ci.get("cap_reason")
            if cr is not None and not (isinstance(cr, str) and cr.strip()):
                p.append(f"{n}  of  cap_reason  must be a nonempty string ")
        for n in caps:
            if n not in c: p.append(f"caps  declared  {n}  not in candidate ")
        for sec in sorted(set(bd) | set(cd), key=str):
            if sec == "model_list": continue
            if sec == "router_settings":
                bs, cs = dict(bd.get(sec) or {}), dict(cd.get(sec) or {})
                bfb = [bs.pop(k, None) for k in ("fallbacks", "context_window_fallbacks")]
                cfb = [cs.pop(k, None) for k in ("fallbacks", "context_window_fallbacks")]
                if tsig(bs) != tsig(cs): p.append("router_settings  except  fallbacks/context_window_fallbacks  other changes present ( type-sensitive comparison )")
                if bfb != cfb and "fallback" not in kinds: p.append("fallbacks  changed but  kind  does not include  fallback")
            elif tsig(bd.get(sec)) != tsig(cd.get(sec)):
                p.append(f" top-level section  {sec}  changed ( non-primary path only permits capacity and  fallbacks)")
        names = set(c)
        required, removed = {}, set()
        removals = ev.get("fallback_removals") or []
        if not (isinstance(removals, list) and all(isinstance(x, str) for x in removals)):
            p.append("evidence.fallback_removals  must be a string list "); removals = []
        removals = set(removals)
        maps = {}
        for sec in ("fallbacks", "context_window_fallbacks"):
            maps[sec] = (_fb_map(bd, sec, set(b), None, "base"), _fb_map(cd, sec, names, p, " candidate "))
        union = {}
        for _, cm_ in maps.values():
            for k_, v_ in cm_.items(): union.setdefault(k_, []).extend(t_ for t_ in v_ if t_ not in union.get(k_, []))
        cyc = _cycle(union)
        if cyc: p.append(f" candidate fallback cycle (fallbacks union context_window_fallbacks  union graph ) {' -> '.join(map(str, cyc))}")
        bcw, ccw = maps["context_window_fallbacks"]; cfb = maps["fallbacks"][1]
        for src in sorted(set(bcw) - set(ccw), key=str):
            seen_, stack_ = set(), list(cfb.get(src, []))
            while stack_:
                t = stack_.pop()
                if t in seen_ or t == src: continue
                seen_.add(t); stack_ += cfb.get(t, [])
            for t in seen_:
                required.setdefault(t, set()).add(f"context_window_fallbacks:{src}")
        for sec in ("fallbacks", "context_window_fallbacks"):
            bm, cm = maps[sec]
            for src in sorted(set(bm) | set(cm), key=str):
                old, new = bm.get(src, []), cm.get(src, [])
                if old == new: continue
                if old and not new:
                    removed.add(f"{sec}:{src}")
                    if f"{sec}:{src}" not in removals: p.append(f"{sec}[{src}]  entire chain removed : required in  evidence.fallback_removals  declares  \"{sec}:{src}\"")
                    continue
                for t in old:
                    if t not in new:
                        removed.add(f"{sec}:{src}:{t}")
                        if f"{sec}:{src}:{t}" not in removals:
                            p.append(f"{sec}[{src}]  removed fallback target  {t}: required in  evidence.fallback_removals  declares  \"{sec}:{src}:{t}\"")
                for i, t in enumerate(new):
                    if t not in old or set(new[:i]) != set(old[:old.index(t)]):
                        required.setdefault(t, set()).add(f"{sec}:{src}")
        for x in sorted(removals - removed):
            p.append(f"fallback_removals  declared  {x!r}  but this removal did not happen ")
        if (required or removed) and "fallback" not in kinds:
            p.append(" fallback edges changed but  kind  does not include  fallback")
        fbl = ev.get("fallbacks")
        if fbl is not None:
            p += check_fallbacks(fbl)
        byname = {}
        for i, f in enumerate(fbl if isinstance(fbl, list) else []):
            n_ = str((f or {}).get("name") or "")
            if n_ not in required:
                p.append(f"fallbacks[{i}] {n_!r}  no corresponding changed edge ( unrelated evidence )")
            byname.setdefault(n_, []).append((i, f))
        for t, srcs in sorted(required.items()):
            ok = False
            for i, f in byname.get(t, []):
                tp = os.path.expanduser(str(f.get("trigger_test") or ""))
                if check_fallbacks([f]): continue
                try: _, d = load_measured(tp)
                except ValueError: continue
                cov = set(d.get("covers") or [])
                if not srcs <= cov:
                    p.append(f"fallbacks[{i}] {t}  of  trigger_test covers  missing  {sorted(srcs - cov)}"); continue
                cs = d.get("candidate_sha256")
                if not (cand_sha and isinstance(cs, str) and CAND_SHA_RE.fullmatch(cs) and cand_sha.startswith(cs)):
                    p.append(f"fallbacks[{i}] {t}  of  trigger_test candidate_sha256  versus candidate  {str(cand_sha)[:SHA_PREFIX]}  mismatch ( must be candidate  sha  of  12  or  64  hex prefix , measurements must bind this candidate )"); continue
                ok = True
            if not ok: p.append(f" fallback target  {t}({sorted(srcs)}) missing verifiable trigger evidence ")
    if "fallback" in kinds and not (ev.get("fallbacks") or ev.get("fallback_removals")):
        p.append("kind  contains  fallback  but  fallbacks  and  fallback_removals  both empty ")
    if not p:
        p += check_resilience(ev, kinds, b, c, cand_sha, cd.get("router_settings") or {})
    pa, adr_file = check_adr(adr, cand_sha)
    return p + pa, ev, adr_file

def check(evidence_path, adr, candidate, base=None):
    'Check.'
    try:
        ev = json.load(open(evidence_path))
        if not isinstance(ev, dict): raise ValueError(" top level is not  object")
    except Exception as e:
        return [f"evidence  unreadable :{e}"], None, None
    if ev.get("kind", "primary") != "primary":
        return check_nonprimary(ev, adr, candidate, base)
    metric = ev.get("quality_metric", "all")
    p = []
    if metric not in ("all", "own", "pack"):
        p.append(f"quality_metric  must be  all/own/pack, received  {metric!r}"); metric = "all"
    po, ko, io, fo = check_side(ev, "old", metric)
    pn, kn, inw, fn = check_side(ev, "new", metric)
    p += po + pn
    if ko and kn and (len(ko | kn) > 1):
        p.append(f" both sides  results.json  of  bank_hash/cats/ arm parameters {ARM_KEYS}  differ , not comparable :old {sorted(ko)} vs new {sorted(kn)}")
    if fo & fn:
        p.append(f" old and new reference the same  results.json {sorted(fo & fn)}( one run cannot represent both sides )")
    elif io and io == inw:
        p.append(f" old and new sides  results.json  of  (model, base_url)  same  {sorted(io)}: same endpoint measured , not an old/new comparison ")
    p += check_fallbacks(ev.get("fallbacks"))
    pc = check_candidate(candidate)
    p += pc
    if not base or not os.path.isfile(base):
        p.append(" primary change also requires  --base < live  config  local copy >( identify changed entry ,  starting version )")
    elif (bl := base_vs_ledger(base)):
        p += bl
    elif not pc:
        p += bind_primary(base, candidate, io, inw)
    pa, adr_file = check_adr(adr, None if pc else sha256(candidate))
    return p + pa, ev, adr_file

def bind_primary(base, candidate, io, inw):
    'Bind old/new measurements to the actual changed upstream endpoints.'
    import yaml
    bd, cd = yaml.safe_load(open(base)), yaml.safe_load(open(candidate))
    def key(m):
        lp = m.get("litellm_params") if isinstance(m.get("litellm_params"), dict) else {}
        model = str(lp.get("model") or "")
        prov = model.split("/", 1)[0] if "/" in model else ""

        return (model.split("/", 1)[-1], str(lp.get("api_base") or "").rstrip("/") or DEFAULT_API_BASE.get(prov, ""))
    bl = [m for m in (bd or {}).get("model_list") or [] if isinstance(m, dict)]
    cl = [m for m in (cd or {}).get("model_list") or [] if isinstance(m, dict)]
    p, changed = [], []
    for lbl, lst in (("base", bl), (" candidate ", cl)):
        names = [m.get("model_name") for m in lst]
        dup = sorted({n for n in names if names.count(n) > 1}, key=str)
        if dup: p.append(f"{lbl}  has duplicate deployments  {dup}:primary  cannot bind evidence to a single deployment , unsupported ")
    if p: return p
    bm = {m.get("model_name"): m for m in bl}
    for m in cl:
        o = bm.get(m.get("model_name"))
        if o is None or key(o) != key(m): changed.append((m.get("model_name"), key(o) if o else None, key(m)))
    if not changed:
        p.append("primary  evidence but no candidate entry changed  model/api_base( non-primary changes must use  kind=capacity/fallback/resilience)")
    cn = {m.get("model_name") for m in cl}
    for sec in ("fallbacks", "context_window_fallbacks"):
        _fb_map(cd or {}, sec, cn, p, " candidate ")
    tsig = lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
    if tsig((bd or {}).get("router_settings")) != tsig((cd or {}).get("router_settings")):
        p.append("primary  change also altered  router_settings( fallback / retries and related settings ): split changes , first  primary  change primary only , then use  kind=fallback  non-primary path ")
    lpk = lambda m: {k: (m.get("litellm_params") or {}).get(k) for k in RES_KEYS} if isinstance(m.get("litellm_params"), dict) else {}
    for m in cl:
        o = bm.get(m.get("model_name"))
        if o is not None and tsig(lpk(o)) != tsig(lpk(m)):
            p.append(f"{m.get('model_name')}  of  timeout/stream_timeout/num_retries  changed alongside primary : split and use  kind=resilience( matrix evidence required )")

    if sorted(map(str, (m.get("model_name") for m in bl))) != sorted(map(str, (m.get("model_name") for m in cl))):
        p.append("primary  change added or removed entries (model_list  name set changed ): split and declare individually ")
    cnames = {n for n, _, _ in changed}
    for m in cl:
        o = bm.get(m.get("model_name"))
        if o is not None and m.get("model_name") not in cnames and tsig(o) != tsig(m):
            p.append(f"{m.get('model_name')}  not an entry changed by this primary change , but its definition changed :primary  unrelated changes forbidden ")
    for sec in sorted(set(bd or {}) | set(cd or {}), key=str):
        if sec not in ("model_list", "router_settings") and tsig((bd or {}).get(sec)) != tsig((cd or {}).get(sec)):
            p.append(f"primary  change also altered top-level section  {sec}: split for separate acceptance ")
    if io & inw:
        p.append(f" old and new measured identities intersect  {sorted(io & inw)}: same upstream measured , not an old/new comparison ")
    for n, o, nw in changed:
        if nw not in inw: p.append(f"{n}  candidate points to  {nw}, is not  new  side measured identity  {sorted(inw)}( quality evidence must bind written model and endpoint )")
        if o is not None and o not in io: p.append(f"{n}  live identity was  {o}, is not  old  side measured identity  {sorted(io)}")
    return p

def append_ledger(entry):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LEDGER, "a+b") as f:
        f.seek(0, 2)
        if f.tell():
            f.seek(-1, 2)
            if f.read(1) != b"\n": f.write(b"\n")
        f.write((json.dumps(entry, ensure_ascii=False) + "\n").encode())

def accept(path, adr):

    import tempfile, shutil
    try: data = read_bytes(path)
    except OSError as e: print(f" REFUSE BASELINE :\n  - {path}  unreadable :{e}"); return 1
    s = bsha(data)
    tmp = tempfile.mkdtemp(prefix="lrc-")
    try:
        snap = os.path.join(tmp, "accept.yaml"); open(snap, "wb").write(data)
        problems = check_candidate(snap)
        if read_bytes(snap) != data: problems.append(" snapshot changed during checks ")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    pa, adr_file = check_adr(adr, s if not problems else None)
    problems += pa
    try: hist = {e["candidate_sha256"] for e in read_ledger()}
    except ValueError as e:
        problems.append(f" ledger  {LEDGER}  corrupt ({e}): cannot bootstrap as an empty ledger , repair the ledger first "); hist = set()
    if not problems and hist and s not in hist:
        problems.append(f" ledger has baseline ,{s[:SHA_PREFIX]}  not a previously registered version :--accept  only for bootstrap or registered rollback ,"
                        " new content requires  --evidence( prevent retrospective approval of unregistered writes )")
    if problems:
        print(" REFUSE BASELINE :")
        for x in problems: print("  - " + x)
        return 1
    append_ledger({"ts": time.time(), "kind": "accept", "adr": adr, "adr_file": adr_file,
                   "source": os.path.abspath(path), "candidate_sha256": s})
    print(f" baseline registered  sha256 {s[:SHA_PREFIX]}…(ADR {os.path.basename(adr_file)});infra_validate.py --drift  will reconcile against this ")
    return 0

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evidence")
    ap.add_argument("--adr", required=True)
    ap.add_argument("--candidate", help=" candidate to write  config.yaml( local file )")
    ap.add_argument("--accept", help=" register baseline :live config  local copy ")
    ap.add_argument("--base", help=" non-primary change (kind=capacity/fallback) required : live  config  local copy , must equal last ledger version ")
    a = ap.parse_args(argv)
    if a.accept:
        return accept(a.accept, a.adr)
    if not (a.evidence and a.candidate):
        ap.error(" requires  --evidence  and  --candidate( or use  --accept  register baseline )")


    import tempfile, shutil
    try:
        cand_b = read_bytes(a.candidate); ev_b = read_bytes(a.evidence)
        base_b = read_bytes(a.base) if a.base and os.path.isfile(a.base) else None
    except OSError as e:
        print(f" REFUSE WRITE  LiteLLM  production  config:\n  -  cannot read inputs :{e}"); return 1
    tmp = tempfile.mkdtemp(prefix="lrc-")
    try:
        snaps = {"candidate.yaml": cand_b, "evidence.json": ev_b}
        if base_b is not None: snaps["base.yaml"] = base_b
        for n, b in snaps.items(): open(os.path.join(tmp, n), "wb").write(b)
        j = lambda n: os.path.join(tmp, n)
        problems, ev, adr_file = check(j("evidence.json"), a.adr, j("candidate.yaml"),
                                       (j("base.yaml") if base_b is not None else a.base) if a.base else None)
        if any(read_bytes(j(n)) != b for n, b in snaps.items()):
            problems = problems + [" snapshot changed during checks ( refuse permit ; hash and permit use the checked bytes )"]
        if problems:
            print(" REFUSE WRITE  LiteLLM  production  config:")
            for x in problems: print("  - " + x)
            return 1
        return _grant(a, ev, adr_file, cand_b, base_b, ev_b)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def _freeze(path, data):
    'Create a read-only snapshot and compare its readback with the checked bytes.'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "wb") as f: f.write(data)
    if read_bytes(path) != data:
        raise RuntimeError(f" frozen copy  {path}  readback differs from checked bytes ")

def _grant(a, ev, adr_file, cand_b, base_b, ev_b):
    metric = ev.get("quality_metric", "all")
    print(" evidence :")
    if ev.get("kind", "primary") != "primary":
        print(f"  kind={ev['kind']}( primary unchanged , entry structure checked )· base {bsha(base_b)[:SHA_PREFIX]}")
        for x in ev.get("caps") or []: print(f"  cap {x.get('entry')} -> {x.get('max_input_tokens')}:{x.get('reason')}")
        ev = dict(ev, old={}, new={})
    for side in ("old", "new"):
        if not ev[side]: continue
        s = ev[side]
        print(f"  {side:3} {s.get('target', '')}:  quality median  {statistics.median(s['quality_runs']):.1f}"
              f"({len(s['quality_runs'])}  runs ,{metric},{s['quality_src']})· prefill {s['prefill_tps']}({s['prefill_src']})"
              f" · decode {s['decode_tps']} tok/s")
    print(f"  fallbacks {len(ev.get('fallbacks') or [])}  targets have trigger evidence  · ADR {os.path.basename(adr_file)}")
    now = time.time()
    cand_sha, ev_sha = bsha(cand_b), bsha(ev_b)
    pid = hashlib.sha256(f"{a.adr}|{ev_sha}|{cand_sha}|{now}".encode()).hexdigest()[:16]
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    os.makedirs(PERMIT_DIR, exist_ok=True)
    cand = os.path.join(PERMIT_DIR, f"{pid}.candidate.yaml"); _freeze(cand, cand_b)
    ev_frozen = os.path.join(PERMIT_DIR, f"{pid}.evidence"); _freeze(ev_frozen, ev_b)


    permit_json = os.path.join(PERMIT_DIR, pid + ".json")
    cmds = [f"LITELLM_ROUTE_PERMIT={pid} scp {shlex.quote(cand)} {HOST}:{STAGING}/{pid}.candidate.yaml",
            f"LITELLM_ROUTE_PERMIT={pid} scp {shlex.quote(permit_json)} {HOST}:{STAGING}/{pid}.json",
            f"LITELLM_ROUTE_PERMIT={pid} ssh {HOST} 'sudo -n /usr/local/sbin/route_apply.py {pid}'"]
    os.makedirs(PERMIT_DIR, exist_ok=True)
    with open(os.path.join(PERMIT_DIR, pid + ".json"), "w") as f:
        json.dump({"id": pid, "adr": a.adr, "adr_file": adr_file,
                   "evidence": ev_frozen, "evidence_source": os.path.abspath(a.evidence), "evidence_sha256": ev_sha,
                   "candidate": cand, "candidate_sha256": cand_sha, "base_sha256": bsha(base_b) if base_b else None, "commands": cmds,
                   "created": now, "expires": now + PERMIT_TTL}, f, ensure_ascii=False, indent=1)
    append_ledger({"ts": now, "kind": "permit", "id": pid, "adr": a.adr, "candidate_sha256": cand_sha})
    print(f"\n ALLOW WRITE ( permit  {pid},30  minutes validity ; execute printed commands verbatim ,applier  accepts only  sha  matching ,  unexpired ,  unconsumed , base  permits matching live state ).  write commands :")
    for c in cmds: print(c)
    print(f"ssh {HOST} 'launchctl kickstart -k gui/$(id -u)/{LABEL}'")
    print(" closeout :infra_validate.py --drift( should report  config  content differs from baseline )-> services.yaml bump revision( comment must name  config.yaml)"
          f"+ revision_at -> infra_validate.py --drift --accept {HOST}:{DRIFT_KEY} -> --probe --drift  all checks pass "
          "(4  reconciliation  gateway.example.invalid config sha  against this ledger )")
    return 0

if __name__ == "__main__":
    sys.exit(main())
