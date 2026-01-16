const katex = require("katex");

function check(expr, opts) {
  try {
    katex.renderToString(expr, {
      throwOnError: true,
      displayMode: !!opts.displayMode,
      strict: opts.strict ?? "warn",
    });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e && e.message ? e.message : String(e) };
  }
}

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buf += chunk;
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx);
    buf = buf.slice(idx + 1);
    if (!line.trim()) continue;
    const req = JSON.parse(line);
    const res = check(req.latex, req.opts || {});
    process.stdout.write(JSON.stringify(res) + "\n");
  }
});
