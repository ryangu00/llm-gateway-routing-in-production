"""Additional stdlib unittest cases. All measurements here are synthetic."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApplierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool = load('apply_test', 'gate/route_apply.py')
        self.pid = 'a' * 16
        self.old = b'model_list:\n- model_name: entry-a\n'
        self.new = b'model_list:\n- model_name: entry-b\n'
        (self.root / 'config.yaml').write_bytes(self.old)
        (self.root / 'staging').mkdir()
        self.permit = {'id': self.pid, 'adr': 'ADR-0001', 'created': time.time(), 'expires': time.time() + 1800,
                       'candidate_sha256': hashlib.sha256(self.new).hexdigest(), 'base_sha256': hashlib.sha256(self.old).hexdigest()}
        self.write()

    def write(self):
        (self.root / 'staging' / (self.pid + '.json')).write_text(json.dumps(self.permit))
        (self.root / 'staging' / (self.pid + '.candidate.yaml')).write_bytes(self.new)

    def apply(self, uid=0):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return self.tool.main(['route_apply.py', self.pid], root=self.root, effective_uid=lambda: uid)

    def refused(self, uid=0):
        before = (self.root / 'config.yaml').read_bytes()
        with self.assertRaises(SystemExit) as error: self.apply(uid)
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(before, (self.root / 'config.yaml').read_bytes())

    def test_apply_backup_readback_and_replay(self):
        self.assertEqual(self.apply(), 0)
        self.assertEqual((self.root / 'config.yaml').read_bytes(), self.new)
        self.assertEqual(next(self.root.glob('config.yaml.bak-*')).read_bytes(), self.old)
        self.assertTrue((self.root / 'consumed' / self.pid).exists())
        ledger = json.loads((self.root / 'applied.jsonl').read_text())
        self.assertEqual(ledger['applied_sha256'], self.permit['candidate_sha256'])
        self.refused()

    def test_noop_apply(self):
        self.new = self.old
        self.permit['candidate_sha256'] = hashlib.sha256(self.new).hexdigest()
        self.write()
        self.assertEqual(self.apply(), 0)

    def test_swapped_candidate(self):
        (self.root / 'staging' / (self.pid + '.candidate.yaml')).write_bytes(self.old)
        self.refused()

    def test_base_mismatch(self):
        self.permit['base_sha256'] = '0' * 64
        self.write()
        self.refused()

    def test_expired_permit(self):
        self.permit['expires'] = time.time() - 1
        self.write()
        self.refused()

    def test_nonroot_refused(self):
        self.refused(uid=1)

    def test_invalid_yaml_refused(self):
        self.new = b'model_list: []\n'
        self.permit['candidate_sha256'] = hashlib.sha256(self.new).hexdigest()
        self.write()
        self.refused()


class AdaptationTests(unittest.TestCase):
    def test_catalog_complete_mock_demo(self):
        demo = load('catalog_demo_test', 'fixtures/catalog_demo.py')
        output = io.StringIO()
        with patch('socket.socket', side_effect=AssertionError('unexpected socket')), contextlib.redirect_stdout(output):
            self.assertEqual(demo.run(mock_transport=True), 0)
        self.assertIn('OK: 4 services, 0 failures', output.getvalue())
        self.assertNotIn('DRIFT SKIP', output.getvalue())
        self.assertIn('registered intentional cap', output.getvalue())

    def test_all_fake_rewrites_every_backend_and_provider(self):
        import trigger_matrix as matrix
        cfg = yaml.safe_load((ROOT / 'examples/gateway.example.yaml').read_text())
        cfg['model_list'][2]['litellm_params'] = {'model': 'openrouter/vendor/model-z'}
        prompts = {'short': ('short', 17), 'input_150k': ('long', 150099), 'input_40k_second_hop_dead': ('long', 40100)}
        _, plan = matrix.make_plan(cfg, prompts, all_fake=True)
        for row in plan:
            self.assertEqual(set(row['injections']), {m['model_name'] for m in cfg['model_list']})
            endpoints = {mode: 'http://127.0.0.1:9001/v1' for mode in row['injections'].values()}
            injected = matrix.inject_config(cfg, row['injections'], endpoints)
            for model in injected['model_list']:
                self.assertEqual(model['litellm_params']['api_base'], 'http://127.0.0.1:9001/v1')
                self.assertTrue(model['litellm_params']['model'].startswith('openai/'))
                self.assertEqual(model['litellm_params']['api_key'], 'fake-local')

    def test_bounded_preserves_exception_not_timeout(self):
        import trigger_matrix as matrix
        def failing(): raise ValueError('synthetic request failure')
        result = matrix.bounded(failing, deadline=1)
        self.assertIn('ValueError', result['error'])
        self.assertIsNone(result['seconds'])
        self.assertFalse(result['stream']['hung'])

    def test_trigger_predecessors_come_from_candidate(self):
        emitter = load('emitter', 'harness/emit_trigger_test.py')
        cfg = yaml.safe_load((ROOT / 'examples/gateway.example.yaml').read_text())
        injections, position = emitter.make_injections(cfg, 'entry-a', 'cloud-tail', 'fallbacks', 17, True)
        self.assertEqual(position, 2)
        self.assertEqual(injections['entry-a'], 'dead-trigger')
        self.assertEqual(injections['second-hop'], 'dead-trigger')
        self.assertEqual(injections['cloud-tail'], 'ok')
        injections, position = emitter.make_injections(cfg, 'entry-a', 'second-hop', 'context_window_fallbacks', 40100, True)
        self.assertEqual(position, 1)
        self.assertEqual(injections['entry-a'], 'ok')
        with self.assertRaises(ValueError):
            emitter.make_injections(cfg, 'entry-a', 'second-hop', 'context_window_fallbacks', 17, True)

    def test_auth_reproduction_is_loopback_and_db_less_configuration(self):
        # Configuration check only: deliberately does not execute the reproduction.
        script = load('auth_example', 'probes/auth_401_as_500.py')
        cfg = yaml.safe_load(script.config('http://127.0.0.1:9001/v1'))
        self.assertEqual(cfg['model_list'][0]['litellm_params']['api_base'], 'http://127.0.0.1:9001/v1')
        self.assertNotIn('database_url', cfg['general_settings'])

    def test_english_needle_builder_checks_tokenizer(self):
        probe = load('ladder_test', 'probes/prefill_ladder.py')
        with patch.object(probe, 'ntok', side_effect=lambda text: len(text.split())) as counter:
            text, count, needle, depth = probe.build(40000, random.Random(1))
        self.assertTrue(text.isascii())
        self.assertIn(needle, text)
        self.assertLessEqual(count, 40000)
        self.assertTrue(0.1 <= depth <= 0.9)
        self.assertGreaterEqual(counter.call_count, 2)

    def test_low_memory_abort_stops_ladder(self):
        probe = load('ladder_abort_test', 'probes/prefill_ladder.py')
        probe.HOSTS = ('synthetic-monitor',)
        with tempfile.TemporaryDirectory() as directory:
            probe.RES = str(Path(directory) / 'result.json')
            with patch.object(probe, 'build', return_value=('prompt', 40000, 'needle', 0.5)), \
                 patch.object(probe, 'mem_avail', return_value={'synthetic-monitor': 0}), \
                 patch.object(probe, 'post', side_effect=OSError('closed')), \
                 patch.object(probe, 'healthy', return_value=True), contextlib.redirect_stdout(io.StringIO()):
                result = {'steps': []}
                self.assertFalse(probe.run_step(40000, random.Random(), result))
                self.assertIn('low memory', result['steps'][0]['error'])

    def test_passthrough_idempotence_and_anchor(self):
        recipe = load('passthrough_test', 'recipes/passthrough.py')
        text = (ROOT / 'examples/gateway.example.yaml').read_text()
        updated, changed = recipe.insert(text)
        self.assertTrue(changed)
        self.assertEqual(recipe.insert(updated), (updated, False))
        self.assertEqual(len(yaml.safe_load(text)['model_list']), len(yaml.safe_load(updated)['model_list']))
        with self.assertRaises(ValueError): recipe.insert(text.replace(recipe.ANCHOR, ''))

    def test_passthrough_immediate_rerun_preserves_candidate_and_backups(self):
        recipe = load('passthrough_repeat_test', 'recipes/passthrough.py')
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate.yaml'
            original = (ROOT / 'examples/gateway.example.yaml').read_text()
            candidate.write_text(original)
            args = argparse.Namespace(config=str(candidate), apply=False)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(recipe.run(args), 0)
                first = candidate.read_bytes()
                self.assertEqual(recipe.run(args), 0)
            self.assertEqual(candidate.read_bytes(), first)
            self.assertEqual(len(list(candidate.parent.glob('candidate.yaml.bak-*'))), 2)
            self.assertIn(original, [p.read_text() for p in candidate.parent.glob('candidate.yaml.bak-*')])

    def test_passthrough_changed_model_count_refuses_success(self):
        recipe = load('passthrough_count_test', 'recipes/passthrough.py')
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate.yaml'
            candidate.write_text((ROOT / 'examples/gateway.example.yaml').read_text())
            args = argparse.Namespace(config=str(candidate), apply=True, service_label='synthetic-service')
            request = Mock(side_effect=[({'data': [{'id': 'entry-a'}]}, 200, 0), ({}, 200, 0), ({'data': []}, 200, 0)])
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'model count changed'):
                recipe.run(args, request, Mock())
            self.assertEqual(request.call_count, 3)

    def test_passthrough_restart_probe_model_count_and_rollback(self):
        recipe = load('passthrough_run_test', 'recipes/passthrough.py')
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate.yaml'
            candidate.write_text((ROOT / 'examples/gateway.example.yaml').read_text())
            args = argparse.Namespace(config=str(candidate), apply=True, service_label='synthetic-service', model='decision-model')
            count = {'data': [{'id': 'entry-a'}]}
            responses = [(count, 200, 0), ({}, 200, 0), (count, 200, 0), ({'id': 'synthetic', 'model': 'decision-model', 'usage': {}}, 200, 0)]
            request, restart = Mock(side_effect=responses), Mock()
            output = io.StringIO()
            with contextlib.redirect_stdout(output): self.assertEqual(recipe.run(args, request, restart), 0)
            restart.assert_called_once()
            self.assertEqual(request.call_args_list[-1].args[0], '/decide/decisions')
            self.assertIn('rollback: cp ', output.getvalue())
            self.assertNotIn('Authorization', output.getvalue())

    def test_synthetic_model_and_tokenizer_responses(self):
        fixture = load('fixture_test', 'fixtures/fake_services.py')
        status, _, body = fixture.reply('GET', '/v1/models', {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['data'][0]['id'], 'model-y')
        _, _, body = fixture.reply('POST', '/tokenize', {'prompt': 'word word'})
        self.assertEqual(json.loads(body)['count'], 2)
        self.assertTrue(json.loads(body)['synthetic'])


class ProbeAcceptanceTests(unittest.TestCase):
    @staticmethod
    def response(group='second-hop', fallback='1', answer='needle', status=200):
        response = io.BytesIO(json.dumps({'choices': [{'message': {'content': answer}}]}).encode())
        response.status = status
        response.headers = {'x-litellm-model-group': group}
        if fallback is not None:
            response.headers['x-litellm-attempted-fallbacks'] = fallback
        return response

    def e2e(self, **response_args):
        module = load('e2e_test', 'probes/e2e_through_gateway.py')
        args = argparse.Namespace(backend='http://127.0.0.1:9002', backend_model='model-y', tokens=240000,
            entry='entry-a', gateway='http://127.0.0.1:4000', credentials_file=None,
            expect_group='second-hop', expect_fallbacks=1, max_seconds=300)
        with patch.object(module.ladder, 'build', return_value=('prompt', 240000, 'needle', 0.5)), \
             patch.object(module, 'read_key', return_value='synthetic-key'), \
             patch.object(module.urllib.request, 'urlopen', return_value=self.response(**response_args)):
            return module.probe(args)

    def test_e2e_success_requires_group_fallback_and_needle(self):
        self.assertTrue(self.e2e()['ok'])
        for condition in ({'group': 'wrong-group'}, {'fallback': '0'}, {'answer': 'wrong answer'}, {'status': 500}):
            with self.subTest(condition=condition):
                self.assertFalse(self.e2e(**condition)['ok'])

    def test_e2e_deadline_is_part_of_acceptance(self):
        with patch('time.monotonic', side_effect=[0, 301]):
            self.assertFalse(self.e2e()['ok'])

    def test_day_one_adapter_checks_every_assertion(self):
        script = (ROOT / 'day-one/acceptance.sh').read_text()
        code = script.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            counter = Path(directory) / 'fallbacks.txt'
            variables = {'ENTRY_ALIAS': 'entry-a', 'LITELLM_MASTER_KEY': 'synthetic-key',
                         'CONSUMER_FALLBACK_FILE': str(counter), 'GATEWAY_URL': 'http://127.0.0.1:4000'}
            for condition, count, success in [({}, '0', True), ({'fallback': None}, '0', True),
                    ({'group': 'wrong'}, '0', False), ({'fallback': '1'}, '0', False),
                    ({'status': 500}, '0', False), ({}, '1', False)]:
                counter.write_text(count)
                values = dict(group='entry-a', fallback='0')
                values.update(condition)
                with self.subTest(condition=condition, counter=count), patch.dict(os.environ, variables), \
                     patch('urllib.request.urlopen', return_value=self.response(**values)), contextlib.redirect_stdout(io.StringIO()):
                    if success:
                        exec(compile(code, 'day-one/acceptance.sh', 'exec'), {})
                    else:
                        with self.assertRaises(AssertionError):
                            exec(compile(code, 'day-one/acceptance.sh', 'exec'), {})


class ShellAdapterTests(unittest.TestCase):
    def test_all_shipped_shell_scripts_parse(self):
        for directory in ('catalog', 'gate', 'day-one', 'recipes', 'fixtures'):
            for script in (ROOT / directory).glob('*.sh'):
                with self.subTest(script=script.name):
                    subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True, check=True)

    def test_installer_check_does_not_deploy(self):
        result = subprocess.run(['bash', str(ROOT / 'gate/install_applier.sh'), '--check'],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(),
            'CHECK: requires macOS, sudo, GW_DIR, GATEWAY_PYTHON, SERVICE_LABEL; no files changed')

    def test_snapshot_diff_with_synthetic_job_and_listener_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            binary = scratch / 'bin'
            binary.mkdir()
            jobs = scratch / 'jobs.txt'
            jobs.write_text('PID Status Label\n123 0 example.old-job\n')
            commands = {
                'launchctl': '#!/bin/sh\n[ "$1" = list ] || exit 2\ncat "$SYNTHETIC_JOBS"\n',
                'lsof': '#!/bin/sh\nprintf "COMMAND PID USER FD TYPE DEVICE SIZE NODE NAME STATE\\nfixture 123 user 1 IPv4 x 0 TCP 127.0.0.1:9002 LISTEN\\n"\n',
            }
            for name, content in commands.items():
                executable = binary / name
                executable.write_text(content)
                executable.chmod(0o755)
            env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
                       SYNTHETIC_JOBS=str(jobs), REBOOT_STATE_DIR=str(scratch / 'state'))
            script = str(ROOT / 'catalog/reboot_diff.sh')
            subprocess.run(['bash', script, 'snapshot'], env=env, capture_output=True, text=True, check=True)
            jobs.write_text('PID Status Label\n123 0 example.new-job\n')
            result = subprocess.run(['bash', script, 'diff'], env=env, capture_output=True, text=True, check=True)
            self.assertIn('Missing jobs\nexample.old-job', result.stdout)
            self.assertIn('Added jobs\nexample.new-job', result.stdout)


if __name__ == '__main__':
    unittest.main()
