"""单轮验收采集：直连 COM8，采 DURATION 秒，输出 PR/SpO2/PI 稳态统计并写 CSV。
用法：python _accept_one.py <轮次名> [秒数]
"""
import sys
import os
import time
import csv
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "03_上位机"))
import serial
import ppg_monitor as pm

ROUND = sys.argv[1] if len(sys.argv) > 1 else "round"
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 35.0
STEADY = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
PORT = "COM8"
OUT = r"./验收_%s.csv" % ROUND

ser = serial.Serial(PORT, 9600, timeout=0.1)
parser = pm.FrameParser()
rows = []
wave_frames = 0
t0 = time.monotonic()
print("START %s  采集 %.0f 秒，请保持手指不动" % (ROUND, DURATION), flush=True)
while True:
    elapsed = time.monotonic() - t0
    if elapsed > DURATION:
        break
    data = ser.read(512)
    if not data:
        continue
    for ftype, payload in parser.feed(data):
        if ftype == 0x01 and len(payload) >= 6:
            spo2, pr, pi10, pitch, roll, state = payload[:6]
            rows.append((round(elapsed, 2), spo2, pr, pi10, state))
        elif ftype == 0x03:
            wave_frames += 1
ser.close()

steady = [r for r in rows if r[0] >= STEADY]
pr_vals = [r[2] for r in steady if r[2] > 0]
spo2_vals = [r[1] for r in steady if r[1] > 0]
pi_vals = [r[3] / 10.0 for r in steady if r[3] > 0]

with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["相对时间(s)", "SpO2(%)", "PR(bpm)", "PI(%)", "状态"])
    for t, s, p, pi10, st in rows:
        w.writerow([t, s, p, "%.1f" % (pi10 / 10.0), st])

print("DATA_FRAMES", len(rows), "WAVE_FRAMES", wave_frames,
      "WAVE_RATE %.1f" % (wave_frames / DURATION))
if pr_vals:
    print("PR n=%d MEAN=%.1f SD=%.2f MIN=%d MAX=%d RANGE=%d" % (
        len(pr_vals), statistics.mean(pr_vals), statistics.pstdev(pr_vals),
        min(pr_vals), max(pr_vals), max(pr_vals) - min(pr_vals)))
    print("PR_SERIES", ",".join(str(v) for v in pr_vals))
else:
    print("PR 无有效值")
if spo2_vals:
    print("SPO2 MED=%d MIN=%d MAX=%d" % (
        int(statistics.median(spo2_vals)), min(spo2_vals), max(spo2_vals)))
else:
    print("SPO2 无有效值")
if pi_vals:
    print("PI MIN=%.1f MAX=%.1f MED=%.1f" % (
        min(pi_vals), max(pi_vals), statistics.median(pi_vals)))
else:
    print("PI 无有效值")
print("CSV", OUT)
