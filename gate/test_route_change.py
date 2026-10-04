#!/usr/bin/env python3
"""Offline regression checks."""
import hashlib, json, os, shutil, sys, time, types
from datetime import date
import pytest

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "route_change.py")
HERE = os.path.dirname(os.path.abspath(__file__))
BANK = "synthetic-bank"
CATS = ["category-a", "category-b"]

def load_mod(mutate=()):
    """Offline regression checks."""
    src = open(TOOL).read()
    for a, b in mutate:
        assert a in src, f" mutation anchor missing ( source changed ?):{a}"
        src = src.replace(a, b)
    m = types.ModuleType("litellm_route_change"); m.__file__ = TOOL
    exec(compile(src, TOOL, "exec"), m.__dict__); return m

def results(env, rid, own, pack, bank=BANK, cats=CATS, model=None, base_url=None, extra=()):
    """Offline regression checks."""
    d = env / "evaluation" / "runs" / rid; d.mkdir(parents=True)
    model = model or ("model-old" if "old" in rid else f"model-{rid}")
    base_url = base_url or ("http://backend-a.example.invalid:9002/v1" if "old" in rid else "http://127.0.0.1:8000/v1")
    (d / "results.json").write_text(json.dumps({"runs": len(own), "bank_hash": bank, "cats": cats, "model": model,
                                                "base_url": base_url, "rows": [
        {"cat": "category-a", "kind": "own(0/1)", "runs": own}, {"cat": "category-b", "kind": "pack(0/1/2)", "runs": pack}, *extra]}))
    return f"runs/{rid}"

def make_env(root, m=None):
    """Offline regression checks."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "adr").mkdir()
    cand = root / "config.candidate.yaml"
    cand.write_text('model_list:\n- model_name: entry-b\n  litellm_params: {model: openai/model-new-run, api_base: "http://127.0.0.1:8000/v1"}\n')
    live = root / "live-primary.yaml"
    live.write_text('model_list:\n- model_name: entry-b\n  litellm_params: {model: openai/model-old, api_base: "http://backend-a.example.invalid:9002/v1"}\n')
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "ledger.jsonl").write_text(json.dumps({"kind": "accept", "candidate_sha256": hashlib.sha256(live.read_bytes()).hexdigest()}) + "\n")
    sha = hashlib.sha256(cand.read_bytes()).hexdigest()
    (root / "adr" / "ADR-0001-route-change.md").write_text(
        f"# ADR-0001  route change ({date.today().isoformat()})\n candidate  config sha256 {sha[:12]}\n")
    results(root, "old-run", [92.0, 94.0], [88.0, 90.0])
    results(root, "new-run", [90.0, 91.0], [86.0, 88.0])
    trig = root / "research" / "trigger-example.json"; trig.parent.mkdir()

    trig.write_text('{"fallback": "second-hop", "requests": 12, "overflowed": 4, "triggered": 4}')

    (root / "research" / "prefill-old.json").write_text('{"prompt_tokens": 65536, "prefill_tps_runs": [2301.5, 2318.0, 2330.2], "model": "model-old", "base_url": "http://backend-a.example.invalid:9002/v1"}')
    (root / "research" / "prefill-new.json").write_text('{"prompt_tokens": 65536, "prefill_tps_runs": [2301.5, 2318.0, 2330.2], "model": "model-new-run", "base_url": "http://127.0.0.1:8000/v1"}')
    if m is not None:
        m.ADR_DIR = str(root / "adr"); m.STATE_DIR = str(root / "state")
        m.PERMIT_DIR = str(root / "state" / "permits"); m.LEDGER = str(root / "state" / "ledger.jsonl")
        m.EVALBANK = str(root / "evaluation"); m.TRIGGER_ROOT = str(root)
    return root

def full(env):
    return {"old": {"target": "SECOND_HOP :8901", "quality_runs": [90.0, 92.0], "quality_src": "runs/old-run",
                    "prefill_tps": 2318, "prefill_src": str(env / "research" / "prefill-old.json"), "decode_tps": 32.8},
            "new": {"target": "FIRST_HOP backend", "quality_runs": [88.0, 89.5], "quality_src": ["runs/new-run"],
                    "prefill_tps": 2318, "prefill_src": str(env / "research" / "prefill-new.json"), "decode_tps": 32.8},
            "fallbacks": [{"name": "second-hop", "trigger_test": str(env / "research" / "trigger-example.json")}]}


DECODE_ONLY_EVIDENCE = {"old": {"target": "SECOND_HOP :8901", "decode_tps": 32.8},
               "new": {"target": "FIRST_HOP backend :8000", "decode_tps": 32.8}, "fallbacks": []}

@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path / "e")

def run(m, env, evidence, adr="ADR-0001", candidate=None, base=None):
    make_env_consts(m, env)
    ev = env / "evidence.json"; ev.write_text(json.dumps(evidence))
    return m.main(["--evidence", str(ev), "--adr", adr, "--candidate", str(candidate or env / "config.candidate.yaml"),
                   "--base", str(base or env / "live-primary.yaml")])

def make_env_consts(m, env):
    m.ADR_DIR = str(env / "adr"); m.STATE_DIR = str(env / "state"); m.PERMIT_DIR = str(env / "state" / "permits")
    m.LEDGER = str(env / "state" / "ledger.jsonl"); m.EVALBANK = str(env / "evaluation"); m.TRIGGER_ROOT = str(env)

def expect_reject(m, env, capsys, evidence, *needles, **kw):
    """Offline regression checks."""
    led = env / "state" / "ledger.jsonl"; before = led.read_bytes() if led.exists() else None
    try:
        rc = run(m, env, evidence, **kw)
    except Exception:
        return False
    out = capsys.readouterr().out
    pd = env / "state" / "permits"
    clean = not (pd.exists() and any(f.endswith(".json") for f in os.listdir(pd))) and (led.read_bytes() if led.exists() else None) == before
    return rc == 1 and " REFUSE WRITE " in out and " ALLOW WRITE " not in out and clean and all(n in out for n in needles)


def test_decode_only_rejected(env, capsys):
    assert expect_reject(load_mod(), env, capsys, DECODE_ONLY_EVIDENCE,
                         "old.quality_runs", "new.quality_runs", "old.prefill_tps", "new.prefill_tps", "quality_src")

def test_single_run_quality_rejected(env, capsys):
    ev = full(env); ev["new"]["quality_runs"] = [89.6]
    assert expect_reject(load_mod(), env, capsys, ev, " only  1  runs ")

def test_missing_old_side_rejected(env, capsys):
    ev = full(env); del ev["old"]
    assert expect_reject(load_mod(), env, capsys, ev, " missing  old  side ")


def test_verbatim_touched_old_adr_unrelated_evidence_path_rejected(tmp_path, capsys):
    """Offline regression checks."""
    env = make_env(tmp_path / "unrelated-evidence"); m = load_mod(); (env / "unrelated.txt").write_text("synthetic unrelated input")
    a = env / "adr" / "ADR-0002-unrelated.md"; a.write_text("#  old  ADR, content unrelated to change ")
    old = time.time() - 10 * 3600; os.utime(a, (old, old)); os.utime(a, None)
    side = {"quality_runs": [90, 91], "quality_src": "x", "prefill_tps": 1, "decode_tps": 1}
    ev = {"old": side, "new": side, "fallbacks": [{"name": "second-hop", "trigger_test": str(env / "unrelated.txt")}]}
    assert expect_reject(m, env, capsys, ev, "old.quality_src x  outside ", "unrelated.txt  outside ", "sha256  prefix  12  digits ", adr="ADR-0002")

def test_quality_runs_must_match_results_json(env, capsys):
    ev = full(env); ev["new"]["quality_runs"] = [91.0, 92.0]
    assert expect_reject(load_mod(), env, capsys, ev, "new.quality_runs [91.0, 92.0]  and  results.json", "[88.0, 89.5]")

def test_quality_src_outside_evaluation_runs_rejected(env, capsys, tmp_path):
    fake = tmp_path / "fake"; fake.mkdir()
    (fake / "results.json").write_text(json.dumps({"runs": 2, "rows": [{"kind": "own", "runs": [88.0, 89.5]}]}))
    ev = full(env); ev["new"]["quality_src"] = str(fake)
    assert expect_reject(load_mod(), env, capsys, ev, " outside ")

def test_quality_src_missing_run_rejected(env, capsys):
    ev = full(env); ev["old"]["quality_src"] = "runs/missing-run"
    assert expect_reject(load_mod(), env, capsys, ev, " cannot find  results.json")

def test_bank_or_cats_mismatch_rejected(env, capsys):
    results(env, "new-otherbank", [90.0, 91.0], [86.0, 88.0], bank="deadbeefdeadbeef")
    ev = full(env); ev["new"]["quality_src"] = "runs/new-otherbank"
    assert expect_reject(load_mod(), env, capsys, ev, "bank_hash/cats/ arm parameters ")

def test_quality_metric_own(env, capsys):
    ev = full(env); ev["quality_metric"] = "own"
    ev["old"]["quality_runs"] = [92.0, 94.0]; ev["new"]["quality_runs"] = [90.0, 91.0]
    m = load_mod()
    assert run(m, env, ev) == 0, capsys.readouterr().out

def test_trigger_test_must_mention_fallback(env, capsys):
    other = env / "research" / "unrelated.json"; other.write_text('{"x": 1}')
    ev = full(env); ev["fallbacks"][0]["trigger_test"] = str(other)
    assert expect_reject(load_mod(), env, capsys, ev, " body does not mention  second-hop")


def test_stale_adr_rejected(env, capsys):
    f = env / "adr" / "ADR-0001-route-change.md"; t = time.time() - 7 * 3600; os.utime(f, (t, t))
    assert expect_reject(load_mod(), env, capsys, full(env), "> 2  hours ")

def test_adr_without_candidate_sha_rejected(env, capsys):
    (env / "adr" / "ADR-0001-route-change.md").write_text(f"# ADR-0001({date.today().isoformat()})\n missing  sha\n")
    assert expect_reject(load_mod(), env, capsys, full(env), "sha256  prefix  12  digits ")

def test_adr_with_only_old_body_date_rejected(env, capsys):
    sha = hashlib.sha256((env / "config.candidate.yaml").read_bytes()).hexdigest()
    (env / "adr" / "ADR-0001-route-change.md").write_text(f"# ADR-0001(2026-09-01)\n{sha[:12]}\n")
    assert expect_reject(load_mod(), env, capsys, full(env), " today or yesterday date ")

def test_missing_adr_rejected(env, capsys):
    assert expect_reject(load_mod(), env, capsys, full(env), "has no  ADR-0999", adr="ADR-0999")

def test_duplicate_adr_number_rejected(env, capsys):
    (env / "adr" / "ADR-0001-other.md").write_text("x")
    assert expect_reject(load_mod(), env, capsys, full(env), " duplicate decision number ")


def test_fallback_without_trigger_test_rejected(env, capsys):
    ev = full(env); ev["fallbacks"] = [{"name": "second-hop"}]
    assert expect_reject(load_mod(), env, capsys, ev, "fallbacks[0] second-hop")
    ev = full(env); del ev["fallbacks"]
    assert expect_reject(load_mod(), env, capsys, ev, " missing  fallbacks")

def test_bad_candidate_rejected(env, capsys):
    bad = env / "bad.yaml"; bad.write_text("model_list: [unclosed\n")
    assert expect_reject(load_mod(), env, capsys, full(env), " invalid  YAML", candidate=bad)

def test_candidate_without_model_list_rejected(env, capsys):
    """Offline regression checks."""
    for text in ("general_settings: {master_key: x}\n", "model_list: []\n"):
        c = env / "nolist.yaml"; c.write_text(text)
        assert expect_reject(load_mod(), env, capsys, full(env), " missing nonempty  model_list", candidate=c), text


def test_full_evidence_allowed_prints_commands_permit_and_ledger(env, capsys):
    m = load_mod()
    assert run(m, env, full(env)) == 0
    out = capsys.readouterr().out
    assert " ALLOW WRITE " in out and " quality median  88.8(2  runs " in out
    permits = [f for f in os.listdir(env / "state" / "permits") if f.endswith(".json")]; assert len(permits) == 1
    p = json.load(open(env / "state" / "permits" / permits[0]))

    assert hashlib.sha256(open(p["candidate"], "rb").read()).hexdigest() == p["candidate_sha256"] and p["candidate"] in p["commands"][0]
    assert p["expires"] - p["created"] == 1800 and len(p["commands"]) == 3
    for c in p["commands"]:
        assert c in out and c.startswith(f"LITELLM_ROUTE_PERMIT={p['id']} ")
    assert "scp " in p["commands"][0] and "gateway.example.invalid:/usr/local/etc/litellm-gateway/staging/" in p["commands"][0]
    assert p["commands"][1].endswith(f"gateway.example.invalid:/usr/local/etc/litellm-gateway/staging/{p['id']}.json")
    assert p["commands"][2].endswith(f"'sudo -n /usr/local/sbin/route_apply.py {p['id']}'") and p.get("base_sha256")
    assert not any("config.yaml.bak" in c or ":examples/gateway.example.yaml" in c for c in p["commands"])
    led = [json.loads(l) for l in open(env / "state" / "ledger.jsonl")]
    assert led[0]["kind"] == "accept" and led[1:] == [{"ts": led[1]["ts"], "kind": "permit", "id": p["id"], "adr": "ADR-0001",
                                                       "candidate_sha256": p["candidate_sha256"]}]

def test_accept_baseline(env, capsys):
    m = load_mod(); make_env_consts(m, env); shutil.rmtree(env / "state")
    live = env / "live.yaml"; live.write_text("model_list:\n- model_name: entry-a\n")
    assert m.main(["--accept", str(live), "--adr", "ADR-0001"]) == 1
    assert not (env / "state").exists()
    sha = hashlib.sha256(live.read_bytes()).hexdigest()
    (env / "adr" / "ADR-0001-route-change.md").write_text(f"#  baseline registration  {date.today().isoformat()}\n{sha[:12]}\n")
    assert m.main(["--accept", str(live), "--adr", "ADR-0001"]) == 0
    led = [json.loads(l) for l in open(env / "state" / "ledger.jsonl")]
    assert led[-1]["kind"] == "accept" and led[-1]["candidate_sha256"] == sha
    assert not (env / "state" / "permits").exists()


def test_reverse_gates_removed(tmp_path, capsys):
    env = make_env(tmp_path / "r")
    gutted = load_mod([("    return p + pa, ev, adr_file", "    return [], ev, adr_file")])
    assert not expect_reject(gutted, env, capsys, DECODE_ONLY_EVIDENCE, "old.quality_runs")
    no_age = load_mod([("if age > ADR_MAX_AGE:", "if False:")])
    f = env / "adr" / "ADR-0001-route-change.md"; t = time.time() - 7 * 3600; os.utime(f, (t, t))
    assert not expect_reject(no_age, env, capsys, full(env), "> 2  hours ")
    env2 = make_env(tmp_path / "r2")
    no_recon = load_mod([("abs(a - b) > 0.1 for a, b in zip(sorted(runs), sorted(computed))", "False")])
    ev = full(env2); ev["new"]["quality_runs"] = [91.0, 92.0]
    assert not expect_reject(no_recon, env2, capsys, ev, " does not match ")
    no_sha = load_mod([("if cand_sha and cand_sha[:SHA_PREFIX] not in body:", "if False:")])
    (env2 / "adr" / "ADR-0001-route-change.md").write_text(f"# {date.today().isoformat()}  none  sha\n")
    assert not expect_reject(no_sha, env2, capsys, full(env2), "sha256  prefix  12  digits ")
    env3 = make_env(tmp_path / "r3")
    no_list = load_mod([("if not (isinstance(ml, list) and ml and", "if False and (isinstance(ml, list) and ml and")])
    c = env3 / "nolist.yaml"; c.write_text("general_settings: {}\n")
    assert not expect_reject(no_list, env3, capsys, full(env3), "model_list", candidate=c)


def test_same_run_both_sides_rejected(env, capsys):
    """Offline regression checks."""
    ev = full(env); ev["new"]["quality_src"] = ev["old"]["quality_src"]; ev["new"]["quality_runs"] = ev["old"]["quality_runs"]
    assert expect_reject(load_mod(), env, capsys, ev, "reference the same  results.json")

def test_same_endpoint_different_runs_rejected(env, capsys):
    results(env, "old-run-rerun", [92.0, 94.0], [88.0, 90.0])
    ev = full(env); ev["new"]["quality_src"] = "runs/old-run-rerun"; ev["new"]["quality_runs"] = [90.0, 92.0]
    assert expect_reject(load_mod(), env, capsys, ev, "(model, base_url)  same ")

def test_results_binding_fields(env, capsys):
    ev = full(env); ev["new"]["base_url"] = "http://backend-a.example.invalid:9002/v1"
    assert expect_reject(load_mod(), env, capsys, ev, "new.base_url", " does not match ")
    results(env, "new-run-b", [90.0, 91.0], [86.0, 88.0], base_url="http://backend-other.example.invalid:9001/v1")
    ev = full(env); ev["new"]["quality_src"] = ["runs/new-run", "runs/new-run-b"]
    ev["new"]["quality_runs"] = [88.0, 89.5, 88.0, 89.5]
    assert expect_reject(load_mod(), env, capsys, ev, " mixed endpoints ")
    d = env / "evaluation" / "runs" / "new-run" / "results.json"
    j = json.loads(d.read_text()); del j["model"]; d.write_text(json.dumps(j))
    assert expect_reject(load_mod(), env, capsys, full(env), " missing  model/base_url")
    ev = full(env); ev["new"]["model"] = "model-new-run"; ev["new"]["base_url"] = "http://127.0.0.1:8000/v1/"
    env2 = make_env(env.parent / "e2")
    ev2 = full(env2); ev2["new"]["model"] = "model-new-run"; ev2["new"]["base_url"] = "http://127.0.0.1:8000/v1/"
    assert run(load_mod(), env2, ev2) == 0

def test_adr_as_trigger_test_rejected(env, capsys):
    """Offline regression checks."""
    adr = env / "adr" / "ADR-0001-route-change.md"
    adr.write_text(adr.read_text() + " decision  2:second-hop  as  fallback  first-hop handling  backend 4  concurrency overflow \n")
    ev = full(env); ev["fallbacks"][0]["trigger_test"] = str(adr)
    assert expect_reject(load_mod(), env, capsys, ev, "trigger_test", " outside ", " decision records are not measurements ")
    for bad in ('{"fallback": "second-hop", "requests": 12, "overflowed": 0}',
                '{"fallback": "second-hop", "triggered": 0}', '{"note": "second-hop  expected to serve "}',
                "second-hop  triggered  4  times (markdown  note )"):
        (env / "research" / "trigger-example.json").write_text(bad)
        assert expect_reject(load_mod(), env, capsys, full(env), "fallbacks[0]"), bad

def test_cherry_pick_subset_of_runs_rejected(env, capsys):
    """Offline regression checks."""
    shutil.rmtree(env / "evaluation" / "runs" / "new-run")
    results(env, "new-run", [90.0, 91.0, 60.0], [86.0, 88.0, 50.0])
    assert expect_reject(load_mod(), env, capsys, full(env), " does not match ")
    shutil.rmtree(env / "evaluation" / "runs" / "new-run")
    results(env, "new-run", [90.0, 91.0, 99.0], [86.0, 88.0, 99.0])
    assert expect_reject(load_mod(), env, capsys, full(env), " does not match ")

def test_unsourced_or_cached_prefill_rejected(env, capsys):
    """Offline regression checks."""
    ev = full(env); ev["new"]["prefill_tps"] = 28894
    assert expect_reject(load_mod(), env, capsys, ev, "new.prefill_tps 28894", " difference  >2%")
    ev = full(env); del ev["old"]["prefill_src"]
    assert expect_reject(load_mod(), env, capsys, ev, "old.prefill_src")
    (env / "research" / "prefill-cached.json").write_text('{"prefill_tps_runs": [28894.0, 28894.0, 28894.0]}')
    ev = full(env); ev["old"]["prefill_tps"] = 28894; ev["old"]["prefill_src"] = str(env / "research" / "prefill-cached.json")
    assert expect_reject(load_mod(), env, capsys, ev, " identical ", "prefix cache")
    ev = full(env); ev["new"]["prefill_src"] = str(env / "adr" / "ADR-0001-route-change.md")
    assert expect_reject(load_mod(), env, capsys, ev, "new.prefill_src", " outside ")

def test_runs_prefix_traversal_rejected(env, capsys):
    """Offline regression checks."""
    fake = env / "evaluation" / "runs2" / "x"; fake.mkdir(parents=True)
    shutil.copy(env / "evaluation" / "runs" / "new-run" / "results.json", fake / "results.json")
    ev = full(env); ev["new"]["quality_src"] = str(fake)
    assert expect_reject(load_mod(), env, capsys, ev, " outside ")
    (env / "research2").mkdir(); shutil.copy(env / "research" / "trigger-example.json", env / "research2" / "o.json")
    ev = full(env); ev["fallbacks"][0]["trigger_test"] = str(env / "research2" / "o.json")
    assert expect_reject(load_mod(), env, capsys, ev, " outside ")

def test_cats_mismatch_rejected(env, capsys):
    """Offline regression checks."""
    results(env, "new-run-cats", [90.0, 91.0], [86.0, 88.0], cats=CATS + ["category-c"],
            extra=[{"cat": "category-c", "kind": "own(0/1)", "runs": [90.0, 91.0]}])
    ev = full(env); ev["new"]["quality_src"] = "runs/new-run-cats"
    assert expect_reject(load_mod(), env, capsys, ev, "bank_hash/cats/ arm parameters ")

def test_accept_only_bootstrap_or_known_version(env, capsys):
    """Offline regression checks."""
    m = load_mod(); make_env_consts(m, env); shutil.rmtree(env / "state")
    def adr_for(*files):
        (env / "adr" / "ADR-0001-route-change.md").write_text(
            f"# {date.today().isoformat()}\n" + "\n".join(hashlib.sha256(f.read_bytes()).hexdigest()[:12] for f in files))
    v1 = env / "v1.yaml"; v1.write_text("model_list:\n- model_name: entry-b\n")
    v2 = env / "v2.yaml"; v2.write_text("model_list:\n- model_name: entry-b\n  model_info: {max_input_tokens: 262144}\n")
    adr_for(v1, v2)
    assert m.main(["--accept", str(v1), "--adr", "ADR-0001"]) == 0
    capsys.readouterr()
    assert m.main(["--accept", str(v2), "--adr", "ADR-0001"]) == 1
    assert " not a previously registered version " in capsys.readouterr().out
    assert m.main(["--accept", str(v1), "--adr", "ADR-0001"]) == 0
    assert [json.loads(l)["candidate_sha256"][:12] for l in open(env / "state" / "ledger.jsonl")] == \
           [hashlib.sha256(v1.read_bytes()).hexdigest()[:12]] * 2

def test_reverse_identity_and_history_gates_removed(tmp_path, capsys):
    env = make_env(tmp_path / "r")
    no_same = load_mod([("    if fo & fn:", "    if False:"), ("    elif io and io == inw:", "    elif False:")])
    ev = full(env); ev["new"]["quality_src"] = ev["old"]["quality_src"]; ev["new"]["quality_runs"] = ev["old"]["quality_runs"]
    assert not expect_reject(no_same, env, capsys, ev, " same file ")
    no_trig = load_mod([("elif d.get(\"fallback\") != name or not (num(n) and n > 0):", "elif False:"),
                        ("    if not any(inside(t, r) for r in measure_dirs()):", "    if False:"),
                        ("    if not isinstance(d, dict):\n        raise", "    if False:\n        raise")])
    env2 = make_env(tmp_path / "r2")
    adr = env2 / "adr" / "ADR-0001-route-change.md"; adr.write_text(adr.read_text() + "second-hop\n")
    ev = full(env2); ev["fallbacks"][0]["trigger_test"] = str(adr)
    assert not expect_reject(no_trig, env2, capsys, ev, "trigger_test")
    no_pf = load_mod([("    if num(s.get(\"prefill_tps\")) and s[\"prefill_tps\"] > 0:\n        try:", "    if False:\n        try:")])
    env3 = make_env(tmp_path / "r3"); ev = full(env3); ev["new"]["prefill_tps"] = 28894
    assert not expect_reject(no_pf, env3, capsys, ev, "prefill")
    no_hist = load_mod([("    if not problems and hist and s not in hist:", "    if False:")])
    make_env_consts(no_hist, env3)
    a = env3 / "a.yaml"; a.write_text("model_list:\n- model_name: x\n"); b = env3 / "b.yaml"; b.write_text("model_list:\n- model_name: y\n")
    (env3 / "adr" / "ADR-0001-route-change.md").write_text(
        f"# {date.today().isoformat()}\n{hashlib.sha256(a.read_bytes()).hexdigest()[:12]}\n{hashlib.sha256(b.read_bytes()).hexdigest()[:12]}\n")
    assert no_hist.main(["--accept", str(a), "--adr", "ADR-0001"]) == 0
    assert no_hist.main(["--accept", str(b), "--adr", "ADR-0001"]) == 0

if __name__ == "__main__":
    sys.exit(pytest.main(["-q", __file__]))


import copy, yaml
BASE_DOC = {"model_list": [
    {"model_name": "entry-a", "litellm_params": {"model": "openai/model-x", "api_base": "http://127.0.0.1:8000/v1"},
     "model_info": {"max_input_tokens": 229376}},
    {"model_name": "second-hop", "litellm_params": {"model": "openai/model-y", "api_base": "http://backend-a.example.invalid:9002/v1"},
     "model_info": {"max_input_tokens": 1000000}},
    {"model_name": "cloud-tail", "litellm_params": {"model": "openrouter/vendor/model-z"}},
    {"model_name": "cloud-other", "litellm_params": {"model": "openrouter/x/y"}}],
    "router_settings": {"num_retries": 1, "fallbacks": [{"entry-a": ["cloud-tail"]}]}}
BASE_CFG = yaml.safe_dump(BASE_DOC, sort_keys=False, allow_unicode=True)
CAP = [{"entry": "second-hop", "max_input_tokens": 131072, "reason": "  prefix  >200K  requires a capacity review "}]

def nonprimary_env(m, env, cand, ledger_base=True):
    make_env_consts(m, env)
    base = env / "live.yaml"; base.write_text(BASE_CFG)
    c = env / "cand-np.yaml"; c.write_text(cand if isinstance(cand, str) else yaml.safe_dump(cand, sort_keys=False, allow_unicode=True))
    if ledger_base:
        (env / "state").mkdir(exist_ok=True)
        (env / "state" / "ledger.jsonl").write_text(json.dumps({"kind": "accept", "candidate_sha256": hashlib.sha256(base.read_bytes()).hexdigest()}) + "\n")
    sha = hashlib.sha256(c.read_bytes()).hexdigest()
    (env / "adr" / "ADR-0001-route-change.md").write_text(f"# ADR-0001({date.today().isoformat()})\n candidate  {sha[:12]}\n")
    return base, c, sha

def trig(env, name, covers, sha):
    t = env / "research" / f"trig-{name}.json"
    t.write_text(json.dumps({"fallback": name, "triggered": 3, "covers": covers, "candidate_sha256": sha[:12]}))
    return {"name": name, "trigger_test": str(t)}

def run_np(m, env, evidence, base, cand):
    ev = env / "evidence-np.json"; ev.write_text(json.dumps(evidence))
    return m.main(["--evidence", str(ev), "--adr", "ADR-0001", "--candidate", str(cand), "--base", str(base)])

def doc(**edits):
    d = copy.deepcopy(BASE_DOC)
    for f in edits.values(): f(d)
    return d
cap131 = lambda d: d["model_list"][1]["model_info"].update(max_input_tokens=131072)
reinsert = lambda d: d["router_settings"].update(fallbacks=[{"entry-a": ["second-hop", "cloud-tail"]}])

def test_nonprimary_capacity_allowed(env, capsys):
    m = load_mod(); base, cand, _ = nonprimary_env(m, env, doc(a=cap131))
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 0
    assert " ALLOW WRITE " in capsys.readouterr().out

def test_nonprimary_capacity_with_cap_reason_allowed(env, capsys):
    m = load_mod(); base, cand, _ = nonprimary_env(m, env, doc(a=cap131, b=lambda d: d["model_list"][1]["model_info"].update(cap_reason="  temporary cap ")))
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 0

def test_nonprimary_cannot_smuggle_primary_change(env, capsys):
    m = load_mod(); base, cand, _ = nonprimary_env(m, env, doc(a=cap131, b=lambda d: d["model_list"][0]["litellm_params"].update(api_base="http://evil:9/v1")))
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 1
    assert " entry structure changed " in capsys.readouterr().out

@pytest.mark.parametrize("key,value", [("rpm", 1), ("tpm", 1), ("weight", 100)])
def test_sibling_routing_keys_rejected(env, capsys, key, value):
    m = load_mod(); base, cand, _ = nonprimary_env(m, env, doc(a=cap131, b=lambda d: d["model_list"][0].update({key: value})))
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 1
    assert " entry structure changed " in capsys.readouterr().out

def test_yaml_merge_key_rejected(env, capsys):
    text = yaml.safe_dump(doc(a=cap131), sort_keys=False, allow_unicode=True).replace("- model_name: entry-a", "- <<: &limits {rpm: 1}\n  model_name: entry-a", 1)
    m = load_mod(); base, cand, _ = nonprimary_env(m, env, text)
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 1

def test_nonprimary_duplicate_model_name_smuggle_rejected(env, capsys):
    m = load_mod(); base, cand, _ = nonprimary_env(m, env, doc(a=cap131, b=lambda d: d["model_list"].append(
        {"model_name": "entry-a", "litellm_params": {"model": "openai/x", "api_base": "http://evil:9/v1"}})))
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 1
    assert " ALLOW WRITE " not in capsys.readouterr().out

def test_nonprimary_base_must_be_ledger_last(env, capsys):
    m = load_mod(); base, cand, _ = nonprimary_env(m, env, doc(a=cap131), ledger_base=False)
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 1
    assert " not the last registered ledger version " in capsys.readouterr().out

def test_nonprimary_undeclared_capacity_change_rejected(env, capsys):
    m = load_mod(); base, cand, _ = nonprimary_env(m, env, doc(a=lambda d: d["model_list"][0]["model_info"].update(max_input_tokens=32768)))
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 1
    assert "caps  undeclared " in capsys.readouterr().out

@pytest.mark.parametrize("value", [None, 0, -1, True, "32768"])
def test_invalid_capacity_rejected(env, capsys, value):
    m = load_mod(); base, cand, _ = nonprimary_env(m, env, doc(a=lambda d: d["model_list"][1]["model_info"].update(max_input_tokens=value)))
    ev = {"kind": "capacity", "caps": [{"entry": "second-hop", "max_input_tokens": value, "reason": " synthetic case "}]}
    assert run_np(m, env, ev, base, cand) == 1
    assert " must be a positive integer " in capsys.readouterr().out

def test_nonprimary_fallback_needs_bound_trigger_test(env, capsys):
    m = load_mod(); base, cand, sha = nonprimary_env(m, env, doc(a=reinsert))
    assert run_np(m, env, {"kind": "fallback", "fallbacks": [{"name": "second-hop"}]}, base, cand) == 1
    capsys.readouterr()
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 1
    assert "kind  does not include  fallback" in capsys.readouterr().out
    wrong_sha = trig(env, "second-hop", ["fallbacks:entry-a"], "0" * 64)
    assert run_np(m, env, {"kind": "fallback", "fallbacks": [wrong_sha]}, base, cand) == 1
    assert "candidate_sha256" in capsys.readouterr().out
    no_cover = trig(env, "second-hop", [], sha)
    assert run_np(m, env, {"kind": "fallback", "fallbacks": [no_cover]}, base, cand) == 1
    assert "covers  missing " in capsys.readouterr().out

def test_nonprimary_capacity_plus_fallback_allowed(env, capsys):
    m = load_mod(); base, cand, sha = nonprimary_env(m, env, doc(a=cap131, b=reinsert))
    only_new = {"kind": "capacity+fallback", "caps": CAP, "fallbacks": [trig(env, "second-hop", ["fallbacks:entry-a"], sha)]}
    assert run_np(m, env, only_new, base, cand) == 1
    assert "cloud-tail" in capsys.readouterr().out
    ev = dict(only_new, fallbacks=only_new["fallbacks"] + [trig(env, "cloud-tail", ["fallbacks:entry-a"], sha)])
    assert run_np(m, env, ev, base, cand) == 0
    assert " ALLOW WRITE " in capsys.readouterr().out

@pytest.mark.parametrize("section", ["fallbacks", "context_window_fallbacks"])
def test_unmeasured_cloud_target_rejected(env, capsys, section):
    m = load_mod(); base, cand, sha = nonprimary_env(m, env, doc(a=lambda d: d["router_settings"].update({section: [{"entry-a": ["cloud-other"]}]})))
    ev = {"kind": "fallback", "fallbacks": [trig(env, "second-hop", [f"{section}:entry-a"], sha)]}
    assert run_np(m, env, ev, base, cand) == 1
    assert "cloud-other" in capsys.readouterr().out

def test_reorder_to_cloud_first_needs_evidence(env, capsys):
    base_doc = doc(a=reinsert)
    m = load_mod(); make_env_consts(m, env)
    base = env / "live.yaml"; base.write_text(yaml.safe_dump(base_doc, sort_keys=False))
    (env / "state").mkdir(exist_ok=True)
    (env / "state" / "ledger.jsonl").write_text(json.dumps({"kind": "accept", "candidate_sha256": hashlib.sha256(base.read_bytes()).hexdigest()}) + "\n")
    cd = copy.deepcopy(base_doc); cd["router_settings"]["fallbacks"] = [{"entry-a": ["cloud-tail", "second-hop"]}]
    c = env / "cand-np.yaml"; c.write_text(yaml.safe_dump(cd, sort_keys=False)); sha = hashlib.sha256(c.read_bytes()).hexdigest()
    (env / "adr" / "ADR-0001-route-change.md").write_text(f"# ADR-0001({date.today().isoformat()})\n candidate  {sha[:12]}\n")
    assert run_np(m, env, {"kind": "fallback", "fallbacks": [trig(env, "second-hop", ["fallbacks:entry-a"], sha)]}, base, c) == 1
    assert "cloud-tail" in capsys.readouterr().out

@pytest.mark.parametrize("fb", [
    [{"entry-a": ["missing-target"]}], [{"entry-a": ["entry-a"]}], [{"Entry-a": ["cloud-other"]}],
    [{"entry-a": "cloud-other"}], [{"entry-a": [{"model": "cloud-other", "api_base": "http://example.invalid/v1"}]}]])
def test_invalid_fallback_shapes_rejected(env, capsys, fb):
    m = load_mod(); base, cand, sha = nonprimary_env(m, env, doc(a=lambda d: d["router_settings"].update(fallbacks=fb)))
    ev = {"kind": "fallback", "fallbacks": [trig(env, "second-hop", ["fallbacks:entry-a"], sha)]}
    assert run_np(m, env, ev, base, cand) == 1

def test_nonprimary_fallback_removal_needs_declaration(env, capsys):
    m = load_mod(); base, cand, sha = nonprimary_env(m, env, doc(a=lambda d: d["router_settings"].update(fallbacks=[])))
    assert run_np(m, env, {"kind": "fallback", "fallbacks": []}, base, cand) == 1
    assert "fallback_removals" in capsys.readouterr().out
    assert run_np(m, env, {"kind": "fallback", "fallback_removals": ["fallbacks:entry-a"]}, base, cand) == 0

def test_candidate_swapped_after_check_uses_checked_snapshot(env, capsys, monkeypatch):
    m = load_mod(); base, cand, sha = nonprimary_env(m, env, doc(a=cap131))
    orig = m.check
    def check_then_swap(*a, **k):
        r = orig(*a, **k); cand.write_text("model_list: []\n"); return r
    monkeypatch.setattr(m, "check", check_then_swap)
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 0
    permits = [f for f in os.listdir(env / "state" / "permits") if f.endswith(".json")]
    pj = json.load(open(env / "state" / "permits" / permits[0]))
    assert pj["candidate_sha256"] == sha and hashlib.sha256(open(pj["candidate"], "rb").read()).hexdigest() == sha
    assert pj["candidate"] in pj["commands"][0]


def _np_env(env, base_doc, cand_doc):
    m = load_mod(); make_env_consts(m, env)
    base = env / "live.yaml"; base.write_text(yaml.safe_dump(base_doc, sort_keys=False))
    (env / "state").mkdir(exist_ok=True)
    (env / "state" / "ledger.jsonl").write_text(json.dumps({"kind": "accept", "candidate_sha256": hashlib.sha256(base.read_bytes()).hexdigest()}) + "\n")
    c = env / "cand-np.yaml"; c.write_text(yaml.safe_dump(cand_doc, sort_keys=False)); sha = hashlib.sha256(c.read_bytes()).hexdigest()
    (env / "adr" / "ADR-0001-route-change.md").write_text(f"# ADR-0001({date.today().isoformat()})\n candidate  {sha[:12]}\n")
    return m, base, c, sha

def _fb(chain):
    return doc(a=lambda d: d["router_settings"].update(fallbacks=[{"entry-a": chain}]))

def test_tail_reorder_needs_evidence(env, capsys):
    m, base, c, sha = _np_env(env, _fb(["second-hop", "cloud-tail", "cloud-other"]), _fb(["second-hop", "cloud-other", "cloud-tail"]))
    assert run_np(m, env, {"kind": "fallback", "fallbacks": []}, base, c) == 1
    out = capsys.readouterr().out; assert "cloud-tail" in out and "cloud-other" in out
    fs = [trig(env, t, ["fallbacks:entry-a"], sha) for t in ("cloud-other", "cloud-tail")]
    assert run_np(m, env, {"kind": "fallback", "fallbacks": fs}, base, c) == 0

def test_swap_keeping_predecessor_set_needs_only_moved(env, capsys):
    m, base, c, sha = _np_env(env, _fb(["second-hop", "cloud-tail", "cloud-other"]), _fb(["cloud-tail", "second-hop", "cloud-other"]))
    fs = [trig(env, t, ["fallbacks:entry-a"], sha) for t in ("cloud-tail", "second-hop")]
    assert run_np(m, env, {"kind": "fallback", "fallbacks": fs}, base, c) == 0

def test_partial_removal_needs_declaration(env, capsys):
    m, base, c, sha = _np_env(env, _fb(["second-hop", "cloud-tail"]), _fb(["second-hop"]))
    assert run_np(m, env, {"kind": "fallback", "fallbacks": [{"name": "never-tested"}]}, base, c) == 1
    out = capsys.readouterr().out; assert "fallbacks:entry-a:cloud-tail" in out and " unrelated evidence " in out
    assert run_np(m, env, {"kind": "fallback", "fallback_removals": ["fallbacks:entry-a:cloud-tail"]}, base, c) == 0

def test_middle_removal_needs_declaration_and_evidence(env, capsys):
    m, base, c, sha = _np_env(env, _fb(["second-hop", "cloud-tail", "cloud-other"]), _fb(["second-hop", "cloud-other"]))
    assert run_np(m, env, {"kind": "fallback", "fallback_removals": ["fallbacks:entry-a:cloud-tail"]}, base, c) == 1
    assert "cloud-other" in capsys.readouterr().out
    ev = {"kind": "fallback", "fallback_removals": ["fallbacks:entry-a:cloud-tail"],
          "fallbacks": [trig(env, "cloud-other", ["fallbacks:entry-a"], sha)]}
    assert run_np(m, env, ev, base, c) == 0

def test_phantom_removal_declaration_rejected(env, capsys):
    m, base, c, sha = _np_env(env, _fb(["second-hop", "cloud-tail"]), _fb(["second-hop"]))
    ev = {"kind": "fallback", "fallback_removals": ["fallbacks:entry-a:cloud-tail", "fallbacks:entry-a:cloud-other"]}
    assert run_np(m, env, ev, base, c) == 1
    assert "this removal did not happen" in capsys.readouterr().out

@pytest.mark.parametrize("prefix", [1, 11, 13, 63])
def test_short_or_odd_sha_prefix_rejected(env, capsys, prefix):
    m, base, c, sha = _np_env(env, BASE_DOC, doc(a=reinsert))
    fs = [trig(env, t, ["fallbacks:entry-a"], sha) for t in ("second-hop", "cloud-tail")]
    for f in fs:
        d = json.loads(open(f["trigger_test"]).read()); d["candidate_sha256"] = sha[:prefix]; open(f["trigger_test"], "w").write(json.dumps(d))
    assert run_np(m, env, {"kind": "fallback", "fallbacks": fs}, base, c) == 1
    assert "12  or  64  hex prefix " in capsys.readouterr().out

def test_full_sha_accepted(env, capsys):
    m, base, c, sha = _np_env(env, BASE_DOC, doc(a=reinsert))
    fs = [trig(env, t, ["fallbacks:entry-a"], sha) for t in ("second-hop", "cloud-tail")]
    for f in fs:
        d = json.loads(open(f["trigger_test"]).read()); d["candidate_sha256"] = sha; open(f["trigger_test"], "w").write(json.dumps(d))
    assert run_np(m, env, {"kind": "fallback", "fallbacks": fs}, base, c) == 0

@pytest.mark.parametrize("value", [True, 1.0])
def test_candidate_cap_type_checked(env, capsys, value):
    cd = copy.deepcopy(BASE_DOC); cd["model_list"][1]["model_info"]["max_input_tokens"] = value
    m, base, c, sha = _np_env(env, BASE_DOC, cd)
    ev = {"kind": "capacity", "caps": [{"entry": "second-hop", "max_input_tokens": 1, "reason": "test"}]}
    assert run_np(m, env, ev, base, c) == 1
    assert " candidate  max_input_tokens  must be a positive integer " in capsys.readouterr().out

def test_cycle_rejected(env, capsys):
    cd = copy.deepcopy(BASE_DOC); cd["router_settings"]["fallbacks"] = [{"entry-a": ["second-hop"]}, {"second-hop": ["entry-a"]}]
    m, base, c, sha = _np_env(env, BASE_DOC, cd)
    fs = [trig(env, "second-hop", ["fallbacks:entry-a"], sha), trig(env, "entry-a", ["fallbacks:second-hop"], sha)]
    assert run_np(m, env, {"kind": "fallback", "fallbacks": fs, "fallback_removals": ["fallbacks:entry-a:cloud-tail"]}, base, c) == 1
    assert " fallback cycle " in capsys.readouterr().out

def test_snapshot_changed_during_check_rejected(env, capsys, monkeypatch):
    m = load_mod(); base, cand, sha = nonprimary_env(m, env, doc(a=cap131))
    orig = m.check
    def swap(*a, **k):
        r = orig(*a, **k)
        forged = yaml.safe_load(open(a[2])); forged["model_list"][0]["litellm_params"]["api_base"] = "http://example.invalid/v1"
        open(a[2], "w").write(yaml.safe_dump(forged)); return r
    monkeypatch.setattr(m, "check", swap)
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 1
    assert " snapshot changed " in capsys.readouterr().out
    assert not (env / "state" / "permits").exists() or not list((env / "state" / "permits").glob("*.json"))

def test_permit_records_checked_evidence_bytes(env, capsys, monkeypatch):
    m = load_mod(); base, cand, sha = nonprimary_env(m, env, doc(a=cap131))
    orig = m.check
    def swap(*a, **k):
        r = orig(*a, **k); (env / "evidence-np.json").write_text('{"kind":"unvalidated"}'); return r
    monkeypatch.setattr(m, "check", swap)
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 0
    pj = json.load(open(next((env / "state" / "permits").glob("*.json"))))
    frozen = json.load(open(pj["evidence"]))
    assert frozen["kind"] == "capacity" and hashlib.sha256(open(pj["evidence"], "rb").read()).hexdigest() == pj["evidence_sha256"]
    assert hashlib.sha256(open(pj["candidate"], "rb").read()).hexdigest() == pj["candidate_sha256"] == sha
    assert not os.access(pj["candidate"], os.W_OK)

def test_frozen_not_rehashed_via_replaceable_function(env, capsys, monkeypatch):
    m = load_mod(); base, cand, sha = nonprimary_env(m, env, doc(a=cap131))
    orig = m.sha256
    def hash_then_swap(path):
        d = orig(path)
        if str(path).endswith(".candidate.yaml"): open(path, "w").write("model_list: []\n")
        return d
    monkeypatch.setattr(m, "sha256", hash_then_swap)
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, cand) == 0
    pj = json.load(open(next((env / "state" / "permits").glob("*.json"))))
    assert orig(pj["candidate"]) == pj["candidate_sha256"] == sha


@pytest.mark.parametrize("base_v,cand_v", [(131072, 131072.0), (1, True)])
def test_equal_value_type_change_rejected(env, capsys, base_v, cand_v):
    bd = copy.deepcopy(BASE_DOC); bd["model_list"][1]["model_info"]["max_input_tokens"] = base_v
    cd = copy.deepcopy(bd); cd["model_list"][1]["model_info"]["max_input_tokens"] = cand_v
    m, base, c, sha = _np_env(env, bd, cd)
    assert run_np(m, env, {"kind": "capacity", "caps": []}, base, c) == 1
    assert " must be a positive integer " in capsys.readouterr().out

def test_cross_section_cycle_rejected(env, capsys):
    cd = copy.deepcopy(BASE_DOC)
    cd["router_settings"]["fallbacks"] = [{"entry-a": ["cloud-tail"]}, {"second-hop": ["entry-a"]}]
    cd["router_settings"]["context_window_fallbacks"] = [{"entry-a": ["second-hop"]}]
    m, base, c, sha = _np_env(env, BASE_DOC, cd)
    fs = [trig(env, "entry-a", ["fallbacks:second-hop"], sha), trig(env, "second-hop", ["context_window_fallbacks:entry-a"], sha)]
    assert run_np(m, env, {"kind": "fallback", "fallbacks": fs}, base, c) == 1
    assert " fallback cycle " in capsys.readouterr().out

def test_cwf_removal_requires_surviving_fallback_evidence(env, capsys):
    bd = copy.deepcopy(BASE_DOC); bd["router_settings"]["context_window_fallbacks"] = [{"entry-a": ["cloud-other"]}]
    cd = copy.deepcopy(bd); del cd["router_settings"]["context_window_fallbacks"]
    m, base, c, sha = _np_env(env, bd, cd)
    decl = {"kind": "fallback", "fallback_removals": ["context_window_fallbacks:entry-a"]}
    assert run_np(m, env, decl, base, c) == 1
    assert "cloud-tail" in capsys.readouterr().out
    ev = dict(decl, fallbacks=[trig(env, "cloud-tail", ["context_window_fallbacks:entry-a"], sha)])
    assert run_np(m, env, ev, base, c) == 0


RES = {"timeout": 180, "stream_timeout": 90, "num_retries": 0}
def res_doc(**over):
    d = copy.deepcopy(BASE_DOC); d["model_list"][0]["litellm_params"].update(dict(RES, **over)); return d

def res_matrix(env, sha, entries=("entry-a",), hang_s=183.0, pass_=True, drop=(), cfg_sha=None, stream_s=92.0):
    rows = [{"scenario": sc, "entry": "entry-a", "ok": True} for sc in
            ("both_local_dead", "input_150k", "input_40k_second_hop_dead", "first_hop_http500") if sc not in drop]
    if "first_hop_hang" not in drop: rows += [{"scenario": "first_hop_hang", "entry": e, "ok": True, "seconds": hang_s} for e in entries]
    for sc in ("first_hop_hang_stream", "first_hop_stall_stream"):
        if sc not in drop: rows += [{"scenario": sc, "entry": e, "ok": True, "seconds": stream_s} for e in entries]
    f = env / "evaluation" / "runs" / f"trigger-matrix-{len(list((env / 'evaluation' / 'runs').glob('trigger-matrix-*')))}.json"
    f.write_text(json.dumps({"config_sha256": cfg_sha or sha, "pass": pass_, "results": rows})); return str(f)

def res_ev(env, sha, **kw):
    return {"kind": "resilience", "resilience": [dict(RES, entry="entry-a", reason="FIRST_HOP  hang  603 s ->  single attempt  180 s  no retry ")],
            "matrix": res_matrix(env, sha, **kw)}

def test_resilience_allowed_with_candidate_matrix(env, capsys):
    m, base, c, sha = _np_env(env, BASE_DOC, res_doc())
    assert run_np(m, env, res_ev(env, sha), base, c) == 0
    assert " ALLOW WRITE " in capsys.readouterr().out

def test_resilience_change_without_kind_rejected(env, capsys):
    m, base, c, sha = _np_env(env, BASE_DOC, res_doc())
    assert run_np(m, env, {"kind": "capacity", "caps": []}, base, c) == 1
    assert "kind  does not include  resilience" in capsys.readouterr().out

@pytest.mark.parametrize("mutate,needle", [
    (lambda ev: ev["resilience"][0].update(timeout=120), " versus declaration "),
    (lambda ev: ev["resilience"].append(dict(RES, entry="second-hop", reason="x")), "resilience settings did not change"),
    (lambda ev: ev["resilience"][0].update(reason=" "), " missing  reason"),
])
def test_resilience_declaration_must_match(env, capsys, mutate, needle):
    m, base, c, sha = _np_env(env, BASE_DOC, res_doc())
    ev = res_ev(env, sha); mutate(ev)
    assert run_np(m, env, ev, base, c) == 1
    assert needle in capsys.readouterr().out

@pytest.mark.parametrize("over", [{"timeout": True}, {"timeout": -1}, {"stream_timeout": "90"}, {"num_retries": 1.5}, {"num_retries": -1}])
def test_resilience_bad_types_rejected(env, capsys, over):
    m, base, c, sha = _np_env(env, BASE_DOC, res_doc(**over))
    ev = res_ev(env, sha); ev["resilience"][0].update(over)
    assert run_np(m, env, ev, base, c) == 1

@pytest.mark.parametrize("kw,needle", [
    ({"cfg_sha": "0" * 64}, " not this candidate full  sha"),
    ({"pass_": False}, " not all passed "),
    ({"drop": ("input_150k",)}, " missing scenarios "),
    ({"drop": ("first_hop_hang",)}, " missing scenarios "),
    ({"entries": ("entry-b",)}, " missing  entry-a  of  first_hop_hang"),
    ({"hang_s": 603.2}, " exceeds budget "),
])
def test_resilience_matrix_evidence_enforced(env, capsys, kw, needle):
    m, base, c, sha = _np_env(env, BASE_DOC, res_doc())
    assert run_np(m, env, res_ev(env, sha, **kw), base, c) == 1
    assert needle in capsys.readouterr().out

def test_resilience_cannot_smuggle_other_param(env, capsys):
    m, base, c, sha = _np_env(env, BASE_DOC, res_doc(api_base="http://evil:9/v1"))
    assert run_np(m, env, res_ev(env, sha), base, c) == 1
    assert " entry structure changed " in capsys.readouterr().out



def test_no_computable_scores_rejected(env, capsys):
    d = env / "evaluation" / "runs" / "new-run" / "results.json"; r = json.loads(d.read_text())
    for row in r["rows"]: row["runs"] = []
    d.write_text(json.dumps(r))
    assert expect_reject(load_mod(), env, capsys, full(env), " cannot compute per-run totals ")

def test_cap_key_removed_rejected(env, capsys):
    cd = copy.deepcopy(BASE_DOC); del cd["model_list"][1]["model_info"]["max_input_tokens"]
    m, base, c, sha = _np_env(env, BASE_DOC, cd)
    assert run_np(m, env, {"kind": "capacity", "caps": CAP}, base, c) == 1
    assert " removed  max_input_tokens" in capsys.readouterr().out

def test_infinite_timeout_rejected(env, capsys):
    m, base, c, sha = _np_env(env, BASE_DOC, res_doc(timeout=float("inf")))
    ev = res_ev(env, sha); ev["resilience"][0]["timeout"] = float("inf")
    assert run_np(m, env, ev, base, c) == 1

def test_infinite_matrix_seconds_rejected(env, capsys):
    m, base, c, sha = _np_env(env, BASE_DOC, res_doc())
    assert run_np(m, env, res_ev(env, sha, hang_s=float("inf")), base, c) == 1

@pytest.mark.parametrize("missing", ["timeout", "stream_timeout", "num_retries"])
def test_resilience_keys_must_be_explicit(env, capsys, missing):
    d = copy.deepcopy(BASE_DOC); lp = dict(RES); lp.pop(missing); d["model_list"][0]["litellm_params"].update(lp)
    m, base, c, sha = _np_env(env, BASE_DOC, d)
    ev = res_ev(env, sha); ev["resilience"][0].pop(missing)
    assert run_np(m, env, ev, base, c) == 1
    assert "must explicitly" in capsys.readouterr().out

def test_retry_policy_rejected(env, capsys):
    d = res_doc(); d["router_settings"]["retry_policy"] = {"TimeoutErrorRetries": 3}
    bd = copy.deepcopy(BASE_DOC); bd["router_settings"]["retry_policy"] = {"TimeoutErrorRetries": 3}
    m, base, c, sha = _np_env(env, bd, d)
    assert run_np(m, env, res_ev(env, sha), base, c) == 1
    assert "retry_policy" in capsys.readouterr().out

@pytest.mark.parametrize("kw,needle", [({"drop": ("first_hop_hang_stream",)}, " missing scenarios "), ({"drop": ("first_hop_stall_stream",)}, " missing scenarios "),
                                       ({"stream_s": 400.0}, " exceeds budget ")])
def test_stream_scenarios_enforced(env, capsys, kw, needle):
    m, base, c, sha = _np_env(env, BASE_DOC, res_doc())
    assert run_np(m, env, res_ev(env, sha, **kw), base, c) == 1
    assert needle in capsys.readouterr().out

def test_covers_reachable_downstream(env, capsys):
    bd = copy.deepcopy(BASE_DOC)
    bd["router_settings"]["fallbacks"] = [{"entry-a": ["second-hop"]}, {"second-hop": ["cloud-tail"]}]
    bd["router_settings"]["context_window_fallbacks"] = [{"entry-a": ["cloud-other"]}]
    cd = copy.deepcopy(bd); del cd["router_settings"]["context_window_fallbacks"]
    m, base, c, sha = _np_env(env, bd, cd)
    decl = {"kind": "fallback", "fallback_removals": ["context_window_fallbacks:entry-a"],
            "fallbacks": [trig(env, "second-hop", ["context_window_fallbacks:entry-a"], sha)]}
    assert run_np(m, env, decl, base, c) == 1
    assert "cloud-tail" in capsys.readouterr().out
    decl["fallbacks"].append(trig(env, "cloud-tail", ["context_window_fallbacks:entry-a"], sha))
    assert run_np(m, env, decl, base, c) == 0

def test_router_value_type_change_rejected(env, capsys):
    d = res_doc(); d["router_settings"]["num_retries"] = True
    m, base, c, sha = _np_env(env, BASE_DOC, d)
    assert run_np(m, env, res_ev(env, sha), base, c) == 1
    assert "router_settings" in capsys.readouterr().out


def test_primary_candidate_must_be_the_tested_endpoint(env, capsys):
    c = env / "untested.yaml"
    c.write_text('model_list:\n- model_name: entry-b\n  litellm_params: {model: openai/UNTESTED, api_base: "http://unmeasured.invalid/v1"}\n')
    (env / "adr" / "ADR-0001-route-change.md").write_text(f"# {date.today().isoformat()}\n{hashlib.sha256(c.read_bytes()).hexdigest()[:12]}\n")
    assert expect_reject(load_mod(), env, capsys, full(env), " is not  new  side measured identity ", candidate=c)

def test_primary_requires_base_from_ledger(env, capsys):
    other = env / "other-live.yaml"; other.write_text("model_list:\n- model_name: entry-b\n  litellm_params: {model: openai/model-old}\n")
    assert expect_reject(load_mod(), env, capsys, full(env), " not the last registered ledger version ", base=other)

def test_primary_without_endpoint_change_rejected(env, capsys):
    same = env / "same.yaml"; same.write_text((env / "live-primary.yaml").read_text())
    (env / "adr" / "ADR-0001-route-change.md").write_text(f"# {date.today().isoformat()}\n{hashlib.sha256(same.read_bytes()).hexdigest()[:12]}\n")
    assert expect_reject(load_mod(), env, capsys, full(env), "no candidate entry changed", candidate=same)


def test_primary_duplicate_deployments_rejected(env, capsys):
    live = env / "live-primary.yaml"
    live.write_text('model_list:\n- model_name: entry-b\n  litellm_params: {model: openai/UNTESTED, api_base: "http://u.invalid/v1"}\n'
                    '- model_name: entry-b\n  litellm_params: {model: openai/model-old, api_base: "http://backend-a.example.invalid:9002/v1"}\n')
    (env / "state" / "ledger.jsonl").write_text(json.dumps({"kind": "accept", "candidate_sha256": hashlib.sha256(live.read_bytes()).hexdigest()}) + "\n")
    c = env / "dup-cand.yaml"
    c.write_text('model_list:\n- model_name: entry-b\n  litellm_params: {model: openai/model-new-run, api_base: "http://127.0.0.1:8000/v1"}\n'
                 '- model_name: entry-b\n  litellm_params: {model: openai/model-old, api_base: "http://backend-a.example.invalid:9002/v1"}\n')
    (env / "adr" / "ADR-0001-route-change.md").write_text(f"# {date.today().isoformat()}\n{hashlib.sha256(c.read_bytes()).hexdigest()[:12]}\n")
    assert expect_reject(load_mod(), env, capsys, full(env), " duplicate deployments ", candidate=c)

def test_same_results_listed_twice_rejected(env, capsys):
    ev = full(env); ev["new"]["quality_src"] = ["runs/new-run", "runs/new-run"]
    assert expect_reject(load_mod(), env, capsys, ev, " duplicate reference to same ")


def _primary_cand(env, text):
    c = env / "p-cand.yaml"; c.write_text(text)
    (env / "adr" / "ADR-0001-route-change.md").write_text(f"# {date.today().isoformat()}\n{hashlib.sha256(c.read_bytes()).hexdigest()[:12]}\n")
    return c

def test_primary_cannot_carry_router_change(env, capsys):
    c = _primary_cand(env, (env / "config.candidate.yaml").read_text() + "router_settings:\n  fallbacks:\n  - entry-b: [entry-b]\n")
    assert expect_reject(load_mod(), env, capsys, full(env), "router_settings", candidate=c)

def test_primary_cannot_carry_resilience_change(env, capsys):
    c = _primary_cand(env, 'model_list:\n- model_name: entry-b\n  litellm_params: {model: openai/model-new-run, api_base: "http://127.0.0.1:8000/v1", timeout: 9999, num_retries: 99}\n')
    assert expect_reject(load_mod(), env, capsys, full(env), "kind=resilience", candidate=c)

def test_new_prefill_must_be_new_endpoint(env, capsys):
    ev = full(env); ev["new"]["prefill_src"] = ev["old"]["prefill_src"]; ev["new"]["prefill_tps"] = 2318
    assert expect_reject(load_mod(), env, capsys, ev, "new.prefill_src  measured identity ")

def test_prefill_without_identity_rejected(env, capsys):
    f = env / "research" / "prefill-new.json"; d = json.loads(f.read_text()); del d["model"]; f.write_text(json.dumps(d))
    assert expect_reject(load_mod(), env, capsys, full(env), "prefill_src  missing  model/base_url")

def test_incomplete_runs_rejected(env, capsys):
    d = env / "evaluation" / "runs" / "new-run" / "results.json"; j = json.loads(d.read_text())
    j["rows"][1]["runs"] = [86.0]; d.write_text(json.dumps(j))
    ev = full(env); ev["new"]["quality_runs"] = [88.0, 91.0]
    assert expect_reject(load_mod(), env, capsys, ev, " incomplete scores ")


@pytest.mark.parametrize("sec", ["fallbacks", "context_window_fallbacks"])
def test_primary_cannot_drop_referenced_entry(env, capsys, sec):
    rs = f"router_settings:\n  {sec}:\n  - entry-b: [rescue]\n"
    live = env / "live-primary.yaml"
    live.write_text('model_list:\n- model_name: entry-b\n  litellm_params: {model: openai/model-old, api_base: "http://backend-a.example.invalid:9002/v1"}\n'
                    '- model_name: rescue\n  litellm_params: {model: openrouter/x/y}\n' + rs)
    (env / "state" / "ledger.jsonl").write_text(json.dumps({"kind": "accept", "candidate_sha256": hashlib.sha256(live.read_bytes()).hexdigest()}) + "\n")
    c = _primary_cand(env, (env / "config.candidate.yaml").read_text() + rs)
    assert expect_reject(load_mod(), env, capsys, full(env), "rescue", candidate=c)


BAD_LEDGER_TAILS = (b"{not json\n", b"\xff\xfe\n", b'{"kind": "permit"}\n', b'["x"]\n',
                    b'{"kind": "permit", "candidate_sha256": "abc"}\n')

def test_corrupt_ledger_not_treated_as_empty(env, capsys):
    """Offline regression checks."""
    m = load_mod(); make_env_consts(m, env)
    live = env / "live-primary.yaml"; led = env / "state" / "ledger.jsonl"
    adr = env / "adr" / "ADR-0001-route-change.md"
    adr.write_text(adr.read_text() + hashlib.sha256(live.read_bytes()).hexdigest()[:12] + "\n")
    good = led.read_bytes()
    for tail in BAD_LEDGER_TAILS:
        led.write_bytes(good + tail)
        assert m.main(["--accept", str(live), "--adr", "ADR-0001"]) == 1, tail
        assert " corrupt " in capsys.readouterr().out, tail
        assert led.read_bytes() == good + tail, tail
        assert expect_reject(m, env, capsys, full(env), " corrupt "), tail
    led.write_bytes(good); led.chmod(0)
    try:
        assert m.main(["--accept", str(live), "--adr", "ADR-0001"]) == 1
        assert " corrupt ( unreadable " in capsys.readouterr().out
    finally:
        led.chmod(0o644)
    led.write_bytes(b"")
    assert m.main(["--accept", str(live), "--adr", "ADR-0001"]) == 0

def _patch_results(env, fn, rids=("old-run", "new-run")):
    for rid in rids:
        p = env / "evaluation" / "runs" / rid / "results.json"
        d = json.loads(p.read_text()); fn(d); p.write_text(json.dumps(d))

def test_results_metadata_required(tmp_path, capsys):
    """Offline regression checks."""
    cases = [(lambda d: (d.pop("bank_hash"), d.pop("cats")), " incomplete metadata "),
             (lambda d: d.update(bank_hash=""), " incomplete metadata "),
             (lambda d: d.update(cats=CATS + ["category-c"]), " every category needs a row "),
             (lambda d: d.update(cats=["category-a", "category-a", "category-b"]), " incomplete metadata "),
             (lambda d: d["rows"].append(dict(d["rows"][0])), " incomplete metadata "),
             (lambda d: d["rows"][1].update(cat="unknown-category"), " incomplete metadata ")]
    for i, (fn, needle) in enumerate(cases):
        env = make_env(tmp_path / f"c{i}"); _patch_results(env, fn)
        assert expect_reject(load_mod(), env, capsys, full(env), needle), i

    env = make_env(tmp_path / "own")
    _patch_results(env, lambda d: d.update(cats=CATS + ["category-c"]))
    _patch_results(env, lambda d: d["rows"].append({"cat": "category-c", "kind": "own(0/1)", "runs": [90.0, 90.0]}),
                   rids=("old-run",))
    ev = full(env); ev["quality_metric"] = "own"
    assert expect_reject(load_mod(), env, capsys, ev, "bank_hash/cats/ arm parameters ")


def test_append_after_missing_final_newline(env, capsys):
    """Offline regression checks."""
    m = load_mod(); make_env_consts(m, env)
    live = env / "live-primary.yaml"; led = env / "state" / "ledger.jsonl"
    adr = env / "adr" / "ADR-0001-route-change.md"
    adr.write_text(adr.read_text() + hashlib.sha256(live.read_bytes()).hexdigest()[:12] + "\n")
    led.write_bytes(led.read_bytes().rstrip(b"\n"))
    assert m.main(["--accept", str(live), "--adr", "ADR-0001"]) == 0
    assert len(m.read_ledger()) == 2 and led.read_bytes().count(b"\n") == 2
    import importlib.util
    spec = importlib.util.spec_from_file_location("iv9", os.path.join(os.path.dirname(HERE), "catalog", "infra_validate.py"))
    iv = importlib.util.module_from_spec(spec); spec.loader.exec_module(iv); iv.LEDGER = str(led)
    assert len(iv.ledger_entries()) == 2


def test_primary_rejects_invalid_capacity(tmp_path, capsys):
    """Offline regression checks."""
    for i, bad in enumerate(("-1", "0", "131072.0", "true")):
        env = make_env(tmp_path / f"e{i}")
        cand = env / "config.candidate.yaml"
        cand.write_text('model_list:\n- model_name: entry-b\n  litellm_params: {model: openai/model-new-run, api_base: "http://127.0.0.1:8000/v1"}\n'
                        f'  model_info: {{max_input_tokens: {bad}}}\n')
        sha = hashlib.sha256(cand.read_bytes()).hexdigest()
        (env / "adr" / "ADR-0001-route-change.md").write_text(f"# ADR-0001  route change ({date.today().isoformat()})\n candidate  config sha256 {sha[:12]}\n")
        assert expect_reject(load_mod(), env, capsys, full(env), " must be a positive integer "), bad


def test_candidate_resilience_types_checked_for_all_entries(tmp_path):
    """Offline regression checks."""
    mod = load_mod()
    bad = tmp_path / "bad.yaml"
    bad.write_text("model_list:\n- model_name: x\n  litellm_params: {model: openai/m, api_base: 'http://127.0.0.1:1/v1', timeout: -1, stream_timeout: false, num_retries: -1}\n  model_info: {max_input_tokens: 100}\n")
    probs = mod.check_candidate(str(bad))
    assert any("timeout  must be finite positive " in x for x in probs) and any("stream_timeout  must be finite positive " in x for x in probs) and any("num_retries  must be a nonnegative integer " in x for x in probs), probs
    good = tmp_path / "good.yaml"
    good.write_text("model_list:\n- model_name: x\n  litellm_params: {model: openai/m, api_base: 'http://127.0.0.1:1/v1', timeout: 1500, stream_timeout: 1500, num_retries: 0}\n  model_info: {max_input_tokens: 100}\n")
    assert mod.check_candidate(str(good)) == []
