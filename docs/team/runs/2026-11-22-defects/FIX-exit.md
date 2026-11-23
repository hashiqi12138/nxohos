# 问题 2 修复交付：优雅退出 + 文案说真话

> 方案 B（用户 2026-11-23 00:00 选定）
> 实测通过：2026-11-23 00:05

---

## 改了什么

### 1. NAPI 新增 `stopGame`（`napi_bridge.c`）

```c
void dm_host_shutdown(const char *why);   // hle_lifecycle.c，新增前向声明

static napi_value js_stop(napi_env env, napi_callback_info info) {
    (void)info; napi_value undef; napi_get_undefined(env, &undef);
    logi("napi: stopGame: the player is leaving; graceful exit");
    dm_host_shutdown("user quit");   // 不返回（_exit(0)）
    return undef;
}
```

导出表加 `{"stopGame", js_stop}`。

**关键**：`dm_host_shutdown` 是 `hle_lifecycle.c` 里已有的完整优雅退出，按顺序做七件事：

```c
hlecount_flush();      // 存档计数落盘
dm_rec_finish();       // 录制缓冲
dm_cheats_exit();      // 金手指状态
dm_audio_suspend(1);   // 暂停音频
dm_gfx_quit_begin();   // 停止预编译
dm_gfx_suspend(1);     // 排空图形（最多 1500ms）
dm_gfx_quit(...);      // 写 pipeline 归档
exit_end(then, arg);   // _exit(0)
```

### 2. `exitToLibrary()` 改调 `stopGame`

`Index.ets:278` 现在先 `dmhost.stopGame()`（走优雅退出），再 `terminateSelf()` 作为兜底（万一排空卡住超过 deadline）。

### 3. 文案改成真话

| 位置 | 改前 | 改后 |
|---|---|---|
| 按钮 `Index.ets:1848` | 退出到游戏库 | **退出游戏** |
| 对话框 body | 将关闭游戏并返回游戏库 | **将关闭游戏并退出应用，下次打开回到游戏库** |

---

## 为什么是「退出应用」而不是「回库页」

`EntryAbility.ets:21` 启动时无条件加载 `pages/LibraryPage`。所以：

- 退出 = 关应用
- **下次打开 = 自动回库页**

用户看到的是「游戏关了，再打开就是库页」，这和「退出到游戏库」的体感一致，只是中间隔了一次冷启。

**为什么不能真回库页**：guest 一旦启动就拥有进程（`napi_bridge.c` 的 `g_game_started` 一次性闩 + `main.c` 的 `nnMain` 永不返回）。要真回库页需要 teardown 七项状态（地址空间/模块镜像/Vulkan/present 线程/audio/atexit/SVC 闸门），等于重写宿主。

---

## 实测验证（真机 PBN-AL80）

### 日志链

```
page: library: leaving the game; graceful exit then terminate
napi: stopGame: the player is leaving; graceful exit
[dm] received user quit: graceful exit
[dm] graceful exit done (1.501 s)
```

**1.501 秒**完成排空——正好是 `EXIT_DRAIN_MS = 1500` 的预算，说明图形管线真的在排空而不是超时跳过。

### 进程与重启

| 步骤 | PID | 页面 |
|---|---|---|
| 退出前 | 3376 | pages/Index（49 fps） |
| 点确认后 | **空（已退出）** | — |
| 重开 | 4452 | pages/LibraryPage ✓ |
| 再启动 Celeste | 4452 | pages/Index（36 fps）✓ |

**「退出后无法再进」在快捷菜单路径上已解决**：因为优雅退出 + 杀进程 + 重开，全程状态干净。

---

## 遗留问题

### 1. 「游戏内 Quit」路径仍未修（问题 3 的另一半）

如果用户是在 Celeste 菜单里选 Quit（而不是快捷菜单），走的是：

```
guest 自己结束 → main() 返回 → front_ohos.c:132 打日志
→ start_trampoline 返回 → g_game_started 仍为 1
→ 之后 startGame 静默失败（napi_bridge.c:56）
→ 页面 isGame=true 但无画面
```

**这条路径的修法**（待做）：`start_trampoline` 返回后置 `g_game_state = 2`（已结束），ArkTS 检测到就提示「本进程已结束一次游戏，请重开应用」。

### 2. 退出按钮仍在 Scroll 内需滚动

`Index.ets:1694` 的 `Scroll()` 包着整个面板，面板内容高度超过屏幕（1260），退出按钮在 y=1065..1207，**需要上滑才露出**。

建议：把「退出游戏」移到面板顶部（标题行右侧），或固定为面板底部不滚动的 footer。

### 3. 其他四个缺陷未动

- 问题 1（点击不弹详情）：待定交互方案
- 问题 4（设置卡死）：`SettingsPage.ets:190` 的 `listGames` → `treeSize` 在 UI 线程
- 问题 5（偶发黑屏）：`Index.ets:1465` 缺 `stopDemo()`
- demo 资源泄漏：`demo_ohos.c` 的 9 处 return 不销毁

---

## 改动清单

```
dm/ohos/napi_bridge.c                          +13 -1   （stopGame 导出）
dm/ohos/hap/entry/src/main/ets/pages/Index.ets +20 -6   （exitToLibrary + 文案）
```

产物：`dmhost-signed.hap` 3306491 B（versionCode 2099000004）
