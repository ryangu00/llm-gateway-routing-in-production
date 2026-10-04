// Transport mocks exercise probe wiring, not real SDK or undici timeout behavior.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../probes/body_timeout_probe.mjs', import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '')
  .replace(/^const require = .*;\n/m, '')
  .replace(/^const OpenAI = .*;\n/m, '');

async function probe(mode, failure = false) {
  const calls = { flushed: 0, writes: [], results: [] };
  let listener;
  let pending;
  const response = {
    writeHead(status, headers) { calls.status = status; calls.headers = headers; },
    flushHeaders() { calls.flushed++; },
    write(text) { calls.writes.push(text); },
    end() { calls.ended = true; },
  };
  const server = {
    address() { return { port: 9002 }; },
    listen(port, host, callback) { calls.host = host; pending = callback(); },
    close() { calls.closed = true; },
  };
  class OpenAI {
    constructor(options) {
      calls.client = options;
      this.chat = { completions: { create: async (body, requestOptions) => {
        calls.body = body;
        calls.requestOptions = requestOptions;
        listener({ resume() {}, on(event, callback) { assert.equal(event, 'end'); callback(); } }, response);
        await options.fetch(options.baseURL, {});
        if (failure) {
          const error = new Error('terminated');
          error.cause = { code: 'UND_ERR_BODY_TIMEOUT' };
          throw error;
        }
        return (async function* () { yield { choices: [{ delta: { content: 'ok' } }] }; })();
      } } };
    }
  }
  vm.runInNewContext(source, {
    http: { createServer(callback) { listener = callback; return server; } }, OpenAI,
    process: { argv: ['node', 'probe', mode, '310'], exit(code) { calls.exit = code; } },
    console: { log(text) { calls.results.push(JSON.parse(text)); } },
    Date, fetch: async () => ({}),
    setTimeout(callback, milliseconds) { calls.delay = milliseconds; callback(); },
  });
  await pending;
  return calls;
}

for (const mode of ['headers', 'noheaders']) {
  test(`${mode}: loopback, SDK settings, delayed SSE, and cleanup`, async () => {
    const calls = await probe(mode);
    assert.equal(calls.host, '127.0.0.1');
    assert.equal(calls.client.baseURL, 'http://127.0.0.1:9002/v1');
    assert.equal(calls.flushed, mode === 'headers' ? 1 : 0);
    assert.equal(calls.delay, 310000);
    assert.equal(calls.requestOptions.timeout, 600000);
    assert.equal(calls.requestOptions.maxRetries, 0);
    assert.equal(calls.body.stream, true);
    assert.match(calls.writes[0], /data: \[DONE\]/);
    assert.equal(calls.results[0].ok, true);
    assert.equal(calls.results[0].text, 'ok');
    assert.equal(calls.closed, true);
  });
}

test('body error retains its cause code without claiming success', async () => {
  const calls = await probe('headers', true);
  assert.equal(calls.results[0].ok, false);
  assert.equal(calls.results[0].cause_code, 'UND_ERR_BODY_TIMEOUT');
  assert.equal(calls.closed, true);
});
