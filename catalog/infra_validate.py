#!/usr/bin/env python3
'Service catalog validation: probes, five drift checks, and expiring cap registrations.'
import sys, re, os, json, shlex, hashlib, subprocess, urllib.request
from datetime import datetime, date

PATH = os.path.expanduser(os.environ.get("CATALOG_PATH", "catalog/services.example.yaml"))
CRED = os.path.expanduser(os.environ.get("CATALOG_CREDENTIALS_FILE", "credentials.env"))
LEDGER = os.path.expanduser(os.environ.get("CATALOG_LEDGER", "state/route-change/ledger.jsonl"))
REQUIRED = ["host", "endpoint", "model", "ssot"]
CFG_WATCH = re.compile(os.environ.get("CATALOG_CONFIG_PATTERN", r"(?:litellm-gateway/config|gateway\.example)\.ya?ml$"))
CHECK_LOOPBACK = False
SKIPPED = []
FAM = {}

def parse(path):

    svcs, cur = {}, None
    in_services = False
    for line in open(path):
        if re.match(r"^services:", line): in_services = True; continue
        if re.match(r"^[a-z]", line): in_services = False
        if not in_services: continue
        m = re.match(r"^  (\w[\w-]*):\s*$", line)
        if m: cur = m.group(1); svcs[cur] = {}; continue
        m = re.match(r"^    (\w+):\s*(.+?)\s*(#.*)?$", line)
        if m and cur: svcs[cur][m.group(1)] = m.group(2)
    return svcs

def resolve_key(name):
    'Resolve key.'
    if not name: return None
    if os.environ.get(name): return os.environ[name]
    try:
        for line in open(CRED):
            m = re.match(r"^\s*(?:export\s+)?" + re.escape(name) + r"\s*=\s*(.*?)\s*$", line)
            if m: return m.group(1).strip("'\"") or None
    except OSError:
        pass
    return None

def http_json(url, key=None):
    req = urllib.request.Request(url)
    if key: req.add_header("Authorization", "Bearer " + key)
    return json.load(urllib.request.urlopen(req, timeout=8))

def load_strict(path):
    'Parse the entire YAML document strictly.'
    import yaml
    try:
        doc = yaml.safe_load(open(path))
        return (doc, None) if isinstance(doc, dict) else (None, " top level is not  mapping")
    except yaml.YAMLError as e:
        return None, str(e).replace("\n", " ")

def all_blocks(doc):
    'Merge every services-suffixed block; call dup_services before trusting the merge.'
    return {n: f for k, v in doc.items() if k.endswith("services") and isinstance(v, dict)
            for n, f in v.items() if isinstance(f, dict)}

def dup_services(doc):
    'Detect duplicate names across service blocks.'
    seen, dups = {}, []
    for k, v in doc.items():
        if k.endswith("services") and isinstance(v, dict):
            for n, f in v.items():
                if isinstance(f, dict):
                    if n in seen: dups.append((n, seen[n], k))
                    else: seen[n] = k
    return dups

def is_loopback_base(base):
    'Is loopback base.'
    from urllib.parse import urlsplit
    import ipaddress
    try:
        h = (urlsplit(base).hostname or "").lower()
    except ValueError:
        return False
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False

def fam(name, target=0, checked=0):
    t = FAM.setdefault(name, [0, 0]); t[0] += target; t[1] += checked

def skip(msg, tag="WARN"):
    print(f"DRIFT {tag} {msg}"); SKIPPED.append(msg)

def revision_at(doc, path):
    'Validate revision precision, calendar day, and catalog file timestamp.'
    v = doc.get("revision_at")
    if v in (None, "") and re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", str(doc.get("revision", ""))):
        v = doc["revision"]
    if v in (None, ""):
        return None, (" has  drift_watch  without  revision_at(bump  timestamp ,ISO  format example  2026-09-24T17:45:00-05:00;"
                      "revision  day precision only , same-day  bump  later changes are invisible ,)")
    try:
        dt = v if isinstance(v, datetime) else datetime.fromisoformat(str(v).strip())
    except ValueError:
        return None, f"revision_at  not parseable : {v!r}( use  ISO  format , for example  2026-09-24T17:45:00-05:00)"
    ts = dt.timestamp()
    day = re.match(r"\d{4}-\d{2}-\d{2}", str(doc.get("revision", "")))
    if not day or datetime.fromtimestamp(ts).strftime("%Y-%m-%d") != day.group():
        return None, f"revision {doc.get('revision')}  and  revision_at {v}  not the same day (bump  update both together )"
    try:
        if ts > os.path.getmtime(path) + 120:
            return None, f"revision_at {v}  later than  services.yaml  file write timestamp ( future timestamps hide drift )"
    except OSError:
        pass
    return ts, None

def split_spec(spec):
    m = re.match(r"^([\w.-]+):(.+)$", spec)
    return (m.group(1), m.group(2)) if m else (None, spec)

def run_remote(host, cmd):
    'Run remote.'
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, cmd],
                           capture_output=True, text=True, timeout=20)
    except Exception as e:
        return None, "unreachable", str(e)
    if r.returncode == 0 and r.stdout.split(): return r.stdout.split(), None, ""
    return None, ("unreachable" if r.returncode == 255 else "missing"), r.stderr.strip()[:200]

def rquote(p):
    return '"$HOME"/' + shlex.quote(p[2:]) if p.startswith("~/") else shlex.quote(p)

def stat_mtime(spec):
    'Stat a local path or use a read-only remote stat command.'
    host, p = split_spec(spec)
    if not host:
        try: return os.path.getmtime(os.path.expanduser(p)), None, ""
        except OSError as e: return None, "missing", str(e)
    q = rquote(p)
    out, err, why = run_remote(host, f"stat -c %Y {q} 2>/dev/null || stat -f %m {q}")
    return (float(out[-1]), None, "") if out else (None, err, why)

HEX64 = re.compile(r"[0-9a-f]{64}")

def file_sha(spec):
    host, p = split_spec(spec)
    if not host:
        try: return hashlib.sha256(open(os.path.expanduser(p), "rb").read()).hexdigest(), None, ""
        except OSError as e: return None, "missing", str(e)
    q = rquote(p)
    out, err, why = run_remote(host, f"shasum -a 256 {q} 2>/dev/null || sha256sum {q}")
    if not out: return None, err, why
    return (out[0], None, "") if HEX64.fullmatch(out[0]) else (None, "unreadable", f" remote output is not  sha256:{' '.join(out[:3])[:80]}")

def signature(spec):
    'Hash file bytes or a directory top-level name/size/mtime listing.'
    host, p = split_spec(spec)
    if not host:
        p = os.path.expanduser(p)
        try:
            if os.path.isdir(p):
                rows = sorted(f"{e.name} {e.stat().st_size} {int(e.stat().st_mtime)}" for e in os.scandir(p) if not e.name.startswith("."))
                return "dir:" + hashlib.sha256("\n".join(rows).encode()).hexdigest(), None, ""
            return hashlib.sha256(open(p, "rb").read()).hexdigest(), None, ""
        except OSError as e:
            return None, ("missing" if isinstance(e, FileNotFoundError) else "unreadable"), str(e)
    q = rquote(p)

    out, err, why = run_remote(host, f"set -o pipefail; if [ -d {q} ]; then echo dir; (stat -c '%n %s %Y' {q}/* 2>/dev/null || stat -f '%N %z %m' {q}/*)"
                                     f" | (shasum -a 256 2>/dev/null || sha256sum); else shasum -a 256 {q} 2>/dev/null || sha256sum {q}; fi")
    if not out: return None, err, why

    if out[0] == "dir" and len(out) > 1 and HEX64.fullmatch(out[1]): return "dir:" + out[1], None, ""
    if HEX64.fullmatch(out[0]): return out[0], None, ""
    return None, "unreadable", f" remote output is not a fingerprint :{' '.join(out[:3])[:80]}"

def baseline_path(path):
    return re.sub(r"\.ya?ml$", "", path) + ".drift-baseline.json"

def drift_content(doc, svcs, path):
    'Compare content fingerprints independently of revision_at.'
    watch = [(n, str(w)) for n, f in svcs.items() for w in (f.get("drift_watch") or [])]
    if not watch: return 0
    bp = baseline_path(path)
    try:
        base = json.load(open(bp))["files"]
        if not isinstance(base, dict): raise ValueError("files  is not  mapping")
    except FileNotFoundError:
        if doc.get("drift_baseline") == "required":
            print(f"DRIFT FAIL services.yaml  declares  drift_baseline: required, but content baseline  {bp}  missing ( deleted ?)"
                  "->  verify each  watch  files first  --drift --accept all  rebuild "); return 1
        print(f"DRIFT NOTE  no content baseline  {bp}: only using  mtime  check , unrelated  bump  can hide earlier drift ; create :--drift --accept all")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"DRIFT FAIL  content baseline  {bp}  unreadable ({e})"); return 1
    fails = 0
    for owner, spec in watch:
        b = base.get(spec)
        if not isinstance(b, dict) or not b.get("sig"):
            print(f"DRIFT FAIL {owner}: {spec}  content baseline unregistered ( new  drift_watch?)->  after verification  --drift --accept {spec}")
            fails += 1; continue
        fam("5 drift_watch  content baseline ", 1)
        sig, err, why = signature(spec)
        if err == "unreachable":
            skip(f"{owner}: {spec}  unreachable , content baseline skipped ({why})"); continue
        if err:
            print(f"DRIFT FAIL {owner}: {spec} " + (" unreadable " if err == "unreadable" else " missing or unreadable ")
                  + f", fingerprint unavailable ({why})"); fails += 1; continue
        fam("5 drift_watch  content baseline ", 0, 1)
        if sig != b["sig"]:
            name = os.path.basename(split_spec(spec)[1].rstrip("/"))
            print(f"DRIFT FAIL {owner}: {spec}  content differs from baseline ( baseline registered at  revision {b.get('revision')})->  deployment changed without update to  SSOT:"
                  f"bump revision( comment must name  {name})+ revision_at, then  --drift --accept {spec}")
            fails += 1
        else:
            print(f"DRIFT OK   {owner}: {spec}  content  =  baseline ({b.get('revision')})")
    return fails

def accept(doc, svcs, path, specs):
    'Atomically record named baselines after revision and comment checks pass.'
    watch = {str(w) for f in svcs.values() for w in (f.get("drift_watch") or [])}
    deadline, why = revision_at(doc, path)
    if why:
        print(f"ACCEPT FAIL {why}"); return 1
    bp = baseline_path(path)
    try:
        data = json.load(open(bp))
    except FileNotFoundError:
        data = {"files": {}}
    files = data.setdefault("files", {})
    todo = sorted(watch - set(files)) if specs == ["all"] else specs
    rev_line = next((l for l in open(path) if l.startswith("revision:")), "")
    fails, new = 0, {}
    for spec in todo:
        if spec not in watch:
            print(f"ACCEPT FAIL {spec}  not in any  drift_watch  in (--watch  temporary paths are not registered )"); fails += 1; continue
        mt, err, why = stat_mtime(spec)
        sig, err2, why2 = signature(spec) if not err else (None, err, why)
        if err or err2:
            print(f"ACCEPT FAIL {spec}  unavailable ({why or why2})"); fails += 1; continue
        if mt > deadline:
            print(f"ACCEPT FAIL {spec} mtime  later than  revision_at {doc.get('revision_at')} ->  first  bump services.yaml  then register "); fails += 1; continue
        old = files.get(spec, {}).get("sig") if isinstance(files.get(spec), dict) else None
        name = os.path.basename(split_spec(spec)[1].rstrip("/"))
        if old and old != sig and name not in rev_line:
            print(f"ACCEPT FAIL {spec}  content changed , but  revision  line comment lacks  {name}(bump  name the changed file ,SSOT  so the change is recorded )"); fails += 1; continue
        new[spec] = {"sig": sig, "revision": str(doc.get("revision")), "revision_at": str(doc.get("revision_at")),
                     "accepted": datetime.now().astimezone().isoformat(timespec="seconds")}
        print(f"ACCEPT OK   {spec}" + ("( content unchanged )" if old == sig else ""))
    if fails:
        print(f"ACCEPT FAIL: {fails}  checks failed , baseline unchanged "); return fails
    files.update(new)
    tmp = bp + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, bp)
    print(f"ACCEPT OK:  register  {len(new)}  items  -> {bp}")
    return 0

def drift_watch(doc, svcs, extra, path=None):
    'Compare watched mtimes with revision_at and require patch coverage.'
    fails = 0
    for n, f in svcs.items():
        patches = f.get("local_patches") or []
        if isinstance(patches, str): patches = [patches]
        if not patches: continue
        dw = [str(w) for w in (f.get("drift_watch") or [])]
        if not dw:
            print(f"DRIFT FAIL {n}:  has  local_patches  without  drift_watch( patch was  brew upgrade  overwritten without detection ,)")
            fails += 1; continue
        for p in patches:
            m = re.search(r"[\w./-]+\.(?:py|json|ya?ml|sh)\b", str(p))
            if m and not any(w.endswith(m.group()) for w in dw):
                print(f"DRIFT FAIL {n}: local_patches  of  {m.group()}  outside  drift_watch  in "); fails += 1
    watch = [(n, w) for n, f in svcs.items() for w in (f.get("drift_watch") or [])] + [("--watch", w) for w in extra]
    if not watch: return fails
    deadline, why = revision_at(doc, path or PATH)
    if why:
        print(f"DRIFT FAIL {why}"); return fails + 1
    rev = doc.get("revision_at") or doc.get("revision")
    for owner, spec in watch:
        fam("2 drift_watch mtime", 1)
        mt, err, why = stat_mtime(str(spec))
        if err == "unreachable":
            skip(f"{owner}: {spec}  unreachable , skipped ({why})"); continue
        fam("2 drift_watch mtime", 0, 1)
        if err:
            print(f"DRIFT FAIL {owner}: {spec}  missing (SSOT  recorded path missing :{why})"); fails += 1; continue
        if mt > deadline:
            ts = datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M")
            print(f"DRIFT FAIL {owner}: {spec} mtime {ts}  later than  revision {doc.get('revision')}(revision_at {rev})"
                  " ->  deployment changed without catalog update  services.yaml(bump revision + revision_at)")
            fails += 1
        else:
            print(f"DRIFT OK   {owner}: {spec}")
    return fails

def cap_reg_problems(ic):
    'Cap reg problems.'
    if not isinstance(ic, dict): return [" registration must be a mapping "]
    p = []
    v = ic.get("max_input_tokens")
    if not (type(v) is int and v > 0): p.append(f"max_input_tokens  must be a positive integer , received  {v!r}")
    if not re.fullmatch(r"ADR-\d{4}", str(ic.get("adr") or "")): p.append(f"adr  must be  ADR-NNNN, received  {ic.get('adr')!r}")
    if not (isinstance(ic.get("backend_model"), str) and ic["backend_model"].strip()): p.append(" missing  backend_model")
    rb = ic.get("review_by")
    try:
        d = rb if isinstance(rb, date) else date.fromisoformat(str(rb))
        if d < date.today(): p.append(f"review_by {d}  expired : review before changing or renewing ")
    except (TypeError, ValueError):
        p.append(f"review_by  must be a real date  YYYY-MM-DD, received  {rb!r}")
    return p

def caps_offline(svcs):
    'Caps offline.'
    fails = 0
    for name, f in svcs.items():
        if f.get("shape") != "gateway": continue
        for mn, ic in (f.get("intentional_caps") or {}).items():
            for x in cap_reg_problems(ic):
                print(f"DRIFT FAIL {name}/{mn}: intentional_caps  register  {x}"); fails += 1
    return fails

def litellm_ctx(svcs):
    'Compare gateway caps with backend limits, subject to expiring registrations.'
    fails = 0
    for name, f in svcs.items():
        if f.get("shape") != "gateway": continue
        fam("3 LiteLLM  capacity reconciliation ", 1)
        ep = str(f.get("endpoint", "")).rstrip("/")
        key_env = f.get("probe_key_env")
        key = resolve_key(key_env)
        if key_env and not key:
            skip(f"{name}:  environment and  {CRED}  both lack  {key_env}, skipped  max_input_tokens  reconciliation ", "KEY_MISSING"); continue
        try:
            entries = http_json(ep + "/model/info", key).get("data")
        except Exception as e:
            skip(f"{name}: {ep}/model/info  unavailable ({e}), skipped "); continue
        if not isinstance(entries, list) or not entries:
            skip(f"{name}: {ep}/model/info  missing  data  list ( returned  {type(entries).__name__}), skipped "); continue
        backends = {}
        for e in entries:
            mn = e.get("model_name")
            lp, mi = e.get("litellm_params") or {}, e.get("model_info") or {}
            base = (lp.get("api_base") or "").rstrip("/")
            if not base: continue
            if is_loopback_base(base) and not CHECK_LOOPBACK:


                skip(f"{name}/{mn}:  backend  {base}  is a gateway-local loopback address , cannot reconcile from here , skipped "); continue
            if base not in backends:
                try:
                    backends[base] = {m["id"]: m.get("max_model_len") for m in http_json(base + "/models").get("data", [])}
                except Exception as ex:
                    skip(f"{name}/{mn}:  backend  {base}/models  unavailable ({ex})"); backends[base] = None
            ids = backends[base]
            if ids is None: continue
            want = str(lp.get("model", "")).split("/", 1)[-1]

            hit = {k: v for k, v in ids.items() if want and k == want}
            if not hit:
                fuzzy = {k: v for k, v in ids.items() if want and want in k}
                hit = fuzzy if len(fuzzy) == 1 else {}
                if len(fuzzy) > 1:
                    fam("3 LiteLLM  capacity reconciliation ", 0, 1)
                    print(f"DRIFT FAIL {name}/{mn}: model {want!r}  at backend  {base}  no exact  ID, ambiguous fuzzy matches  {sorted(fuzzy)}, cannot determine capacity "); fails += 1
                    continue
            if not hit:
                fam("3 LiteLLM  capacity reconciliation ", 0, 1)
                print(f"DRIFT FAIL {name}/{mn}: model {want!r}  not at backend  {base}  of  /v1/models {sorted(ids)}(  same pattern )"); fails += 1
                continue
            lens = {v for v in hit.values() if v}
            mit = mi.get("max_input_tokens")
            if not lens or mit is None:
                skip(f"{name}/{mn}: max_input_tokens={mit}  backend  max_model_len={sorted(lens) or ' none '}, missing side prevents reconciliation "); continue
            fam("3 LiteLLM  capacity reconciliation ", 0, 1)
            ic = (f.get("intentional_caps") or {}).get(mn) or {}
            reg = (mit not in lens and type(mit) is int and 0 < mit < min(lens) and ic.get("max_input_tokens") == mit
                   and str(ic.get("backend_model") or "") in hit
                   and isinstance(mi.get("cap_reason"), str) and mi["cap_reason"].strip())
            if reg and not cap_reg_problems(ic):
                print(f"DRIFT OK   {name}/{mn}: max_input_tokens {mit} <  backend  {sorted(lens)}, registered intentional cap ({ic['adr']}; review deadline  {ic.get('review_by')}:{ic.get('review', '—')})")
            elif reg:
                print(f"DRIFT FAIL {name}/{mn}: max_input_tokens {mit}  intentional cap registration invalid ( see above )")
            elif mit not in lens:
                print(f"DRIFT FAIL {name}/{mn}: max_input_tokens {mit} !=  backend  {base} max_model_len {sorted(lens)}"
                      f"{'( exceeds backend )' if type(mit) is int and mit > max(lens) else '( intentional caps require registration in  services.yaml  this service  intentional_caps  register same value +ADR-NNNN+backend_model+review_by, and  model_info  write  cap_reason)'}"); fails += 1
            else:
                print(f"DRIFT OK   {name}/{mn}: max_input_tokens {mit}")
    return fails

def ledger_entries():
    'Read valid ledger rows; corruption is a failure, never an empty baseline.'
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

def config_sha(svcs):
    'Compare watched gateway config hashes with the route gate ledger.'
    fails = 0
    for name, f in svcs.items():
        for spec in (str(w) for w in (f.get("drift_watch") or []) if CFG_WATCH.search(str(w))):
            try:
                ents = ledger_entries()
            except ValueError as e:
                print(f"DRIFT FAIL {name}:  ledger  {LEDGER}  corrupt ({e})-> {spec} sha  cannot reconcile ; repair the ledger first "); fails += 1; continue
            last = ents[-1] if ents else None
            if last is None:
                skip(f"{name}:  ledger  {LEDGER}  no baseline ,{spec} sha  reconciliation disabled "
                     "( first  ssh gateway.example.invalid cat  to local file , then  route_change.py --accept < copy > --adr ADR-NNNN  register )", "SKIP")
                continue
            fam("4 LiteLLM config sha", 1)
            sha, err, why = file_sha(spec)
            if err == "unreachable":
                skip(f"{name}: {spec}  unreachable ,sha  reconciliation skipped ({why})"); continue
            fam("4 LiteLLM config sha", 0, 1)
            if err:
                print(f"DRIFT FAIL {name}: {spec}  missing or unreadable ({why})"); fails += 1
            elif sha != last.get("candidate_sha256"):
                print(f"DRIFT FAIL {name}: {spec} sha256 {sha[:12]} !=  last ledger row  {last.get('kind')} "
                      f"{str(last.get('candidate_sha256'))[:12]}({last.get('adr')})->  write bypassed  route_change.py  write "
                      "( or latest permit is unapplied ); investigate then use  --accept  register ")
                fails += 1
            else:
                print(f"DRIFT OK   {name}: {spec} sha256 {sha[:12]} =  ledger ({last.get('kind')} {last.get('adr')})")
    return fails

def main():
    global PATH, CRED, LEDGER, CHECK_LOOPBACK
    for option, name in (("--catalog", "PATH"), ("--credentials-file", "CRED"), ("--ledger", "LEDGER")):
        if option in sys.argv:
            index = sys.argv.index(option)
            if index + 1 >= len(sys.argv):
                raise SystemExit(f"{option} requires a path")
            globals()[name] = os.path.expanduser(sys.argv[index + 1])
    CHECK_LOOPBACK = "--local-backends" in sys.argv
    probe = "--probe" in sys.argv
    drift = "--drift" in sys.argv
    extra_watch = [sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == "--watch"]
    specs = None
    if "--accept" in sys.argv:
        specs = []
        for a in sys.argv[sys.argv.index("--accept") + 1:]:
            if a.startswith("--"): break
            specs.append(a)
        if not (drift and specs):
            print(" usage : infra_validate.py --drift --accept <drift_watch  in  spec…>|all"); sys.exit(2)
    SKIPPED.clear(); FAM.clear()
    svcs = parse(PATH)
    fails = 0
    doc = None
    if drift:
        doc, err = load_strict(PATH)
        if err: print(f"FAIL services.yaml  invalid  YAML(yaml.safe_load): {err}"); fails += 1
        else:
            svcs = all_blocks(doc)
            for n, a, b in dup_services(doc):
                print(f"FAIL {n}:  duplicate service name in  {a}  and  {b}( merging silently overwrites the earlier entry ,  checks omitted ,)"); fails += 1
            if specs is None: fails += caps_offline(svcs)
        if specs is not None:
            if doc is None: sys.exit(1)
            sys.exit(min(accept(doc, svcs, PATH, specs), 250))
    if not svcs: print("FAIL:  cannot parse any  service"); sys.exit(1)
    ports = {}
    for name, f in svcs.items():
        for k in REQUIRED:
            if k not in f: print(f"FAIL {name}:  missing field  {k}"); fails += 1
        ep = str(f.get("endpoint", ""))
        m = re.search(r"://([\d.]+):(\d+)", ep)
        if m:
            key = (m.group(1), m.group(2))
            if key in ports: print(f"FAIL {name}:  duplicate port  {key} vs {ports[key]}"); fails += 1
            ports[key] = name
        else:
            print(f"FAIL {name}: endpoint  not parseable : {ep}"); fails += 1
    if probe:

        for name, f in parse(PATH).items():
            ep = f.get("endpoint", "").rstrip("/")
            want = f.get("model", "")
            key_env = f.get("probe_key_env")
            key = resolve_key(key_env)
            if key_env and not key:

                print(f"PROBE KEY_MISSING {name}:  environment and  {CRED}  both lack  {key_env}, skipped "); continue
            try:
                r = http_json(ep + "/models", key)
                ids = [x["id"] for x in r.get("data", [])]
                if any(want == i or want in i for i in ids):
                    print(f"PROBE OK   {name}: {want} @ {ep}")
                else:
                    print(f"PROBE FAIL {name}:  expected  {want}, actual  {ids}"); fails += 1
            except Exception as e:
                print(f"PROBE FAIL {name}: {ep}  unreachable ({e})"); fails += 1
    if drift and doc is not None:
        fails += drift_watch(doc, svcs, extra_watch)
        fails += litellm_ctx(svcs)
        fails += config_sha(svcs)
        fails += drift_content(doc, svcs, PATH)
        for fname, (target, checked) in FAM.items():
            if target and checked <= 0:
                print(f"DRIFT FAIL {fname}: {target}  targets , 0  actual checks ( all skipped )->  cannot report success "); fails += 1
    tag = f" (skipped {len(SKIPPED)})" if drift and SKIPPED else ""
    print(f"{'FAIL' if fails else 'OK'}{tag}: {len(svcs)} services, {fails} failures")
    sys.exit(min(fails, 250))

if __name__ == "__main__":
    main()
