#!/usr/bin/env python3
"""Idempotent non-chat route recipe, with a non-networked candidate-only default."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
import urllib.request
import yaml

ANCHOR = '  allow_requests_on_db_unavailable: true'
ROUTES = [('/decide/decisions', 'https://aggregator.example/api/alpha/decisions'),
          ('/decide/sdk', 'https://aggregator.example/api/v1/sdk-route')]


def insert(text):
    doc = yaml.safe_load(text)
    settings = doc.get('general_settings') or {}
    if 'pass_through_endpoints' in settings:
        return text, False
    if ANCHOR not in text.splitlines():
        raise ValueError('anchor line missing in general_settings')
    endpoints = [{'path': path, 'target': target, 'methods': ['POST'], 'headers': {
        'Authorization': 'Bearer os.environ/AGG_API_KEY', 'content-type': 'application/json',
        'accept': 'application/json'}} for path, target in ROUTES]
    settings['pass_through_request_timeout'] = 60
    settings['pass_through_endpoints'] = endpoints
    doc['general_settings'] = settings
    rendered = yaml.safe_dump(doc, sort_keys=False)
    checked = yaml.safe_load(rendered)
    if len(checked['model_list']) != len(doc['model_list']):
        raise ValueError('model count changed')
    return rendered, True


def http_json(base, path, body=None):
    key = os.environ.get('LITELLM_MASTER_KEY')
    if not key: raise ValueError('LITELLM_MASTER_KEY missing')
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    request = urllib.request.Request(base + path, None if body is None else json.dumps(body).encode(), headers)
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=60) as response:
        data, status = json.load(response), response.status
    return data, status, time.monotonic() - started


def run(args, request=None, restart=None):
    request = request or (lambda path, body=None: http_json(args.gateway.rstrip('/'), path, body))
    restart = restart or (lambda: subprocess.run(['launchctl', 'kickstart', '-k', args.service_label], check=True))
    candidate = Path(args.config)
    raw = candidate.read_text()
    rendered, changed = insert(raw)
    backup = candidate.with_name(candidate.name + '.bak-' + time.strftime('%Y%m%dT%H%M%S') + '-' + str(time.time_ns()))
    with backup.open('x') as output: output.write(raw)
    print(f'backup: {backup}')
    rollback = f'cp {shlex.quote(str(backup))} {shlex.quote(str(candidate))}'
    if args.apply: rollback += ' && launchctl kickstart -k ' + shlex.quote(args.service_label)
    print('rollback: ' + rollback)
    if not changed:
        print('already present')
        return 0
    before = len(request('/v1/models')[0]['data']) if args.apply else None
    candidate.write_text(rendered)
    parsed = yaml.safe_load(candidate.read_text())
    print('yaml ok; paths:', [e['path'] for e in parsed['general_settings']['pass_through_endpoints']],
          '| models', len(parsed['model_list']))
    if not args.apply:
        print('candidate only: restart and HTTP probes not run')
        return 0
    restart()
    for _ in range(40):
        try:
            if request('/health/liveliness')[1] == 200: break
        except OSError: pass
        time.sleep(1)
    else: raise RuntimeError('liveliness did not recover')
    after = len(request('/v1/models')[0]['data'])
    if before != after: raise RuntimeError('model count changed after restart')
    payload = {'model': args.model, 'state': 'Help! My payouts have been failing for 3 days.',
               'questions': {'is_urgent': {'type': 'noul', 'instructions': 'Does this message convey urgency?'}}}
    data, status, elapsed = request('/decide/decisions', payload)
    print(json.dumps({'probe': {k: data.get(k) for k in ('id', 'model', 'usage')}, 'http': status, 'seconds': elapsed}))
    if status != 200: raise RuntimeError('pass-through probe failed')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--gateway', default='http://127.0.0.1:4000')
    parser.add_argument('--service-label')
    parser.add_argument('--model', default='decision-model')
    args = parser.parse_args(argv)
    if args.apply and not args.service_label: parser.error('--apply requires --service-label')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
