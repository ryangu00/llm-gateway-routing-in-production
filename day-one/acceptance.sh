#!/usr/bin/env bash
# Run once per consumer with that consumer's environment and fallback-count artifact.
set -euo pipefail
: "${GATEWAY_URL:=http://127.0.0.1:4000}"
: "${ENTRY_ALIAS:?Set the requested consumer alias}"
: "${LITELLM_MASTER_KEY:?Load the consumer credential}"
: "${CONSUMER_FALLBACK_FILE:?Counter from the same consumer call; do not fabricate it}"
python3 - <<'PY'
import json, os, urllib.request
from pathlib import Path
entry = os.environ['ENTRY_ALIAS']
body = {'model': entry, 'messages': [{'role': 'user', 'content': '17*23=? Reply with the number only.'}]}
request = urllib.request.Request(os.environ.get('GATEWAY_URL', 'http://127.0.0.1:4000').rstrip('/') + '/v1/chat/completions',
    json.dumps(body).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['LITELLM_MASTER_KEY']})
with urllib.request.urlopen(request, timeout=300) as response:
    assert response.status == 200, 'HTTP status'
    assert response.headers.get('x-litellm-model-group') == entry, 'serving group'
    assert response.headers.get('x-litellm-attempted-fallbacks', '0') == '0', 'gateway fallback'
    assert json.load(response)['choices'], 'completion missing'
assert Path(os.environ['CONSUMER_FALLBACK_FILE']).read_text().strip() == '0', 'consumer fallback'
print('PASS: HTTP 200; requested group; gateway fallback 0; consumer fallback 0')
PY
