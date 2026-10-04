#!/usr/bin/env python3
"""Offline regression checks."""
import json, os, subprocess, sys, time, types
from datetime import datetime, timedelta
import pytest, yaml
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_YAML = os.path.join(HERE, "services.example.yaml")

def load_mod(mutate=()):
    """Offline regression checks."""
    path = os.path.join(HERE, "infra_validate.py")
    src = open(path).read()
    for a, b in mutate:
        assert a in src, f" mutation anchor missing ( source changed ?):{a}"
        src = src.replace(a, b)
    m = types.ModuleType("infra_validate"); m.__file__ = path
    exec(compile(src, path, "exec"), m.__dict__); return m

def harden(m, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "CRED", str(tmp_path / "credentials.env"))
    monkeypatch.setattr(m, "LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    monkeypatch.setattr(m, "http_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError(f" not  mock  of  HTTP {a}")))
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError(f" not  mock  of  ssh {a}")))
    return m

@pytest.fixture
def iv(monkeypatch, tmp_path):
    return harden(load_mod(), monkeypatch, tmp_path)

GATEWAY = """services:
  litellm-gateway:
    host: gateway.example.invalid
    endpoint: http://127.0.0.1:4000/v1
    model: entry-a
    probe_key_env: LITELLM_MASTER_KEY
    ssot: examples/gateway.example.yaml
    shape: gateway
"""

def write_yaml(tmp_path, text):
    p = tmp_path / "services.yaml"; p.write_text(text); return str(p)

def run_main(m, monkeypatch, path, *args):
    monkeypatch.setattr(m, "PATH", path)
    monkeypatch.setattr(sys, "argv", ["infra_validate.py", *args])
    with pytest.raises(SystemExit) as e:
        m.main()
    return e.value.code

def fake_http(routes, calls=None):
    def f(url, key=None):
        if calls is not None: calls.append((url, key))
        if url not in routes: raise OSError(f"no route {url}")
        r = routes[url]
        if isinstance(r, Exception): raise r
        return r
    return f

def capacity_routes(max_input, model="openai/model-x"):

    return {
        "http://127.0.0.1:4000/v1/model/info": {"data": [
            {"model_name": "entry-a",
             "litellm_params": {"model": model, "api_base": "http://backend-a.example.invalid:9002/v1"},
             "model_info": {"max_input_tokens": max_input}},
            {"model_name": "cloud-tail", "litellm_params": {"model": "openrouter/vendor/model-z"}, "model_info": {}},
        ]},
        "http://backend-a.example.invalid:9002/v1/models": {"data": [{"id": "model-x", "max_model_len": 1000000}]},
    }

def with_key(tmp_path):
    (tmp_path / "credentials.env").write_text("LITELLM_MASTER_KEY=fixture-key\n")

def scenario_capacity(m, monkeypatch, tmp_path, max_input=131072, routes=None):
    with_key(tmp_path)
    monkeypatch.setattr(m, "http_json", fake_http(routes or capacity_routes(max_input)))
    return run_main(m, monkeypatch, write_yaml(tmp_path, "revision: 2026-09-24.1\n" + GATEWAY), "--drift")


def test_max_input_tokens_mismatch_fails(iv, monkeypatch, tmp_path, capsys):
    assert scenario_capacity(iv, monkeypatch, tmp_path) == 1
    out = capsys.readouterr().out
    assert "DRIFT FAIL litellm-gateway/entry-a: max_input_tokens 131072 !=" in out
    assert "cloud-tail" not in out

def test_fixed_value_passes(iv, monkeypatch, tmp_path, capsys):
    assert scenario_capacity(iv, monkeypatch, tmp_path, max_input=1000000) == 0
    out = capsys.readouterr().out
    assert "DRIFT OK   litellm-gateway/entry-a" in out and out.rstrip().endswith("OK: 1 services, 0 failures")

def test_low10_model_id_not_in_backend_fails(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    assert scenario_capacity(iv, monkeypatch, tmp_path, 1000000, capacity_routes(1000000, "openai/model-y")) == 1
    assert " not at backend " in capsys.readouterr().out

def test_loopback_backend_is_warn_not_probe(iv, monkeypatch, tmp_path, capsys):
    routes = capacity_routes(131072)
    routes["http://127.0.0.1:4000/v1/model/info"]["data"][0]["litellm_params"]["api_base"] = "http://127.0.0.1:8000/v1"
    calls = []
    with_key(tmp_path)
    monkeypatch.setattr(iv, "http_json", fake_http(routes, calls))
    assert iv.litellm_ctx(iv.all_blocks(yaml.safe_load(GATEWAY))) == 0
    assert " loopback address " in capsys.readouterr().out
    assert not any("8000" in u for u, _ in calls)

    assert run_main(iv, monkeypatch, write_yaml(tmp_path, "revision: 2026-09-24.1\n" + GATEWAY), "--drift") == 1


DOWN = GATEWAY.replace("127.0.0.1:4000", "127.0.0.1:9")

@pytest.mark.parametrize("reply", [ConnectionRefusedError("[Errno 61] Connection refused"),
                                   {"data": []}, {"error": {"message": "auth"}}])
def test_mid22_gateway_unreachable_or_empty_is_not_green(iv, monkeypatch, tmp_path, capsys, reply):
    with_key(tmp_path)
    monkeypatch.setattr(iv, "http_json", fake_http({"http://127.0.0.1:9/v1/model/info": reply}))
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, "revision: 2026-09-24.2\n" + DOWN), "--drift") == 1
    out = capsys.readouterr().out.rstrip().splitlines()
    assert "0  actual checks " in "\n".join(out) and out[-1] == "FAIL (skipped 1): 1 services, 1 failures"

def test_mid22_partial_skip_shows_count(iv, monkeypatch, tmp_path, capsys):
    routes = capacity_routes(1000000)
    routes["http://127.0.0.1:4000/v1/model/info"]["data"].append(
        {"model_name": "loopback-entry", "litellm_params": {"model": "openai/x", "api_base": "http://127.0.0.1:8000/v1"}, "model_info": {}})
    assert scenario_capacity(iv, monkeypatch, tmp_path, routes=routes) == 0
    assert capsys.readouterr().out.rstrip().splitlines()[-1] == "OK (skipped 1): 1 services, 0 failures"


REV_AT = {"2026-09-23.4": "2026-09-23T23:40:00", "2026-09-24.1": "2026-09-24T16:30:00"}

def watch_yaml(rev, watched, at=None, extra=""):
    at = at if at is not None else REV_AT.get(rev)
    return (f"revision: {rev}\n" + (f"revision_at: '{at}'\n" if at else "") + f"""services: {{}}
aux_services:
  aux-backend:
    host: gateway.example.invalid
    endpoint: http://127.0.0.1:8000/v1
    model: model-x
    ssot: x
    drift_watch: [{watched}]
""" + extra)

def touched(tmp_path, when="2026-09-24 15:52", name="model_settings.json"):

    f = tmp_path / name; f.write_text("{}")
    t = datetime.strptime(when, "%Y-%m-%d %H:%M").timestamp(); os.utime(f, (t, t)); return str(f)

def test_revision_older_than_model_settings_fails(iv, monkeypatch, tmp_path, capsys):
    f = touched(tmp_path)
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-23.4", f)), "--drift") == 1
    assert " later than  revision 2026-09-23.4" in capsys.readouterr().out

def test_revision_same_day_passes_and_time_precision(iv, monkeypatch, tmp_path):
    f = touched(tmp_path)
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-24.1", f)), "--drift") == 0
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-24T15:00", f)), "--drift") == 1
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-24T16:10", f)), "--drift") == 0

def test_cli_watch_flag(iv, monkeypatch, tmp_path):
    f = touched(tmp_path)
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-23.4", "")), "--drift", "--watch", f) == 1

def test_watched_path_gone_fails(iv, monkeypatch, tmp_path, capsys):

    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-24.1", str(tmp_path / "models-parked"))), "--drift") == 1
    assert " missing " in capsys.readouterr().out

def test_same_day_drift_verbatim(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    f = touched(tmp_path, "2026-09-23 16:49", "config.yaml")
    y = "revision: 2026-09-23.3\nservices:\n  litellm-gateway:\n    host: gateway.example.invalid\n    endpoint: http://127.0.0.1:4000/v1\n    model: entry-a\n    ssot: x\n"
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, y), "--drift", "--watch", f) >= 1
    assert " without  revision_at" in capsys.readouterr().out

def test_revision_at_catches_same_day_drift(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    f = touched(tmp_path, "2026-09-23 16:49", "config.yaml")
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-23.3", f, "2026-09-23T15:00:00")), "--drift") == 1
    assert "mtime 2026-09-23 16:49" in capsys.readouterr().out
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-23.3", f, "2026-09-23T17:00:00")), "--drift") == 0
    g = touched(tmp_path, "2026-09-24 16:28")
    at_tz = datetime(2026, 9, 24, 16, 0).astimezone().isoformat()
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-24.2", g, at_tz)), "--drift") == 1
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-24.2", g, "2026-09-24T16:48:54")), "--drift") == 0

def test_revision_at_sanity(iv, monkeypatch, tmp_path, capsys):
    f = touched(tmp_path)
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-24.2", f, "2026-09-23T23:00:00")), "--drift") == 1
    assert " not the same day " in capsys.readouterr().out
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-24.2", f, " invalid-date ")), "--drift") == 1
    assert " not parseable " in capsys.readouterr().out
    fut = datetime.now() + timedelta(hours=3)
    y = watch_yaml(fut.strftime("%Y-%m-%d") + ".9", f, fut.strftime("%Y-%m-%dT%H:%M:%S"))
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, y), "--drift") == 1
    assert " later than  services.yaml  file write timestamp " in capsys.readouterr().out


PATCHED = """revision: 2026-09-24.1
revision_at: '2026-09-24T16:30:00'
services: {{}}
aux_services:
  aux-backend:
    host: gateway.example.invalid
    endpoint: http://127.0.0.1:8000/v1
    model: m
    ssot: x
    local_patches: ["backend/api/utils.py _NATIVE_REASONING_MODEL_TYPES += hy_v3", "backend/engine/vlm.py DSML  parser patch "]
{dw}"""

def test_local_patches_without_drift_watch_fail(iv, monkeypatch, tmp_path, capsys):
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, PATCHED.format(dw="")), "--drift") == 1
    assert " has  local_patches  without  drift_watch" in capsys.readouterr().out
    t = datetime(2026, 9, 24, 10, 59).timestamp()
    for sub in ("api/utils.py", "engine/vlm.py"):
        f = tmp_path / "backend" / sub; f.parent.mkdir(parents=True, exist_ok=True); f.write_text("x"); os.utime(f, (t, t))
    u, v = tmp_path / "backend/api/utils.py", tmp_path / "backend/engine/vlm.py"
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, PATCHED.format(dw=f"    drift_watch: [{u}]\n")), "--drift") == 1
    out = capsys.readouterr().out
    assert "backend/engine/vlm.py  outside  drift_watch  in " in out and "utils.py  outside " not in out
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, PATCHED.format(dw=f"    drift_watch: [{u}, {v}]\n")), "--drift") == 0

def test_synthetic_catalog_watch_structure(iv, monkeypatch):
    doc = yaml.safe_load(open(FIXTURE_YAML))
    svcs = iv.all_blocks(doc)
    assert {"backend-a", "backend-b", "gateway", "aux-helper"} == set(svcs)
    assert "examples/gateway.example.yaml" in svcs["gateway"]["drift_watch"]
    assert "fixtures/backend-a.sh" in svcs["backend-a"]["drift_watch"]
    monkeypatch.setattr(iv, "stat_mtime", lambda spec: (0.0, None, ""))
    monkeypatch.setattr(iv.os.path, "getmtime", lambda path: datetime(2026, 10, 4, 12).timestamp())
    assert iv.drift_watch(doc, svcs, [], FIXTURE_YAML) == 0

def test_remote_stat_unreachable_warn_missing_fail(iv, monkeypatch, tmp_path, capsys):
    seen = []
    def run(cmd, **k):
        seen.append(cmd)
        rc = {"host-down": 255, "gateway.example.invalid": 0, "host-nofile": 1, "backend-a.example.invalid": 0}[cmd[5]]
        out = str(int(datetime.strptime("2026-09-24 15:52", "%Y-%m-%d %H:%M").timestamp()))
        return subprocess.CompletedProcess(cmd, rc, stdout=out if rc == 0 else "", stderr="x")
    monkeypatch.setattr(iv.subprocess, "run", run)
    p = write_yaml(tmp_path, watch_yaml("2026-09-24.1", "host-down:~/backend/settings.json"))

    assert run_main(iv, monkeypatch, p, "--drift") == 1
    out = capsys.readouterr().out
    assert "DRIFT WARN" in out and "0  actual checks " in out
    assert seen[0][:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]

    assert seen[0][6] == 'stat -c %Y "$HOME"/backend/settings.json 2>/dev/null || stat -f %m "$HOME"/backend/settings.json'
    p = write_yaml(tmp_path, watch_yaml("2026-09-24.1", "host-nofile:~/backend/settings.json"))
    assert run_main(iv, monkeypatch, p, "--drift") == 1
    p = write_yaml(tmp_path, watch_yaml("2026-09-23.4", "gateway.example.invalid:~/backend/model_settings.json"))
    assert run_main(iv, monkeypatch, p, "--drift") == 1
    p = write_yaml(tmp_path, watch_yaml("2026-09-24.1", "backend-a.example.invalid:~/backend/adapter.py"))
    assert run_main(iv, monkeypatch, p, "--drift") == 0


SHA_YAML = "revision: 2026-09-24.1\nrevision_at: '2026-09-24T16:30:00'\n" + GATEWAY.replace(
    "    shape: gateway\n", "    drift_watch: [{cfg}]\n")

def gw_cfg(tmp_path):
    (tmp_path / "litellm-gateway").mkdir(exist_ok=True)
    return touched(tmp_path, "2026-09-23 23:25", "litellm-gateway/config.yaml")

def test_config_sha_vs_ledger(iv, monkeypatch, tmp_path, capsys):
    cfg = gw_cfg(tmp_path)
    p = write_yaml(tmp_path, SHA_YAML.format(cfg=cfg))
    assert run_main(iv, monkeypatch, p, "--drift") == 0
    out = capsys.readouterr().out
    assert " no baseline " in out and "--accept" in out and "(skipped 1)" in out
    import hashlib
    sha = hashlib.sha256(open(cfg, "rb").read()).hexdigest()
    (tmp_path / "ledger.jsonl").write_text(json.dumps({"kind": "accept", "adr": "ADR-0001", "candidate_sha256": sha}) + "\n")
    assert run_main(iv, monkeypatch, p, "--drift") == 0
    assert "=  ledger " in capsys.readouterr().out

    open(cfg, "a").write("# entry-b -> FIRST_HOP\n"); t = datetime(2026, 9, 23, 23, 25).timestamp(); os.utime(cfg, (t, t))
    assert run_main(iv, monkeypatch, p, "--drift") == 1
    assert " write bypassed  route_change.py  write " in capsys.readouterr().out

def test_config_sha_remote_command_and_unreachable(iv, monkeypatch, tmp_path, capsys):
    (tmp_path / "ledger.jsonl").write_text(json.dumps({"kind": "permit", "adr": "ADR-0001", "candidate_sha256": "ab" * 32}) + "\n")
    seen = []
    def run(cmd, **k):
        seen.append(cmd)
        if cmd[6].startswith("stat"): return subprocess.CompletedProcess(cmd, 0, stdout="1790223909", stderr="")
        return subprocess.CompletedProcess(cmd, 255, stdout="", stderr="Connection refused")
    monkeypatch.setattr(iv.subprocess, "run", run)
    p = write_yaml(tmp_path, SHA_YAML.format(cfg="gateway.example.invalid:examples/gateway.example.yaml"))
    assert run_main(iv, monkeypatch, p, "--drift") == 1
    assert any(c[6].startswith("shasum -a 256 examples/gateway.example.yaml") for c in seen)


def test_no_key_is_key_missing_not_fail(iv, monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(iv, "http_json", fake_http({}, calls))
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, GATEWAY), "--probe") == 0
    out = capsys.readouterr().out
    assert "PROBE KEY_MISSING litellm-gateway" in out and "PROBE FAIL" not in out
    assert calls == []

    assert run_main(iv, monkeypatch, write_yaml(tmp_path, "revision: 2026-09-24.1\n" + GATEWAY), "--drift") == 1
    out = capsys.readouterr().out
    assert "DRIFT KEY_MISSING" in out and "PROBE FAIL" not in out and calls == []

def scenario_credentials(m, monkeypatch, tmp_path, calls):
    (tmp_path / "credentials.env").write_text("AGG_API_KEY=other\nexport LITELLM_MASTER_KEY='fixture-key'\n")
    def http(url, key=None):
        calls.append(key)
        if key != "fixture-key": raise OSError("HTTP Error 500")
        return {"data": [{"id": "entry-a"}]}
    monkeypatch.setattr(m, "http_json", http)
    return run_main(m, monkeypatch, write_yaml(tmp_path, GATEWAY), "--probe")

def test_key_read_from_credentials_not_printed(iv, monkeypatch, tmp_path, capsys):
    calls = []
    assert scenario_credentials(iv, monkeypatch, tmp_path, calls) == 0
    assert calls == ["fixture-key"]
    assert "fixture-key" not in capsys.readouterr().out


BAD_YAML_LINE = GATEWAY.replace("    host: gateway.example.invalid\n", "    host: **gateway.example.invalid(FIRST_HOP Ultra) since  2026-09-23 F1**; via  launchd  tunnel \n")

def test_double_star_is_yaml_fail(iv, monkeypatch, tmp_path, capsys):
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, BAD_YAML_LINE), "--drift") >= 1
    assert " invalid  YAML" in capsys.readouterr().out

def test_synthetic_catalog_is_strict_yaml_with_aux():
    doc = yaml.safe_load(open(FIXTURE_YAML))
    assert {"backend-a", "backend-b", "gateway", "aux-helper"} == set(load_mod().all_blocks(doc))
    assert doc["services"]["gateway"]["host"] == "localhost"

def test_aux_service_field_gap_only_caught_in_drift(iv, monkeypatch, tmp_path, capsys):
    p = write_yaml(tmp_path, """revision: 2026-09-24.1
services:
  litellm-gateway:
    host: gateway.example.invalid
    endpoint: http://127.0.0.1:4000/v1
    model: entry-a
    ssot: x
aux_services:
  aux-backend:
    host: gateway.example.invalid
    endpoint: http://127.0.0.1:8000/v1
    model: model-x
""")
    assert run_main(iv, monkeypatch, p) == 0
    assert run_main(iv, monkeypatch, p, "--drift") == 1
    assert "FAIL aux-backend:  missing field  ssot" in capsys.readouterr().out

def test_default_and_probe_last_line_format_unchanged(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, GATEWAY), "--probe") == 0
    assert capsys.readouterr().out.rstrip().splitlines()[-1] == "OK: 1 services, 0 failures"


def test_reverse_gates_removed(monkeypatch, tmp_path):
    def mod(*pairs): return harden(load_mod(pairs), monkeypatch, tmp_path)
    m = mod(); monkeypatch.setattr(m, "litellm_ctx", lambda svcs: 0)
    assert scenario_capacity(m, monkeypatch, tmp_path) == 0
    m = mod(("            if target and checked <= 0:", "            if False:"))
    monkeypatch.setattr(m, "http_json", fake_http({"http://127.0.0.1:9/v1/model/info": {"data": []}}))
    with_key(tmp_path)
    assert run_main(m, monkeypatch, write_yaml(tmp_path, "revision: 2026-09-24.2\n" + DOWN), "--drift") == 0

    m2 = mod(); monkeypatch.setattr(m2, "drift_watch", lambda doc, svcs, extra, path=None: 0)
    f = touched(tmp_path)
    assert run_main(m2, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-23.4", f)), "--drift") == 0

    m3 = mod(); monkeypatch.setattr(m3, "load_strict", lambda p: ({"revision": "2026-09-24.1", "services": m3.parse(p)}, None))
    monkeypatch.setattr(m3, "http_json", fake_http({}))
    assert run_main(m3, monkeypatch, write_yaml(tmp_path, BAD_YAML_LINE.replace("    shape: gateway\n", "")), "--drift") == 0


    m4 = mod(); monkeypatch.setattr(m4, "resolve_key", lambda name: os.environ.get(name))
    calls = []
    scenario_credentials(m4, monkeypatch, tmp_path, calls)
    assert calls != ["fixture-key"]

    missing_key_gate = mod(("            if key_env and not key:\n", "            if False:\n"))
    monkeypatch.setattr(missing_key_gate, "CRED", str(tmp_path / "nonexistent.env"))
    monkeypatch.setattr(missing_key_gate, "http_json", fake_http({}))
    assert run_main(missing_key_gate, monkeypatch, write_yaml(tmp_path, GATEWAY), "--probe") == 1


    m6 = mod(("    if v in (None, \"\"):\n        return None, (", "    if v in (None, \"\"):\n        return datetime.strptime(str(doc.get('revision'))[:10], '%Y-%m-%d').timestamp() + 86399, None\n        return None, ("))
    g = touched(tmp_path, "2026-09-23 16:49", "config.yaml")
    assert run_main(m6, monkeypatch, write_yaml(tmp_path, watch_yaml("2026-09-23.3", g)), "--drift") == 0

    m7 = mod(("        patches = f.get(\"local_patches\") or []", "        patches = []"))
    assert run_main(m7, monkeypatch, write_yaml(tmp_path, PATCHED.format(dw="")), "--drift") == 0

    m8 = mod(("            if not hit:\n", "            hit = hit or ids\n            if not hit:\n"))
    assert scenario_capacity(m8, monkeypatch, tmp_path, 1000000, capacity_routes(1000000, "openai/other")) == 0

    m9 = mod(("            elif sha != last.get(\"candidate_sha256\"):", "            elif False:"))
    cfg = gw_cfg(tmp_path)
    (tmp_path / "ledger.jsonl").write_text(json.dumps({"kind": "accept", "candidate_sha256": "0" * 64}) + "\n")
    assert run_main(m9, monkeypatch, write_yaml(tmp_path, SHA_YAML.format(cfg=cfg)), "--drift") == 0


def ts(when): return datetime.strptime(when, "%Y-%m-%d %H:%M").timestamp()

def settle(path, when):
    os.utime(path, (ts(when), ts(when)))

def lyaml(tmp_path, rev, at, watched, comment="", extra=""):
    """Offline regression checks."""
    return write_yaml(tmp_path, f"revision: {rev}" + (f"   # {comment}" if comment else "") + f"\nrevision_at: '{at}'\n" + extra +
                      f"services: {{}}\naux_services:\n  aux-backend:\n    host: gateway.example.invalid\n    endpoint: http://127.0.0.1:8000/v1\n"
                      f"    model: m\n    ssot: x\n    drift_watch: [{watched}]\n")

def test_unrelated_bump_does_not_launder_prior_drift(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    f = tmp_path / "model_settings.json"; f.write_text('{"nf": {"temperature": 1.0}}'); settle(f, "2026-09-24 14:00")
    y = lyaml(tmp_path, "2026-09-24.1", "2026-09-24T15:00:00", f)
    assert run_main(iv, monkeypatch, y, "--drift", "--accept", "all") == 0
    assert json.load(open(tmp_path / "services.drift-baseline.json"))["files"][str(f)]["revision"] == "2026-09-24.1"
    f.write_text('{"nf": {"temperature": 0.6}}'); settle(f, "2026-09-24 15:52")
    y = lyaml(tmp_path, "2026-09-24.2", "2026-09-24T16:48:00", f, "auxiliary embedding  endpoint update ")
    capsys.readouterr()
    assert run_main(iv, monkeypatch, y, "--drift") == 1
    out = capsys.readouterr().out
    assert "DRIFT OK   aux-backend: " + str(f) in out
    assert " content differs from baseline ( baseline registered at  revision 2026-09-24.1)" in out and "model_settings.json" in out

    assert run_main(iv, monkeypatch, y, "--drift", "--accept", str(f)) == 1
    assert "revision  line comment lacks  model_settings.json" in capsys.readouterr().out
    assert run_main(iv, monkeypatch, y, "--drift", "--accept", "all") == 0
    assert run_main(iv, monkeypatch, y, "--drift") == 1

    y = lyaml(tmp_path, "2026-09-24.3", "2026-09-24T17:00:00", f, "model_settings.json model-x temperature 1.0->0.6")
    assert run_main(iv, monkeypatch, y, "--drift", "--accept", str(f)) == 0
    assert run_main(iv, monkeypatch, y, "--drift") == 0

def test_verbatim_without_baseline_needs_required(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    f = touched(tmp_path)
    y = lyaml(tmp_path, "2026-09-24.2", "2026-09-24T16:48:00", f)
    assert run_main(iv, monkeypatch, y, "--drift") == 0
    assert "DRIFT NOTE  no content baseline " in capsys.readouterr().out
    y = lyaml(tmp_path, "2026-09-24.2", "2026-09-24T16:48:00", f, extra="drift_baseline: required\n")
    assert run_main(iv, monkeypatch, y, "--drift") == 1
    assert "drift_baseline: required, but content baseline " in capsys.readouterr().out

def test_unregistered_watch_and_accept_guards(iv, monkeypatch, tmp_path, capsys):
    a = touched(tmp_path, "2026-09-24 14:00", "a.json"); b = touched(tmp_path, "2026-09-24 14:00", "b.json")
    y = lyaml(tmp_path, "2026-09-24.1", "2026-09-24T15:00:00", a)
    assert run_main(iv, monkeypatch, y, "--drift", "--accept", "all") == 0
    y = lyaml(tmp_path, "2026-09-24.1", "2026-09-24T15:00:00", f"{a}, {b}")
    capsys.readouterr()
    assert run_main(iv, monkeypatch, y, "--drift") == 1
    assert f"{b}  content baseline unregistered " in capsys.readouterr().out
    bp = tmp_path / "services.drift-baseline.json"; before = bp.read_text()
    assert run_main(iv, monkeypatch, y, "--drift", "--accept", str(b), str(tmp_path / "unwatched.txt")) == 1
    assert " not in any  drift_watch  in " in capsys.readouterr().out and bp.read_text() == before
    settle(b, "2026-09-24 15:30")
    assert run_main(iv, monkeypatch, y, "--drift", "--accept", str(b)) == 1
    assert "mtime  later than  revision_at" in capsys.readouterr().out and bp.read_text() == before
    settle(b, "2026-09-24 14:00")
    assert run_main(iv, monkeypatch, y, "--drift", "--accept", "all") == 0 and run_main(iv, monkeypatch, y, "--drift") == 0
    bp.write_text("{not json")
    assert run_main(iv, monkeypatch, y, "--drift") == 1 and " unreadable " in capsys.readouterr().out
    assert run_main(iv, monkeypatch, y, "--accept", "all") == 2

def test_signature_dir_and_remote(iv, monkeypatch, tmp_path):
    d = tmp_path / "models" / "model-x"; d.mkdir(parents=True); (d / "config.json").write_text("{}")
    s1 = iv.signature(str(d))[0]; (d / "model-00001.safetensors").write_text("x")
    assert s1.startswith("dir:") and iv.signature(str(d))[0] != s1
    (d / "config.json").write_text('{"max_position_embeddings": 1000000}')
    s2 = iv.signature(str(d))[0]; os.utime(d / "config.json", (1, 1)); assert iv.signature(str(d))[0] != s2
    assert iv.signature(str(tmp_path / "gone"))[1] == "missing"
    seen = []
    def run(cmd, **k):
        seen.append(cmd[6])
        out = {"dirhost": "dir\n" + "ab" * 32 + "  -\n", "filehost": "cd" * 32 + "  /x\n"}.get(cmd[5], "")
        return subprocess.CompletedProcess(cmd, 255 if cmd[5] == "down" else 0, stdout=out, stderr="")
    monkeypatch.setattr(iv.subprocess, "run", run)
    assert iv.signature("dirhost:~/models/model-x") == ("dir:" + "ab" * 32, None, "")
    assert iv.signature("filehost:~/backend/settings.json") == ("cd" * 32, None, "")
    assert iv.signature("down:~/x")[1] == "unreachable"
    assert seen[0].startswith('set -o pipefail; if [ -d "$HOME"/models/model-x ]') and "stat -c '%n %s %Y'" in seen[0] and "sha256sum" in seen[0]

def test_content_unreachable_all_skipped_not_green(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    y = lyaml(tmp_path, "2026-09-24.1", "2026-09-24T15:00:00", "gateway.example.invalid:~/backend/model_settings.json")
    (tmp_path / "services.drift-baseline.json").write_text(json.dumps({"files": {"gateway.example.invalid:~/backend/model_settings.json": {"sig": "x"}}}))
    monkeypatch.setattr(iv.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 255, stdout="", stderr="down"))
    assert run_main(iv, monkeypatch, y, "--drift") >= 1
    assert "5 drift_watch  content baseline : 1  targets , 0  actual checks " in capsys.readouterr().out

def test_local_patches_as_string(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    base = PATCHED.replace('["backend/api/utils.py _NATIVE_REASONING_MODEL_TYPES += hy_v3", "backend/engine/vlm.py DSML  parser patch "]',
                           "'backend/engine/vlm.py DSML  patch '")
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, base.format(dw="")), "--drift") == 1
    other = touched(tmp_path, "2026-09-24 10:00", "settings.json")
    assert run_main(iv, monkeypatch, write_yaml(tmp_path, base.format(dw=f"    drift_watch: [{other}]\n")), "--drift") == 1
    assert "backend/engine/vlm.py  outside  drift_watch  in " in capsys.readouterr().out

def test_unquoted_tz_revision_at(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    f = tmp_path / "w.json"; f.write_text("{}")
    now = time.time(); os.utime(f, (now - 3600, now - 3600))
    at = datetime.fromtimestamp(now - 60).astimezone().isoformat(timespec="seconds")
    y = write_yaml(tmp_path, f"revision: {datetime.fromtimestamp(now - 60).strftime('%Y-%m-%d')}.9\nrevision_at: {at}\nservices:\n  gw:\n"
                             f"    host: h\n    endpoint: http://127.0.0.1:9001/v1\n    model: m\n    ssot: x\n    drift_watch: ['{f}']\n")
    assert run_main(iv, monkeypatch, y, "--drift") == 0
    assert "DRIFT OK" in capsys.readouterr().out

def test_synthetic_catalog_requires_complete_baseline(iv, monkeypatch, tmp_path):
    doc = yaml.safe_load(open(FIXTURE_YAML))
    assert doc["drift_baseline"] == "required"
    svcs = iv.all_blocks(doc)
    watch = {str(w) for f in svcs.values() for w in f.get("drift_watch", [])}
    assert watch
    path = write_yaml(tmp_path, Path(FIXTURE_YAML).read_text())
    monkeypatch.setattr(iv, "stat_mtime", lambda spec: (0.0, None, ""))
    monkeypatch.setattr(iv, "signature", lambda spec: ("a" * 64, None, ""))
    monkeypatch.setattr(iv.os.path, "getmtime", lambda p: datetime(2026, 10, 4, 12).timestamp())
    assert iv.accept(doc, svcs, path, ["all"]) == 0
    baseline = json.load(open(iv.baseline_path(path)))["files"]
    assert watch == set(baseline)
    assert iv.drift_content(doc, svcs, path) == 0

def test_reverse_content_gate_removed(monkeypatch, tmp_path):
    m = harden(load_mod([("        fails += drift_content(doc, svcs, PATH)\n", "")]), monkeypatch, tmp_path)
    f = tmp_path / "model_settings.json"; f.write_text("A"); settle(f, "2026-09-24 14:00")
    assert run_main(m, monkeypatch, lyaml(tmp_path, "2026-09-24.1", "2026-09-24T15:00:00", f), "--drift", "--accept", "all") == 0
    f.write_text("B"); settle(f, "2026-09-24 15:52")
    assert run_main(m, monkeypatch, lyaml(tmp_path, "2026-09-24.2", "2026-09-24T16:48:00", f), "--drift") == 0
    m2 = harden(load_mod([("        if old and old != sig and name not in rev_line:", "        if False:")]), monkeypatch, tmp_path)
    assert run_main(m2, monkeypatch, lyaml(tmp_path, "2026-09-24.2", "2026-09-24T16:48:00", f, "auxiliary"), "--drift", "--accept", str(f)) == 0

if __name__ == "__main__":
    sys.exit(pytest.main(["-q", __file__]))


CAPS_OK = GATEWAY + """    intentional_caps:
      entry-a: {max_input_tokens: 131072, adr: ADR-0001, backend_model: model-x, review_by: 2099-01-01, review:   review before restoring }
"""
def scenario_cap(m, monkeypatch, tmp_path, max_input, reason, spec=CAPS_OK):
    routes = capacity_routes(max_input)
    if reason is not None: routes["http://127.0.0.1:4000/v1/model/info"]["data"][0]["model_info"]["cap_reason"] = reason
    with_key(tmp_path); monkeypatch.setattr(m, "http_json", fake_http(routes))
    return run_main(m, monkeypatch, write_yaml(tmp_path, "revision: 2026-09-24.1\n" + spec), "--drift")

def test_registered_intentional_cap_passes(iv, monkeypatch, tmp_path, capsys):
    assert scenario_cap(iv, monkeypatch, tmp_path, 131072, "  temporary cap ") == 0
    assert " registered intentional cap (ADR-0001" in capsys.readouterr().out

@pytest.mark.parametrize("max_input,reason,spec", [
    (131072, "  temporary cap ", GATEWAY),
    (131072, " ", CAPS_OK),
    (131072, None, CAPS_OK),
    (13107, "  temporary cap ", CAPS_OK),
    (0, "typo", CAPS_OK),
    (2000000, "  temporary cap ", CAPS_OK),
    (131072, "  temporary cap ", CAPS_OK.replace("adr: ADR-0001", "adr: none")),
    (131072, "  temporary cap ", CAPS_OK.replace("backend_model: model-x", "backend_model: other")),
    (131072, "  temporary cap ", CAPS_OK.replace("adr: ADR-0001", "adr: ADR-")),
    (131072, "  temporary cap ", CAPS_OK.replace("review_by: 2099-01-01", "review_by: 2000-01-01")),
    (131072, "  temporary cap ", CAPS_OK.replace("review_by: 2099-01-01, ", "")),
    (131072, "  temporary cap ", CAPS_OK.replace("review_by: 2099-01-01", "review_by: soon")),
])
def test_unregistered_or_mismatched_cap_fails(iv, monkeypatch, tmp_path, capsys, max_input, reason, spec):
    assert scenario_cap(iv, monkeypatch, tmp_path, max_input, reason, spec) == 1


def _loopback_ctx(iv, monkeypatch, reg):
    entry = {"model_name": "route", "litellm_params": {"model": "openai/m", "api_base": "http://127.0.0.1:8000/v1"},
             "model_info": {"max_input_tokens": 100, "cap_reason": "x"}}
    monkeypatch.setattr(iv, "http_json", lambda url, key=None: {"data": [entry]})
    svcs = {"g": {"shape": "gateway", "endpoint": "http://gateway/v1", "intentional_caps": {"route": reg}}}
    return iv.caps_offline(svcs) + iv.litellm_ctx(svcs)

@pytest.mark.parametrize("review_by", ["2000-01-01", None, "2099-02-30", "2099-99-99", "soon"])
def test_registration_checked_offline_even_for_loopback(iv, monkeypatch, review_by):
    reg = {"max_input_tokens": 100, "adr": "ADR-0001", "backend_model": "m"}
    if review_by is not None: reg["review_by"] = review_by
    assert _loopback_ctx(iv, monkeypatch, reg) >= 1

def test_valid_registration_on_loopback_not_failed(iv, monkeypatch):
    reg = {"max_input_tokens": 100, "adr": "ADR-0001", "backend_model": "m", "review_by": "2099-01-01"}
    assert _loopback_ctx(iv, monkeypatch, reg) == 0


def test_offline_caps_before_any_network(iv, monkeypatch, tmp_path):
    order = []
    real_caps = iv.caps_offline
    monkeypatch.setattr(iv, "caps_offline", lambda svcs: order.append("caps") or real_caps(svcs))
    routes = capacity_routes(131072); routes["http://127.0.0.1:4000/v1/model/info"]["data"][0]["model_info"]["cap_reason"] = "x"
    fh = fake_http(routes)
    monkeypatch.setattr(iv, "http_json", lambda url, key=None: order.append("HTTP") or fh(url, key))
    with_key(tmp_path)
    run_main(iv, monkeypatch, write_yaml(tmp_path, "revision: 2026-09-24.1\n" + CAPS_OK), "--probe", "--drift")
    assert order and order[0] == "caps", order


def _ctx_with_models(iv, monkeypatch, gw_model, mit, models):
    entry = {"model_name": "route", "litellm_params": {"model": gw_model, "api_base": "http://backend.example.invalid/v1"}, "model_info": {"max_input_tokens": mit}}
    monkeypatch.setattr(iv, "http_json", lambda url, key=None: {"data": [entry]} if url.endswith("/model/info") else {"data": models})
    return iv.litellm_ctx({"g": {"shape": "gateway", "endpoint": "http://gateway/v1"}})

def test_exact_id_wins_over_similar(iv, monkeypatch):
    models = [{"id": "model-x", "max_model_len": 32768}, {"id": "model-x-long", "max_model_len": 131072}]
    assert _ctx_with_models(iv, monkeypatch, "openai/model-x", 131072, models) == 1
    assert _ctx_with_models(iv, monkeypatch, "openai/model-x", 32768, models) == 0

def test_ambiguous_fuzzy_match_fails(iv, monkeypatch):
    models = [{"id": "org/model-x-a", "max_model_len": 32768}, {"id": "org/model-x-b", "max_model_len": 131072}]
    assert _ctx_with_models(iv, monkeypatch, "openai/model-x", 131072, models) == 1


def test_unreadable_watch_file_is_fail_not_green(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    f = tmp_path / "model_settings.json"; f.write_text('{"a": 1}'); settle(f, "2026-09-24 14:00")
    y = lyaml(tmp_path, "2026-09-24.1", "2026-09-24T15:00:00", f)
    assert run_main(iv, monkeypatch, y, "--drift", "--accept", "all") == 0
    f.chmod(0)
    try:
        capsys.readouterr()
        assert run_main(iv, monkeypatch, y, "--drift") == 2
        out = capsys.readouterr().out
        assert " unreadable , fingerprint unavailable " in out and " content  =  baseline " not in out and "0  actual checks " in out
    finally:
        f.chmod(0o644)

def test_remote_signature_failure_is_fail(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    spec = "gateway.example.invalid:~/backend/settings.json"; seen = []
    def run(cmd, **k):
        seen.append(cmd[6])
        if cmd[6].startswith("stat"): return subprocess.CompletedProcess(cmd, 0, stdout="1790223909", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Permission denied")
    monkeypatch.setattr(iv.subprocess, "run", run)
    y = lyaml(tmp_path, "2026-09-24.1", "2026-09-24T15:00:00", spec)
    (tmp_path / "services.drift-baseline.json").write_text(json.dumps({"files": {spec: {"sig": "ab" * 32, "revision": "2026-09-24.1"}}}))
    assert run_main(iv, monkeypatch, y, "--drift") == 2
    out = capsys.readouterr().out
    assert " missing or unreadable , fingerprint unavailable " in out and " content  =  baseline " not in out and "0  actual checks " in out
    assert any(c.startswith("set -o pipefail; if [ -d ") for c in seen)

def test_corrupt_ledger_is_fail_not_skip(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    import hashlib
    cfg = gw_cfg(tmp_path); p = write_yaml(tmp_path, SHA_YAML.format(cfg=cfg))
    good = (json.dumps({"kind": "accept", "adr": "ADR-0001",
                        "candidate_sha256": hashlib.sha256(open(cfg, "rb").read()).hexdigest()}) + "\n").encode()
    led = tmp_path / "ledger.jsonl"
    for tail in (b"{not json\n", b"\xff\xfe\n", b'{"kind": "permit"}\n', b'["x"]\n', b'{"candidate_sha256": "abc"}\n'):
        led.write_bytes(good + tail)
        assert run_main(iv, monkeypatch, p, "--drift") == 1, tail
        out = capsys.readouterr().out
        assert " corrupt " in out and " no baseline " not in out, tail
    led.write_bytes(good); led.chmod(0)
    try:
        assert run_main(iv, monkeypatch, p, "--drift") == 1
        assert " corrupt ( unreadable " in capsys.readouterr().out
    finally:
        led.chmod(0o644)
    led.write_bytes(b"")
    assert run_main(iv, monkeypatch, p, "--drift") == 0 and " no baseline " in capsys.readouterr().out


def test_remote_stdout_prefix_not_fingerprint(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    spec = "gateway.example.invalid:~/backend/settings.json"; real = "cd" * 32
    def run(cmd, **k):
        if cmd[6].startswith("stat"): return subprocess.CompletedProcess(cmd, 0, stdout="1790223909", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=f"SHELL_NOTICE\n{real}  /x/settings.json\n", stderr="")
    monkeypatch.setattr(iv.subprocess, "run", run)
    y = lyaml(tmp_path, "2026-09-24.1", "2026-09-24T15:00:00", spec)
    bp = tmp_path / "services.drift-baseline.json"
    bp.write_text(json.dumps({"files": {spec: {"sig": "SHELL_NOTICE", "revision": "2026-09-24.1"}}}))
    assert run_main(iv, monkeypatch, y, "--drift") == 2
    out = capsys.readouterr().out
    assert " remote output is not a fingerprint " in out and " content  =  baseline " not in out
    bp.unlink()
    assert run_main(iv, monkeypatch, y, "--drift", "--accept", "all") == 1
    assert not bp.exists()
    sha, err, why = iv.file_sha(spec)
    assert sha is None and err == "unreadable" and why.startswith(" remote output is not  sha256:SHELL_NOTICE")

    monkeypatch.setattr(iv.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout=f"dir\n{real}  -\n", stderr=""))
    assert iv.signature(spec) == ("dir:" + real, None, "")


def test_duplicate_service_across_blocks_fails(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    y = write_yaml(tmp_path, "revision: 2026-09-24.1\nservices:\n  dup:\n    host: gateway.example.invalid\n    endpoint: http://127.0.0.1:4001/v1\n    model: m\n"
                             "aux_services:\n  dup:\n    host: gateway.example.invalid\n    endpoint: http://127.0.0.1:4002/v1\n    model: m\n    ssot: x\n")
    assert run_main(iv, monkeypatch, y, "--drift") >= 1
    assert " duplicate service name in  services  and  aux_services" in capsys.readouterr().out

def test_empty_backend_model_list_is_fail_not_silent(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    routes = capacity_routes(1000000)
    routes["http://127.0.0.1:4000/v1/model/info"]["data"].append(
        {"model_name": "entry-d", "litellm_params": {"model": "openai/model-x", "api_base": "http://backend-b.example.invalid:9001/v1"},
         "model_info": {"max_input_tokens": 1000000}})
    routes["http://backend-b.example.invalid:9001/v1/models"] = {"data": []}
    assert scenario_capacity(iv, monkeypatch, tmp_path, routes=routes) >= 1
    out = capsys.readouterr().out
    assert "litellm-gateway/entry-d" in out and " not at backend  http://backend-b.example.invalid:9001/v1" in out

def test_ipv6_loopback_backend_is_skipped(iv, monkeypatch, tmp_path, capsys):
    """Offline regression checks."""
    routes = capacity_routes(131072)
    routes["http://127.0.0.1:4000/v1/model/info"]["data"][0]["litellm_params"]["api_base"] = "http://[::1]:8000/v1"
    routes["http://[::1]:8000/v1/models"] = {"data": [{"id": "model-x", "max_model_len": 131072}]}
    calls = []
    with_key(tmp_path)
    monkeypatch.setattr(iv, "http_json", fake_http(routes, calls))
    assert iv.litellm_ctx(iv.all_blocks(yaml.safe_load(GATEWAY))) == 0
    assert " loopback address " in capsys.readouterr().out
    assert not any("[::1]" in u for u, _ in calls)
    assert all(iv.is_loopback_base(b) for b in ("http://127.0.0.2:1/v1", "http://localhost:8000/v1", "http://[::1]:8000/v1"))
    cloud = "https://open" + "router." + "ai/api/v1"
    assert not any(iv.is_loopback_base(b) for b in ("http://backend-a.example.invalid:9002/v1", cloud))
