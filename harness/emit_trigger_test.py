#!/usr/bin/env python3
"""Emit candidate-bound per-target trigger evidence using temporary gateways."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import socket
import sys
import tempfile
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parent))
import trigger_matrix as tm


def make_injections(cfg, entry, target, section, tokens, all_fake):
    models = {m['model_name']: m for m in cfg['model_list']}
    mappings = dict(item for row in cfg['router_settings'][section] for item in row.items())
    chain = [entry] + mappings[entry]
    position = chain.index(target)
    if position == 0:
        raise ValueError('target must be a fallback')
    injections = {name: 'ok' for name in models} if all_fake else {}
    for name in chain[:position]:
        cap = models[name].get('model_info', {}).get('max_input_tokens')
        if section == 'context_window_fallbacks' and cap is not None and tokens > cap:
            continue
        injections[name] = 'dead-trigger'
    if section == 'context_window_fallbacks' and tokens <= models[entry]['model_info']['max_input_tokens']:
        raise ValueError('window input must exceed entry cap')
    return injections, position


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--entry', action='append', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--section', choices=['fallbacks', 'context_window_fallbacks'], default='fallbacks')
    parser.add_argument('--tokens', type=int, default=40000)
    parser.add_argument('--all-fake', action='store_true')
    parser.add_argument('--credentials-file')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    raw = Path(args.config).read_bytes()
    cfg, sha = yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()
    text, tokens = tm.SHORT, tm.count_prompt(tm.SHORT)
    if args.section == 'context_window_fallbacks':
        count = args.tokens // 3
        for _ in range(12):
            text = tm.words(count, random.Random())
            tokens = tm.count_prompt(text)
            if args.tokens <= tokens < args.tokens + 1000: break
            count = max(1, int(count * (args.tokens + 100) / max(tokens, 1)))
        else: raise ValueError('unable to size window input')
    rows = []
    rundir = tempfile.mkdtemp(prefix='trigger-', dir=os.getcwd())
    with ExitStack() as stack:
        server, ok_base = tm.fake_backend('ok')
        stack.callback(tm.close_fake, server)
        dead = stack.enter_context(socket.socket())
        dead.bind(('127.0.0.1', 0))
        endpoints = {'ok': ok_base, 'dead-trigger': f'http://127.0.0.1:{dead.getsockname()[1]}/v1'}
        for entry in args.entry:
            injections, position = make_injections(cfg, entry, args.target, args.section, tokens, args.all_fake)
            bases = {name: endpoints[mode] for name, mode in injections.items()}
            gateway = tm.Gateway(rundir, entry, tm.inject_config(cfg, injections, endpoints),
                                 tm.cred_env(args.credentials_file, args.all_fake), bases)
            try:
                row = tm.bounded(gateway.chat, entry, text, 900, deadline=930)
                row.update(entry=entry, litellm_tokens=tokens)
                row['ok'] = row['http'] == 200 and row['group'] == args.target and row['attempted_fallbacks'] == str(position)
                if args.section == 'context_window_fallbacks':
                    control = gateway.chat(entry, tm.SHORT, 300)
                    row['short_control'] = control
                    row['ok'] = row['ok'] and control['http'] == 200 and control['group'] == entry and control['attempted_fallbacks'] in (None, '0')
                rows.append(row)
            finally:
                gateway.close()
    hit = [row for row in rows if row['ok']]
    result = {'fallback': args.target, 'triggered': len(hit),
              'covers': [f'{args.section}:{row["entry"]}' for row in hit], 'candidate_sha256': sha,
              'method': 'Temporary gateway, dead predecessors, explicit backend isolation',
              'all_fake': args.all_fake, 'detail': rows}
    with open(args.output, 'x') as output: json.dump(result, output, indent=2)
    passed = len(hit) == len(rows)
    if passed: shutil.rmtree(rundir)
    else: print(f'Diagnostics retained at {rundir}')
    print('PASS' if passed else 'FAIL')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
