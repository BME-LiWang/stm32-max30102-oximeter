# 固件目录说明

当前固件为 2026-11-27 的 **4 页设置 + PR 区间联动** 版本：

| 文件 | 大小 | 说明 |
|---|---|---|
| `main.c` | 125,865 B | 当前源码，4 页 OLED、S1–S4 设置、`0x04` 心率上下限 |
| `LED_Blink.hex` | 86,760 B | 当前烧录产物，SHA256 `90BCFB251F59D246E19B0F2DD48DB70CF8826A406729B7636013EEF3E16080B6` |
| `CubeIDE工程/Src/main.c` | 125,865 B | 与根目录 `main.c` 相同的当前源码 |
| `STM32板子烧录与调试配置文档.md` | — | 工具链、编译、烧录和排错 |
| `版本更新_20261127.md` | — | 本次 4 页设置与 `0x04` 的变更摘要 |

## CubeIDE 工程

STM32CubeIDE 工程，裸机寄存器级实现（非 HAL）。没有 `.ioc` 文件，未用 CubeMX 生成骨架，外设全部手写配置。

导入方式：`File → Import → General → Existing Projects into Workspace`，选中本目录，然后 `Project → Build`。构建产物在 `Debug/` 下。

`Debug/` 目录未纳入仓库（属构建产物），`.launch` 调试配置也未纳入（可能含本机路径）。

## 历史固件

`历史固件/` 保留各阶段里程碑 hex，包括 Maxim 官方算法、自相关混合、频域主判据、四页设置等版本，仅作阶段对照；当前版本以根目录 `LED_Blink.hex` 为准。

## 演进快照

开发期间的中间源码备份，见 [`演进快照/README.md`](演进快照/README.md)。
