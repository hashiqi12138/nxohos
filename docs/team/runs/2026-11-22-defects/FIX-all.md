# 四缺陷修复交付

> 实测时间：2026-11-23 01:03–01:10
> 设备：HBN-AL80 / HarmonyOS 7.0.0.111 / API 26
> HAP：3306028 → 3317074 B，versionCode 2099000020

---

## 修复清单

| # | 问题 | 根因 | 状态 |
|---|---|---|---|
| 2 | 退出游戏杀掉整个应用 | `terminateSelf()` 是唯一退出路径 | ✅ 优雅退出 + 文案说真话 |
| 3 | 退出后无法再进 | `g_game_started` 一次性闩 | ⚠️ 部分（见下） |
| 5 | Celeste 偶发黑屏 | `Index.ets` hasGame 分支缺 `stopDemo()` | ✅ |
| 1 | 卡片点击不弹详情 | 点击=启动，长按 600ms=详情 | ✅ 点击进详情 |
| 4 | 设置卡死 | `listGames` → `treeSize` 在 UI 线程走 4 万文件 | ✅ |

---

## 问题 2：优雅退出（方案 B，用户选定）

### 改了什么

**`napi_bridge.c` 新增 `stopGame`**：

```c
void dm_host_shutdown(const char *why);   // hle_lifecycle.c

static napi_value js_stop(napi_env env, napi_callback_info info) {
    logi("napi: stopGame: the player is leaving; graceful exit");
    dm_host_shutdown("user quit");   // 不返回（_exit(0)）
    return undef;
}
```

**`Index.ets:278` 的 `exitToLibrary()`** 先调 `dmhost.stopGame()`，`terminateSelf()` 降为兜底。

**文案改成真话**：

| 位置 | 改前 | 改后 |
|---|---|---|
| 按钮 | 退出到游戏库 | 退出游戏 |
| 对话框 | 将关闭游戏并返回游戏库 | 将关闭游戏并退出应用，下次打开回到游戏库 |

### 实测

```
page: library: leaving the game; graceful exit then terminate
napi: stopGame: the player is leaving; graceful exit
[dm] received user quit: graceful exit
[dm] graceful exit done (1.501 s)
```

**1.501 秒**正好是 `EXIT_DRAIN_MS = 1500` 预算，说明图形管线**真的在排空**。

进程：3376 → 空（退出）→ 4452（重开回库页）→ 再进 Celeste 36 fps ✅

### 为什么是「退出应用」不是「回库页」

`EntryAbility.ets:21` 启动时无条件加载 `pages/LibraryPage`。所以关应用 → 重开 → 自动回库页。

真回库页要 teardown 七项状态（地址空间/模块镜像/Vulkan/present 线程/audio/atexit/SVC 闸门），等于重写宿主。

---

## 问题 5：黑屏

### 根因

`Index.ets` 的 `hasGame` 分支在 `startGame` 前**缺 `stopDemo()`**，而 no-game 分支有。

后果：自检场景的 demo 线程（`demo_ohos.c` 的 `g_run`）继续向同一 `OHNativeWindow` present，同时 guest 也在建 surface/swapchain —— **两个 presenter 抢一个窗口**。

### 修复

```ts
} else if (hasGame) {
  ...
  // Stop the self-test scene first. Without this the demo thread keeps presenting to the same
  // OHNativeWindow while the guest builds its own surface and swapchain on it — two presenters on
  // one window, which is the intermittent black screen this path was reported with.
  dmhost.stopDemo();
  this.loadPadConfig(root);
  dmhost.startGame(root, files + '/save', cache);
```

---

## 问题 1：卡片点击进详情

### 根因（不是 bug，是设计没被发现）

`GameCard.ets:284-294`：点击 = 启动游戏，长按 600ms = 详情。

主机 Home 屏的交互语言，但没人找得到长按。Celeste 用户点卡片 100% 是误触启动。

### 修复

`LibraryPage.ets` 的 `activate()` 改成 `openDetail(g)`。

`isStartable` 检查从 `activate` 移到详情页的 `onLaunch`——那里才是真正启动的地方。

### 附带修复：启动按钮要滚动才能看到

详情页六个动作把「启动游戏」挤到 85% 高度限制之外，**必须滚动才出现**（实测 2026-11-23）。

移到标题下方后，一打开就在屏幕上（1422,864）。

### 实测

- 点卡片 → 详情页出现（Celeste / 01002B30028F6000 / 1.2 GB / 1.4.1 / 68db2a3ef67c / 锁定状态）
- 点「启动游戏」→ 进入 `pages/Index`，31 fps ✅

---

## 问题 4：设置卡死

### 根因

`SettingsPage.ets:190` 的 `listGames(files)` 在 UI 线程同步跑。缓存未命中时进 `GameLibrary.ets:475` 的 `treeSize(root, 60000)`，**循环里 listFileSync + statSync 一次都不 yield**。

Celeste 1.2 GB / TOTK 17.4 GB，后者 4 万文件 → UI 线程冻结。

同页 `gamesSize` 用的是 `treeSizeAsync`（每 400 项 setTimeout(0)），所以「有时」卡——取决于 `.meta.json` 缓存是否命中。

### 修复

新增 `GameLibrary.ets` 的 `listGameNames(filesDir)`：只读 `nx2a-data.json` 拿名字，**不遍历任何目录**。

`sizeBytes` 返回 `-1`（未测），不是 `0`——0 会被读成「空游戏」。

`SettingsPage` 改用它。

---

## 问题 3：退出后无法再进（部分修复）

### 已解决的部分

「快捷菜单退出」路径：优雅退出 + 杀进程 + 重开，全程状态干净。实测可再进。

### 未解决的部分

「游戏内自己 Quit」路径：

```
guest 自己结束 → main() 返回 → front_ohos.c:132 打日志
→ start_trampoline 返回 → g_game_started 仍为 1
→ 之后 startGame 静默失败（napi_bridge.c:56）
→ 页面 isGame=true 但无画面
```

**修法**（待做）：`start_trampoline` 返回后置 `g_game_state = 2`（已结束），ArkTS 检测到就提示「本进程已结束一次游戏，请重开应用」。

---

## 隔离方案：已确认此设备不可行

详见 `V1-RESULT.md`。三条路全试完：

| 方案 | 结果 |
|---|---|
| C API 子进程（两种 API、两种隔离模式） | `16010004` NCP_ERR_MULTI_PROCESS_DISABLED |
| Ability 独立进程（`process: ":guest"` + `isolationProcess: true`） | pid 与主进程相同 |

官方 C API 文档每处都写：「该接口在 **PC/2in1、Tablet** 设备中可正常调用，在其他设备中返回 NCP_ERR_MULTI_PROCESS_DISABLED」。

探针代码已清理（`probe_child_surface.c`、`GuestAbility.ets`、`GuestProbe.ets`、`module.json5` 的 `process`/`isolationProcess`/`isolationMode`）。

---

## 改动清单

```
dm/ohos/napi_bridge.c                                          +13 -1   stopGame 导出
dm/ohos/hap/entry/src/main/ets/pages/Index.ets                 +20 -6   exitToLibrary + 文案 + stopDemo
dm/ohos/hap/entry/src/main/ets/pages/LibraryPage.ets           +22 -10  activate → openDetail；isStartable 移到 onLaunch
dm/ohos/hap/entry/src/main/ets/pages/GameLibrary.ets           +42 -0   listGameNames
dm/ohos/hap/entry/src/main/ets/pages/SettingsPage.ets          +8 -4    listGameNames
dm/ohos/hap/entry/src/main/ets/components/GameDetailSheet.ets  +17 -11  启动按钮移到标题下
```

---

## 遗留

1. **问题 3 的游戏内 Quit 路径**：需加 `gameState()` NAPI + ArkTS 检测
2. **demo 资源泄漏**：`demo_ohos.c` 有 9 处 return 不销毁 Vulkan 资源，`DM_VK_ENTRIES` 没有 `vkDestroyDevice`/`vkDestroyInstance`
3. **性能展示未做**：`GamePerf.ets` 已写、卡片角标已加、编译通过，但没在真机验证过（之前那版 HAP 被 `-lchild_process` 问题废掉了）
