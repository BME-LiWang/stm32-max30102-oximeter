"""带实时显示的验收采集器：看着 PI 调到 3~8%，后 20 秒计分。
用法：python _accept_gui.py <轮次名>
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

ROUND = sys.argv[1] if len(sys.argv) > 1 else "round"
PORT = "COM8"
TUNE_S = 20.0     # 前半段：调压
SCORE_S = 20.0    # 后半段：计分
OUT = r"./验收_%s.csv" % ROUND

FG = "#e8e8e8"
BG = "#14161a"
ACC = "#38e08a"
WARN = "#ffb648"
BAD = "#ff5a5a"


class App:
    def __init__(self, root):
        self.root = root
        self.ser = None
        self.parser = pm.FrameParser()
        self.rows = []
        self.t0 = None
        self.running = False
        self.result = None

        root.title("MAX30102 验收采集 — %s" % ROUND)
        root.configure(bg=BG)
        root.geometry("640x470+80+80")
        root.attributes("-topmost", True)

        self.tip = tk.Label(root, text="点「开始」后按上手指，把 PI 压到 5~6 就定住",
                            font=("Microsoft YaHei", 13), fg=WARN, bg=BG)
        self.tip.pack(pady=(14, 4))

        self.pi_txt = tk.Label(root, text="--", font=("Consolas", 88, "bold"),
                               fg=FG, bg=BG)
        self.pi_txt.pack()
        tk.Label(root, text="灌注指数 PI (%)", font=("Microsoft YaHei", 12),
                 fg="#9aa4b2", bg=BG).pack()

        row = tk.Frame(root, bg=BG)
        row.pack(pady=(12, 0))
        self.pr_txt = tk.Label(row, text="--", font=("Consolas", 34, "bold"),
                               fg="#5fd0ff", bg=BG)
        self.pr_txt.grid(row=0, column=0, padx=26)
        self.spo2_txt = tk.Label(row, text="--", font=("Consolas", 34, "bold"),
                                 fg="#c78bff", bg=BG)
        self.spo2_txt.grid(row=0, column=1, padx=26)
        tk.Label(row, text="   PR (bpm)                        SpO2 (%)   ",
                 font=("Microsoft YaHei", 11), fg="#9aa4b2", bg=BG).grid(row=1, column=0, columnspan=2)

        self.clock = tk.Label(root, text="准备就绪", font=("Microsoft YaHei", 20, "bold"),
                              fg=FG, bg=BG)
        self.clock.pack(pady=(16, 0))

        self.btn = tk.Button(root, text="开始采集", font=("Microsoft YaHei", 14, "bold"),
                             bg=ACC, fg="#06210f", relief="flat", width=16,
                             command=self.start)
        self.btn.pack(pady=14)

        self.out = tk.Label(root, text="", font=("Microsoft YaHei", 11),
                            fg=FG, bg=BG, justify="left")
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
        self.result = None
        self.t0 = time.monotonic()
        self.running = True
        self.btn.config(state="disabled", text="采集中…")
        self.root.after(20, self.tick)

    def tick(self):
        if not self.running:
            return
        el = time.monotonic() - self.t0
        try:
            data = self.ser.read(512)
        except Exception:
            data = b""
        if data:
            for ftype, payload in self.parser.feed(data):
                if ftype == 0x01 and len(payload) >= 6:
                    spo2, pr, pi10, pitch, roll, state = payload[:6]
                    self.rows.append((round(el, 2), spo2, pr, pi10))

        pi = pr = sp = 0
        if self.rows:
            _, sp, pr, pi10 = self.rows[-1]
            pi = pi10 / 10.0

        if el < TUNE_S:
            stage = "调压中 %2.0f s" % (TUNE_S - el)
            if pi < 5.0:
                tip = "太轻，再加力（目标 5~6）"
            elif pi <= 6.5:
                tip = "到位了，现在开始完全别动"
            elif pi <= 8.0:
                tip = "略重，松一点点（目标 5~6）"
            else:
                tip = "压太重，波形会变形，松力到 5~6"
            self.clock.config(fg=WARN)
        elif el < TUNE_S + SCORE_S:
            stage = "计分 %2.0f s" % (TUNE_S + SCORE_S - el)
            tip = "保持住，一点力都别加也别减"
            self.clock.config(fg=ACC)
        else:
            self.finish()
            return
        self.clock.config(text=stage)
        self.tip.config(text=tip, fg=WARN if el < TUNE_S else ACC)

        self.pi_txt.config(text=("%.1f" % pi) if pi > 0 else "--",
                           fg=ACC if 5.0 <= pi <= 6.5 else (BAD if pi > 8.0 else WARN))
        self.pr_txt.config(text=str(pr) if pr > 0 else "--")
        self.spo2_txt.config(text=str(sp) if sp > 0 else "--")

        self.root.after(20, self.tick)

    def finish(self):
        self.running = False
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = None

        steady = [r for r in self.rows if r[0] >= TUNE_S]
        prs = [r[2] for r in steady if r[2] > 0]
        sps = [r[1] for r in steady if r[1] > 0]
        pis = [r[3] / 10.0 for r in steady if r[3] > 0]

        with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["相对时间(s)", "SpO2(%)", "PR(bpm)", "PI(%)"])
            for t, s, p, pi10 in self.rows:
                w.writerow([t, s, p, "%.1f" % (pi10 / 10.0)])

        msg = []
        if prs:
            msg.append("PR 均值 %.1f  SD %.2f  极差 %d（%d~%d）" % (
                statistics.mean(prs), statistics.pstdev(prs),
                max(prs) - min(prs), min(prs), max(prs)))
        else:
            msg.append("PR 无有效值")
        if pis:
            msg.append("PI %.1f~%.1f   SpO2 %d" % (
                min(pis), max(pis),
                int(statistics.median(sps)) if sps else 0))
        msg.append("CSV：%s" % OUT)

        self.clock.config(text="完成", fg=ACC)
        self.tip.config(text="采完了，把窗口内容告诉助手", fg=ACC)
        self.out.config(text="\n".join(msg), fg=FG)
        self.btn.config(state="normal", text="再采一次", command=self.start)


root = tk.Tk()
App(root)
root.mainloop()
