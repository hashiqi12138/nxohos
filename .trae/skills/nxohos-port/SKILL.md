---
name: nxohos-port
description: Guides and validates nx2ohos game-host ports to HarmonyOS/OpenHarmony. Use when the user mentions nxohos、NSO/AOT、libdmhost、多 HAP、rawfile staging、Vulkan present、WiFi hdc 或真机黑屏. Do not use for ordinary ArkUI pages.
license: MIT
---

# nxohos HarmonyOS 移植

先阅读 [移植适配参考](references/porting.md)，再按当前任务所处阶段执行。保留 nx2ohos 的 guest、HLE、NVN 和 Vulkan replay，只调整 HarmonyOS 平台边界。

## 约束

- 中文交流并自主连续执行；只使用本机 WiFi hdc 或 AGC 云调试，不依赖 USB。
- 先定位根因再修改。每次实验只改变一个变量，并保留当前 PID、日志和真实帧证据。
- 优先实现通用 Horizon、NVN、Vulkan 或平台语义修复，不为单一游戏散写硬编码分支。
- 所有实际代码改动先经 subagent 审核，再构建和真机验证。
- 不把替代 shader、direct present、常量色或任意非黑图当作原始渲染修复。

## 任务分流

- 构建或链接失败：先核对 SDK、ASCII 构建路径、JBR、源码平台宏和未解析符号，不进入设备安装。
- 安装或 staging 失败：先核对签名、模块清单、manifest、rawfd 和 stamp，不进入图形调试。
- 有 present 但游戏未推进：先查 guest 线程、模块加载、RomFS、存档和 HLE，不替换最终 shader。
- 有源纹理但最终画面错误：按当前 PID 分别检查原始 vertex、fragment、descriptor、depth mode 和 present，每次只换一个变量。
- 帧时间异常：先看 `g_vkst`、pool 同步和 copy-back 的实际字节数，再优化 replay 或 GPU 成本。

## 移植流程

1. 核对 Title ID、游戏版本、NSO build ID、模块列表、profile、内存布局和 shader 输入。
2. 先运行 porter dry-run，确认 `rewrite → shaders → host → datapack → hap → install` 的输入与输出。
3. 使用 `DM_TARGET=ohos` 在构建期完成 NSO/AOT 重写。不要依赖设备上的运行时 JIT、匿名 RW→RX 或运行时下载 SO。
4. 生成完整 `shaders.dmsv`。运行期捕获的 missing shader 必须重新进入转换链，不能长期用替代 SPV 绕过。
5. 用 `build_ohos.py` 和 OHOS SDK 构建 `libdmhost.so`，与 Android 输出目录分开，链接时解析全部符号。
6. 按统一布局生成 `mods/`、`RomFS/`、`aoc/`、`shaders.dmsv` 和相对路径 `env.txt`。
7. 用 `mkhap.py --lib ... --data ...` 打包。大数据按默认约 3500 MiB 拆成 entry 和 dataN feature 模块；不要用 `assembleApp` 替代多 HAP。
8. 单 HAP 用 `hdc install -r`。多 HAP 整组推送后用 `bm install -p ... -r -w 600`，然后停止旧实例并启动 `EntryAbility`。
9. 验证 ArkTS 按三列 manifest 跨模块 staging，所有文件复制成功并写入 stamp 后才允许启动游戏。
10. 按 XComponent → surfaceId → OHNativeWindow → VkSurfaceOHOS → swapchain 验证窗口和 present，再进入真实游戏调试。

## 真机诊断

- 每次启动前停止旧实例并清理旧 PPM；启动后记录 PID，只采用该 PID 的日志和截图。
- 启动早期 `frame0` 可能是正常黑帧。继续抓取菜单或实际场景后的帧，不用首帧判定最终渲染。
- 等 PPM 完整写入后再拉取，避免截断文件；系统截图不能单独证明 XComponent 内容。
- 使用 `g_vkst` 判断真实 draw/pass；`gfx_common.c` 的空 `mtl_stats()` 不作为绘制证据。
- 用 `DM_VK_SHOT` 区分游戏窗口纹理与 present 问题，用替代 SPV 做单变量 A/B，完成后立即关闭。
- 对 copied/shadow pools 同时检查同步范围、copy-back 范围和实际复制量，避免整池复制造成数量级性能回退。

## 验收

按顺序确认构建、签名、安装、staging、RomFS、存档、guest 推进、shader、draw/compute、音频、原始画面、输入和性能。

最终画面验收必须满足：

- [ ] 关闭 `DM_VK_SPV_ALT`、direct present 和其他替代路径；
- [ ] 使用原始 bundle 和正常最终 blit；
- [ ] 菜单、实际场景和转场均正确，而非只有一张非黑图；
- [ ] 性能数据来自原始 shader 路径；
- [ ] 更新 `ohos-port/docs/verify/20261107-数据包与present.md`，记录版本、设备、PID、日志、截图和未解决项。

当前 BOTW 的完成度和诊断基线限制见参考资料第 1、6 节，不得将历史进度描述扩大为发布就绪。
