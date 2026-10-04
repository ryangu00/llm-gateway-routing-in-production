"""Offline regression checks."""
import ast
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

import yaml
import trigger_matrix as tm

ROOT = Path(__file__).resolve().parent


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import types, sys
        fake = types.ModuleType("litellm")
        fake.token_counter = lambda *, messages: len(messages[0]["content"].split())
        cls.counter_patch = patch.dict(sys.modules, {"litellm": fake})
        cls.counter_patch.start()
        cls.addClassCleanup(cls.counter_patch.stop)
        cls.raw = (ROOT.parent / "examples/gateway.example.yaml").read_bytes()
        cls.cfg = yaml.safe_load(cls.raw)
        cls.prompts = tm.make_prompts()

    def plan(self, cfg=None):
        return tm.make_plan(cfg or self.cfg, self.prompts)[1]

    def test_current_topology(self):
        rows = self.plan()
        self.assertEqual(tm.ENTRIES, ("entry-a", "entry-b", "entry-c"))
        self.assertEqual(len(rows), 21)
        self.assertEqual({r["scenario"] for r in rows}, set(tm.SCENARIOS))
        expected = {"input_150k": ("second-hop", 1), "input_40k_second_hop_dead": ("isolation-group", 2),
                    "first_hop_http500": ("second-hop", 1), "first_hop_hang": ("second-hop", 1),
                    "first_hop_hang_stream": ("second-hop", 1)}
        for r in rows:
            want = expected.get(r["scenario"])
            if r["scenario"] == "both_local_dead":
                want = ("cloud-tail-b" if r["entry"] == "entry-c" else "cloud-tail", 2)
            if r["scenario"] == "first_hop_stall_stream": want = (r["entry"], 0)
            self.assertEqual((r["expect_group"], r["expect_fallbacks"]), want)

    def test_route_gate_scenario_contract(self):
        tree = ast.parse((ROOT.parent / "gate/route_change.py").read_text())
        gate = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("RES_SCENARIOS", "HANG_BUDGET")}
        self.assertEqual(tm.SCENARIOS, gate["RES_SCENARIOS"])
        self.assertTrue(set(gate["HANG_BUDGET"]) <= set(tm.TIMEOUTS))

    def test_prompt_counts_use_router_counter(self):
        from litellm import token_counter
        for name, (text, count) in self.prompts.items():
            self.assertEqual(count, token_counter(messages=[{"role": "user", "content": text}]))
            if name != "short":
                target = 150000 if name == "input_150k" else 40000
                self.assertTrue(target <= count < target + 1000)

    def test_chain_order_and_count_come_from_config(self):
        cfg = copy.deepcopy(self.cfg)
        for item in cfg["router_settings"]["fallbacks"]:
            for entry in tm.ENTRIES:
                if entry in item: item[entry] = ["cloud-tail-b", "second-hop", "cloud-tail"]
        for r in self.plan(cfg):
            if r["scenario"] in ("both_local_dead", "first_hop_http500", "first_hop_hang", "first_hop_hang_stream"):
                self.assertEqual((r["expect_group"], r["expect_fallbacks"]), ("cloud-tail-b", 1))
        for item in cfg["router_settings"]["context_window_fallbacks"]:
            for entry in tm.ENTRIES:
                if entry in item: item[entry] = ["isolation-group", "second-hop", "cloud-tail-b"]
        for r in self.plan(cfg):
            if r["scenario"].startswith("input_"):
                self.assertEqual((r["expect_group"], r["expect_fallbacks"]), ("isolation-group", 1))

    def test_capacity_boundary_and_backend_identity(self):
        cfg = copy.deepcopy(self.cfg)
        models = {m["model_name"]: m for m in cfg["model_list"]}
        tokens = self.prompts["input_150k"][1]
        for cap, expected in ((tokens, ("second-hop", 1)), (tokens - 1, ("isolation-group", 2))):
            models["second-hop"]["model_info"]["max_input_tokens"] = cap
            for r in self.plan(cfg):
                if r["scenario"] == "input_150k":
                    self.assertEqual((r["expect_group"], r["expect_fallbacks"]), expected)

        models["second-hop"]["litellm_params"]["api_base"] = tm.SECOND_HOP[1]
        for r in self.plan(cfg):
            if r["scenario"] == "input_40k_second_hop_dead":
                self.assertEqual(r["injections"]["second-hop"], "dead-second-hop")
                self.assertEqual(r["expect_group"], "isolation-group")

    def test_all_scenarios_isolate_1m_and_long_cloud(self):
        original = copy.deepcopy(self.cfg)
        for row in self.plan():
            injections = row["injections"]
            self.assertEqual(injections["isolation-group"], "ok")
            endpoints = {mode: f"http://127.0.0.1:{49152+i}/v1" for i, mode in enumerate(sorted(set(injections.values())))}
            injected = tm.inject_config(self.cfg, injections, endpoints)
            for m in injected["model_list"]:
                name, params = m["model_name"], m["litellm_params"]
                if name in injections:
                    self.assertEqual(params["api_base"], endpoints[injections[name]])
                if injections.get(name) == "ok":
                    self.assertTrue(params["model"].startswith("openai/"))
                    self.assertEqual(params["api_key"], "fake-local")
            if row["scenario"].startswith("input_"):
                for m in self.cfg["model_list"]:
                    if m["litellm_params"].get("api_base") not in (tm.FIRST_HOP, *tm.SECOND_HOP):
                        self.assertEqual(injections[m["model_name"]], "ok")
            else:
                self.assertNotIn("cloud-tail", injections)
        self.assertEqual(self.cfg, original)

    def test_invalid_or_ambiguous_config_fails(self):
        for bad_cap in (False, 0, -1, "250000", 250000.0):
            cfg = copy.deepcopy(self.cfg)
            cfg["model_list"][0]["model_info"]["max_input_tokens"] = bad_cap
            with self.assertRaises(ValueError): self.plan(cfg)
        cfg = copy.deepcopy(self.cfg)
        cfg["router_settings"]["fallbacks"][0]["entry-a"] = ["absent"]
        with self.assertRaises(ValueError): self.plan(cfg)
        cfg = copy.deepcopy(self.cfg)
        cfg["model_list"][0]["model_info"].pop("max_input_tokens")
        with self.assertRaisesRegex(ValueError, " missing  max_input_tokens"): self.plan(cfg)
        cfg = copy.deepcopy(self.cfg)
        cfg["router_settings"]["enable_pre_call_checks"] = False
        with self.assertRaises(ValueError): self.plan(cfg)

    def test_plan_only_has_no_runtime_side_effects_and_hashes_exact_bytes(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            cfg = Path(td) / "candidate.yaml"
            raw = self.raw + b"\n# exact-byte hash regression\n"
            cfg.write_bytes(raw)
            out = io.StringIO()
            with contextlib.ExitStack() as stack:
                for name in ("cred_env", "free_port", "fake_backend", "Gateway"):
                    stack.enter_context(patch.object(tm, name, side_effect=AssertionError(name)))
                stack.enter_context(patch.object(tm.tempfile, "mkdtemp", side_effect=AssertionError("tempdir")))
                stack.enter_context(patch.object(tm.subprocess, "Popen", side_effect=AssertionError("process")))
                stack.enter_context(patch.object(tm.socket, "socket", side_effect=AssertionError("socket")))
                with contextlib.redirect_stdout(out):
                    self.assertEqual(tm.main(["--config", str(cfg), "--plan-only"]), 0)
            data = json.loads(out.getvalue())
            self.assertTrue(data["plan_only"])
            self.assertNotIn("pass", data)
            self.assertNotIn("results", data)
            self.assertEqual(data["config_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(len(data["plan"]), 21)

    def test_only_and_skip_hang(self):
        for flags, count in ((["--skip-hang"], 12), (["--only", "first_hop_hang"], 3), (["--only", "first_hop_keepalive"], 3)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(tm.main(["--config", str(ROOT.parent / "examples/gateway.example.yaml"), "--plan-only", *flags]), 0)
            self.assertEqual(len(json.loads(out.getvalue())["plan"]), count)

    def test_fake_ok_json_and_sse(self):
        # Exercise the HTTP handler with in-memory transport; no socket permission needed.
        from types import SimpleNamespace
        for stream in (False, True):
            body = json.dumps({"model": "fake", "messages": [], "stream": stream}).encode()
            incoming = io.BytesIO(b"POST /v1/chat/completions HTTP/1.0\r\nContent-Length: " +
                                  str(len(body)).encode() + b"\r\n\r\n" + body)
            outgoing = io.BytesIO()
            connection = SimpleNamespace(makefile=lambda *args: incoming, sendall=outgoing.write)
            handler = type("MemoryOK", (tm.Fake,), {"mode": "ok"})
            handler(connection, ("127.0.0.1", 0), SimpleNamespace())
            head, data = outgoing.getvalue().split(b"\r\n\r\n", 1)
            self.assertIn(b"200 OK", head)
            if stream:
                self.assertIn(b'"content": "ok"', data)
                self.assertTrue(data.endswith(b"data: [DONE]\n\n"))
            else:
                self.assertEqual(json.loads(data)["choices"][0]["message"]["content"], "ok")

    def test_model_info_checks_the_injected_group_not_just_any_base(self):
        gateway = tm.Gateway.__new__(tm.Gateway)
        gateway.port = 49152
        gateway.p = unittest.mock.Mock(pid=123)
        gateway.p.poll.return_value = None
        gateway.get = lambda _: {"data": [{"model_name": "wrong-group", "litellm_params": {"api_base": "fake"}}]}
        with patch.object(tm.urllib.request, "urlopen", return_value=unittest.mock.Mock(status=200)), patch.object(
            tm.subprocess, "run", return_value=unittest.mock.Mock(stdout="123\n")
        ):
            with self.assertRaisesRegex(RuntimeError, "isolation-group"):
                gateway._verify("test", {"isolation-group": "fake"})

    def test_runtime_result_contract_exclusive_output_and_cleanup(self):

        with tempfile.TemporaryDirectory(dir=ROOT) as td, contextlib.ExitStack() as stack:
            mkdtemp = tm.tempfile.mkdtemp
            stack.enter_context(patch.object(tm.tempfile, "mkdtemp", side_effect=lambda **kw: mkdtemp(**dict(kw, dir=td))))
            stack.enter_context(patch.object(tm, "OUT_DIR", td))
            stack.enter_context(patch.object(tm, "cred_env", return_value={}))
            stack.enter_context(patch.object(tm.signal, "signal"))
            stack.enter_context(patch.object(tm.time, "strftime", return_value="fixed"))
            stack.enter_context(patch.object(tm.random, "randrange", return_value=42))
            backend = unittest.mock.Mock()
            stack.enter_context(patch.object(tm, "fake_backend", return_value=(backend, "http://127.0.0.1:49152/v1")))
            gateway = unittest.mock.Mock()
            gateway.chat.return_value = {"http": 200, "seconds": 0.1, "group": "second-hop", "attempted_fallbacks": "1"}
            gateway_factory = stack.enter_context(patch.object(tm, "Gateway", return_value=gateway))
            argv = ["--config", str(ROOT.parent / "examples/gateway.example.yaml"), "--only", "first_hop_http500"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tm.main(argv), 0)
                path, = Path(td).glob("trigger-matrix-*.json")
                before = path.read_bytes()
                with self.assertRaises(FileExistsError): tm.main(argv)
                self.assertEqual(path.read_bytes(), before)
            data = json.loads(before)
            self.assertEqual(data["config_sha256"], hashlib.sha256(self.raw).hexdigest())
            self.assertTrue(data["pass"])
            self.assertEqual(data["n"], 3)
            for r in data["results"]:
                self.assertTrue({"scenario", "entry", "ok", "seconds", "group", "attempted_fallbacks", "expect_group"} <= r.keys())
            self.assertEqual(gateway.close.call_count, 2)

            self.assertEqual(backend.server_close.call_count, 4)
            for call in gateway_factory.call_args_list:
                _, _, injected, _, bases = call.args
                self.assertIn("isolation-group", bases)
                m1 = next(m for m in injected["model_list"] if m["model_name"] == "isolation-group")
                self.assertEqual(m1["litellm_params"]["api_base"], bases["isolation-group"])


if __name__ == "__main__":
    unittest.main()
