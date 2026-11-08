# nx2ohos — Switch 游戏原生移植到 HarmonyOS NEXT

把任天堂 Switch 游戏以**原生执行**方式搬到鸿蒙（HarmonyOS NEXT / OpenHarmony）：
不模拟 CPU，游戏 ARM64 机器码直跑，只 HLE 顶替 Horizon 系统调用，
NVN 图形调用离线翻译成 SPIR-V 后经 Vulkan 重放上屏。

> 当前状态：**M1 已跑通** —— 解包 / NSO 重写 / 着色器翻译 / dmhost 构建 /
> HAP 打包 / 真机 Vulkan 上屏，已在真机运行《塞尔达传说：旷野之息》进入标题画面。
> 验证记录见 `docs/verify/`（G0.1–G1.5 闸门 PASS + 真机截图）。

## 仓库布局

```
nx2android/                上游 nx2android（main@3d98ec0）工作区快照 + 鸿蒙改动
├── nxnative/dm/ohos/       鸿蒙平台层（构建、HAP 壳、NAPI、present、音频、movie）
├── porter/platform_ohos.py porter --platform hap 流水线
└── nxnative/dm/            NSO 重写器 + dmhost 宿主运行时（复用上游，少量改动）
ohos-port/                  M0 探针工程（libprobe.so + PC 单测 + 闸门验收）
docs/                      两份技术方案报告（含全部里程碑与验收策略）
.trae/skills/nxohos-port/   移植开发 skill（给 AI 协作的开发规程）
```

## 快速开始（当前基线）

```sh
# 1. T0 单测（PC 可跑，无真机）
python ohos-port/tools/verify/run_unit_tests.py
python ohos-port/probe/build_probe.py --host-check

# 2. 构建鸿蒙 dmhost（需 OHOS_SDK 环境变量）
cd nx2android/nxnative/dm/ohos
python build_ohos.py --game <TITLE_ID> --lib
python mkhap.py --game <TITLE_ID> --data <数据目录> --out <输出>

# 3. porter 全流水线（重写 → shader → host → 数据包 → HAP → 安装）
python -m porter port <游戏目录> -k prod.keys --platform hap [--install]
```

## 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 设备能力验证（Vulkan/BCn/mmap/寄存器语义） | ✅ 真机 PASS（Pura 70 系） |
| M1 | 核心跑通（dmhost 构建 / NAPI 壳 / 上屏 / porter / 打包） | ✅ G1.3/G1.5 PASS，BOTW 真机出画面 |
| M2 | 输入 / 音频 / 生命周期 / 存档 | ⏳ 进行中 |
| M3 | 重型游戏与性能（多线程重放 / 帧率节奏 / 过场） | ⬜ |
| M4 | 工程化（GUI / 设备检测 / 崩溃采集 / 上游同步） | ⬜ |

详细技术方案见 [docs/](docs/) 两份报告；每个闸门的验收命令与结论见 `docs/verify/`。

## 合规与数据

- **本仓库不含任何游戏文件、密钥、固件**（.gamedump/、tools/、prod.keys 等均在忽略列表）。
- 游戏数据与密钥需用户自备，放在 `.gamedump/<Game>/`（git 忽略）。
- 上游：nxnative / nx2android，MIT。本仓库为衍生工作，见 [LICENSE](LICENSE)。

## 开发约定

- 闸门状态机：PASS / FAIL(#issue) / SKIP(依赖) / FLAKE，由 `archive_gate.py` 约束，
  修复涉及某闸门代码后必须重跑该闸门全部用例。
- 每款游戏的调校参数进 `dm/games/<title_id>/` 的 profile，不进通用代码。
- 提交信息用英文；日志红色行 `[RED] ` 表示未实现或错误。
