# P3.3：鸿蒙平台优化 API 实测清单（游戏模式 / Vulkan 底层加速）

> 状态：**真机实测完成**（HBN-AL80 / Maleoon 910 / HarmonyOS 7.0.0.111 / API 26）
> 方法：`/data/local/tmp` 在本机禁止执行（SELinux `data_local_tmp`），所以走 HAP + NAPI 探针，
> 报告写到 `filesDir`，用 `hdc shell cat /data/app/el2/100/base/<bundle>/haps/entry/files/probe*.txt` 取回。
> 所有 API 用 `dlopen`/`dlsym` 调用，探针二进制只链 libc/libdl。

---

## 0. 一句话结论

| 需求 | 鸿蒙有没有 | 本项目能不能用 |
|---|---|---|
| 「进入游戏自动开启游戏模式」的 API | **没有**（不是 API，是系统按应用分类 + 用户开关决定的） | — |
| 让系统知道"我在跑游戏、该给我多少帧/多少频率" | **有**：OpenGTX（LTPO）/ GamePerformance（场景感知） | ✅ 实测全部返回 0，可直接接 |
| Vulkan 平台特有的底层优化 | **有**：XEngine（XEG_* 扩展） | ⚠️ 本机 GPU 只暴露 2 个扩展 |
| 超帧（插帧） | **有**：libframegeneration（Vulkan 版） | ✅ 上下文能建，但需要真图像才能 dispatch |
| 自适应分辨率（ABR） | **有** | ❌ **只有 GLES 版**，Vulkan 路径用不了 |
| 帧率/刷新率请求 | **有**：NativeDisplaySoloist / NativeVsync | ✅ 30/60 精确生效，45 被量化到 30 |
| 线程调度类 | **有**：QoS（libqos） | ✅ 6 档全部可设，但**代码里用错了档** |

---

## 1. 关于「游戏模式」——它不是一个 API

用户直觉里"进入游戏自动开游戏模式"，在鸿蒙上的真实形态是三层：

### 1.1 系统侧（应用无法直接调用）

`游戏助手 / 高性能模式`（HarmonyOS 5.0.1.130 起，6.0 起部分机型默认开启）由系统根据
**应用分类 = 游戏**（AGC 上架时选择）+ 用户在游戏助手里的单游戏开关决定。
公开 API 里**没有** `setGameMode()` 这种东西；`@ohos.power` 的 `MODE_PERFORMANCE` 属于系统接口，
普通应用申请不到。

> 参考：[配置应用分类标签](https://developer.huawei.com/consumer/cn/doc/app/agc-help-release-game-class-0000002398530749)、
> [创建游戏（AGC）](https://developer.huawei.com/consumer/cn/doc/doccenter-capabilities/gameservice-gameperformance-config-agc)

### 1.2 应用侧：主动告诉系统"我在跑什么"（**这才是本项目该做的**）

两条通道，实测都通：

**A. OpenGTX（GPU Turbo X 的开放入口）—— 管帧率/刷新率/SOC-DDR 频率**

```
libopengtx.so  HMS_OpenGTX_CreateContext / SetConfiguration / Activate
                / DispatchGameSceneInfo / DispatchFrameRenderInfo / Deactivate / DestroyContext
```

实测（bundle = 本应用自己的包名，ADAPTIVE_MODE，target 60，vulkanSupport = true）：

```
gtx: CreateContext=0x5b0a80f800
gtx: SetConfiguration(ADAPTIVE,target 60,vulkan 1)=0
gtx: Activate=0
gtx: DispatchGameSceneInfo(PLAYING)=0
gtx: DispatchFrameRenderInfo=0
gtx: Deactivate=0
```

**全部 0 = 成功**。`OPENGTX_SUCCESS = 0`。
（未配置就 Activate 会返回 `1009502001 = OPENGTX_CONTEXT_NOT_CONFIG`，说明校验是真的在跑。）

要生效还需要一步**声明**（官方指南要求，本项目 `module.json5` 目前**没有**）：

```json5
"metadata": [
  { "name": "GraphicsAccelerateKit_LTPO", "value": "true" }
]
```

三种 LTPO 模式：`SCENE_MODE(0x1)` / `TOUCH_MODE(0x10)` / `ADAPTIVE_MODE(0x100)`。
对本项目这种"手柄 + 稳定 30/60"的形态，`SCENE_MODE` 比 `ADAPTIVE_MODE` 更合适——
TOUCH_MODE 是按触控频率调帧率，而 nx2ohos 的输入是虚拟手柄，触控信号没有意义。

**B. GamePerformance（游戏场景感知）—— 管温控/GPU 负载反馈**

```
libgame_performance.z.so  HMS_GamePerformance_Init / QueryGpuInfo / QueryThermalInfo / RegisterThermalLevelChangedCallback
```

实测（bundle = 本应用，Init 成功）：

```
gp: Init=0
gp: QueryGpuInfo=0
gp:   GpuLoadLevel    = 1        (1..10)
gp:   CurrentFrequency= 308000 kHz
gp:   BandwidthLoadLevel = 1
gp: QueryThermalInfo(NULL params)=401      ← 必须传查询参数结构体
```

`401` 是参数校验失败，不是不支持：要用
`CreateThermalInfoQueryParameters` → `SetNeedsPrediction` / `SetTargetThermalLevel` → `QueryThermalInfo`。

**这正好补上 `perf_ohos.c` 里那句注释的空缺**：

```c
// Android's Android Dynamic Performance Framework hint is left out (HarmonyOS has no such API here)
```

—— 鸿蒙确实有对应物，就是 GamePerformance。它给的 `tempLevel(1..6)` / `thermalTrend` /
`thermalMargin(还有多少秒到某档)` 可以喂给现有的 `dm_present_period_adapt()`，
比现在纯靠帧时间自适应的反应更快（能在掉帧**之前**降档）。

### 1.3 场景语义（`GameSceneInfo.sceneID`）

官方定义：`1 启动 / 2 游戏内更新 / 3 登录 / 4 主界面 / 5 加载 / 6 等待其他玩家 / 7 游戏中 / 8 观战`。
本项目可以直接映射：标题画面 = 4，加载 = 5，进入游戏 = 7。系统据此决定给多少资源。

---

## 2. Vulkan 平台特有优化：XEngine

`libxengine.so`，通过 `HMS_XEG_EnumerateDeviceExtensionProperties(physicalDevice, ...)` 查询。

**实测本机（Maleoon 910）只暴露 2 个扩展**：

```
xeg: enumerate r=0 count=2
xeg:   XEG_spatial_upscale v1
xeg:   XEG_adaptive_vrs  v1
```

| 扩展 | 本机 | 对 nx2ohos 的意义 |
|---|---|---|
| `XEG_spatial_upscale` | ✅ | **最值得接**：空域 GPU 超分。游戏内部按低分辨率渲染 → 超分到屏幕。BOTW 现在 1920×1080 的窗口纹理放大到 2844×1260 是每帧的固定开销，这条路可以替掉手写的 `vkCmdBlitImage` |
| `XEG_adaptive_vrs` | ✅ | 自适应可变着色率：按上一帧颜色+当前深度算着色率图，GPU 自动降片元着色密度。对 BOTW 的草地/远景填充率有效 |
| `XEG_temporal_upscale` | ❌ | 时域 AI 超分（需要运动矢量 + 深度 + 抖动）。**本机不支持**，只在别的 GPU 上 |
| `XEG_neural_upscale` | ❌ | 空域 AI 超分，**GLES only**，且要 `OH_NativeBuffer` 输入 |
| `XEG_rtgi` / `rt_shadow_ao` / `rt_reflection` | ❌ | 光追系列，本机不暴露 |
| `XEG_hps_radix_sort` | ❌ | 高性能基数排序（GPU 粒子/剔除用） |

> 注意：设备上 `/system/lib64/ndk/libxengine.so` 的 `strings` 里有 `HMS_XEG_CreateTemporalUpscale`
> 等全部符号，但**扩展枚举不返回** = 驱动这一侧不支持。符号存在 ≠ 能用。

### 2.1 `XEG_spatial_upscale` 的接入点

```c
#include <xengine/xeg_vulkan_spatial_upscale.h>   // libxengine.so

XEG_SpatialUpscaleCreateInfo ci = {
    .inputSize  = { 1920, 1080 },
    .inputRegion= { {0,0}, {1920,1080} },
    .outputSize = { 2844, 1260 },
    .outputRegion={ {0,0}, {2844,1260} },
    .format     = VK_FORMAT_R8G8B8A8_UNORM,
    .sharpness  = 0.2f,
};
HMS_XEG_CreateSpatialUpscale(V.dev, &ci, &g_xeg_su);
// 每帧，在 present 前：
XEG_SpatialUpscaleDescription d = { .inputImage = game_view, .outputImage = swapchain_view };
HMS_XEG_CmdRenderSpatialUpscale(cb, g_xeg_su, &d);
```

直接替换 `gfx_vk_present_ohos.c: record_frame()` 里的
`vkCmdBlitImage(..., VK_FILTER_NEAREST)`（1920×1080 → 2844×1260 每帧一次，P3.1 记录它是 GPU 时间的大头）。

---

## 3. 超帧（Frame Generation，Vulkan 版）

`libframegeneration.so`。实测上下文能建：

```
fg: CreateContext_VK(framesInFlight 2)=0x5b0a7b9b80
fg: SetAlgorithmMode(INTERP,BASIC)=0
fg: DestroyContext=0
```

`FG_ContextDescription_VK` 要的是真的 `VkInstance` / `VkPhysicalDevice` / `VkDevice` +
`fnVulkanLoaderFunction = vkGetInstanceProcAddr`，本项目 `gfx_vk_core.c` 里全都有，接得进去。

两种模式：
- **内插**（`INTERPOLATION`）：N-1 与 N 帧之间插 N-0.5。画质好，但**多一帧延迟**。
- **外插**（`EXTRAPOLATION`）：预测 N+0.5。延迟低，但鬼影多。

对 nx2ohos：BOTW 是 30fps 目标、动作向，**外插更合适**；但超帧要求输入是"不透明场景颜色 + 深度"，
而 NVN 的渲染管线有大量 alpha/透明 pass（`hle_nvn.c` 的 `feedback_snap`、粒子），
基础模式在"复杂粒子特效场景可能引入鬼影"——官方明说了这个约束。
**建议排在 XEG_spatial_upscale 之后**，先拿超分，再考虑插帧。

`FG_ME_MODE_ENHANCED`（增强运动估计）官方明确 **仅 GLES，Vulkan 不支持**。

---

## 4. ABR：不能用（只有 GLES）

```
abr: CreateContext(GLES)=0        ← 0 就是 NULL，创建失败
```

`ABR_RenderAPI_Type` 枚举**只有 `RENDER_API_GLES = 0` 一个值**，没有 Vulkan。
nx2ohos 是纯 Vulkan 后端（`build_ohos.py --gfx vk`），这条路直接排除。
分辨率自适应只能在 XEG_spatial_upscale / 自己的 render scale 上做。

---

## 5. 帧率请求：NativeDisplaySoloist 实测

`libnative_display_soloist.so`，`OH_DisplaySoloist_SetExpectedFrameRateRange`。

实测（每档 700 ms，本机面板当前 60 Hz）：

| 请求 | 实测 | 首个间隔 | 结论 |
|---|---|---|---|
| 不设 | 60.3 Hz | — | 默认跟随面板 |
| 30 | **30.9 Hz** | 33.15 ms | ✅ 精确 |
| 45 | **30.2 Hz** | 33.15 ms | ⚠️ 被量化到 30 |
| 60 | **60.3 Hz** | 16.57 ms | ✅ 精确 |
| 90 | 60.3 Hz | 16.57 ms | 面板上限 |
| 120 | 60.3 Hz | 16.57 ms | 面板上限 |

**两个可用的结论**：

1. **45 不是合法档位**——平台按面板频率的整除档位量化（60 → 30/60），
   本项目 `perf_ohos.c` 的 `STEPS[] = {60, 40, 30}` 里 **40 fps 这一档在鸿蒙上落不到 30 或 60 之间的任何真实刷新率**，
   会表现为"请求 40 实际 30"。建议改成 `{60, 30}` 两档，或 30/60/120。
2. `OH_DisplaySoloist_FrameCallback` 带回 **`targetTimestamp`**（下一帧预期时间），
   这正是 `mtl_presented_time()` 现在返回 0 缺的东西（`gfx_vk_present_ohos.c:438`
   `// no display timing here; the governor's late check stays off`）。
   接上它，`hle_nvn.c` 的 `mtl_snap_on()` 迟到判定（`g_fs.screen`）就能真正工作。

`OH_NativeVSync_GetPeriod()` 实测返回 **8.287 ms**（≈120.7 Hz）——注意这是**面板能力**，
不是当前刷新率；当前刷新率要另取。`DVSyncSwitch` 返回 0（可用），但只对**自绘制动画**有意义。

---

## 6. QoS：能用，但代码里档位选错了

实测 6 档全部可设、可读回：

```
qos: set(0..5)=0, get -> 0..5     (BACKGROUND/UTILITY/DEFAULT/USER_INITIATED/DEADLINE_REQUEST/USER_INTERACTIVE)
qos: reset=0
```

**但项目里两处不一致**：

| 位置 | 用的档 | 依据 |
|---|---|---|
| `compat/darwin_compat.c:186` | `QOS_USER_INTERACTIVE` | P3.1 实测：`DEADLINE_REQUEST` 下 "demoted in 300 of 300 frames" |
| `gfx_vk_present_ohos.c:346` | `QOS_DEADLINE_REQUEST` | ← **仍然是错的档** |

P3.1 文档已经写明 `DEADLINE_REQUEST` 会被设备调度器立刻收回，而 present 线程至今还用着它。
**这是一处待修的实际缺陷**，不是新特性。

---

## 7. 落地优先级（按收益/成本）

| # | 改动 | 收益 | 成本 |
|---|---|---|---|
| 1 | `gfx_vk_present_ohos.c:346` `DEADLINE_REQUEST` → `USER_INTERACTIVE` | present 线程不再每帧被降级 | 1 行 |
| 2 | `module.json5` 加 `GraphicsAccelerateKit_LTPO=true` metadata + 接 OpenGTX `SCENE_MODE` | 系统按游戏场景调频/调帧率 | ~150 行 C |
| 3 | `mtl_presented_time()` 用 DisplaySoloist 的 `targetTimestamp` 填上 | 迟到判定生效，帧步进决策准确 | ~60 行 |
| 4 | XEG_spatial_upscale 替掉 present 的 `vkCmdBlitImage` | 每帧固定 GPU 开销下降 | ~120 行 |
| 5 | GamePerformance 的 `tempLevel/thermalTrend` 喂给 `dm_present_period_adapt()` | 掉帧前降档 | ~80 行 |
| 6 | `STEPS[]` 去掉 40 fps 档 | 自适应不再请求不存在的刷新率 | 1 行 |
| 7 | XEG_adaptive_vrs | 填充率 | ~200 行，需改 pass 结构 |
| 8 | Frame Generation 外插 | 30→60 观感 | ~300 行，鬼影风险 |

**不建议**：ABR（只有 GLES）、temporal/neural upscale（本机不暴露）、rtgi（不暴露）。

---

## 8. 复现方法

```powershell
# 1) 编译探针（三个 .c 见本机临时目录，已归档到 docs/ohos/probe/）
clang --target=aarch64-linux-ohos --sysroot=<SDK>/native/sysroot -O2 -fPIC -shared `
      -o libohosprobeN.so probeN.c -ldl -lvulkan -lace_napi.z

# 2) 放进 hap/entry/libs/arm64-v8a/，hvigor assembleHap（DevEco 自动签名）

# 3) 安装 + 拉起
hdc install -r entry-default-signed.hap
hdc shell "aa start -a EntryAbility -b dev.eden_emu.ohos"

# 4) 取报告
hdc shell "cat /data/app/el2/100/base/dev.eden_emu.ohos/haps/entry/files/probe3.txt"
```

**坑**：
- `/data/local/tmp` 在本机 **不可执行**（`Permission denied`，SELinux 上下文 `data_local_tmp`），
  别想直接 `hdc file send` 一个 ELF 就跑。
- `run-as` 在本机不存在；读沙箱文件走 `/data/app/el2/100/base/<bundle>/haps/entry/files/`。
- `app.json5` 里带注释时 PowerShell 改 `versionCode` 会破坏 JSON5 → 用 hvigor 构建前把注释去掉。
- 设备锁屏时 `aa start` 报 `10106102`；先 `power-shell wakeup` + `uitest uiInput swipe` 解锁。
