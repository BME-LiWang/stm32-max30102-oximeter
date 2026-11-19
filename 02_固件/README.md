# 固件目录说明

本目录同时存在两版 `main.c`，来源不同，请注意区分：

| 位置 | 日期 | 大小 | 说明 |
|---|---|---|---|
| `main.c`（本目录） | 2026-11-16 | 104 KB | 定型版，课设报告定稿时的代码 |
| `CubeIDE工程/Src/main.c` | 2026-11-18 | 116 KB | 最后一次调试版 |

11-18 版比定型版多 12 KB，是最后一天追加的诊断代码，包含若干定义了但未调用的函数（`calc_spo2`、`i2c_scan`、`jdy_at_configure`、`max_probe_soft` 等），编译时会报 unused-function 警告。功能分支与定型版一致。

`LED_Blink.hex`（74 KB）对应 11-16 定型版；用 `CubeIDE工程` 重新编译得到的是 11-18 版固件（81 KB）。

## CubeIDE工程

STM32CubeIDE 工程，裸机寄存器级实现（非 HAL）。没有 `.ioc` 文件，未用 CubeMX 生成骨架，外设全部手写配置。

已验证可编译：用 CubeIDE 自带的 arm-none-eabi-gcc 14.2.1 做过全量重建（`make -B all`），exit 0，结果：

```
text    data     bss     dec     hex   filename
28980    104   12528   41612    a28c   LED_Blink.elf
```

导入方式：`File → Import → General → Existing Projects into Workspace`，选中本目录，然后 `Project → Build`。构建产物在 `Debug/` 下。

`Debug/` 目录未纳入仓库（属构建产物）。`.launch` 调试配置也未纳入（内含本机路径）。

## 演进快照

开发期间的中间版本备份，见 [`演进快照/README.md`](演进快照/README.md)。

## 历史固件

`历史固件/` 下三个 hex 是各阶段的里程碑构建产物，无法对应到源码快照，仅作体积与阶段参考。
