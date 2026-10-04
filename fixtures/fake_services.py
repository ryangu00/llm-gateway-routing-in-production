#!/usr/bin/env python3
"""Synthetic OpenAI-shaped service; whitespace counts are NOT real tokenization."""
import argparse
import http.server
import json
import re


def reply(method, path, body, model='model-y', limit=256000, entries=None, group=None):
    headers = {'Content-Type': 'application/json'}
    if group:
        headers.update({'x-litellm-model-group': group, 'x-litellm-attempted-fallbacks': '1'})
    if path.endswith('/health') or path.endswith('/health/liveliness'):
        return 200, headers, b'{"status":"ok","synthetic":true}'
    if path.endswith('/models'):
        return 200, headers, json.dumps({'data': [{'id': model, 'max_model_len': limit}]}).encode()
    if path.endswith('/model/info'):
        return 200, headers, json.dumps({'data': entries or []}).encode()
    if path.endswith('/tokenize'):
        return 200, headers, json.dumps({'count': len(body['prompt'].split()), 'synthetic': True}).encode()
    if method == 'POST' and path.endswith('/chat/completions'):
        text = body['messages'][0]['content']
        needle = re.search(r'The code for this document is ([\w-]+)', text)
        answer = needle.group(1) if needle else '2' if '1+1' in text else '391'
        common = {'id': 'synthetic', 'model': model, 'created': 0}
        usage = {'prompt_tokens': len(text.split()), 'completion_tokens': 1, 'total_tokens': len(text.split()) + 1}
        if body.get('stream'):
            headers['Content-Type'] = 'text/event-stream'
            chunk = dict(common, object='chat.completion.chunk', usage=usage,
                choices=[{'index': 0, 'delta': {'content': answer}, 'finish_reason': 'stop'}])
            return 200, headers, ('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode()
        data = dict(common, object='chat.completion', usage=usage,
            choices=[{'index': 0, 'message': {'role': 'assistant', 'content': answer}, 'finish_reason': 'stop'}])
        return 200, headers, json.dumps(data).encode()
    return 404, headers, b'{"error":"unknown synthetic route"}'


def handler(model, limit, entries=None, group=None):
    class Handler(http.server.BaseHTTPRequestHandler):
        def dispatch(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            status, headers, data = reply(self.command, self.path, json.loads(raw) if raw else {}, model, limit, entries, group)
            self.send_response(status)
            for name, value in headers.items(): self.send_header(name, value)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = dispatch
        do_POST = dispatch

        def log_message(self, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9002)
    parser.add_argument('--model', default='model-y')
    parser.add_argument('--limit', type=int, default=256000)
    parser.add_argument('--group')
    args = parser.parse_args()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), handler(args.model, args.limit, group=args.group))
    print(f'Synthetic service: http://127.0.0.1:{server.server_port}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__':
    main()
