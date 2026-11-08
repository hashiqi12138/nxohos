# ohos-port — nx2android 鸿蒙移植 M0 阶段（探针工程）

配套文档（仓库外）：
- 《Switch原生移植_鸿蒙技术方案报告》（技术方案 A）
- 《鸿蒙移植_实现方案与验证策略》（实现方案与验证策略 B，M0 伪代码与 TC 契约的来源）

## 目录布局与上游路径映射

上游 nx2android 仓库布局（G0.x 闸门留档在 `<repo>/docs/verify/`）：

| 上游路径 | 本目录路径 | 说明 |
|---|---|---|
| `<repo>/probe` | `probe/` | M0 探针工程（libprobe.so + PC 单测） |
| `<repo>/tools/verify` | `tools/verify/` | 验证工具（PC 侧 python / 真机 shell） |
| `<repo>/configs` | `configs/` | 阈值集中配置 |
| `<repo>/fixtures` | `fixtures/` | 二进制测试资产（SHA256 登记） |
| `<repo>/docs/verify` | `docs/verify/` | 闸门验收记录（G0.1–G0.5 骨架已建，真机执行后回填） |
| `<repo>/nxnative/dm/ohos` | `dm/ohos/`（M1 阶段） | 鸿蒙后端（本阶段未开始） |

> 归属说明：本目录当前按"独立 ohos-port"交付；上游仓库已克隆至
> `../nx2android`（main@3d98ec0，镜像 gitclone.com）。M0 验收后按上表并入
> 仓库并建 `ohos` 分支（不覆盖上游既有文件）。

## 探针工程结构

```
probe/
├── probe_main.c        NAPI 模块：hilog、fork 崩溃隔离、worker 线程、报告落盘
├── probe_checks.h      检查项内部接口（编排契约）
├── probe_regs.c        M0.5 七项（tpidr/tpidrro/cntpct/TLS/futex/W^X/重写码）
├── probe_mem.c         M0.4 三段（5G 预留 / +8G 精确衔接 / 2G commit）
├── probe_vk.c          M0.2 上屏 + M0.3 BCn/ASTC 探测
├── vk_ohos_shim.h      OHOS WSI 声明集中点（以真 SDK 头为准）
├── probe_json.c        ProbeReport→JSON（纯逻辑，PC 可单测）+ 判定数学
├── probe_report.h      报告结构与 JSON 键名契约（TC 判定基线）
├── mini_host.c/.h      重写码宿主装载器（mmap RW→mprotect RX→调用）
├── build_probe.py      M0.1 构建（设备 + --host-check）
└── tests/
    ├── dmtest.h/.c     轻量断言头（TEST/EXPECT_*，无第三方依赖）
    ├── t_probe_units.c PC 单测（T0）
    ├── t_mini_nso.c    重写码真机测试（TC0-5-1/2，独立可执行）
    └── mini_nso/mini_nso.S  重写形态迷你 NSO 机码
```

## 现在就能跑的（PC 侧，已验证）

```sh
python tools/verify/run_unit_tests.py     # T0：C 单测 + 四个 python 工具自测
python probe/build_probe.py --host-check  # TC0-1-1 的 PC 半边（语法检查）
```

## 需要真机/SDK 的（待验证清单）

| 项 | 依赖 | 命令 |
|---|---|---|
| TC0-1-1 设备构建 | OHOS SDK（`OHOS_SDK` 环境变量） | `python probe/build_probe.py` |
| TC0-1-2 hilog started | 真机 + 签名 hap + XComponent 壳 | `tools/verify/run_probe.sh out/probe.hap` |
| TC0-1-3 hdc 写沙盒 | debug/release 签名包 | 结论并入 G0.1 留档 |
| TC0-2-1/2/3 | 真机 Vulkan 驱动 + 探针 UI | `run_probe.sh` + `verify_pixel.py` |
| TC0-3-1/2/3 | 真机 GPU 格式支持 | `probe_report.json` + fixtures |
| TC0-4-1/2 | 真机内存布局 | `readmem.sh` + `probe_report.json`（探针已内建进程内 vm/rss 自取） |
| TC0-5-1/2 | 真机寄存器语义 | `hdc shell /data/local/tmp/t_mini_nso` |

> **无实体真机？** 可用华为 AGC「云调试」远程真机（每日 360 分钟免费，
> 个人实名账号即可）：探针已内建 hilog 分块输出报告 JSON，PC 端用
> `tools/verify/reassemble_hilog_json.py` 重组判定。完整流程见
> [docs/verify/cloud_debug_guide.md](docs/verify/cloud_debug_guide.md)。
>
> **目标真机固定为 HUAWEI P70 Pro+**：云调试须选中该机型，报告
> `meta.device_model` 与之不符时结果仅作参考、不计入闸门判定。

## 已知边界（诚实清单）

1. `vk_ohos_shim.h` 已按真 SDK 头落地（2026-11-06 真机构建实测）：直接包含
   `<vulkan/vulkan_ohos.h>`，用 `VkSurfaceCreateInfoOHOS`，缺扩展即 `#error`。
   真名回填表见 [docs/verify/G0.1.md](docs/verify/G0.1.md)。
2. 探针 hap 壳在 `probe/hap/`（ArkTS XComponent + EntryAbility），窗口经
   `getXComponentSurfaceId()` → `setSurfaceId()` 注入（本 SDK 已移除旧的
   napi_value 版 XComponent 接口）。hwigor 拒绝中文路径，须用
   `probe/build_hap.py`（自动复制到 ASCII 目录构建）。签名需在 DevEco Studio
   内一键完成（密钥库口令在 IDE 安全存储，不可导出）。
3. `MAP_FIXED_NOREPLACE` 需内核 ≥4.17；老内核下 headroom 项将记 FAIL
   （属真实结论，不视为探针缺陷）。
4. PC 单测在 Windows 上跳过 `__linux__` 运行时用例（futex/W^X），Linux PC
   上全跑；mini NSO 五用例仅真机可跑。
5. 内存外部观测（TC0-4-2 的 VmSize/RSS）需要 `DM_MEM_HOLD_SECS` 环境变量
   重建触发（≤50s，父进程 60s 超时会 SIGKILL 兜底）；默认立即释放。
6. readback 兜底（G0.2 截屏色偏时）依赖 `DM_DUMP_FRAMEBUFFER` 导出末帧 +
   swapchain 支持 TRANSFER_SRC（不支持时 note 归因，不翻转主结论）。
7. fixtures 的二进制素材尚未入库（见 `fixtures/fixtures_index.md`），
   TC0-3-2 在素材入库前只能判定到 TC0-3-1。
