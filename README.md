# STM32 + MAX30102 血氧/脉率监测系统

> 基于 STM32F103C8T6（Blue Pill）的指夹式 PPG 采集与算法验证平台：裸机固件完成 SpO2 / 脉率 / 灌注指数计算与跌倒检测，数据通过串口与蓝牙同步到电脑和手机。

课程设计项目。重点不在"能测"，而在**脉率判据怎么从不准做到准**——五轮算法迭代的过程、失败判据和实测数据都完整保留在仓库里。

![系统框图](docs/system-architecture.png)

---

## 硬件

| 部件 | 型号 | 接口 |
|---|---|---|
| MCU | STM32F103C8T6（Blue Pill，72 MHz） | — |
| 血氧传感器 | MAX30102（红光 660 nm / 红外 880 nm） | I2C2，地址 `0x57` |
| 六轴传感器 | MPU6050 | I2C1，地址 `0x68` |
| 显示屏 | SH1106 128×64 OLED | I2C1，地址 `0x3C` |
| 蓝牙 | JDY-31（SPP） | USART2，9600 8N1 |
| 蜂鸣器 | 无源，4 kHz | TIM1_CH1N |
| 转接板 | 自绘 v3/v5（立创 EDA） | — |

固件引脚分配：

```
I2C1   PB6/PB7   → OLED SH1106 (0x3C) + MPU6050 (0x68)
I2C2   PB10/PB11 → MAX30102 (0x57)
USART2 PA2/PA3   → JDY-31 (9600)
BEEP   PB13        LED PC13
KEY1-4 PB1 / PA6 / PA8 / PB15
```

## 功能

- **50 Hz 采样**，400 点滑动窗（8 s），每窗输出一次结果
- **脉率**：自相关 lag 投票 + 频域主判据双路，按灌注指数 PI 自动切换
- **血氧**：R 值查表，`SpO2 = 110 - 17R` 线性近似
- **灌注指数 PI**：AC/DC 比，反映末梢灌注强弱
- **跌倒检测**：MPU6050 三阶段判据——冲击 ≥ 2.2 g → 220 ms 内静止 0.65~1.35 g → 重力方向与竖直夹角 ≥ 63°，满足后蜂鸣器报警，10 s 自动复位
- **OLED 三页轮播**：3 s 自动翻页；手指在位时锁定在测量页，拿开后恢复轮播
- **三端显示**：PyQt5 电脑上位机、Flask + SSE 手机网页、原生 Android APK

## 实测指标

以手机健康 App 静息心率 **77 bpm** 为同刻对照（非医疗级设备，仅作相对基准）：

| 场景 | 样本 | 本机结果 | 偏差 |
|---|---|---|---|
| 稳态 90 s，固定压力 | n=97 | 75.3 ± 0.67 bpm | 1.7 |
| 4 分钟，每 30 s 换力度 | n=258 | 76.91 ± 3.28 bpm | 0.09 |

- 稳态标准差 **SD < 1 bpm**
- 启动收敛约 **11 s**
- 分辨率上限 **±4 bpm**（50 Hz 采样、400 点窗的物理约束，非参数可解）
- 手指在位时典型值：SpO2 97%、PI 3.4~8.7%

> 注意：这是教学/验证装置，**不是医疗设备**，数值不可用于诊断。

## 目录

```
01_报告/              课设报告（定稿 + 两份过程稿，保留算法演进轨迹）
02_固件/              main.c（裸机寄存器级）、LED_Blink.hex、烧录与调试文档
  历史固件/           三版里程碑 hex
03_上位机/            ppg_monitor.py（PyQt5 串口版）、web_monitor.py（Flask 手机网页版）
04_验收数据/          采集 CSV、原始 400 点窗、可复跑的采集脚本
05_设计资料/          转接板交接文档、原理图、旧板验证报告
06_Android手机端App/  MainActivity.java、已构建 APK、使用说明
```

算法怎么一步步改过来的，看 `01_报告/课设报告_MAX30102脉率判据五轮演进_定稿_20261116.md`。

## 快速开始

### 编译与烧录

固件用 STM32CubeIDE 的 GNU 工具链构建（裸机，非 HAL）：

```bash
arm-none-eabi-objcopy -O ihex LED_Blink.elf LED_Blink.hex

STM32_Programmer_CLI.exe -c port=SWD freq=1000 \
  -w LED_Blink.hex -v -rst -run
```

看到 `Download verified successfully` 即成功。详细参数见 `02_固件/STM32板子烧录与调试配置文档.md`。

### 电脑上位机

```bash
pip install pyserial pyqt5
python 03_上位机/ppg_monitor.py
```

选串口（默认 `COM8`，请改成你的端口）、9600，点连接。

### 手机端

两种方式：

1. **网页版**：`python 03_上位机/web_monitor.py`，手机浏览器打开终端打印的地址，同一局域网即可；
2. **APK**：安装 `06_Android手机端App/血氧监测.apk`，在系统蓝牙里配对 `JDY-31-SPP`（PIN `1234`），再在 App 内连接。

### 采集数据

```bash
python 04_验收数据/_accept_long.py long3 240   # 4 分钟长采，按 PI 自动分档
```

脚本默认 `COM8`，运行前请关掉占用该串口的其他程序。

## 通信协议

串口 9600 8N1，帧格式：

```
AA 55 | type(1) | len(1) | payload(len) | checksum(1)
checksum = 前 4+len 字节求和 & 0xFF
```

| type | 含义 | payload |
|---|---|---|
| `0x01` | 测量结果，25 帧/s | len=6：SpO2 / PR / PI(×10) / pitch+90 / roll+90 / state |
| `0x02` | 跌倒事件 | len=0 或 1 |
| `0x03` | 原始波形 | len=6（IR×2）或 12（ir0/red0/ir1/red1，大端） |

`state`：0 空闲、1 测量中、2 信号弱、4 跌倒。

## 已知限制

1. 按键 S1–S4 因封装 1-2 / 3-4 共用短地而失效，当前靠 3 秒自动轮播绕过（v6 PCB 计划修正，见 `05_设计资料`）
2. 板子布局导致无法 USB 供电，只能由 ST-Link 的 3.3V 针供电
3. 无运动伪差抑制，晃动时脉率会漂
4. SpO2 未与医用血氧仪对照，仅有 PI/PR 的相对验证
5. `pr_val` 在手指拿开后不会超时清零
6. 历史固件与部分文档带有调试残留（warning 未清）

## 许可

MIT License，见 [LICENSE](LICENSE)。报告与数据同样可自由引用，注明出处即可。
