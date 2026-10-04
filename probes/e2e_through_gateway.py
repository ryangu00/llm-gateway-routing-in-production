#!/usr/bin/env python3
"""Check routing and a needle; requires the backend /tokenize endpoint."""
import argparse
import json
import os
import random
import sys
import time
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prefill_ladder as ladder


def read_key(path=None):
    key = os.environ.get('LITELLM_MASTER_KEY')
    if path:
        with open(os.path.expanduser(path)) as source:
            for line in source:
                line = line.strip().removeprefix('export ')
                if line.startswith('LITELLM_MASTER_KEY='):
                    key = line.split('=', 1)[1].strip().strip('"\'')
    if not key:
        raise ValueError('LITELLM_MASTER_KEY missing')
    return key


def probe(args):
    ladder.BASE, ladder.MODEL = args.backend.rstrip('/'), args.backend_model
    text, tokens, code, depth = ladder.build(args.tokens, random.Random())
    body = {'model': args.entry, 'messages': [{'role': 'user', 'content': text}],
            'max_tokens': 32, 'temperature': 0}
    request = urllib.request.Request(args.gateway.rstrip('/') + '/v1/chat/completions',
        json.dumps(body).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + read_key(args.credentials_file)})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            data, headers, status = json.load(response), response.headers, response.status
        answer = data['choices'][0]['message']['content'] or ''
        group = headers.get('x-litellm-model-group')
        fallbacks = int(headers.get('x-litellm-attempted-fallbacks', '0'))
        elapsed = time.monotonic() - started
        return {'ok': status == 200 and group == args.expect_group and fallbacks == args.expect_fallbacks
                      and code in answer and elapsed <= args.max_seconds,
                'seconds': round(elapsed, 1), 'backend_tokenizer_count': tokens, 'needle_depth': depth,
                'model_group': group, 'attempted_fallbacks': fallbacks, 'needle_ok': code in answer}
    except Exception as exc:
        return {'ok': False, 'seconds': round(time.monotonic() - started, 1), 'error': type(exc).__name__}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway', default='http://127.0.0.1:4000')
    parser.add_argument('--backend', default='http://127.0.0.1:9002')
    parser.add_argument('--backend-model', default='model-y')
    parser.add_argument('--entry', default='entry-a')
    parser.add_argument('--tokens', type=int, default=240000)
    parser.add_argument('--expect-group', default='second-hop')
    parser.add_argument('--expect-fallbacks', type=int, default=1)
    parser.add_argument('--max-seconds', type=float, default=300)
    parser.add_argument('--credentials-file')
    parser.add_argument('--output', default='e2e-gateway.json')
    args = parser.parse_args(argv)
    result = probe(args)
    with open(args.output, 'x') as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
