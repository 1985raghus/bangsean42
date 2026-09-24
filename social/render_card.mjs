// Renders social/week-card.html to a 2160x2700 PNG (1080x1350 at 2x) for Instagram.
//   node social/render_card.mjs [out.png] [photo.jpg]
// The photo path is relative to social/ (e.g. ../images/Image1.jpg); omit it for
// the type-only card. Needs Chrome installed; no npm packages.
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const W = 1080, H = 1350;
const out = process.argv[2] || 'social/week-card.png';
const photo = process.argv[3];
const base = pathToFileURL(new URL('week-card.html', import.meta.url).pathname.replace(/^\//, '')).href;
const page = photo ? `${base}?photo=${encodeURIComponent(photo)}` : base;
const CH = process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const port = 9600 + Math.floor(Math.random() * 300);
const prof = `${process.env.TEMP || '/tmp'}/cdp-card-${port}`;
mkdirSync(prof, { recursive: true });
const chrome = spawn(CH, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--allow-file-access-from-files',
  `--remote-debugging-port=${port}`, `--user-data-dir=${prof}`, 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let target;
for (let i = 0; i < 60 && !target; i++) {
  try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === 'page'); } catch {}
  if (!target) await sleep(250);
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => { ws.onopen = r; });
let seq = 0; const pending = new Map();
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
const send = (method, params = {}) => new Promise((r) => { const id = ++seq; pending.set(id, r); ws.send(JSON.stringify({ id, method, params })); });

await send('Page.enable');
await send('Runtime.enable');
// deviceScaleFactor 2 already doubles the pixels; clip.scale must stay 1 or CDP
// multiplies them and writes a 4320x5400 file that Instagram just recompresses.
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 2, mobile: false });
await send('Page.navigate', { url: page });
for (let i = 0; i < 40; i++) {
  const r = await send('Runtime.evaluate', { expression: 'document.fonts.ready.then(() => document.readyState)', awaitPromise: true, returnByValue: true });
  if (r.result?.result?.value === 'complete') break;
  await sleep(250);
}
await sleep(400); // let the graded photo paint
const shot = await send('Page.captureScreenshot', { format: 'png', clip: { x: 0, y: 0, width: W, height: H, scale: 1 } });
writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
console.log(`wrote ${out} at ${W * 2}x${H * 2}${photo ? ` with photo ${photo}` : ' (type only)'}`);
ws.close(); chrome.kill(); process.exit(0);
