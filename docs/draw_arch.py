# -*- coding: utf-8 -*-
"""生成 README 用的系统框图。输出 docs/system-architecture.png"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

BG      = "#f7f9fb"
NAVY    = "#1f3a5f"
TEAL    = "#0aa3a3"
GREY    = "#6b7a8f"
LIGHT   = "#ffffff"

fig, ax = plt.subplots(figsize=(12, 6.6), dpi=200)
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)
ax.set_xlim(0, 180)
ax.set_ylim(0, 100)
ax.axis("off")


def box(x, y, w, h, title, sub="", fc=LIGHT, ec=NAVY, tc=NAVY, ts=11, ss=8, lw=1.4):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle="round,pad=0.6,rounding_size=2.2",
                       linewidth=lw, edgecolor=ec, facecolor=fc, zorder=3)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2 + (1.8 if sub else 0), title,
            ha="center", va="center", fontsize=ts, color=tc, weight="bold", zorder=4)
    if sub:
        ax.text(x + w / 2, y + h / 2 - 2.6, sub,
                ha="center", va="center", fontsize=ss, color=GREY, zorder=4)


def arrow(p1, p2, color=TEAL, label="", lx=0, ly=0, style="-|>", ls="-", lw=1.3):
    a = FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=11,
                        linewidth=lw, color=color, linestyle=ls,
                        shrinkA=3, shrinkB=3, zorder=2,
                        connectionstyle="arc3,rad=0")
    ax.add_patch(a)
    if label:
        mx, my = (p1[0] + p2[0]) / 2 + lx, (p1[1] + p2[1]) / 2 + ly
        ax.text(mx, my, label, ha="center", va="center", fontsize=7.6,
                color=color, zorder=5,
                bbox=dict(boxstyle="round,pad=0.25", fc=BG, ec="none"))


# ---- 中心 MCU ----
box(62, 34, 56, 32, "STM32F103C8T6",
    "Cortex-M3 · 72 MHz · 64 KB Flash", fc=NAVY, ec=NAVY, tc="white", ts=12.5, ss=8)
ax.text(90, 63.2, "裸机寄存器级固件", ha="center", fontsize=8, color="#9fc8e8", zorder=4)

# ---- 外设 ----
box(4, 44, 36, 16, "MAX30102", "红光 660 / 红外 880 nm")
box(62, 72, 30, 16, "MPU6050", "六轴 · 重力方向")
box(96, 72, 32, 16, "SH1106 OLED", "128×64 · 三页轮播")
box(136, 44, 38, 16, "JDY-31", "SPP 蓝牙 · 9600 8N1")
box(66, 8, 30, 14, "蜂鸣器", "4 kHz · 跌倒报警", ec=GREY, tc=GREY)
box(106, 8, 32, 14, "PC 上位机", "PyQt5 · 波形 + 指标", ec=GREY, tc=GREY)
box(144, 8, 32, 14, "手机 App", "Android / 网页版", ec=GREY, tc=GREY)

# ---- 连线 ----
arrow((40, 52), (62, 52), label="I2C2  0x57  50 Hz", ly=3.2)
arrow((77, 72), (77, 66), label="I2C1  0x68", lx=-9)
arrow((112, 72), (110, 66), label="I2C1  0x3C", lx=9)
arrow((118, 52), (136, 52), label="USART2  9600", ly=3.2)
arrow((81, 34), (81, 22), label="PB13", lx=-7)
arrow((104, 34), (118, 22), label="USB 串口", lx=8, ly=-2, color=GREY)
arrow((174, 52), (162, 22), label="蓝牙 SPP", lx=-13, ly=-2, color=GREY, ls="--")

# ---- 注释 ----
ax.text(4, 95, "系统框图", fontsize=15, color=NAVY, weight="bold")
ax.text(4, 90, "50 Hz 采样 · 400 点滑动窗 · 三端同步显示", fontsize=9, color=GREY)

import os
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system-architecture.png")
plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
plt.savefig(OUT, facecolor=BG, dpi=200, bbox_inches="tight", pad_inches=0.15)
print("saved", OUT)
