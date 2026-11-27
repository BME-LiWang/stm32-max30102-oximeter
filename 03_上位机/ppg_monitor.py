#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PPG_STM32 转接板 v6 —— PC 上位机

串口 / 蓝牙虚拟 COM → 实时 PPG 波形 + SpO2 / PR / PI + 跌倒状态

通信协议（保持与现有固件一致）
    AA 55 | type | len | payload ... | sum
    0x01 (len=6) : SpO2(1) PR(1) PI*10(1) pitch+90(1) roll+90(1) state(1)
    0x02 (len=0/1) : 跌倒事件（兼容两种事件包长度）
    0x03 (len=6) : ir0[18bit BE] ir1[18bit BE]，两个连续 IR 采样点
    0x04 (len=2) : 心率报警区间 min(1) max(1)，来自 STM32 第 4 页设置

校验和：从帧头到 payload 末尾逐字节累加，取低 8 位。

安装依赖：
    py -m pip install PyQt5 pyqtgraph pyserial
运行：
    py ppg_monitor.py

显示与存储：
    主图默认显示 0.5–5 Hz 去基线带通 + 4 秒滚动 RMS 归一化 IR；
    下方小窗固定做双通道形态对比：IR 与 RED 各自 0.5–5 Hz 带通 + 4 s RMS 归一化后叠加，
    只用于观察两路脉搏波形态/相位差（血氧比值法原理），主图可切换原始 / 纯带通 / 调整后波形。
    显示滤波不参与 SpO2 / PR / PI 计算。
    CSV 使用 UTF-8 BOM，逐帧保存原始 IR、带通、归一化、采样率、指标和事件。
"""

import csv
import math
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime

# pythonw / 非标准工作目录启动时，Qt 可能找不到平台插件，这里显式兜底。
try:
    import PyQt5 as _pq_mod

    _plugin_dir = os.path.join(os.path.dirname(_pq_mod.__file__), "Qt5", "plugins")
    if os.path.isdir(_plugin_dir):
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", _plugin_dir)
        os.environ.setdefault("QT_PLUGIN_PATH", _plugin_dir)
except ImportError:
    pass

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
    import pyqtgraph as pg
    import serial
    import serial.tools.list_ports
except ImportError as exc:
    raise SystemExit(
        f"缺少运行依赖：{exc.name}\n"
        "请先执行：py -m pip install PyQt5 pyqtgraph pyserial"
    ) from exc


FRAME_HEAD = b"\xAA\x55"
FRAME_LENGTHS = {
    0x01: {6},       # 指标帧
    0x02: {0, 1},    # 跌倒事件帧，兼容原型的 len=1 与常见的 len=0
    0x03: {6, 12},   # 波形帧: 6=仅IR(旧), 12=IR+RED 双通道(新)
    0x04: {2},       # 心率区间帧: min, max（下位机设置页同步）
}
WAVE_POINTS = 1000   # 波形窗口保留的 IR 采样点数
MAX_VALUE_18BIT = 0x3FFFF
DEFAULT_SAMPLE_RATE = 50.0   # 25 个 0x03 帧/s × 每帧两个采样点 = 50Hz
FILTER_LOW_HZ = 0.5
FILTER_HIGH_HZ = 5.0
NORMALIZE_WINDOW_S = 4.0
DATA_TIMEOUT_S = 3.0
FALL_NAMES = {
    0: "正常",
    1: "失重",
    2: "冲击",
    3: "疑似跌倒",
    4: "跌倒报警",
}
VALUE_STYLE = "color:{color};font-size:25px;font-weight:600;"
PR_NORMAL_COLOR = "#76ff03"   # 与原来卡片一致的绿色
PR_ALARM_COLOR = "#ff1744"    # 超出下位机设置区间时红色
PR_IDLE_COLOR = "#8fa3b5"     # 无有效 PR 时灰色
HR_LIMIT_LO = 30
HR_LIMIT_HI = 220
HR_LIMIT_GAP = 5


def local_timestamp():
    """返回带毫秒的本地时间，供界面日志和 CSV 共用。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class BiquadFilter:
    """RBJ 二阶 IIR；高通负责去基线，低通抑制高频噪声。"""

    def __init__(self, kind, cutoff_hz, sample_rate, q=None):
        if not 0 < cutoff_hz < sample_rate / 2:
            raise ValueError("滤波截止频率必须位于 0 与奈奎斯特频率之间")
        q = q or 1.0 / math.sqrt(2.0)
        omega = 2.0 * math.pi * cutoff_hz / sample_rate
        cosine = math.cos(omega)
        sine = math.sin(omega)
        alpha = sine / (2.0 * q)

        if kind == "highpass":
            b0 = (1.0 + cosine) / 2.0
            b1 = -(1.0 + cosine)
            b2 = b0
        elif kind == "lowpass":
            b0 = (1.0 - cosine) / 2.0
            b1 = 1.0 - cosine
            b2 = b0
        else:
            raise ValueError(f"未知滤波器类型：{kind}")

        a0 = 1.0 + alpha
        self.b0 = b0 / a0
        self.b1 = b1 / a0
        self.b2 = b2 / a0
        self.a1 = (-2.0 * cosine) / a0
        self.a2 = (1.0 - alpha) / a0
        self.z1 = 0.0
        self.z2 = 0.0

    def process(self, value):
        # 转置直接 II 型，状态连续，适合逐点实时处理。
        output = self.b0 * value + self.z1
        self.z1 = self.b1 * value - self.a1 * output + self.z2
        self.z2 = self.b2 * value - self.a2 * output
        return output


class DisplayPPGProcessor:
    """
    仅用于显示与显示质量评估：0.5 Hz 高通 + 5 Hz 低通 + 滚动 RMS 归一化。
    原始值始终另行保留，SpO2 / PR / PI 不在此处重新计算或覆盖。
    """

    def __init__(self, sample_rate=DEFAULT_SAMPLE_RATE):
        if sample_rate <= FILTER_HIGH_HZ * 2:
            raise ValueError("采样率必须大于最高截止频率的两倍")
        self.sample_rate = float(sample_rate)
        self._highpass = BiquadFilter(
            "highpass", FILTER_LOW_HZ, self.sample_rate
        )
        self._lowpass = BiquadFilter(
            "lowpass", FILTER_HIGH_HZ, self.sample_rate
        )
        self._squares = deque(
            maxlen=max(8, int(round(self.sample_rate * NORMALIZE_WINDOW_S)))
        )
        self._square_sum = 0.0
        self.processed_samples = 0

    @property
    def description(self):
        return (
            f"HP={FILTER_LOW_HZ:g}Hz, LP={FILTER_HIGH_HZ:g}Hz, "
            f"RMS={NORMALIZE_WINDOW_S:g}s, Fs={self.sample_rate:g}Hz"
        )

    def process(self, raw_value):
        filtered = self._lowpass.process(
            self._highpass.process(float(raw_value))
        )

        # 当前样点也计入 RMS，避免连接/记录起点的直流瞬态被归一化放大。
        if len(self._squares) == self._squares.maxlen:
            self._square_sum -= self._squares[0]
        square = filtered * filtered
        self._squares.append(square)
        self._square_sum += square

        rms = math.sqrt(max(0.0, self._square_sum) / len(self._squares))
        scale = max(1.0, 3.0 * rms)
        normalized = filtered / scale
        self.processed_samples += 1
        return filtered, normalized

    def process_frame(self, samples):
        results = [self.process(value) for value in samples]
        filtered = [item[0] for item in results]
        normalized = [item[1] for item in results]
        return filtered, normalized


class FrameParser:
    """增量协议解析器，可处理粘包、断帧、噪声和校验错误。"""

    def __init__(self):
        self._buf = bytearray()
        self.valid_frames = 0
        self.checksum_errors = 0
        self.format_errors = 0
        self.dropped_bytes = 0

    @property
    def stats(self):
        return self.valid_frames, self.checksum_errors, self.format_errors

    def feed(self, data):
        """输入任意长度的字节流，返回 [(frame_type, payload), ...]。"""
        self._buf.extend(data)
        frames = []

        while self._buf:
            head_at = self._buf.find(FRAME_HEAD)
            if head_at < 0:
                # 末尾的 0xAA 可能是下一帧帧头的一半，必须保留。
                keep = 1 if self._buf[-1] == FRAME_HEAD[0] else 0
                drop = len(self._buf) - keep
                if drop:
                    del self._buf[:drop]
                    self.dropped_bytes += drop
                break

            if head_at:
                del self._buf[:head_at]
                self.dropped_bytes += head_at

            if len(self._buf) < 4:
                break

            frame_type = self._buf[2]
            payload_len = self._buf[3]
            allowed_lengths = FRAME_LENGTHS.get(frame_type)

            # 不认识的类型或长度不能一直等待，否则一个字节错误就会卡死后续解析。
            if allowed_lengths is None or payload_len not in allowed_lengths:
                del self._buf[0]
                self.format_errors += 1
                self.dropped_bytes += 1
                continue

            checksum_at = 4 + payload_len
            frame_len = checksum_at + 1
            if len(self._buf) < frame_len:
                break

            expected = sum(self._buf[:checksum_at]) & 0xFF
            received = self._buf[checksum_at]
            if received != expected:
                # 仅滑过一个字节，保留当前帧内部可能存在的下一个帧头。
                del self._buf[0]
                self.checksum_errors += 1
                self.dropped_bytes += 1
                continue

            payload = bytes(self._buf[4:checksum_at])
            del self._buf[:frame_len]
            self.valid_frames += 1
            frames.append((frame_type, payload))

        return frames


class SerialWorker(QtCore.QThread):
    """后台串口读取与协议分发，所有界面更新均通过 Qt 信号完成。"""

    dataFrame = QtCore.pyqtSignal(int, int, int, int, int, int)
    hrLimits = QtCore.pyqtSignal(int, int)
    fallEvent = QtCore.pyqtSignal()
    waveFrame = QtCore.pyqtSignal(list)
    waveRed = QtCore.pyqtSignal(list)
    statusMsg = QtCore.pyqtSignal(str)
    connectionChanged = QtCore.pyqtSignal(bool)
    statsUpdated = QtCore.pyqtSignal(int, int, int)

    def __init__(self, port, baud):
        super().__init__()
        self.port = port
        self.baud = baud
        self._stop_event = threading.Event()
        self._serial = None

    def stop(self):
        self._stop_event.set()
        ser = self._serial
        if ser is not None:
            try:
                ser.cancel_read()
            except (AttributeError, OSError, serial.SerialException):
                pass

    def run(self):
        parser = FrameParser()
        ser = None
        reason = "已断开"

        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.1)
            self._serial = ser
            self.statusMsg.emit(f"已连接 {self.port} @ {self.baud}")
            self.connectionChanged.emit(True)
            self.statsUpdated.emit(*parser.stats)
            last_stats_at = time.monotonic()

            while not self._stop_event.is_set():
                try:
                    waiting = ser.in_waiting
                    chunk = ser.read(max(1, min(4096, waiting)))
                except (OSError, serial.SerialException) as exc:
                    reason = f"串口读取异常：{exc}"
                    break

                if chunk:
                    for frame_type, payload in parser.feed(chunk):
                        self._dispatch(frame_type, payload)

                now = time.monotonic()
                if now - last_stats_at >= 1.0:
                    self.statsUpdated.emit(*parser.stats)
                    last_stats_at = now

        except (OSError, serial.SerialException) as exc:
            reason = f"打开串口失败：{exc}"
        except Exception as exc:  # 防止工作线程静默退出后界面一直显示“已连接”。
            reason = f"串口线程异常：{exc}"
        finally:
            self._serial = None
            if ser is not None:
                try:
                    ser.close()
                except (OSError, serial.SerialException):
                    pass
            self.statsUpdated.emit(*parser.stats)
            self.connectionChanged.emit(False)
            if reason != "已断开":
                self.statusMsg.emit(reason)
            else:
                self.statusMsg.emit("已断开")

    def _dispatch(self, frame_type, payload):
        if frame_type == 0x01:
            self.dataFrame.emit(*payload[:6])
        elif frame_type == 0x02:
            self.fallEvent.emit()
        elif frame_type == 0x04:
            if len(payload) == 2:
                self.hrLimits.emit(payload[0], payload[1])
        elif frame_type == 0x03:
            if len(payload) >= 12:
                ir0 = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                red0 = (payload[3] << 16) | (payload[4] << 8) | payload[5]
                ir1 = (payload[6] << 16) | (payload[7] << 8) | payload[8]
                red1 = (payload[9] << 16) | (payload[10] << 8) | payload[11]
                self.waveFrame.emit([ir0, ir1])
                self.waveRed.emit([red0, red1])
            else:
                ir0 = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                ir1 = (payload[3] << 16) | (payload[4] << 8) | payload[5]
                self.waveFrame.emit([ir0, ir1])


class CsvRecorder:
    """逐帧保存原始 IR、带通、归一化和指标；显示暂停或抽稀不影响写盘。"""

    HEADER = [
        "时间", "类型", "SpO2(%)", "PR(bpm)", "PI(%)",
        "Pitch(°)", "Roll(°)", "状态",
        "IR0_Raw", "IR1_Raw", "IR0_BP", "IR1_BP",
        "IR0_Norm", "IR1_Norm", "采样率(Hz)", "信号质量", "事件",
    ]

    def __init__(self, path):
        self.path = path
        self.rows = 0
        self._file = open(path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._file)
        self._writer.writerow(self.HEADER)

    def _write(self, row):
        if self._file is None:
            return
        if len(row) != len(self.HEADER):
            raise ValueError("CSV 列数不一致")
        self._writer.writerow(row)
        self.rows += 1

    def write_data(self, spo2, pr, pi10, pitch, roll, state, sample_rate, quality):
        self._write([
            local_timestamp(), "指标", spo2, pr, f"{pi10 / 10.0:.1f}",
            pitch - 90, roll - 90, FALL_NAMES.get(state, f"未知({state})"),
            "", "", "", "", "", "", f"{sample_rate:g}", quality, "",
        ])

    def write_wave(self, samples, filtered, normalized, sample_rate, quality):
        # 每个 0x03 帧写一行，两个原始值与其对应的处理结果一一对齐。
        self._write([
            local_timestamp(), "波形", "", "", "", "", "", "",
            samples[0], samples[1],
            f"{filtered[0]:.8f}", f"{filtered[1]:.8f}",
            f"{normalized[0]:.8f}", f"{normalized[1]:.8f}",
            f"{sample_rate:g}", quality, "",
        ])

    def write_event(self, message, sample_rate, quality):
        self._write([
            local_timestamp(), "事件", "", "", "", "", "", "",
            "", "", "", "", "", "", f"{sample_rate:g}", quality, message,
        ])

    def flush(self):
        if self._file is not None:
            self._file.flush()

    def close(self):
        if self._file is not None:
            file_obj = self._file
            self._file = None
            try:
                file_obj.flush()
            finally:
                file_obj.close()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PPG_STM32 v6 上位机 — 血氧与跌倒监测")
        self.resize(1320, 900)
        self.setMinimumSize(1080, 740)

        self.worker = None
        self.recorder = None
        self.raw_ir_data = deque(maxlen=WAVE_POINTS)
        self.raw_red_data = deque(maxlen=WAVE_POINTS)
        self.filtered_ir_data = deque(maxlen=WAVE_POINTS)
        self.normalized_ir_data = deque(maxlen=WAVE_POINTS)
        # 红光通道单独一套显示处理（同一 0.5–5 Hz 带通 + 4 s 滚动 RMS 归一化），
        # 只服务副图的双通道形态对比，绝不参与 SpO2 / PR / PI 计算。
        self.red_filtered_data = deque(maxlen=WAVE_POINTS)
        self.red_normalized_data = deque(maxlen=WAVE_POINTS)
        self.processor = DisplayPPGProcessor()
        self.red_processor = DisplayPPGProcessor()
        self._red_hist = []
        self._last_finger = None
        self._wave_dirty = False
        self._connected = False
        self._close_after_worker = False
        self._last_data_at = 0.0
        self._last_wave_at = 0.0
        self._latest_pi10 = None
        self._last_pr = 0
        self.hr_min = 60
        self.hr_max = 100
        self._hr_limits_seen = False
        self._last_pr_color = None
        self._quality = "等待信号"
        self._quality_reason = "等待足够的原始样点"
        self._quality_view_override = False
        self._view_before_quality = "filtered"
        self._changing_view = False
        self._wave_total = 0
        self._wave_last_total = 0
        self._wave_rate = 0.0
        self._last_rate_at = time.monotonic()
        self._valid_frames = 0
        self._checksum_errors = 0
        self._format_errors = 0

        self._build_ui()
        self._refresh_ports()
        self._reset_monitor_values()
        self._log("程序就绪，请选择串口并连接。")

    # ---------- 界面 ----------
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(9)

        connection_bar = QtWidgets.QHBoxLayout()
        connection_bar.addWidget(QtWidgets.QLabel("串口"))
        self.cmb_port = QtWidgets.QComboBox()
        self.cmb_port.setMinimumWidth(340)
        self.cmb_port.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        connection_bar.addWidget(self.cmb_port, 1)

        self.btn_refresh = QtWidgets.QPushButton("刷新")
        self.btn_refresh.clicked.connect(self._refresh_ports)
        connection_bar.addWidget(self.btn_refresh)

        connection_bar.addWidget(QtWidgets.QLabel("波特率"))
        self.cmb_baud = QtWidgets.QComboBox()
        self.cmb_baud.addItems([
            "9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600",
        ])
        connection_bar.addWidget(self.cmb_baud)

        self.btn_conn = QtWidgets.QPushButton("连接")
        self.btn_conn.setCheckable(True)
        self.btn_conn.setMinimumWidth(92)
        self.btn_conn.clicked.connect(self._toggle_conn)
        connection_bar.addWidget(self.btn_conn)
        root.addLayout(connection_bar)

        action_bar = QtWidgets.QHBoxLayout()
        self.btn_csv = QtWidgets.QPushButton("开始记录")
        self.btn_csv.setCheckable(True)
        self.btn_csv.clicked.connect(self._toggle_csv)
        action_bar.addWidget(self.btn_csv)

        action_bar.addWidget(QtWidgets.QLabel("主图"))
        self.cmb_view = QtWidgets.QComboBox()
        self.cmb_view.addItem("调整后 IR（带通＋归一化）", "filtered")
        self.cmb_view.addItem("带通 IR（未归一化）", "bandpass")
        self.cmb_view.addItem("原始 IR", "raw")
        self.cmb_view.setMinimumWidth(220)
        self.cmb_view.currentIndexChanged.connect(self._on_main_view_changed)
        action_bar.addWidget(self.cmb_view)

        self.btn_pause = QtWidgets.QPushButton("暂停波形")
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self._toggle_pause)
        action_bar.addWidget(self.btn_pause)

        self.btn_clear = QtWidgets.QPushButton("清空波形")
        self.btn_clear.clicked.connect(self._clear_wave)
        action_bar.addWidget(self.btn_clear)

        self.chk_auto = QtWidgets.QCheckBox("Y 轴自适应")
        self.chk_auto.setChecked(True)
        action_bar.addWidget(self.chk_auto)
        action_bar.addSpacing(12)

        self.lbl_record = QtWidgets.QLabel("未记录")
        self.lbl_record.setStyleSheet("color:#8fa3b5;")
        action_bar.addWidget(self.lbl_record)
        action_bar.addStretch(1)
        root.addLayout(action_bar)

        processing_bar = QtWidgets.QHBoxLayout()
        self.chk_auto_raw = QtWidgets.QCheckBox("信号质量差时主图自动切原始")
        self.chk_auto_raw.setChecked(True)
        self.chk_auto_raw.toggled.connect(self._on_auto_raw_toggled)
        processing_bar.addWidget(self.chk_auto_raw)
        processing_bar.addSpacing(12)
        processing_bar.addWidget(QtWidgets.QLabel("IR 采样率"))
        self.cmb_fs = QtWidgets.QComboBox()
        self.cmb_fs.addItems(["50", "100", "200"])
        self.cmb_fs.setCurrentText(str(int(DEFAULT_SAMPLE_RATE)))
        self.cmb_fs.setToolTip("当前协议按 50 帧/s、每帧两个连续 IR 点计算，默认 100 Hz")
        self.cmb_fs.currentTextChanged.connect(self._on_sample_rate_changed)
        processing_bar.addWidget(self.cmb_fs)
        processing_bar.addWidget(QtWidgets.QLabel("Hz"))
        processing_bar.addSpacing(12)
        filter_note = QtWidgets.QLabel("显示滤波不参与 SpO₂ / PR / PI 计算；原始小窗与原始记录始终保留")
        filter_note.setStyleSheet("color:#8fa3b5;")
        processing_bar.addWidget(filter_note)
        processing_bar.addStretch(1)
        root.addLayout(processing_bar)

        cards = QtWidgets.QHBoxLayout()
        cards.setSpacing(8)
        self.lbl_spo2 = self._make_card(cards, "血氧 SpO₂", "-- %", "#00e5ff")
        self.lbl_pr = self._make_card(cards, "脉率 PR", "-- bpm", "#76ff03")
        self.lbl_pr.setToolTip("正常区间 60–100 bpm（随 STM32 设置页自动同步）")
        self.lbl_pi = self._make_card(cards, "灌注指数 PI", "-- %", "#ffd600")
        self.lbl_quality = self._make_card(cards, "信号质量（显示辅助）", "等待信号", "#8fa3b5")
        self.lbl_state = self._make_card(cards, "跌倒状态", "--", "#00e676")
        self.lbl_pitch = self._make_card_pitch(cards)
        root.addLayout(cards)

        self.lbl_alarm = QtWidgets.QLabel("检测到跌倒事件，请立即核实佩戴者状态！")
        self.lbl_alarm.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_alarm.setStyleSheet(
            "background:#5b1118;color:#ff8a96;border:1px solid #ff1744;"
            "border-radius:6px;padding:8px;font-size:15px;font-weight:600;"
        )
        self.lbl_alarm.hide()
        root.addWidget(self.lbl_alarm)

        pg.setConfigOptions(antialias=False, background="#101418", foreground="#c8d0d8")
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "采样点")
        self.plot.setXRange(0, WAVE_POINTS - 1, padding=0)
        self.plot.setYRange(-1.2, 1.2, padding=0.02)
        self.plot.setMouseEnabled(x=True, y=True)
        self.curve = self.plot.plot(pen=pg.mkPen("#00e5ff", width=1.35))
        self.curve.setClipToView(True)
        self.curve.setDownsampling(auto=True, method="peak")
        root.addWidget(self.plot, 1)

        # 原始小窗始终存在，主图切到滤波后也不会隐藏硬件调试所需的原始信号。
        self.raw_plot = pg.PlotWidget()
        self.raw_plot.setTitle("双通道形态对比（0.5–5 Hz 带通＋4 s RMS 归一化｜橙=IR，红=RED）", size="10pt")
        self.raw_plot.showGrid(x=True, y=True, alpha=0.20)
        self.raw_plot.setLabel("left", "归一化幅度（±1 = 4 s RMS 的 3 倍）")
        self.raw_plot.setLabel("bottom", "采样点")
        self.raw_plot.setXRange(0, WAVE_POINTS - 1, padding=0)
        self.raw_plot.setYRange(-0.45, 0.45, padding=0.02)
        self.raw_plot.setMinimumHeight(145)
        self.raw_plot.setMaximumHeight(175)
        self.raw_plot.setXLink(self.plot)
        self.raw_curve = self.raw_plot.plot(pen=pg.mkPen("#ffab40", width=1.1))
        self.red_curve = self.raw_plot.plot(pen=pg.mkPen("#ff1744", width=1.3))
        self.raw_curve.setClipToView(True)
        self.red_curve.setClipToView(True)
        self.raw_curve.setDownsampling(auto=True, method="peak")
        self.red_curve.setDownsampling(auto=True, method="peak")
        root.addWidget(self.raw_plot)
        self._apply_main_view()

        info_bar = QtWidgets.QHBoxLayout()
        self.lbl_stats = QtWidgets.QLabel("波形 0 帧/s｜有效帧 0｜校验错误 0｜格式错误 0")
        self.lbl_stats.setStyleSheet("color:#8fa3b5;")
        info_bar.addWidget(self.lbl_stats)
        info_bar.addStretch(1)
        self.lbl_data_age = QtWidgets.QLabel("等待连接")
        self.lbl_data_age.setStyleSheet("color:#8fa3b5;")
        info_bar.addWidget(self.lbl_data_age)
        root.addLayout(info_bar)

        log_box = QtWidgets.QGroupBox("事件日志")
        log_layout = QtWidgets.QVBoxLayout(log_box)
        log_layout.setContentsMargins(8, 8, 8, 8)
        self.event_log = QtWidgets.QPlainTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(300)
        self.event_log.setFixedHeight(104)
        self.event_log.setPlaceholderText("连接、断开、存盘与跌倒事件将在这里显示")
        log_layout.addWidget(self.event_log)
        root.addWidget(log_box)

        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("就绪")

        self.redraw_timer = QtCore.QTimer(self)
        self.redraw_timer.timeout.connect(self._redraw)
        self.redraw_timer.start(40)  # 25 FPS，串口接收不被绘图阻塞。

        self.housekeeping_timer = QtCore.QTimer(self)
        self.housekeeping_timer.timeout.connect(self._on_housekeeping)
        self.housekeeping_timer.start(1000)

    def _make_card(self, layout, title, value, color):
        box = QtWidgets.QGroupBox(title)
        box.setStyleSheet(
            "QGroupBox{border:1px solid #2a3540;border-radius:7px;margin-top:8px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;"
            "color:#8fa3b5;}"
        )
        vertical = QtWidgets.QVBoxLayout(box)
        label = QtWidgets.QLabel(value)
        label.setStyleSheet(VALUE_STYLE.format(color=color))
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setMinimumHeight(42)
        vertical.addWidget(label)
        layout.addWidget(box, 1)
        return label

    def _make_card_pitch(self, layout):
        """姿态卡片：上方是"模拟跌倒"按钮，下方是 Pitch / Roll 数值。"""
        box = QtWidgets.QGroupBox("姿态 / 跌倒模拟")
        box.setStyleSheet(
            "QGroupBox{border:1px solid #2a3540;border-radius:7px;margin-top:8px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;"
            "color:#8fa3b5;}"
        )
        vertical = QtWidgets.QVBoxLayout(box)
        vertical.setSpacing(3)
        vertical.setContentsMargins(6, 4, 6, 4)

        self.btn_fall = QtWidgets.QPushButton("模拟跌倒")
        self.btn_fall.setCheckable(True)
        self.btn_fall.setMinimumHeight(24)
        self.btn_fall.setStyleSheet(
            "QPushButton{background:#2a3540;color:#d6dee6;border:1px solid #3a4757;"
            "border-radius:5px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#33404e;}"
            "QPushButton:checked{background:#5b1118;color:#ff8a96;border:1px solid #ff1744;}"
        )
        self.btn_fall.setToolTip("按下触发跌倒报警（蜂鸣器响），再按一次清除报警")
        self.btn_fall.clicked.connect(self._on_fall_sim_clicked)
        vertical.addWidget(self.btn_fall)

        label = QtWidgets.QLabel("-- / --")
        label.setStyleSheet(VALUE_STYLE.format(color="#b388ff"))
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setMinimumHeight(26)
        vertical.addWidget(label)
        layout.addWidget(box, 1)
        return label

    def _on_fall_sim_clicked(self, checked):
        """发送 'F' 给固件：报警中收到即清除，空闲收到即进入报警。"""
        worker = self.worker
        ser = getattr(worker, "_serial", None) if worker is not None else None
        if ser is None:
            self.btn_fall.setChecked(not checked)
            self.statusBar().showMessage("串口未连接，无法发送模拟跌倒命令", 4000)
            return
        try:
            ser.write(b"FFFF")
            ser.flush()
        except (OSError, serial.SerialException) as exc:
            self.btn_fall.setChecked(not checked)
            self.statusBar().showMessage(f"发送失败：{exc}", 4000)
            return
        if checked:
            self.btn_fall.setText("清除报警")
            self.statusBar().showMessage("已触发模拟跌倒：蜂鸣器报警中，再按一次清除", 6000)
        else:
            self.btn_fall.setText("模拟跌倒")
            self.statusBar().showMessage("已发送清除报警命令", 4000)

    def _apply_style(self):
        self.setStyleSheet(
            "QMainWindow,QWidget{background:#151a20;color:#d6dee6;}"
            "QComboBox,QPlainTextEdit{background:#1d252d;border:1px solid #35424e;"
            "border-radius:4px;padding:5px;selection-background-color:#16658a;}"
            "QComboBox QAbstractItemView{background:#1d252d;color:#d6dee6;"
            "selection-background-color:#16658a;}"
            "QPushButton{background:#24313b;border:1px solid #3b4b58;border-radius:5px;"
            "padding:7px 13px;}"
            "QPushButton:hover{background:#2d3e4b;}"
            "QPushButton:checked{background:#125a78;border-color:#00a5cf;}"
            "QPushButton:disabled{color:#6e7b86;background:#1b2229;}"
            "QCheckBox{color:#b7c5d0;}"
            "QGroupBox{font-weight:500;}"
        )

    # ---------- 串口与连接状态 ----------
    def _refresh_ports(self):
        previous = self.cmb_port.currentData()
        self.cmb_port.clear()
        ports = sorted(serial.tools.list_ports.comports(), key=lambda item: item.device)
        for port in ports:
            description = port.description or "未知设备"
            self.cmb_port.addItem(f"{port.device}  ({description})", port.device)
        if ports:
            previous_index = self.cmb_port.findData(previous)
            if previous_index >= 0:
                self.cmb_port.setCurrentIndex(previous_index)
        else:
            self.cmb_port.addItem("未发现串口，请检查 USB 或蓝牙配对", None)
        self.status.showMessage(f"发现 {len(ports)} 个串口", 3000)

    def _toggle_conn(self, checked):
        if not checked:
            self._request_disconnect()
            return

        if self.worker is not None:
            self.btn_conn.setChecked(True)
            return

        port = self.cmb_port.currentData()
        if not port:
            self.btn_conn.setChecked(False)
            self.status.showMessage("没有可用串口")
            return

        baud = int(self.cmb_baud.currentText())
        self._reset_monitor_values()
        worker = SerialWorker(port, baud)
        self.worker = worker
        worker.dataFrame.connect(self._on_data)
        worker.hrLimits.connect(self._on_hr_limits)
        worker.waveFrame.connect(self._on_wave)
        worker.waveRed.connect(self._on_wave_red)
        worker.fallEvent.connect(self._on_fall)
        worker.statusMsg.connect(self._on_status)
        worker.connectionChanged.connect(self._on_connection_changed)
        worker.statsUpdated.connect(self._on_stats)
        worker.finished.connect(self._on_worker_finished)

        self.btn_conn.setText("连接中…")
        self.btn_conn.setEnabled(False)
        self._set_port_controls_enabled(False)
        self._log(f"正在连接 {port} @ {baud}…")
        worker.start()

    def _request_disconnect(self):
        self.btn_conn.setChecked(False)
        self.btn_conn.setEnabled(False)
        self.btn_conn.setText("断开中…")
        if self.worker is not None:
            self.worker.stop()
            self.status.showMessage("正在断开…")
        else:
            self._on_worker_finished()

    def _on_connection_changed(self, connected):
        self._connected = connected
        if connected:
            self.btn_conn.setChecked(True)
            self.btn_conn.setText("断开")
            self.btn_conn.setEnabled(True)
            self._set_port_controls_enabled(False)
            self._log("串口连接成功。")
        else:
            self.btn_conn.setChecked(False)
            self.btn_conn.setText("连接")
            self._set_port_controls_enabled(True)
            self.lbl_data_age.setText("连接已断开")
            self.lbl_data_age.setStyleSheet("color:#ff8a96;")
            self._wave_rate = 0.0

    def _on_worker_finished(self):
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()

        self._connected = False
        self.btn_conn.setChecked(False)
        self.btn_conn.setText("连接")
        self.btn_conn.setEnabled(True)
        self._set_port_controls_enabled(True)
        self._update_stats_label()

        if self._close_after_worker:
            QtCore.QTimer.singleShot(0, self.close)

    def _set_port_controls_enabled(self, enabled):
        self.cmb_port.setEnabled(enabled)
        self.cmb_baud.setEnabled(enabled)
        self.btn_refresh.setEnabled(enabled)

    def _on_status(self, message):
        self.status.showMessage(message)
        self._log(message)

    # ---------- 数据显示与显示预处理 ----------
    def _sample_rate(self):
        try:
            return float(self.cmb_fs.currentText())
        except (TypeError, ValueError):
            return DEFAULT_SAMPLE_RATE

    def _reset_processing(self):
        self.raw_red_data.clear()
        self.red_filtered_data.clear()
        self.red_normalized_data.clear()
        self._red_hist = []
        self.processor = DisplayPPGProcessor(self._sample_rate())
        self.red_processor = DisplayPPGProcessor(self._sample_rate())
        self.raw_ir_data.clear()
        self.filtered_ir_data.clear()
        self.normalized_ir_data.clear()
        self._last_wave_at = 0.0
        self._wave_dirty = True
        self.curve.setData([])
        self.raw_curve.setData([])
        self.red_curve.setData([])
        self._quality = "等待信号"
        self._quality_reason = "等待足够的原始样点"
        self.lbl_quality.setText(self._quality)
        self.lbl_quality.setStyleSheet(VALUE_STYLE.format(color="#8fa3b5"))
        self.lbl_quality.setToolTip(self._quality_reason)

    def _on_sample_rate_changed(self, text):
        try:
            sample_rate = float(text)
            if sample_rate <= FILTER_HIGH_HZ * 2:
                raise ValueError("采样率过低")
        except (TypeError, ValueError):
            return
        self._reset_processing()
        self._quality_view_override = False
        self._set_main_view("filtered")
        message = f"IR 采样率已改为 {sample_rate:g} Hz，显示滤波器已复位"
        self._log(message)
        self._csv_call(
            "write_event", message, sample_rate, self._quality
        )

    def _on_main_view_changed(self):
        if self._changing_view:
            return
        # 用户手动选择后，不再在一次质量恢复时强行替用户切回。
        self._quality_view_override = False
        self._apply_main_view()
        self._redraw()

    def _set_main_view(self, mode, automatic=False):
        index = self.cmb_view.findData(mode)
        if index < 0:
            return
        self._changing_view = True
        self.cmb_view.setCurrentIndex(index)
        self._changing_view = False
        self._apply_main_view()
        if automatic:
            self._log("信号质量差，主图已自动切换为原始 IR；原始小窗保持显示。")

    def _apply_main_view(self):
        mode = self.cmb_view.currentData()
        if mode == "raw":
            title = "原始红外 PPG（主图）｜未滤波、未归一化"
            y_label = "原始 IR ADC"
            color = "#ffab40"
            default_range = (0.0, float(MAX_VALUE_18BIT))
        elif mode == "bandpass":
            title = "带通红外 PPG｜0.5–5 Hz 去基线（未归一化）"
            y_label = "带通后 ADC 变化量"
            color = "#76ff03"
            default_range = (-1000.0, 1000.0)
        else:
            title = "调整后红外 PPG｜0.5–5 Hz 去基线带通＋4 s 滚动 RMS 归一化"
            y_label = "归一化幅值"
            color = "#00e5ff"
            default_range = (-1.2, 1.2)

        self.plot.setTitle(title, size="11pt")
        self.plot.setLabel("left", y_label)
        self.curve.setPen(pg.mkPen(color, width=1.35))
        if not self.chk_auto.isChecked():
            self.plot.setYRange(*default_range, padding=0)
        self._wave_dirty = True

    def _reset_monitor_values(self):
        self.lbl_spo2.setText("-- %")
        self.lbl_pr.setText("-- bpm")
        self.lbl_pi.setText("-- %")
        self._last_pr = 0
        self._apply_pr_color()
        self.lbl_state.setText("--")
        self.lbl_state.setStyleSheet(VALUE_STYLE.format(color="#00e676"))
        self.lbl_pitch.setText("-- / --")
        self.lbl_alarm.hide()
        self._quality_view_override = False
        self._view_before_quality = "filtered"
        self._set_main_view("filtered")
        self._reset_processing()
        self._latest_pi10 = None
        self._last_data_at = 0.0
        self._wave_total = 0
        self._wave_last_total = 0
        self._wave_rate = 0.0
        self._last_rate_at = time.monotonic()
        self._valid_frames = 0
        self._checksum_errors = 0
        self._format_errors = 0
        self.lbl_data_age.setText("等待数据" if self.worker else "等待连接")
        self.lbl_data_age.setStyleSheet("color:#8fa3b5;")
        self._update_stats_label()

    def _on_data(self, spo2, pr, pi10, pitch, roll, state):
        self._last_data_at = time.monotonic()
        self._latest_pi10 = pi10
        self._last_pr = pr
        self.lbl_spo2.setText(f"{spo2} %" if spo2 > 0 else "-- %")
        self.lbl_pr.setText(f"{pr} bpm" if pr > 0 else "-- bpm")
        self._apply_pr_color()
        self.lbl_pi.setText(f"{pi10 / 10.0:.1f} %")
        self.lbl_pitch.setText(f"{pitch - 90}° / {roll - 90}°")

        name = FALL_NAMES.get(state, f"未知({state})")
        self.lbl_state.setText(name)
        if state == 4:
            color = "#ff1744"
            self._show_alarm("跌倒状态 ALARM：请立即核实佩戴者状态！", force=False)
        elif state in (1, 2, 3):
            color = "#ff9100"
        else:
            color = "#00e676" if state == 0 else "#8fa3b5"
            if state == 0:
                self.lbl_alarm.hide()
        self.lbl_state.setStyleSheet(VALUE_STYLE.format(color=color))

        self._csv_call(
            "write_data", spo2, pr, pi10, pitch, roll, state,
            self.processor.sample_rate, self._quality
        )

    def _on_hr_limits(self, hr_min, hr_max):
        """接收 STM32 设置页的 Min/Max，PR 颜色随区间实时变化。"""
        if not (
            HR_LIMIT_LO <= hr_min <= HR_LIMIT_HI
            and HR_LIMIT_LO <= hr_max <= HR_LIMIT_HI
            and hr_max - hr_min >= HR_LIMIT_GAP
        ):
            return

        changed = (not self._hr_limits_seen) or (
            hr_min != self.hr_min or hr_max != self.hr_max
        )
        self.hr_min = hr_min
        self.hr_max = hr_max
        self._hr_limits_seen = True
        self.lbl_pr.setToolTip(
            f"正常区间 {hr_min}–{hr_max} bpm（随 STM32 设置页自动同步）"
        )
        if changed:
            self._log(f"心率报警区间已同步：{hr_min}–{hr_max} bpm")
        self._apply_pr_color()

    def _apply_pr_color(self):
        if self._last_pr <= 0:
            color = PR_IDLE_COLOR
        elif self._last_pr < self.hr_min or self._last_pr > self.hr_max:
            color = PR_ALARM_COLOR
        else:
            color = PR_NORMAL_COLOR
        if color == self._last_pr_color:
            return
        self._last_pr_color = color
        self.lbl_pr.setStyleSheet(VALUE_STYLE.format(color=color))

    def _on_wave(self, samples):
        # 原始样点先入独立缓冲区；处理结果另存，绝不覆盖原始值。
        self.raw_ir_data.extend(samples)
        filtered, normalized = self.processor.process_frame(samples)
        self.filtered_ir_data.extend(filtered)
        self.normalized_ir_data.extend(normalized)
        self._wave_total += 1
        self._last_wave_at = time.monotonic()
        self._wave_dirty = True
        # 无论主图模式、暂停状态或绘图降采样如何，每个原始样点均写入 CSV。
        self._csv_call(
            "write_wave", samples, filtered, normalized,
            self.processor.sample_rate, self._quality
        )

    def _on_wave_red(self, samples):
        # 红光通道仅做显示用带通 + 归一化，不参与 SpO2 / PR / PI。
        self.raw_red_data.extend(samples)
        # 固件只给 IR 修了 0xFF 坏字节，RED 没有；这里用 3 点中值滤波清掉孤立坏点。
        # 注意不能用"相邻差超阈值就保持上一点"的限幅：手指按上去时 RED 直流会从
        # 空载 1864 跳到 183000（差 18 万），限幅会把真实阶跃当坏点锁死，结果滤波后恒 0。
        pool = self._red_hist
        clean = []
        for value in samples:
            pool.append(value)
            if len(pool) > 3:
                pool.pop(0)
            clean.append(sorted(pool)[1] if len(pool) >= 2 else value)
        filtered, normalized = self.red_processor.process_frame(clean)
        self.red_filtered_data.extend(filtered)
        self.red_normalized_data.extend(normalized)
        self._wave_dirty = True

    def _on_fall(self):
        self._show_alarm("收到跌倒事件包：请立即核实佩戴者状态！", force=True)

    def _show_alarm(self, message, force):
        if not force and self.lbl_alarm.isVisible():
            return
        self.lbl_alarm.setText(message)
        self.lbl_alarm.show()
        self._log(message)
        self._csv_call(
            "write_event", message, self.processor.sample_rate, self._quality
        )
        QtWidgets.QApplication.beep()

    def _redraw(self):
        if not self._wave_dirty or self.btn_pause.isChecked():
            return
        self._wave_dirty = False
        raw_values = list(self.raw_ir_data)
        mode = self.cmb_view.currentData()
        if mode == "raw":
            main_values = raw_values
        elif mode == "bandpass":
            main_values = list(self.filtered_ir_data)
        else:
            main_values = list(self.normalized_ir_data)

        self.curve.setData(main_values)

        # 双通道形态对比（带通 + RMS 归一化版）：
        # 两条线都取 0.5–5 Hz 带通 + 4 s 滚动 RMS 归一化后的曲线，中轴都是 0、量级都在 ±0.3，
        # DC 漂移（手指按压导致的长斜坡）和高频噪声都不再参与比较。
        # 剩下的差异就是红光/红外的真实形态与相位差 —— 血氧比值法
        # ratio = (RED_AC/RED_DC)/(IR_AC/IR_DC) 真正关心的量。
        # 注意：早期版本直接在原始计数上对齐（只平移均值 / 或连幅度一起归一），
        # 低灌注（PI<1%）时会把两路不相关噪声放大成"锯齿交叉"，这里已彻底绕开原始计数。
        # 空载（没放手指）时必须压成贴中轴的平线：RMS 归一化对噪声同样会归一化，
        # 若照常绘制，几十计数的空载噪声会被放大到满幅抖动，看着"像有心跳"。
        # 判据与固件一致：最近 60 个 IR 原始样本均值 > 80000 才算放上手指。
        finger = self._finger_present()
        if finger:
            ir_cmp = self._smooth(list(self.normalized_ir_data))
            red_cmp = self._smooth(list(self.red_normalized_data))
        else:
            n = max(len(self.normalized_ir_data), len(self.red_normalized_data))
            ir_cmp = [0.0] * n
            red_cmp = [0.0] * n
        self.raw_curve.setData(ir_cmp)
        self.red_curve.setData(red_cmp)

        if finger != self._last_finger:
            self._last_finger = finger
            self.raw_plot.setTitle(
                "双通道形态对比（0.5–5 Hz 带通＋4 s RMS 归一化｜橙=IR，红=RED）"
                if finger
                else "空载：两路归一化输出均为 0（两条重合在中轴，红线覆盖橙线）",
                size="10pt",
            )

        if self.chk_auto.isChecked():
            if main_values:
                self._auto_y_range(self.plot, main_values, raw=(mode == "raw"))
            if finger:
                both = ir_cmp + red_cmp
                if both:
                    self._auto_y_range(self.raw_plot, both, raw=False)
        if not finger:
            # 空载时固定量程，避免上一次有手指时的自适应量程把平线撑满窗口
            self.raw_plot.setYRange(-0.45, 0.45, padding=0.02)

    def _finger_present(self):
        """与固件同一口径判手指：IR 原始 ADC 均值 > 80000（空载 1864~8798，按压 19 万+）。"""
        if len(self.raw_ir_data) < 30:
            return False
        recent = list(self.raw_ir_data)[-60:]
        return sum(recent) / len(recent) > 80000.0

    @staticmethod
    def _smooth(values, tap=3):
        """对称三点滑动平均，仅用于副图显示（压掉红光通道的高频毛边）。"""
        if tap < 3 or len(values) < tap:
            return values
        half = tap // 2
        out = list(values)
        for i in range(half, len(values) - half):
            acc = 0.0
            for k in range(-half, half + 1):
                acc += values[i + k]
            out[i] = acc / tap
        return out

    @staticmethod
    def _auto_y_range(plot, values, raw=False):
        minimum = min(values)
        maximum = max(values)
        span = maximum - minimum
        if raw:
            padding = max(2.0, span * 0.08)
            lower = max(0.0, minimum - padding)
            upper = maximum + padding
        else:
            padding = max(0.05, span * 0.08)
            lower = minimum - padding
            upper = maximum + padding
        if upper <= lower:
            upper = lower + 1.0
        plot.setYRange(lower, upper, padding=0)

    def _toggle_pause(self, paused):
        self.btn_pause.setText("继续波形" if paused else "暂停波形")
        self._log(
            "两张波形图已暂停（预处理、串口接收与完整 CSV 记录继续）。"
            if paused else "两张波形图已继续。"
        )
        if not paused:
            self._wave_dirty = True
            self._redraw()

    def _clear_wave(self):
        self.raw_ir_data.clear()
        self.raw_red_data.clear()
        self.filtered_ir_data.clear()
        self.normalized_ir_data.clear()
        self.red_filtered_data.clear()
        self.red_normalized_data.clear()
        self._wave_dirty = False
        self.curve.setData([])
        self.raw_curve.setData([])
        self.red_curve.setData([])
        self._log("波形显示窗口已清空；滤波器状态和 CSV 记录不受影响。")

    def _assess_signal_quality(self, now):
        """显示辅助判据，不替代固件算法，也不用于临床判断。"""
        if not self._connected:
            return "等待信号", "尚未连接串口", "#8fa3b5", False
        if self._last_wave_at and now - self._last_wave_at > DATA_TIMEOUT_S:
            return "质量差", "波形超过 3 秒未更新", "#ff1744", True

        minimum_samples = max(8, int(self.processor.sample_rate * 2))
        if len(self.raw_ir_data) < minimum_samples:
            return "等待信号", "正在积累至少 2 秒原始样点", "#8fa3b5", False

        window_size = min(
            len(self.raw_ir_data), int(self.processor.sample_rate * 4)
        )
        raw = list(self.raw_ir_data)[-window_size:]
        low_rail = sum(value <= 2 for value in raw)
        high_rail = sum(value >= MAX_VALUE_18BIT - 2 for value in raw)
        rail_ratio = (low_rail + high_rail) / len(raw)
        peak_to_peak = max(raw) - min(raw)

        if rail_ratio >= 0.01:
            return "质量差", f"原始信号触轨/饱和样点占 {rail_ratio:.1%}", "#ff1744", True
        if peak_to_peak < 4:
            return "质量差", "原始信号接近直线，可能未检测到手指或 LED 异常", "#ff1744", True

        pi_fresh = (
            self._latest_pi10 is not None
            and self._last_data_at
            and now - self._last_data_at <= DATA_TIMEOUT_S
        )
        if pi_fresh and self._latest_pi10 < 2:
            return "质量差", "PI < 0.2%，灌注偏弱或无有效脉搏", "#ff1744", True
        if rail_ratio > 0:
            return "一般", "原始信号存在少量触轨样点", "#ff9100", False
        if pi_fresh and self._latest_pi10 < 5:
            return "一般", "PI < 0.5%，建议减少运动并检查佩戴", "#ff9100", False
        if peak_to_peak < 8:
            return "一般", "原始 AC 变化较小，建议结合原始小窗检查", "#ff9100", False
        return "未见异常", "未发现触轨、平线、低 PI 或数据中断", "#00e676", False

    def _update_signal_quality(self):
        quality, reason, color, poor = self._assess_signal_quality(time.monotonic())
        previous = self._quality
        previous_reason = self._quality_reason
        self._quality = quality
        self._quality_reason = reason
        self.lbl_quality.setText(quality)
        self.lbl_quality.setStyleSheet(VALUE_STYLE.format(color=color))
        self.lbl_quality.setToolTip(reason)

        if quality != previous or reason != previous_reason:
            self._log(f"信号质量：{quality}；{reason}")
            self._csv_call(
                "write_event", f"信号质量：{quality}；{reason}",
                self.processor.sample_rate, quality
            )
        if poor:
            self.status.showMessage(f"信号质量差：{reason}", 5000)

        if poor and self.chk_auto_raw.isChecked() and self.cmb_view.currentData() != "raw":
            if not self._quality_view_override:
                self._view_before_quality = self.cmb_view.currentData()
            self._quality_view_override = True
            self._set_main_view("raw", automatic=True)
        elif not poor and quality in ("一般", "未见异常") and self._quality_view_override:
            self._quality_view_override = False
            self._set_main_view(self._view_before_quality)
            self._log("信号质量恢复，主图已恢复此前的显示模式。")

    def _on_auto_raw_toggled(self, checked):
        if not checked and self._quality_view_override:
            self._quality_view_override = False
            self._set_main_view(self._view_before_quality)
            self._log("已关闭质量差自动切换，恢复用户此前的主图模式。")

    # ---------- 统计、超时与存盘维护 ----------
    def _on_stats(self, valid, checksum_errors, format_errors):
        self._valid_frames = valid
        self._checksum_errors = checksum_errors
        self._format_errors = format_errors
        self._update_stats_label()

    def _update_stats_label(self):
        self.lbl_stats.setText(
            f"波形 {self._wave_rate:.0f} 帧/s（累计 {self._wave_total}）｜"
            f"有效帧 {self._valid_frames}｜校验错误 {self._checksum_errors}｜"
            f"格式错误 {self._format_errors}"
        )

    def _on_housekeeping(self):
        now = time.monotonic()
        elapsed = max(0.001, now - self._last_rate_at)
        self._wave_rate = (self._wave_total - self._wave_last_total) / elapsed
        self._wave_last_total = self._wave_total
        self._last_rate_at = now
        self._update_stats_label()

        if self._connected:
            if not self._last_data_at:
                self.lbl_data_age.setText("指标：等待首帧")
                self.lbl_data_age.setStyleSheet("color:#8fa3b5;")
            else:
                age = now - self._last_data_at
                self.lbl_data_age.setText(f"指标：{age:.0f} 秒前更新")
                color = "#ff8a96" if age > DATA_TIMEOUT_S else "#8fa3b5"
                self.lbl_data_age.setStyleSheet(f"color:{color};")

        self._update_signal_quality()

        if self.recorder is not None:
            self._csv_call("flush")
            if self.recorder is not None:
                self.lbl_record.setText(f"已写入 {self.recorder.rows} 行")

    # ---------- CSV ----------
    def _toggle_csv(self, checked):
        if not checked:
            self._stop_recording()
            return

        default_name = f"ppg_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "保存全部监测数据", default_name, "CSV (*.csv)"
        )
        if not path:
            self.btn_csv.setChecked(False)
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        try:
            self.recorder = CsvRecorder(path)
        except OSError as exc:
            self.recorder = None
            self.btn_csv.setChecked(False)
            QtWidgets.QMessageBox.warning(self, "无法开始记录", str(exc))
            return

        # 记录起点同时复位显示滤波器，确保 CSV 中的原始与处理序列可从同一状态复现。
        self._reset_processing()
        self.btn_csv.setText("停止记录")
        self.lbl_record.setText("已写入 0 行")
        self.status.showMessage(f"正在记录：{path}")
        self._log(f"开始完整记录：{path}")
        self._csv_call(
            "write_event",
            f"记录开始；原始 IR 逐帧完整保存；滤波器零状态复位；{self.processor.description}",
            self.processor.sample_rate,
            self._quality,
        )

    def _stop_recording(self):
        recorder = self.recorder
        self.recorder = None
        error = None
        if recorder is not None:
            try:
                recorder.close()
                self._log(f"记录已保存：{recorder.path}（{recorder.rows} 行）")
                self.status.showMessage(f"记录已保存：{recorder.path}")
            except OSError as exc:
                error = exc

        self.btn_csv.setChecked(False)
        self.btn_csv.setText("开始记录")
        self.lbl_record.setText("未记录")
        if error is not None:
            QtWidgets.QMessageBox.warning(self, "保存记录失败", str(error))

    def _csv_call(self, method_name, *args):
        recorder = self.recorder
        if recorder is None:
            return
        try:
            getattr(recorder, method_name)(*args)
        except (OSError, ValueError, csv.Error) as exc:
            path = recorder.path
            self._stop_recording()
            self._log(f"CSV 写入失败：{exc}")
            QtWidgets.QMessageBox.warning(
                self, "记录已停止", f"无法继续写入：\n{path}\n\n{exc}"
            )

    # ---------- 日志与退出 ----------
    def _log(self, message):
        self.event_log.appendPlainText(f"[{local_timestamp()}] {message}")

    def closeEvent(self, event):
        self._stop_recording()

        if self.worker is not None and self.worker.isRunning():
            event.ignore()
            if not self._close_after_worker:
                self._close_after_worker = True
                self._request_disconnect()
            return

        if self.worker is not None:
            self.worker.wait(500)
            self.worker.deleteLater()
            self.worker = None
        event.accept()


def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 9))
    app.setApplicationName("PPG Monitor")

    window = MainWindow()
    window._apply_style()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
