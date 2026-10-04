// Separate flushed headers from delayed body data using the local OpenAI SDK.
import http from "node:http";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const OpenAI = require("openai").default ?? require("openai");

const mode = process.argv[2] ?? "headers";
const delay = Number(process.argv[3] ?? 310);
if (!["headers", "noheaders"].includes(mode) || !Number.isFinite(delay) || delay < 0) {
  throw new Error("Usage: body_timeout_probe.mjs headers|noheaders delay_seconds");
}
const server = http.createServer((req, res) => {
  req.resume();
  req.on("end", () => {
    res.writeHead(200, { "content-type": "text/event-stream" });
    if (mode === "headers") res.flushHeaders();
    setTimeout(() => {
      const chunk = { id: "x", object: "chat.completion.chunk", created: 0, model: "m",
        choices: [{ index: 0, delta: { role: "assistant", content: "ok" }, finish_reason: "stop" }] };
      res.write(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`); res.end();
    }, delay * 1000);
  });
});
server.listen(0, "127.0.0.1", async () => {
  const port = server.address().port;
  const t0 = Date.now(); let headerMs = null, firstChunkMs = null;
  const tracedFetch = async (url, init) => { const r = await fetch(url, init); headerMs = Date.now() - t0; return r; };
  const client = new OpenAI({ apiKey: "fixture-key", baseURL: `http://127.0.0.1:${port}/v1`, fetch: tracedFetch });
  try {
    const stream = await client.chat.completions.create(
      { model: "m", messages: [{ role: "user", content: "hi" }], stream: true }, { timeout: 600000, maxRetries: 0 });
    let text = "";
    for await (const c of stream) { if (firstChunkMs === null) firstChunkMs = Date.now() - t0; text += c.choices?.[0]?.delta?.content ?? ""; }
    console.log(JSON.stringify({ mode, delay, ok: true, text, header_ms: headerMs, first_chunk_ms: firstChunkMs, total_ms: Date.now() - t0 }));
  } catch (e) {
    const cause = e?.cause ?? e?.error?.cause;
    console.log(JSON.stringify({ mode, delay, ok: false, header_ms: headerMs, first_chunk_ms: firstChunkMs, fail_ms: Date.now() - t0,
      error: String(e?.message ?? e).slice(0, 160), cause_code: String(cause?.code ?? cause?.name ?? cause ?? "").slice(0, 80) }));
  }
  server.close(); process.exit(0);
});
