"""长采集 + 按 PI 自动分档（轻/中/重）分析。
用法：python _accept_long.py <轮次名> [秒数，默认240]
按上手指自然按压即可，力度可以随意变化。
"""
import sys
import os
import time
import csv
import statistics
import tkinter as tk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "03_上位机"))
import serial
import ppg_monitor as pm

ROUND = sys.argv[1] if len(sys.argv) > 1 else "long"
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 240.0
PORT = "COM8"
OUT = r"./验收_%s.csv" % ROUND

FG = "#e8e8e8"
BG = "#14161a"
ACC = "#38e08a"
WARN = "#ffb648"
BAD = "#ff5a5a"
LIGHT = "#5fd0ff"

# 分档边界（PI，%）
SEG_LIGHT = (0.3, 3.0)
SEG_MID = (3.0, 8.0)
SEG_HEAVY = (8.0, 20.5)


def tier(pi):
    if pi <= 0:
        return "无手指"
    if pi < SEG_LIGHT[1]:
        return "轻"
    if pi < SEG_MID[1]:
        return "中"
    return "重"


def stats(rs):
    if not rs:
        return None
    return (len(rs), statistics.mean(rs), statistics.pstdev(rs), min(rs), max(rs))


class App:
    def __init__(self, root):
        self.root = root
        self.ser = None
        self.parser = pm.FrameParser()
        self.rows = []
        self.t0 = None
        self.running = False

        root.title("MAX30102 长采集 — %s" % ROUND)
        root.configure(bg=BG)
        root.geometry("700x560+60+60")
        root.attributes("-topmost", True)

        self.tip = tk.Label(root, text="点「开始」，手指自然按上去，力度随便变，撑满 %d 分钟" % int(DURATION / 60),
                            font=("Microsoft YaHei", 13), fg=WARN, bg=BG)
        self.tip.pack(pady=(12, 4))

        self.clock = tk.Label(root, text="--:--", font=("Consolas", 40, "bold"), fg=FG, bg=BG)
        self.clock.pack()

        self.pi_txt = tk.Label(root, text="--", font=("Consolas", 72, "bold"), fg=FG, bg=BG)
        self.pi_txt.pack()
        tk.Label(root, text="灌注指数 PI (%)", font=("Microsoft YaHei", 11),
                 fg="#9aa4b2", bg=BG).pack()

        row = tk.Frame(root, bg=BG)
        row.pack(pady=(8, 0))
        self.pr_txt = tk.Label(row, text="--", font=("Consolas", 30, "bold"), fg=LIGHT, bg=BG)
        self.pr_txt.grid(row=0, column=0, padx=24)
        self.spo2_txt = tk.Label(row, text="--", font=("Consolas", 30, "bold"), fg="#c78bff", bg=BG)
        self.spo2_txt.grid(row=0, column=1, padx=24)
        tk.Label(row, text="   PR (bpm)                          SpO2 (%)   ",
                 font=("Microsoft YaHei", 10), fg="#9aa4b2", bg=BG).grid(row=1, column=0, columnspan=2)

        self.seg_txt = tk.Label(root, text="", font=("Microsoft YaHei", 11), fg="#9aa4b2", bg=BG)
        self.seg_txt.pack(pady=(8, 0))

        self.btn = tk.Button(root, text="开始采集", font=("Microsoft YaHei", 14, "bold"),
                             bg=ACC, fg="#06210f", relief="flat", width=16, command=self.start)
        self.btn.pack(pady=10)

        self.out = tk.Label(root, text="", font=("Consolas", 11), fg=FG, bg=BG, justify="left")
        self.out.pack()

    def start(self):
        if self.running:
            return
        try:
            self.ser = serial.Serial(PORT, 9600, timeout=0)
        except Exception as e:
            self.tip.config(text="打不开 %s：%s" % (PORT, e), fg=BAD)
            return
        self.rows = []
        self.parser = pm.FrameParser()
        self.t0 = time.monotonic()
        self.running = True
        self.btn.config(state="disabled", text="采集中…")
        self.out.config(text="")
        self.root.after(20, self.tick)

    def tick(self):
        if not self.running:
            return
        el = time.monotonic() - self.t0
        try:
            data = self.ser.read(1024)
        except Exception:
            data = b""
        if data:
            for ftype, payload in self.parser.feed(data):
                if ftype == 0x01 and len(payload) >= 6:
                    spo2, pr, pi10, pitch, roll, state = payload[:6]
                    self.rows.append((round(el, 2), spo2, pr, pi10))

        if el >= DURATION:
            self.finish()
            return

        left = DURATION - el
        self.clock.config(text="%d:%02d" % (int(left) // 60, int(left) % 60))

        pi = pr = sp = 0
        if self.rows:
            _, sp, pr, pi10 = self.rows[-1]
            pi = pi10 / 10.0

        t = tier(pi)
        col = {"轻": WARN, "中": ACC, "重": BAD, "无手指": "#666"}.get(t, FG)
        self.pi_txt.config(text=("%.1f" % pi) if pi > 0 else "--", fg=col)
        self.pr_txt.config(text=str(pr) if pr > 0 else "--")
        self.spo2_txt.config(text=str(sp) if sp > 0 else "--")
        self.seg_txt.config(text="当前：%s   （轻 <3 ｜ 中 3~8 ｜ 重 >8）" % t, fg=col)

        self.root.after(20, self.tick)

    def finish(self):
        self.running = False
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = None

        with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["相对时间(s)", "SpO2(%)", "PR(bpm)", "PI(%)", "分档"])
            for t, s, p, pi10 in self.rows:
                w.writerow([t, s, p, "%.1f" % (pi10 / 10.0), tier(pi10 / 10.0)])

        lines = []
        for name, seg in (("轻", SEG_LIGHT), ("中", SEG_MID), ("重", SEG_HEAVY)):
            rs = [r for r in self.rows if r[2] > 0 and seg[0] <= r[3] / 10.0 < seg[1]]
            st = stats([r[2] for r in rs])
            if st:
                n, mean, sd, lo, hi = st
                lines.append("%s  n=%2d  PR %6.1f ±%5.2f   %3d~%3d  极差%3d  PI %.1f~%.1f" % (
                    name, n, mean, sd, lo, hi, hi - lo,
                    min(r[3] / 10.0 for r in rs), max(r[3] / 10.0 for r in rs)))
            else:
                lines.append("%s  n= 0  （无有效帧）" % name)
        lines.append("总帧 %d，CSV：%s" % (len(self.rows), OUT))

        self.clock.config(text="完成")
        self.tip.config(text="三段结果如下，可直接截图发给助手", fg=ACC)
        self.out.config(text="\n".join(lines), fg=FG)
        self.btn.config(state="normal", text="再采一次", command=self.start)


root = tk.Tk()
App(root)
root.mainloop()
