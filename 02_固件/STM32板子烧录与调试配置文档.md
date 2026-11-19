# STM32F103 板子烧录与调试环境配置文档（接手 Agent 照此配置）

> **用途**：本文件自包含、可直接照做，用于在这台 Windows 电脑上配置并执行 STM32F103C8T6（蓝 pill，插在 PPG 转接板上）的**编译、烧录与调试**。所有路径、版本均已实测确认。
>
> **运行环境**：Windows，**Bash 不可用，所有命令走 PowerShell（pwsh）**；Python 3.13 可用。
>
> **配套文档**：硬件/EDA 设计见 `STM32转接板_v3_交接设计文档.md`。

---

## 1. 总体方案与工具链概览

本项目采用 **GCC 工具链离线编译 + STM32CubeProgrammer CLI 通过 SWD 烧录** 的全命令行方案，不依赖 IDE 图形界面，可被脚本/Agent 全自动驱动。

| 环节 | 工具 | 实测版本 | 作用 |
|------|------|----------|------|
| 编译/链接 | arm-none-eabi-gcc | GNU Tools for STM32 **14.3.rel1**（gcc 14.3.1 20250623） | 编译 C、生成 elf |
| 构建调度 | make | STM32CubeIDE 自带 make（2.2.200） | 读 makefile 构建 |
| 格式转换 | arm-none-eabi-objcopy | 随 GCC 14.3.rel1 | elf → hex/bin |
| 烧录 | STM32_Programmer_CLI | STM32CubeProgrammer **v2.23.0** | SWD 写入 + 校验 + 复位 |
| 物理接口 | ST-Link（V2 类）SWD | — | 连接 PC 与板 J6 |
| 目标芯片 | STM32F103C8T6 | Device ID 0x410 | 128KB Flash、Cortex-M3 |

---

## 2. 硬件连接（ST-Link ↔ 转接板 J6）

### 2.1 J6 SWD 插座针序（4P，板上原点 1255,190，rot180）

| J6 针 | 网络 | 接 ST-Link |
|-------|------|------------|
| 1 | GND | GND（地，必须共地） |
| 2 | 3V3 | 3.3V（参考电平/供电，按 ST-Link 类型决定是否接） |
| 3 | SWDIO | SWDIO（数据线） |
| 4 | SWCLK | SWCLK（时钟线） |

**接线对应（4 根有效线）：**

```
ST-Link        转接板 J6
 GND  ────────  pin1 GND
 3.3V ───────  pin2 3V3
 SWDIO ──────  pin3 SWDIO
 SWCLK ──────  pin4 SWCLK
```

### 2.2 连接注意事项

1. **GND 必须接**，保证电平参考一致；SWDIO/SWCLK 不要接反。
2. 若板子由外部独立供电（H24 接 5V），ST-Link 的 3.3V 针可只作电平参考、不供电；若靠 ST-Link 供电，则接 3.3V 针。
3. 标准 SWD 只需 4 线（含地），**不需要接 NRST**（本设计也没引出）；遇到低功耗/休眠导致连不上时，再考虑加 NRST 做"under reset"连接。
4. 插上后确认 ST-Link 被系统识别（设备管理器/USB 无黄色感叹号）；若提示驱动，STM32CubeProgrammer 安装目录通常自带 ST-Link 驱动。
5. 报 `No debug probe detected` = 探针没插好/没被识别，先查 USB 与驱动，不是芯片问题。

---

## 3. 工具链路径与版本（已实测存在）

### 3.1 烧录工具

```
D:\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe   （v2.23.0）
```

### 3.2 GCC 与 make（随 STM32CubeIDE 2.2.0 安装，路径较长，按实测）

```
GCC bin：
D:\STM 32\STM32CubeIDE_2.2.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.win32_1.0.100.202602081740\tools\bin

make bin：
D:\STM 32\STM32CubeIDE_2.2.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.make.win32_2.2.200.202604021615\tools\bin
```

已确认存在：`arm-none-eabi-gcc.exe`、`arm-none-eabi-objcopy.exe`、`make.exe`。

### 3.3 工程目录

```
工程根：   D:\STM 32\LED_Blink
主源码：   D:\STM 32\LED_Blink\Src\main.c
构建目录： D:\STM 32\LED_Blink\Debug（含 makefile）
链接脚本： D:\STM 32\LED_Blink\STM32F103C8TX_FLASH.ld
输出：     Debug\LED_Blink.elf / .hex / .bin / .map / .list
```

### 3.4 验证版本（配置后应能跑出版本号）

```powershell
& 'D:\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe'
& 'D:\STM 32\STM32CubeIDE_2.2.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.win32_1.0.100.202602081740\tools\bin\arm-none-eabi-gcc.exe' --version
```

---

## 4. 环境变量配置

构建前需要把 GCC、make 加入 PATH，并指定临时目录（避免路径含空格/中文导致问题）：

```powershell
$gccBin  = 'D:\STM 32\STM32CubeIDE_2.2.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.win32_1.0.100.202602081740\tools\bin'
$makeBin = 'D:\STM 32\STM32CubeIDE_2.2.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.make.win32_2.2.200.202604021615\tools\bin'
$env:PATH = $gccBin + ';' + $makeBin + ';' + $env:PATH

# 临时目录（make/编译器需要）
$env:TMP  = 'D:\Temp\gcc_tmp'
$env:TEMP = 'D:\Temp\gcc_tmp'
$env:HOME = 'D:\Temp\gcc_tmp'
if (-not (Test-Path -LiteralPath 'D:\Temp\gcc_tmp')) {
    New-Item -ItemType Directory -Path 'D:\Temp\gcc_tmp' -Force | Out-Null
}
```

> 注意：这些是**进程级**环境变量，每次新开 PowerShell 都要重新设置；一键脚本已包含，无需手动。

---

## 5. 完整构建流程（编译 → elf → hex）

### 5.1 进入构建目录并执行 make

```powershell
Set-Location -LiteralPath 'D:\STM 32\LED_Blink\Debug'
& (Join-Path $makeBin 'make.exe') -j1 all
```

- 成功生成 `LED_Blink.elf`、`LED_Blink.map`、`LED_Blink.list`。
- 检查 `$LASTEXITCODE` 必须为 0；非 0 即构建失败，看输出定位。
- 编译期的 `missing braces`、`defined but not used` 属 **warning，不影响烧录**。

### 5.2 用 objcopy 生成 hex（烧录格式）

```powershell
& (Join-Path $gccBin 'arm-none-eabi-objcopy.exe') -O ihex `
    'D:\STM 32\LED_Blink\Debug\LED_Blink.elf' `
    'D:\STM 32\LED_Blink\Debug\LED_Blink.hex'
```

- 生成 `LED_Blink.hex`（Intel HEX 格式，烧录用）。
- 若需要纯二进制：把 `-O ihex` 换成 `-O binary`、输出 `.bin`。
- **务必确认 hex 的修改时间已更新**，防止"make 成功但 hex 是旧的"（见第 8 章踩坑）。

### 5.3 构建产物（Debug 目录实测）

| 文件 | 典型大小 | 说明 |
|------|----------|------|
| LED_Blink.elf | ~43KB | 带调试信息的可执行文件 |
| LED_Blink.hex | ~14KB | 烧录用 Intel HEX |
| LED_Blink.bin | ~3KB | 纯二进制镜像 |
| LED_Blink.map | ~98KB | 内存/符号映射 |
| LED_Blink.list | ~72KB | 反汇编清单 |

---

## 6. 烧录命令（核心，参数详解 + 成功判据）

### 6.1 标准烧录一条命令

```powershell
& 'D:\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe' `
    -c port=SWD freq=1000 `
    -w 'D:\STM 32\LED_Blink\Debug\LED_Blink.hex' `
    -v -rst
```

### 6.2 参数逐项解释

| 参数 | 含义 |
|------|------|
| `-c port=SWD` | 用 SWD 接口建立连接（区别于 JTAG/串口/USB DFU） |
| `freq=1000` | SWD 时钟频率 1000 kHz（=1MHz，稳定；连不上可降到 400） |
| `-w <hex>` | 把指定 hex 写入 Flash（write） |
| `-v` | 写入后回读校验（verify），确保数据正确 |
| `-rst` | 烧录完成后复位目标，让程序立即运行 |

### 6.3 ★ 成功判据（必须看到，不能只看退出码）

烧录输出最后出现：

```
Download verified successfully
```

即表示"写入 + 校验"全部通过，且已复位运行。这是本项目判定烧录成功的**唯一权威标志**。
- 若只写不校验，不建议作为成功依据；
- 出现任何 `Error` / `error` 都视为失败，按第 8 章处理。

### 6.4 只读连接探测（不烧录，用于确认链路/读芯片信息）

```powershell
& 'D:\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe' -c port=SWD freq=1000
```

- 正常会打印芯片信息（Device ID、Flash size、Revision 等），用于确认 SWD 连通。
- 报 `No debug probe detected` 是探针层面问题；报 `No device found on target` 是目标芯片连接/会话问题（第 8 章）。

---

## 7. 一键脚本（可直接复用/重建）

工作目录已有两个现成脚本，封装了上面全部流程。下面给出**完整内容**，即使脚本丢失也能照此重建。

### 7.1 `build.ps1`（只编译，不烧录）

```powershell
$env:TMP  = 'D:\Temp\gcc_tmp'
$env:TEMP = 'D:\Temp\gcc_tmp'
$env:HOME = 'D:\Temp\gcc_tmp'
if (-not (Test-Path -LiteralPath 'D:\Temp\gcc_tmp')) { New-Item -ItemType Directory -Path 'D:\Temp\gcc_tmp' -Force | Out-Null }

$gccBin  = 'D:\STM 32\STM32CubeIDE_2.2.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.win32_1.0.100.202602081740\tools\bin'
$makeBin = 'D:\STM 32\STM32CubeIDE_2.2.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.make.win32_2.2.200.202604021615\tools\bin'
$env:PATH = $gccBin + ';' + $makeBin + ';' + $env:PATH

Set-Location -LiteralPath 'D:\STM 32\LED_Blink\Debug'
$log = './build.log'
& (Join-Path $makeBin 'make.exe') -j1 all *>&1 | Out-File -LiteralPath $log -Encoding utf8
"exit=$LASTEXITCODE" | Out-File -LiteralPath $log -Append -Encoding utf8

$hex = 'D:\STM 32\LED_Blink\Debug\LED_Blink.hex'
if (Test-Path -LiteralPath $hex) {
    $i = Get-Item -LiteralPath $hex
    "HEX: $($i.Length) bytes  modified $($i.LastWriteTime)" | Out-File -LiteralPath $log -Append -Encoding utf8
}
'built'
```

运行：

```powershell
powershell -ExecutionPolicy Bypass -File './build.ps1'
```

### 7.2 `flash.ps1`（刷新时间戳 → 编译 → 生成 hex → 烧录，全自动）

```powershell
$ErrorActionPreference = 'Stop'

# ---- 1. 按当前时间刷新 BOOT_SEC / BOOT_DATE（main.c 内的走时起点）----
$now  = Get-Date
$sec  = $now.Hour * 3600 + $now.Minute * 60 + $now.Second + 16
$date = $now.ToString('yyyy-MM-dd')

$src   = 'D:\STM 32\LED_Blink\Src\main.c'
$bytes = [IO.File]::ReadAllBytes($src)
$hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)

$txt = [IO.File]::ReadAllText($src, (New-Object Text.UTF8Encoding($false)))
$txt = [regex]::Replace($txt, 'static const uint32_t BOOT_SEC = \d+U;', "static const uint32_t BOOT_SEC = ${sec}U;")
$txt = [regex]::Replace($txt, 'static const char BOOT_DATE\[\] = "\d{4}-\d{2}-\d{2}";', 'static const char BOOT_DATE[] = "' + $date + '";')
[IO.File]::WriteAllText($src, $txt, (New-Object Text.UTF8Encoding($hasBom)))
"patched: BOOT_SEC=$sec  DATE=$date  srcHasBom=$hasBom"

# ---- 2. 编译 ----
$env:TMP  = 'D:\Temp\gcc_tmp'
$env:TEMP = 'D:\Temp\gcc_tmp'
$env:HOME = 'D:\Temp\gcc_tmp'
if (-not (Test-Path -LiteralPath 'D:\Temp\gcc_tmp')) { New-Item -ItemType Directory -Path 'D:\Temp\gcc_tmp' -Force | Out-Null }

$gccBin  = 'D:\STM 32\STM32CubeIDE_2.2.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.win32_1.0.100.202602081740\tools\bin'
$makeBin = 'D:\STM 32\STM32CubeIDE_2.2.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.make.win32_2.2.200.202604021615\tools\bin'
$env:PATH = $gccBin + ';' + $makeBin + ';' + $env:PATH

Set-Location -LiteralPath 'D:\STM 32\LED_Blink\Debug'
& (Join-Path $makeBin 'make.exe') -j1 all | Out-Null
if ($LASTEXITCODE -ne 0) { throw "make failed: $LASTEXITCODE" }
"build ok"

# ---- 3. 生成 hex ----
& (Join-Path $gccBin 'arm-none-eabi-objcopy.exe') -O ihex 'D:\STM 32\LED_Blink\Debug\LED_Blink.elf' 'D:\STM 32\LED_Blink\Debug\LED_Blink.hex'
if ($LASTEXITCODE -ne 0) { throw "objcopy failed: $LASTEXITCODE" }
"hex ok: " + (Get-Item -LiteralPath 'D:\STM 32\LED_Blink\Debug\LED_Blink.hex').LastWriteTime

# ---- 4. 烧录 ----
& 'D:\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe' -c port=SWD freq=1000 -w 'D:\STM 32\LED_Blink\Debug\LED_Blink.hex' -v -rst |
    Select-String -Pattern 'Download verified|Error|error' | ForEach-Object { $_.Line }
"flash finished at " + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
```

运行：

```powershell
powershell -ExecutionPolicy Bypass -File './flash.ps1'
```

> 脚本说明：`flash.ps1` 会先改 `main.c` 里的 `BOOT_SEC/BOOT_DATE`（固件用它做时钟起点），保留原文件是否带 BOM 的状态，再编译、objcopy、烧录，全程用 `$LASTEXITCODE` 把关，任何一步失败即抛错停止。

---

## 8. 常见故障与恢复（按报错对症处理）

### 8.1 `No debug probe detected`（探针层）

- 含义：PC 没识别到 ST-Link。
- 处理：① 重新插拔 ST-Link USB；② 设备管理器查驱动/黄色感叹号，必要时重装 ST-Link 驱动；③ 换 USB 口/数据线；④ 确认 ST-Link 本身通电。

### 8.2 `Unable to get core ID` / `No device found on target`（目标层，最常见）

- 经验 `stlink-swd-session-hang-recover-by-usb-replug`：多为**调试会话挂死**，不是芯片坏。
- 处理顺序：
  1. **拔掉 ST-Link 的 USB 再插回**（最有效，复位调试会话）；
  2. 降低 SWD 频率：`freq=400`（甚至 100）重试；
  3. 检查 J6 四根线（GND/SWDIO/SWCLK/3V3）是否接牢、接对；
  4. 确认板子已供电（3V3 轨有电）；
  5. 若固件进了低功耗/关 SWD 的状态，用"under reset"方式连接（按住/接 NRST 时建连）。

### 8.3 CubeIDE 报 GDB server 失败，但 CLI 能烧（磨字/克隆芯片）

- 经验 `stm32-cubeide-gdbserver-reject-clone-idcode`：部分磨字/克隆芯片 ID 校验过不了 IDE，但 **CubeProgrammer CLI 同硬件可正常烧录**。
- 处理：直接用本文档的 CLI 流程烧，不必改走串口、不必纠结 IDE 报错。

### 8.4 make 成功但烧的是旧固件（hex 未更新）

- 经验 `stm32-cubeide-make-hex-stale`：手工跑 makefile 可能"make 报成功"但 hex 没刷新。
- 处理：① 显式跑一次 objcopy 重新生成 hex；② 烧录前核对 hex 的 `LastWriteTime` 是当前时间；③ 必要时先 `make clean` 再全量构建。

### 8.5 烧录校验失败 / 写入 Error

- 处理：① 降 SWD 频率重试；② 确认供电稳定（3V3 不跌落，C1 10µF 正常）；③ 检查是否写超 Flash 范围（128KB）；④ 重新插拔后再试；⑤ 仍失败再怀疑芯片/焊接。

### 8.6 执行策略拦截（脚本无法运行）

- 现象：PowerShell 禁止运行 `.ps1` 脚本。
- 处理：调用时加 `-ExecutionPolicy Bypass`（见第 7 章运行命令），或当前会话 `Set-ExecutionPolicy -Scope Process Bypass`。

---

## 9. 备用烧录方式（SWD 不可用时的补充）

> 本项目默认 SWD；以下仅在 SWD 链路硬件受损等场景下使用，需要板子对应引脚支持。

### 9.1 USB DFU（需要 BOOT0 拉高 + 片上 USB）

- 蓝 pill 把 BOOT0 接 1、BOOT1(PB2)=0，复位进入系统存储器 bootloader，用 USB 接 PC。
- 连接：`STM32_Programmer_CLI.exe -c port=usb1`，再 `-w <hex> -v -rst`。
- 代价：需要手动操作 BOOT0/跳线，与"不手动"偏好冲突，仅作兜底。

### 9.2 串口 UART bootloader（USART1 PA9/PA10）

- 同样 BOOT0=1 进 bootloader，经 USART1 串口（需 USB-TTL）。
- 连接：`-c port=COMx br=115200`（串口号按实际），再写入。
- 注意：本项目主外设占用的是 USART2（PA2/PA3），串口 bootloader 走 USART1，互不相同。

---

## 10. 配置验收清单（逐条满足才算环境就绪）

- [ ] ST-Link USB 被系统识别，驱动正常，无黄色感叹号。
- [ ] ST-Link 与 J6 接线正确：GND/3V3/SWDIO/SWCLK 一一对应、共地。
- [ ] 能跑出版本：Programmer **v2.23.0**、GCC **14.3.1**；make、objcopy 路径存在。
- [ ] 环境变量（PATH/TMP/TEMP/HOME）按第 4 章设置，临时目录 `D:\Temp\gcc_tmp` 存在。
- [ ] 只读连接探测 `-c port=SWD freq=1000` 能读到目标芯片（无 No device/No probe 报错）。
- [ ] `make` 构建返回 `$LASTEXITCODE=0`，生成 elf；objcopy 生成**当前时间戳**的 hex。
- [ ] 烧录命令输出 **`Download verified successfully`**，并自动复位运行。
- [ ] `build.ps1` / `flash.ps1` 可在 `-ExecutionPolicy Bypass` 下一键跑通。
- [ ] 知道 8.1/8.2 的拔插恢复法，遇到会话挂死能自行恢复，不依赖用户手动。
- [ ] 全程 PowerShell、不使用 Bash；所有改动只作用于本工程，不影响其他项目。

---

> **结论**：按本文档配置后，接手 Agent 可全自动完成"改源码 → 编译 → objcopy → SWD 烧录 → 校验复位"闭环，成功以 `Download verified successfully` 为准；最常见的连接问题靠 ST-Link 拔插 + 降频即可恢复。硬件设计与项目背景见配套的 `STM32转接板_v3_交接设计文档.md`。

---

## 11. 当前固件算法参数与分辨率极限（2026-11-16 补写）

### 11.1 板上固件

| 项目 | 值 |
|---|---|
| 版本 | 第五版（lag 域投票） |
| 编译产物 | `Debug\LED_Blink.elf` 109,564 B、`Debug\LED_Blink.hex` **76,058 B** |
| 主频 | HCLK 72 MHz / APB1 36 MHz（HSE+PLL9；失败退 HSI 8 MHz） |
| 采样 | MAX30102 100 sps、18 bit、SMP_AVG=2 → **输出 50 Hz** |
| LED 电流 | RED/IR 各 `0x34`（10.4 mA） |
| 读 FIFO | 指针法，`want=(wr-rd) mod 32`，`want<4` 不读；IR 末字节 0xFF 用上一样本低字节补齐 |

### 11.2 PR 判据（改这两处前先读报告 §3）

| 环节 | 关键参数 |
|---|---|
| 预处理 | 去直流 → 4 点滑动均值 → 一阶差分 → 2 点均值 → 5 点汉明窗 `{41,276,512,276,41}/1146` → 阈值=绝对值均值 |
| 峰检测 | `n_min_distance = 8` |
| 自相关 | `AC_MW=16`、`AC_LAG_MIN=18`、`AC_LAG_MAX=80`、`AC_R_MIN=0.20`、`AC_R_FULL=0.30`、生理区间优先 lag∈[26,40] |
| lag 投票 | `lag_ring[15]`，`ac_r>=0.18` 且 PR∈[35,140] 才入环，±1 聚簇取众数，`best_cnt>=5` 才采用，两簇计数接近时取**较短** lag |
| 频域 | `SP_N=400`、`SP_K=6~20`（45~150 bpm）、`g_sp_q>=3`、峰贴边（`best_k<=7` 或 `>=19`）作废、仅可信窗（峰不贴边且峰/均值>=2）更新 `sp_sm` |
| 仲裁 | `pi_val >= 2.5f ? achr : spr`（自相关优先） |
| 平滑 | 15 点中位数 → 迟滞（差 >12 bpm 需连续 3 窗）→ `pr_slew` ±5 bpm/帧 |
| SpO₂/PI | Maxim AC/DC + `uch_spo2_table[184]`，与 PR 路径完全独立 |

### 11.3 分辨率极限（为什么 77 与 83 分不清）

- **频域**：`Δf = fs/N = 50/400 = 0.125 Hz` → **7.5 bpm/bin**。k=10→75.0、k=11→82.5、k=12→90.0。77 bpm（1.283 Hz，k=10.27）与 83.5 bpm（1.392 Hz，k=11.13）**同处 k=10/11 边界**，三点抛物线插值只能在峰位正确时细化，峰落错 bin 时插值无效。
- **自相关**：`PR = 3000/lag`，lag 只能取整数，**每差 1 点 ≈ 2~3 bpm**。lag 33→90.9、36→83.3、39→76.9。76.9 与 83.3 只差 3 个 lag 点。
- **系统合成**：**±4 bpm 量级**，与手机自带心率"误差 <3 次/分"同量级。
- **实证**：4 分钟动态采集里出现过"PR 83 恒定 25 帧、1 秒内 83→78→75"。1 秒跳 8 bpm 不可能是生理变化，属 lag 36 与 39 之间的判决切换。
- **要不要突破**：只能加长窗口（16 s/800 点 → 3.75 bpm/bin），代价是启动延时翻倍且窗内可能跨两个心率值。**当前方案不追求突破，±4 bpm、启动约 11 s、稳态 SD<1 即交付指标。**

### 11.4 验收数据存放

`./` 下：

| 文件 | 内容 |
|---|---|
| `验收_cal77.csv` | 第五版稳态 90 s（手机 77 bpm 对照）：三档 75.5±0.84 / 75.3±0.67 / 75.2±0.53 |
| `验收_long2.csv` | 第五版 4 分钟反复换压：258 帧，全体 76.91±3.28，与手机差 0.09 bpm |
| `验收_long1.csv` | 第四版 4 分钟（投票前对照）：91 连续 22 帧 + 55s 后锁 75/83 |

三组数据的解读见 `课设报告_MAX30102脉率判据五轮演进_定稿_20261116.md`。

