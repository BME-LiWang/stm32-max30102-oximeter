#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手机端上位机 —— PC 读蓝牙串口，手机浏览器直接访问。

用法:
    python web_monitor.py                 # 读 COM9 @9600
    python web_monitor.py --port COM5     # 换串口
    python web_monitor.py --demo          # 模拟数据, 不开串口(界面自检用)
    python web_monitor.py --http 8080     # 换端口

手机浏览器打开:  http://<电脑局域网IP>:8080
"""

import argparse
import json
import math
import random
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FRAME_HEAD = b"\xAA\x55"
FRAME_LENGTHS = {0x01: {6}, 0x02: {0, 1}, 0x03: {6, 12}}
FALL_NAMES = ["正常", "自由落体", "冲击", "跌倒后静止", "跌倒报警"]


class FrameParser:
    """与 ppg_monitor.py 同一套协议: AA 55 | type | len | payload | checksum"""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf.extend(data)
        out = []
        while self.buf:
            i = self.buf.find(FRAME_HEAD)
            if i < 0:
                keep = 1 if self.buf[-1] == FRAME_HEAD[0] else 0
                drop = len(self.buf) - keep
                if drop:
                    del self.buf[:drop]
                break
            if i:
                del self.buf[:i]
            if len(self.buf) < 4:
                break
            ftype, plen = self.buf[2], self.buf[3]
            if plen not in FRAME_LENGTHS.get(ftype, set()):
                del self.buf[0]
                continue
            total = 5 + plen
            if len(self.buf) < total:
                break
            if (sum(self.buf[:4 + plen]) & 0xFF) != self.buf[4 + plen]:
                del self.buf[0]
                continue
            out.append((ftype, bytes(self.buf[4:4 + plen])))
            del self.buf[:total]
        return out


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = dict(spo2=None, pr=None, pi=None, pitch=0, roll=0, state=0,
                         wave=[], wave_red=[], frames=0, connected=False, demo=False, port="")

    def update(self, **kw):
        with self.lock:
            self.data.update(kw)

    def bump(self):
        with self.lock:
            self.data["frames"] += 1

    def push_wave(self, v, red=None):
        with self.lock:
            w = self.data["wave"]
            w.append(int(v))
            if len(w) > 700:
                del w[:len(w) - 700]
            if red is not None:
                rw = self.data["wave_red"]
                rw.append(int(red))
                if len(rw) > 700:
                    del rw[:len(rw) - 700]

    def snapshot(self):
        with self.lock:
            d = dict(self.data)
            d["wave"] = list(self.data["wave"])
            d["wave_red"] = list(self.data["wave_red"])
            d["state_name"] = FALL_NAMES[d["state"]] if 0 <= d["state"] < len(FALL_NAMES) else "未知"
            return d


STORE = Store()
SERIAL_HOLDER = {"s": None, "lock": threading.Lock()}


def serial_worker(store, port, baud, stop=None):
    import serial
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            s = serial.Serial(port, baud, timeout=0.15)
        except Exception as exc:
            store.update(connected=False, port="%s (%s)" % (port, exc))
            if stop.wait(1.5):
                break
            continue
        if stop.is_set():
            try:
                s.close()
            except Exception:
                pass
            break
        with SERIAL_HOLDER["lock"]:
            SERIAL_HOLDER["s"] = s
        store.update(connected=True, port=port)
        parser = FrameParser()
        try:
            while not stop.is_set():
                chunk = s.read(512)
                if not chunk:
                    continue
                for ftype, pl in parser.feed(chunk):
                    if ftype == 0x01:
                        spo2, pr, pi, pitch, roll, st = pl
                        store.update(spo2=(spo2 if spo2 else None),
                                     pr=(pr if pr else None),
                                     pi=round(pi / 10.0, 1),
                                     pitch=pitch - 90, roll=roll - 90, state=st)
                        store.bump()
                    elif ftype == 0x03:
                        if len(pl) >= 12:
                            ir0 = (pl[0] << 16) | (pl[1] << 8) | pl[2]
                            red0 = (pl[3] << 16) | (pl[4] << 8) | pl[5]
                            ir1 = (pl[6] << 16) | (pl[7] << 8) | pl[8]
                            red1 = (pl[9] << 16) | (pl[10] << 8) | pl[11]
                            store.push_wave(ir0, red0)
                            store.push_wave(ir1, red1)
                        else:
                            ir0 = (pl[0] << 16) | (pl[1] << 8) | pl[2]
                            ir1 = (pl[3] << 16) | (pl[4] << 8) | pl[5]
                            store.push_wave(ir0)
                            store.push_wave(ir1)
                    elif ftype == 0x02:
                        store.update(state=4)
        except Exception:
            pass
        finally:
            with SERIAL_HOLDER["lock"]:
                SERIAL_HOLDER["s"] = None
            try:
                s.close()
            except Exception:
                pass
            store.update(connected=False)
            if stop.wait(1.0):
                break
    with SERIAL_HOLDER["lock"]:
        SERIAL_HOLDER["s"] = None
    store.update(connected=False)


SERIAL_CTL = {"thread": None, "port": "COM9", "baud": 9600, "stop": threading.Event()}


def start_serial(port=None, baud=None):
    if port is not None:
        SERIAL_CTL["port"] = port
    if baud is not None:
        SERIAL_CTL["baud"] = baud
    SERIAL_CTL["stop"].clear()
    t = threading.Thread(target=serial_worker,
                         args=(STORE, SERIAL_CTL["port"], SERIAL_CTL["baud"], SERIAL_CTL["stop"]),
                         daemon=True)
    SERIAL_CTL["thread"] = t
    t.start()


def stop_serial(timeout=2.5):
    SERIAL_CTL["stop"].set()
    t = SERIAL_CTL["thread"]
    if t is not None:
        t.join(timeout)
    SERIAL_CTL["thread"] = None
    with SERIAL_HOLDER["lock"]:
        SERIAL_HOLDER["s"] = None
    STORE.update(connected=False, port="已断开", spo2=None, pr=None, pi=None)


def demo_worker(store):
    """没有硬件时用模拟数据把界面跑起来(仅用于自检/演示界面)。"""
    store.update(connected=True, port="DEMO", demo=True)
    t = 0.0
    while True:
        a = 190000 + 4000 * math.sin(2 * math.pi * 1.15 * t) + random.randint(-50, 50)
        b = 190000 + 4000 * math.sin(2 * math.pi * 1.15 * (t + 0.02)) + random.randint(-50, 50)
        store.push_wave(a, a - 13000)
        store.push_wave(b, b - 13000)
        t += 0.04
        if int(t * 100) % 25 == 0:
            pr = int(76 + 2 * math.sin(t * 0.3))
            store.update(spo2=97, pr=pr, pi=round(3.6 + random.random() * 0.9, 1),
                         pitch=int(2 * math.sin(t * 0.2)), roll=int(3 * math.cos(t * 0.15)),
                         state=0)
            store.bump()
        time.sleep(0.02)


def post_command(body):
    key = SERIAL_HOLDER["s"]
    if key is None:
        return False
    try:
        with SERIAL_HOLDER["lock"]:
            SERIAL_HOLDER["s"].write(body)
            SERIAL_HOLDER["s"].flush()
        return True
    except Exception:
        return False


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#0d1117">
<title>血氧监测 · 手机端</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#0d1117;color:#e6edf3;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
     padding:14px 14px 26px;min-height:100vh}
h1{font-size:19px;margin:0 0 4px;font-weight:600;letter-spacing:.5px}
.sub{font-size:12px;color:#8b949e;display:flex;align-items:center;gap:8px;margin-bottom:14px}
.dot{width:9px;height:9px;border-radius:50%;background:#f85149;display:inline-block}
.dot.on{background:#3fb950}
#banner{display:none;background:#f85149;color:#fff;border-radius:12px;padding:14px;font-size:17px;
        font-weight:700;text-align:center;margin-bottom:12px;animation:blink 1s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.55}}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
.card{background:#161b22;border:1px solid #21262d;border-radius:14px;padding:12px 14px}
.card .k{font-size:12px;color:#8b949e;margin-bottom:6px}
.card .v{font-size:30px;font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums}
.card .u{font-size:13px;color:#8b949e;font-weight:400;margin-left:3px}
.wide{grid-column:1/3;display:flex;gap:18px;align-items:center;justify-content:space-between}
canvas{width:100%;height:186px;display:block;background:#0b0f14;border-radius:12px;border:1px solid #21262d;margin-bottom:10px}
button{width:100%;padding:15px;border-radius:12px;border:1px solid #30363d;background:#21262d;color:#e6edf3;
       font-size:16px;font-weight:600;font-family:inherit}
button:active{opacity:.75}
button.danger{background:#da3633;border-color:#f85149}
body.alarm{background:#2b0f0f}
.row{display:flex;gap:10px}
.row .card{flex:1}
</style>
</head>
<body>
<h1>血氧监测 · 手机端</h1>
<div class="sub"><span class="dot" id="dot"></span><span id="status">连接中…</span></div>
<div id="banner">⚠ 跌倒报警</div>

<div class="grid">
  <div class="card"><div class="k">血氧 SpO₂</div><div class="v"><span id="spo2">--</span><span class="u">%</span></div></div>
  <div class="card"><div class="k">脉率 PR</div><div class="v"><span id="pr">--</span><span class="u">bpm</span></div></div>
</div>
<div class="grid">
  <div class="card"><div class="k">灌注指数 PI</div><div class="v"><span id="pi">--</span><span class="u">%</span></div></div>
  <div class="card"><div class="k">状态</div><div class="v" style="font-size:20px" id="state">--</div></div>
</div>
<div class="grid">
  <div class="card wide"><div><div class="k">Pitch / Roll</div><div class="v" style="font-size:22px"><span id="att">--</span></div></div>
  <div style="text-align:right"><div class="k">帧数</div><div class="v" style="font-size:22px" id="frames">0</div></div></div>
</div>

<canvas id="wave"></canvas>
<div class="row" style="margin-bottom:10px">
  <button id="btnFall">模拟跌倒</button>
  <button id="btnSerial">断开串口</button>
</div>
<script>
const $ = id => document.getElementById(id);
const cv = $('wave'), ctx = cv.getContext('2d');

function fitCanvas(){
  const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  cv.width = Math.floor(r.width * dpr); cv.height = Math.floor(r.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
fitCanvas();
window.addEventListener('resize', fitCanvas);

function scaleSeries(arr, n){
  let lo = Infinity, hi = -Infinity;
  const off = Math.max(0, arr.length - n);
  for (let i = off; i < arr.length; i++){ const v = arr[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
  if (hi - lo < 1) hi = lo + 1;
  const pad = (hi - lo) * 0.12, bot = lo - pad, top = hi + pad;
  const out = [];
  for (let i = off; i < arr.length; i++){ out.push((arr[i] - bot) / (top - bot)); }
  return out;
}

function strokeSeries(norm, W, yTop, yBot, color, width){
  if (norm.length < 2) return;
  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  for (let i = 0; i < norm.length; i++){
    const x = (i / (norm.length - 1)) * W;
    const y = yBot - norm[i] * (yBot - yTop);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.stroke();
}

function bpFilter(arr){
  const n = arr.length, out = new Array(n).fill(0);
  const win = 40; let sum = 0; const q = [];
  for (let i = 0; i < n; i++){
    q.push(arr[i]); sum += arr[i];
    if (q.length > win) sum -= q.shift();
    out[i] = arr[i] - sum / q.length;
  }
  const sm = new Array(n).fill(0);
  for (let i = 0; i < n; i++){
    let s = 0, c = 0;
    for (let k = -2; k <= 2; k++){ const j = i + k; if (j >= 0 && j < n){ s += out[j]; c++; } }
    sm[i] = s / c;
  }
  return sm;
}

function findPeaks(sig, minGap, thrRatio){
  const peaks = []; let mx = -Infinity, mn = Infinity;
  for (const v of sig){ if (v > mx) mx = v; if (v < mn) mn = v; }
  const pp = mx - mn;
  if (pp < 1200) return {peaks: peaks, pp: pp};
  const thr = mn + pp * (thrRatio || 0.5);
  let last = -9999;
  for (let i = 1; i < sig.length - 1; i++){
    if (sig[i] > thr && sig[i] >= sig[i-1] && sig[i] > sig[i+1] && (i - last) >= minGap){ peaks.push(i); last = i; }
  }
  return {peaks: peaks, pp: pp};
}

function estimateBpm(peaks, fs){
  if (peaks.length < 3) return null;
  const gaps = [];
  for (let i = 1; i < peaks.length; i++) gaps.push(peaks[i] - peaks[i-1]);
  const sorted = gaps.slice().sort((a, b) => a - b);
  const med = sorted[Math.floor(sorted.length / 2)];
  const good = gaps.filter(g => Math.abs(g - med) < med * 0.35);
  if (good.length < gaps.length * 0.6) return null;
  const avg = good.reduce((a, b) => a + b, 0) / good.length;
  const bpm = 60 / (avg / fs);
  if (bpm < 35 || bpm > 200) return null;
  return Math.round(bpm);
}

function drawWave(w, wr){
  const W = cv.clientWidth, H = cv.clientHeight;
  ctx.clearRect(0, 0, W, H);
  const third = H / 3;
  ctx.strokeStyle = '#21262d'; ctx.lineWidth = 1;
  for (let y = 0; y <= 3; y++){ const yy = third * y; ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(W, yy); ctx.stroke(); }
  ctx.font = '11px sans-serif';
  ctx.textAlign = 'left';
  if (!w || w.length < 6){ ctx.fillStyle = '#8b949e'; ctx.fillText('等待数据…', 8, 18); return; }

  strokeSeries(scaleSeries(w, 300), W, 2, third - 3, '#58a6ff', 1.6);
  ctx.fillStyle = '#58a6ff'; ctx.fillText('IR', 6, 14);

  if (wr && wr.length >= 4){
    strokeSeries(scaleSeries(wr, 300), W, third + 3, third * 2 - 3, '#f85149', 1.4);
  }
  ctx.fillStyle = '#f85149'; ctx.fillText('RED', 6, third + 14);

  const seg = w.slice(-300);
  const bp = bpFilter(seg);
  const norm = scaleSeries(bp, bp.length);
  strokeSeries(norm, W, third * 2 + 3, H - 2, '#3fb950', 1.5);

  const res = findPeaks(bp, 22, 0.5);
  const bpm = estimateBpm(res.peaks, 50);
  const cnt = bp.length;
  if (bpm){
    res.peaks.forEach(i => {
      const x = (i / (cnt - 1)) * W;
      const y = (H - 2) - norm[i] * ((H - 2) - (third * 2 + 3));
      ctx.beginPath(); ctx.arc(x, y, 2.3, 0, Math.PI * 2);
      ctx.fillStyle = '#3fb950'; ctx.fill();
    });
  }
  ctx.textAlign = 'left';
  ctx.fillStyle = '#3fb950'; ctx.fillText('PULSE', 6, third * 2 + 14);
  ctx.textAlign = 'right';
  if (bpm){
    ctx.fillStyle = '#3fb950';
    ctx.fillText('≈' + bpm + ' bpm', W - 6, third * 2 + 14);
  } else if (res.pp < 1200){
    ctx.fillStyle = '#8b949e';
    ctx.fillText('信号弱，指腹覆盖', W - 6, third * 2 + 14);
  } else {
    ctx.fillStyle = '#8b949e';
    ctx.fillText('波形不稳', W - 6, third * 2 + 14);
  }
}

let lastState = 0;
function render(d){
  $('dot').className = 'dot' + (d.connected ? ' on' : '');
  $('status').textContent = (d.connected ? '已连接 ' : '未连接 ') + (d.port || '');
  $('spo2').textContent = d.spo2 == null ? '--' : d.spo2;
  $('pr').textContent   = d.pr == null ? '--' : d.pr;
  $('pi').textContent   = d.pi == null ? '--' : d.pi.toFixed ? d.pi.toFixed(1) : d.pi;
  $('state').textContent = d.state_name || '--';
  $('att').textContent = (d.pitch >= 0 ? '+' : '') + d.pitch + '° / ' + (d.roll >= 0 ? '+' : '') + d.roll + '°';
  $('frames').textContent = d.frames;
  drawWave(d.wave, d.wave_red);

  const alarm = d.state === 4;
  document.body.classList.toggle('alarm', alarm);
  $('banner').style.display = alarm ? 'block' : 'none';
  $('btnFall').textContent = alarm ? '清除报警' : '模拟跌倒';
  $('btnFall').className = alarm ? 'danger' : '';

  const isDemo = (d.port || '').indexOf('DEMO') === 0;
  $('btnSerial').style.display = isDemo ? 'none' : '';
  $('btnSerial').textContent = d.connected ? '断开串口' : '重新连接串口';
  $('btnSerial').dataset.connected = d.connected ? '1' : '0';
  if (alarm && lastState !== 4 && navigator.vibrate) navigator.vibrate([400, 200, 400, 200, 600]);
  lastState = d.state;
}

const es = new EventSource('/stream');
es.onmessage = e => { try { render(JSON.parse(e.data)); } catch (_) {} };
es.onerror = () => { $('dot').className = 'dot'; $('status').textContent = '服务中断, 正在重连…'; };

$('btnFall').addEventListener('click', async () => {
  $('btnFall').disabled = true;
  try {
    await fetch('/cmd', { method: 'POST', body: 'f' });
  } catch (_) {}
  setTimeout(() => { $('btnFall').disabled = false; }, 500);
});

$('btnSerial').addEventListener('click', async () => {
  const btn = $('btnSerial');
  const connected = btn.dataset.connected === '1';
  btn.disabled = true;
  btn.textContent = connected ? '正在释放串口…' : '正在连接…';
  try {
    await fetch('/serial', { method: 'POST', body: connected ? 'disconnect' : 'connect' });
  } catch (_) {}
  setTimeout(() => { btn.disabled = false; }, 1200);
});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                while True:
                    payload = json.dumps(STORE.snapshot(), ensure_ascii=False)
                    self.wfile.write(("data: %s\n\n" % payload).encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.05)
            except Exception:
                return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path == "/serial":
            action = body.strip().lower().decode("utf-8", "ignore")
            ok = False
            if action == "disconnect":
                stop_serial()
                ok = True
            elif action == "connect":
                start_serial()
                ok = True
            data = json.dumps({"ok": ok, "connected": STORE.snapshot()["connected"]},
                              ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/cmd":
            ok = post_command(b"F" * 12) if body.strip().lower() == b"f" else False
            data = json.dumps({"ok": ok}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM9", help="串口名, 默认 COM9")
    ap.add_argument("--baud", type=int, default=9600, help="波特率, 默认 9600")
    ap.add_argument("--http", type=int, default=8080, help="HTTP 端口, 默认 8080")
    ap.add_argument("--demo", action="store_true", help="模拟数据模式, 不打开串口")
    args = ap.parse_args()

    if args.demo:
        threading.Thread(target=demo_worker, args=(STORE,), daemon=True).start()
    else:
        start_serial(args.port, args.baud)

    ip = local_ip()
    server = ThreadingHTTPServer(("0.0.0.0", args.http), Handler)
    server.daemon_threads = True
    print("=" * 52)
    print(" 手机端上位机已启动" + ("（模拟数据模式）" if args.demo else ""))
    print(" 电脑访问:  http://127.0.0.1:%d" % args.http)
    print(" 手机访问:  http://%s:%d" % (ip, args.http))
    print(" 串口:      %s @ %d" % ("(demo)" if args.demo else args.port, args.baud))
    print("=" * 52)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
