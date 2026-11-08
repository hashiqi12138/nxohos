# nx2ohos 工程化与开源方案

> 状态：基线已入库（`912a269`）。本文件是后续工程化路线的总设计。

## 1. 现状与目标

- 已完成：M0 探针（真机 PASS）、M1 核心跑通（解包/重写/shader/打包/真机 Vulkan 上屏），
  BOTW `01007EF00011E000` 真机进标题画面。
- 基线仓库已建立：`nx2android/`（上游快照+鸿蒙改动）、`ohos-port/`（探针工程）、
  `docs/`（两份方案报告）、`.trae/skills/`（移植 skill）。
- 目标：公开仓库 → CI 可构建 → 社区可复现 → 可持续演进（M2–M4）。

## 2. 仓库与分支策略

```
main           可运行基线（当前 HEAD），只合并已验证的改动
dev            日常开发分支（可选，单人开发可省）
release/0.x    发布分支，打 v0.x.y tag
```

- 分支保护（开公后）：main 要求 PR + 至少 1 人审阅；禁止直接 push。
- 每次发布：`chore(release): v0.x.y` → tag `v0.x.y` → GitHub Release
  （沿用上游节奏：版本号在 `porter/__init__.py`，0.MINOR.PATCH）。

## 3. 上游同步机制（M4.4，关键）

本仓库 = 上游快照 + 鸿蒙层。为避免与上游脱节：

```sh
git remote add upstream https://github.com/shirai91/nx2android.git
git fetch upstream
git checkout -b sync/upstream-<日期> upstream/main
# 冲突点集中在：
#   porter/（cli/pipeline/datapack）— 鸿蒙加 --platform hap 分支
#   nxnative/dm/host/* — loader/thread/main 的 DM_TARGET=ohos 分支
#   nxnative/dm/android/* — 鸿蒙复用的 gfx 层
# 合回 main 前必须重跑 T0 单测 + 本机构建
```

- 冲突不可避免时，优先**保留鸿蒙分支**、把上游新逻辑包进通用路径
  （沿用 AGENTS.md 原则：先找通用解，游戏/平台特例最后考虑）。
- 同步节奏：上游有实质更新时手动做，不做自动化 merge（避免静默破坏基线）。

## 4. CI/CD（GitHub Actions）

```
.github/workflows/
├── unit-tests.yml      # push/PR：T0 单测（PC 可跑，无需真机/密钥）
│   ├── python ohos-port/tools/verify/run_unit_tests.py
│   └── python ohos-port/probe/build_probe.py --host-check
├── build-hap.yml       # workflow_dispatch：完整 HAP 构建
│   ├── 输入：TITLE_ID、数据包路径（artifact 上传）
│   ├── 需要 OHOS SDK + 签名材料 → secrets 注入，不落库
│   └── 产物：probe/hap 或目标游戏 HAP，attach 到 workflow run
├── release.yml         # tag v*：打 Release（沿用上游 --clobber 模式）
└── verify-doc.yml      # push：校验 docs/verify/ 闸门记录完整性（可选）
```

- 真机验收（G 闸门）不进 CI —— 需要真机 + 游戏数据，走本地 `verify_doc.py` 记录。
- 游戏数据/密钥一律 secrets 或本地，CI 只碰公开源。

## 5. 工程化组件清单（M4 落地项，按优先级）

| 优先级 | 组件 | 说明 |
|---|---|---|
| P0 | `porter device` 子命令 | hdc 自动发现设备、型号/API/Vulkan 能力上报，代替手工 hdc list |
| P0 | 崩溃/日志采集 | hilog 定向抓取 + `Documents/log.txt` 式回传，落 `out/`（git 忽略） |
| P1 | HAP 壳参数化 | bundleName/签名从 `configs/` 读取，支持多应用共存 |
| P1 | 数据包增量 | `dm.files.tsv` sha1 增量比对，避免整包重推 |
| P2 | GUI（可选） | 游戏列表 + 一键装/卸/启停 + 日志查看，ArkTS 壳 |
| P2 | 性能埋点 | 帧率/帧时间/重放耗时，`snapshot_display` 截图存档 |

## 6. 发布与合规

- LICENSE：MIT（继承上游），保留 seedds/shirai91 版权行，本仓库贡献者追加版权行。
- 发布物：源码 + Release zip（构建好的 HAP 模板 + 工具），**不含游戏/密钥/固件**。
- README 明确：游戏数据用户自备，放 `.gamedump/<Game>/`。
- 仓库说明页写清"这是衍生工作" + 上游链接（合规 + 引流）。

## 7. 里程碑路线（M2→M4）

- M2 输入/音频/生命周期/存档：游戏内可操作、有声、可存读档。
- M3 性能与重型游戏：多线程重放、帧率节奏（FRAME_STEP 类）、过场/视频。
- M4 工程化：上表 P0–P2 + 上游同步机制跑通一次。
- 每个闸门：`docs/verify/G<里程碑>.<序号>.md` 记录命令+结论+证据图。

## 8. 待办（下一个动作）

1. 推 GitHub（需认证，见会话问题）。
2. 建 `.github/workflows/unit-tests.yml` 并验证 runner 通过。
3. 补 BOTW 真机完整验证记录（`docs/verify/G1.6-BOTW.md`，含截图引用）。
4. 首个 tag `v0.1.0` 发布。
