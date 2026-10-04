#!/usr/bin/env python3
"""Reader reproduction candidate: NOT re-run by us in a clean environment.

Run in a pre-provisioned LiteLLM 1.99.0 environment without prisma or a database.
Only loopback is used. The script observes responses; it does not fabricate 500.
"""
import argparse
import http.server
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request


class Backend(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        data = json.dumps({'id': 'fake', 'object': 'chat.completion', 'created': 0,
            'model': 'model-x', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'},
                                          'finish_reason': 'stop'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def config(base):
    return ('model_list:\n- model_name: entry-a\n  litellm_params:\n'
            f'    model: openai/model-x\n    api_base: {base}\n    api_key: local\n'
            'general_settings:\n  master_key: os.environ/LITELLM_MASTER_KEY\n'
            '  disable_spend_logs: true\n  disable_adding_master_key_hash_to_db: true\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--litellm', default='litellm')
    args = parser.parse_args()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    work = Path(tempfile.mkdtemp(prefix='auth-repro-', dir=Path.cwd()))
    candidate = work / 'config.yaml'
    candidate.write_text(config(f'http://127.0.0.1:{server.server_port}/v1'))
    env = dict(os.environ, LITELLM_MASTER_KEY='fixture-key', LITELLM_LOCAL_MODEL_COST_MAP='True')
    env.pop('DATABASE_URL', None)
    env.pop('DIRECT_URL', None)
    with (work / 'gateway.log').open('w') as log:
        child = subprocess.Popen([args.litellm, '--config', str(candidate), '--host', '127.0.0.1', '--port', str(port)],
            env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            url = f'http://127.0.0.1:{port}'
            for _ in range(180):
                if child.poll() is not None: raise RuntimeError('gateway exited; inspect the local log')
                try:
                    with urllib.request.urlopen(url + '/health/liveliness', timeout=2): break
                except OSError: time.sleep(1)
            else: raise RuntimeError('gateway did not become ready')
            for label, key in [('missing', None), ('valid', 'fixture-key'), ('wrong', 'wrong-fixture-key')]:
                headers = {'Content-Type': 'application/json'}
                if key: headers['Authorization'] = 'Bearer ' + key
                body = json.dumps({'model': 'entry-a', 'messages': [{'role': 'user', 'content': 'hi'}]}).encode()
                request = urllib.request.Request(url + '/v1/chat/completions', body, headers)
                try:
                    with urllib.request.urlopen(request, timeout=30) as response: status = response.status
                except urllib.error.HTTPError as error: status = error.code
                print(json.dumps({'credential_case': label, 'http': status}))
        finally:
            try: os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            server.shutdown()
            server.server_close()
    print(f'Local diagnostics retained at {work.name}/gateway.log')


if __name__ == '__main__':
    main()
