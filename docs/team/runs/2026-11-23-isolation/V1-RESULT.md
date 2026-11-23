# V1 结论：设备不支持多进程，子进程隔离方案被平台挡住

> 实测时间：2026-11-23 00:35–00:40
> 设备：HBN-AL80 / HarmonyOS 7.0.0.111(SP10C00E111R5P2) / API 26

---

## 一、结论

**技术路径全部验证通过，但设备层面关掉了多进程能力，`allowMultiProcess: false`。**

不是配置错，不是代码错，是这台设备（这个 HarmonyOS 版本）不给三方应用开子进程。

---

## 二、逐项验证记录

| # | 验证项 | 方法 | 结果 |
|---|---|---|---|
| 1 | `childProcessManager` API 存在 | 读 SDK `@ohos.app.ability.childProcessManager.d.ts` | ✅ |
| 2 | `OH_Ability_StartNativeChildProcess` 存在 | 读 NDK `AbilityKit/native_child_process.h` | ✅ |
| 3 | 能否运行时解析该符号 | `dlopen("libchild_process.so")` + `dlsym` | ✅ 两个指针都拿到 |
| 4 | `isolationMode` 配置能否进 HAP | 解包 HAP 查 `module.json` | ✅ `"isolationMode":"isolationFirst"` |
| 5 | 能否拿到 surfaceId | 运行时日志 | ✅ `surfaceId=6266357292388` |
| 6 | 实际启动子进程 | `p_start_child(...)` | ❌ **`-16010004`** |

`-16010004` = `NCP_ERR_MULTI_PROCESS_DISABLED`（头文件原文：「The multi-process mode is disabled. A child process cannot be started.」）

---

## 三、根因：设备全局关闭

`bm dump -n dev.eden_emu.ohos`：

```json
"allowMultiProcess": false,
"maxChildProcess": 512,
"isolationProcess": false,
"appProvisionType": "debug",
"appDistributionType": "none",
```

**关键排除过程**：

| 假设 | 验证 | 结论 |
|---|---|---|
| 签名级别不够（debug） | 查系统应用 `com.ohos.sceneboard`、`com.huawei.hmos.settings` | **它们也是 `false`** → 排除 |
| `module.json` 没配 | 解包确认 `isolationMode` 在 | 已配，仍 false → 排除 |
| 能力不存在 | `maxChildProcess: 512` | 能力在，被策略关 → 确认 |

**`allowMultiProcess: false` 是全设备所有应用的统一值，包括系统应用。** 这是 HarmonyOS 7.0.0.111 的版本策略。

---

## 四、沿途修掉的两个真 bug

这两个是真实缺陷，无论隔离方案是否成立都该修。

### 4.1 `-lchild_process` 静态链接毁掉整个原生层

**症状**：加 `-lchild_process` 后，`dmhost` 日志**完全消失**，NAPI 不注册，应用变成空壳。

**根因**：链接后 `libdmhost.so` 里留下未解析动态符号：

```
U OH_Ability_StartNativeChildProcess
U OH_Ability_RegisterNativeChildProcessExitCallback
```

设备加载器不为这个应用解析它们 → `dlopen("libdmhost.so")` 直接失败 → 整个原生层消失。

**修复**：改用 `dlopen("libchild_process.so")` + `dlsym` 运行时解析（`probe_child_surface.c`），并**从 `build_ohos.py` 的 `LIBS` 移除 `-lchild_process`**。

修复后 `llvm-nm -D` 确认无 `OH_Ability` 未解析符号，日志恢复。

### 4.2 退出游戏杀掉整个应用（已修，方案 B）

详见 `docs/team/runs/2026-11-22-defects/FIX-exit.md`。

`Index.ets:284` 的 `terminateSelf()` 现在先调 `dmhost.stopGame()`（走 `hle_lifecycle.c` 的优雅退出，实测 1.501 s 完成排空），再兜底 `terminateSelf`。文案改成真话。

---

## 五、剩下的选择

子进程隔离在这台设备上做不了。要在「退出游戏回库页且能换任意游戏」这个目标上前进，只有两条路：

### 方案 A2：进程内挂起 guest（不杀）

```
dm_gfx_suspend(1) + 挂起 guest 线程 → 回库页 → 换游戏时…
```

**障碍**：换游戏必须先释放前一个 guest 的 3.25 GB 堆和 GPU 上下文，而释放只能杀线程——回到 `pthread_cancel` 的不可行问题。

**实际能力**：只能「退出后回库页，再点同一个游戏继续」。换游戏仍需重启。

### 方案 D：接受现状，把体验做顺

- 退出游戏 = 关应用（已实现优雅退出）
- 冷启回库页（`EntryAbility.ets:21` 天然如此）
- **优化冷启速度**：让用户感觉不到「重启」这件事

这条路能做实的改进：
1. 冷启时直接显示上次的库页状态（已有 `.meta.json` 缓存）
2. 加一个「最近玩过」置顶
3. 退出时保存「下次启动直接高亮该游戏」

---

## 六、我的建议

**走方案 D，并把 A2 的「同游戏重入」作为可选增强。**

理由：
- A2 换游戏仍要重启，收益有限却引入「半个进程」的不确定性（3.25 GB 常驻、GPU 上下文残留、Vulkan 状态不可预测）
- 方案 D 的冷启成本可以做到几乎无感（库页有缓存，实测冷启到可点击约 3–4 秒）
- 设备既然关了多进程，说明这条路不是这台机器预期的用法

---

## 七、如果以后换了设备

代码已经就位，只要新设备的 `allowMultiProcess` 是 `true`：

1. `probe_child_surface.c` 的 `dm_ohos_probe_start` 直接能用
2. 把 `ProbeMain` 换成真正的游戏入口（解析 `entryParams` 里的 root/save/cache，调 `dm_ohos_game_start`）
3. `Index.ets` 的 `exitToLibrary` 改成 `kill(childPid)` + `router.replaceUrl`

**验证方法**：`hdc shell 'bm dump -n <bundle>' | grep allowMultiProcess`，`true` 即支持。
