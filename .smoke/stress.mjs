import { spawn } from "node:child_process";

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PORT = 9231;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--hide-scrollbars",
  "--window-size=1280,800", `--remote-debugging-port=${PORT}`,
  "--autoplay-policy=no-user-gesture-required", "about:blank"], { stdio: "ignore" });
let ws;
try {
  let targets = [];
  for (let i = 0; i < 50 && !targets.length; i++) {
    await sleep(200);
    try {
      targets = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).filter((t) => t.type === "page");
    } catch {}
  }
  ws = new WebSocket(targets[0].webSocketDebuggerUrl, []);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(String(ev.data));
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  };
  const send = (method, params = {}) => new Promise((resolve) => {
    const mid = ++id; pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const run = (e) => send("Runtime.evaluate", { expression: e, awaitPromise: true }).then((m) => m?.result?.result?.value);
  await send("Page.enable");
  await send("Page.navigate", { url: "http://localhost:5173/" });
  await sleep(6000);
  await run(`(async () => { document.querySelector('.lv-splash-skip')?.click(); return 1; })()`);
  await sleep(2000);
  // Install observers, then fast-scroll top->bottom with wheel bursts.
  await run(`(async () => {
    window.__lt = [];
    window.__t0 = performance.now();
    new PerformanceObserver((l) => l.getEntries().forEach((e) => {
      const at = (e.attribution && e.attribution[0] && e.attribution[0].containerSrc) || (e.name || "");
      window.__lt.push(Math.round(e.duration) + "@" + Math.round(e.startTime - window.__t0) + "ms:" + String(at).split("/").pop().slice(-40));
    })).observe({ entryTypes: ['longtask'] });
    window.__frames = [];
    let last = performance.now();
    const tick = (t) => { window.__frames.push(Math.round(t - last)); last = t; if (window.__frames.length < 600) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    return 1;
  })()`);
  for (let i = 0; i < 24; i++) {
    await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: 640, y: 400, deltaX: 0, deltaY: 900 });
    await sleep(120);
  }
  await sleep(2500);
  console.log(await run(`(async () => {
    const lt = window.__lt.slice();
    const fr = window.__frames;
    const over50 = fr.filter((f) => f > 50).length;
    const over100 = fr.filter((f) => f > 100).length;
    const avg = Math.round(fr.reduce((a, b) => a + b, 0) / Math.max(1, fr.length));
    return JSON.stringify({
      longtasks: lt.length, worst5: lt.slice(0, 5),
      frames: fr.length, avgFps: Math.round(1000 / Math.max(1, avg)),
      over50ms: over50, over100ms: over100,
      scrollY: Math.round(window.scrollY),
    });
  })()`));
} finally {
  try { ws?.close(); } catch {}
  chrome.kill();
}
