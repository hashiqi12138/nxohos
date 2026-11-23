# 评审卡 · 五缺陷根因（实测修正版）

**流程**：product-team skill → 3 角色并行 → 我逐条实测核实

> 本版修正了上一版的 3 个错误判断，全部基于真机实验。

---

## 一、实测证据链（决定性）

| 实验 | 命令/操作 | 结果 | 结论 |
|---|---|---|---|
| 启动 Celeste | 点卡片 | PID 53298 → 53298 | `startGame` 同进程执行 |
| Celeste 渲染 | 采 hilog | 50-53 fps，16670 帧 | 游戏真在跑 |
| 菜单退出按钮 | 滚动面板后 dump | `x=1821..2790 y=1065..1207` | **按钮存在**，需滚动才可见 |
| 点退出 → 确认 | 点 (1992,755) | **PID 变空，进程退出** | `terminateSelf` 真的杀进程 |
| 重启应用 | `aa start` | 新 PID 64712，回 LibraryPage | 冷启干净 |
| 再次启动 Celeste | 点卡片 | 29 fps，1500 帧，无 `startGame ignored` | **冷启后能再进** |

---

## 二、五个缺陷的最终定性

### 问题 1：卡片点击不弹详情

**真因**：`GameCard.ets:284-294` —— `.onClick` 调 `onActivate()`（启动游戏），600ms 长按才 `onDetails()`。

**这是设计，不是 bug**。但用户不知道，且 Celeste 卡片实测显示「可运行」，点击就进游戏了。

**我上一版的错误**：harmony-dev 说「Celeste 恒为 Incomplete 所以点击被吞」。**实测推翻**：设备上 `mods/main.patched.bin` 存在（82 MB），卡片显示「可运行」，点击确实启动了游戏。

### 问题 2：游戏内退出退的是应用

**真因**：`Index.ets:284` `terminateSelf()` + `Index.ets:1857` 文案撒谎。

**实测确认**：点「退出」→ 进程 PID 变空 → 真的关了应用。文案却说「返回游戏库」。

**这是架构必然**：`front_ohos.c:131` 的 `main()` 一旦返回，guest 就结束了；`main.c:42-43` 的 `nnMain` 永不返回。宿主模型 = 一进程一游戏。

### 问题 3：退出后无法再次进入

**真因（这是本次最重要的发现）**：

```c
// napi_bridge.c:48
static void *start_trampoline(void *unused) { 
    dm_ohos_game_start(g_root, g_save, g_temp);   // 游戏退出后返回
    return NULL;                                   // ← g_game_started 仍是 1
}
// napi_bridge.c:56
if (atomic_exchange(&g_game_started, 1)) { 
    logi("napi: startGame ignored"); return undef;  // ← 第二次永远被挡
}
```

**机制**：如果**游戏自己退出**（Celeste 菜单里的 Quit）→ `dm_ohos_game_start` 返回 → 线程结束但**进程活着** → `g_game_started` 卡在 1 → 之后任何 `startGame` 都静默失败 → 页面照样 `isGame=true` → 黑屏无输入。

**为什么我复现不了**：我走的是快捷菜单的 `terminateSelf`，它杀进程，静态变量随之重置。**用户走的是游戏内退出**，进程没死。

**验证方法**（待做）：在 Celeste 里选 Quit，然后 `pidof` 看进程是否还活着。

### 问题 4：设置卡死

**真因（推翻我上一版）**：不是 `SettingsPage.ets:124` 的 `listFileSync`（那只是列一层目录）。

真凶是 `SettingsPage.ets:190` 的 `listGames(files)` → `GameLibrary.ets:475` 的 `treeSize`（`:348-377`），在 UI 线程同步走 40000 文件**无 yield**。同页 `gamesSize` 用 `treeSizeAsync`（`:651-702`，每 400 项 setTimeout(0)），所以「有时」卡——取决于 `.meta.json` 缓存是否命中。

### 问题 5：Celeste 偶发黑屏

**真因（harmony-dev 找到，比我原推断硬）**：`Index.ets:1465` 的 hasGame 分支**缺 `dmhost.stopDemo()`**，而 `:1479` 的无游戏分支有。

先落自检场景 → demo 线程起来（`demo_ohos.c:302` 的 `g_run` 守卫）→ 导入完成走 hasGame 分支 → **游戏和 demo 同时向同一 `OHNativeWindow` 建 surface/swapchain** → 间歇黑屏。

**叠加**：`demo_ohos.c` 停止时不销毁任何 Vulkan 资源（`:289` 跳过 `:281-287`），且 `DM_VK_ENTRIES`（`:33-44`）**没有 `vkDestroyDevice`/`vkDestroyInstance`** —— dev/inst 物理上无法销毁。泄漏的 surface 绑定已销毁的 `OHNativeWindow`（`front_ohos.c:37`），驱动 abort。

---

## 三、修复方案

### P0-1：修问题 3 的根因（最重要）

```c
// napi_bridge.c:48 改为
static void *start_trampoline(void *unused) {
    dm_ohos_game_start(g_root, g_save, g_temp);
    // The guest returned: it is over for this process. Clear the latch so a second start reports
    // "the guest already ran" rather than silently doing nothing — and let the page learn it.
    atomic_store(&g_game_state, 2);   // 2 = finished
    return NULL;
}
```

配合新增 `gameState()`：`0=未启动 1=运行中 2=已结束`。ArkTS 在 `startOnce` 里先查：

- `1` 或 `2` → 不调 `startGame`，显示「本进程已结束一次游戏，请完全关闭应用后重开」，并提示退出

### P0-2：修问题 2 的文案

`Index.ets:1857` 改为：
> 将关闭应用，下次打开回到游戏库。未保存的进度会丢失。

`Index.ets:1831` 按钮文字改为「退出游戏（关闭应用）」。

### P0-3：修问题 5

`Index.ets:1465` 的 hasGame 分支开头补 `dmhost.stopDemo();`，与 `:1479` 对齐。

### P1：修问题 4

`SettingsPage.ets:190` 改调 `listGamesAsync()`；或更简单——设置页只读 `.meta.json`，不算 size。

### P1：修问题 1

改成**点击进详情，详情页里「启动游戏」**（按钮已存在 `GameDetailSheet.ets:203-212`）。这样点击和长按不再竞争。

### P2：demo 资源清理

`demo_ohos.c` 加 `cleanup` 标签统一销毁；`DM_VK_ENTRIES` 补 `vkDestroyDevice`（`vkDestroyInstance` 因 Maleoon 驱动风险先不加）。

---

## 四、待你裁决

**Q1：问题 3 的修法**

| 方案 | 做法 | 代价 |
|---|---|---|
| A（建议） | 加 `gameState()`，检测到"已结束"就明确告知并引导退出 | 小，符合"一进程一游戏"架构 |
| B | 让 guest 退出后线程自杀（`_exit(0)`） | 进程直接消失，用户看到闪退 |
| C | 重写宿主支持真重入 | 要 teardown 七项，等于重写 |

**Q2：问题 1 的交互**

保持「点击=启动，长按=详情」加提示，还是改成「点击=详情，详情内启动」？

---

## 五、诚实说明：我的实测缺口

- **没验证「游戏内 Quit」路径**：需要进 Celeste 菜单选 Quit，但这要操作游戏内 UI，我没做到
- **没复现黑屏**：需要"先自检再导入"的特定时序
- **TOTK 只测了标题画面**：8 fps，没进游戏内

---

## 六、我上一版评审卡的三个错误（已在本版修正）

| 错误 | 实际 |
|---|---|
| 「Celeste 恒为 Incomplete，点击被吞」 | 实测「可运行」，点击能启动 |
| 「设置卡死是 listFileSync」 | 是 treeSize 在 UI 线程无 yield |
| 「退出按钮被挤出屏幕不可见」 | 在 Scroll 内，滚动后可见（用户指出） |
