#!/usr/bin/env python3
"""Disposable synthetic catalog demo. --mock-transport does not open sockets."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta
import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_services


def load_validator():
    spec = importlib.util.spec_from_file_location('demo_validator', ROOT / 'catalog/infra_validate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(mock_transport=False):
    validator = load_validator()
    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.catalog-demo-', dir=Path.cwd())))
        responses = {}

        def endpoint(model, limit, entries=None, port=9001):
            if mock_transport:
                base = f'http://127.0.0.1:{port}/v1'
            else:
                server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), fake_services.handler(model, limit, entries))
                stack.callback(server.server_close)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                stack.callback(thread.join)
                stack.callback(server.shutdown)
                base = f'http://127.0.0.1:{server.server_port}/v1'
            responses[base + '/models'] = {'data': [{'id': model, 'max_model_len': limit}]}
            responses[base + '/model/info'] = {'data': entries or []}
            return base

        first = endpoint('model-x', 250000)
        second = endpoint('model-y', 250000, port=9002)
        entries = [
            {'model_name': 'entry-a', 'litellm_params': {'model': 'openai/model-x', 'api_base': first},
             'model_info': {'max_input_tokens': 32768, 'cap_reason': 'Synthetic deliberate cap'}},
            {'model_name': 'second-hop', 'litellm_params': {'model': 'openai/model-y', 'api_base': second},
             'model_info': {'max_input_tokens': 250000}},
        ]
        gateway = endpoint('entry-a', 250000, entries, port=4000)
        config = directory / 'gateway.example.yaml'
        config.write_text(yaml.safe_dump({'model_list': entries}))
        watches = [config]
        for name in ('backend-a.sh', 'backend-b.sh'):
            destination = directory / name
            destination.write_bytes((ROOT / 'fixtures' / name).read_bytes())
            watches.append(destination)

        now = datetime.now().astimezone()
        doc = yaml.safe_load((ROOT / 'catalog/services.example.yaml').read_text())
        doc.update(revision=now.date().isoformat(), revision_at=now.isoformat(), verified_at=now.date().isoformat())
        for name, base, watch in (('backend-a', first, watches[1]), ('backend-b', second, watches[2]), ('gateway', gateway, config)):
            service = doc['services'][name]
            service.update(endpoint=base, ssot=str(watch), drift_watch=[str(watch)])
            if 'local_patches' in service:
                service['local_patches'] = [str(watch)]
        doc['services']['gateway']['intentional_caps']['entry-a']['review_by'] = (now.date() + timedelta(days=1)).isoformat()
        catalog = directory / 'services.yaml'
        catalog.write_text(yaml.safe_dump(doc, sort_keys=False))
        ledger = directory / 'ledger.jsonl'
        ledger.write_text(json.dumps({'kind': 'accept', 'adr': 'ADR-0001',
                                     'candidate_sha256': hashlib.sha256(config.read_bytes()).hexdigest()}) + '\n')
        validator.PATH, validator.LEDGER = str(catalog), str(ledger)
        # This synthetic credential must never resolve to a reader's real credential.
        stack.enter_context(patch.object(validator, 'resolve_key', return_value='synthetic-key'))
        stack.enter_context(patch.object(validator, 'run_remote', side_effect=AssertionError('unexpected remote command')))
        if mock_transport:
            stack.enter_context(patch.object(validator, 'http_json', side_effect=lambda url, key=None: responses[url]))
        print('SYNTHETIC catalog demo: ' + ('mapped HTTP responses; no sockets' if mock_transport else 'loopback HTTP servers'))

        def invoke(options):
            with patch.object(sys, 'argv', ['infra_validate.py', *options]):
                try:
                    validator.main()
                except SystemExit as result:
                    return result.code
            return 0

        code = invoke(['--drift', '--accept', 'all'])
        if code:
            return code
        return invoke(['--probe', '--drift', '--local-backends'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mock-transport', action='store_true')
    raise SystemExit(run(parser.parse_args().mock_transport))
