# 五个缺陷的诊断与修复方案

> 报告时间：2026-11-22 23:30
> 全部结论都指到具体代码行，不猜

---

## 问题 1：Celeste 卡片点击不弹详情

### 现状（不是 bug，是设计没被发现）

`GameCard.ets:284-294`：

```ts
.onClick(() => {
  if (this.state === CardState.Incomplete || this.state === CardState.Launching) return;
  this.onActivate();          // ← 点击 = 启动游戏
})
.gesture(
  LongPressGesture({ repeat: false, duration: 600 })
    .onAction(() => {
      if (this.state === CardState.Launching) return;
      this.onDetails();       // ← 长按 600ms = 打开详情
    })
)
```

**点击启动游戏，长按 600ms 才开详情。** 用户点卡片期待看详情，结果直接进游戏了。

### 判定

这是主机 Home 屏的交互语言（点=启动，长按=选项），但对新用户不可发现。

### 修复方案（三选一）

| 方案 | 做法 | 代价 |
|---|---|---|
| A | 保持点击启动，在卡片上加「⋯」角标提示长按 | 最小改动，仍需学习 |
| B | 点击进详情，详情页里放大的「启动」按钮 | 启动多一步，但符合直觉 |
| C | 点击进详情，详情页「启动」；长按直接启动 | 反转现有习惯 |

**建议 B**：卡片是「查看」的对象，启动是「决定」。现在 Celeste 用户点卡片 100% 是误触启动。

---

## 问题 2：游戏内点退出，退出的是应用不是游戏

### 根因（确切）

`Index.ets:278-291`：

```ts
private exitToLibrary(): void {
  this.confirmExit = false;
  dmhost.log('library: terminating to return to the library');
  const abilityCtx = ctx as common.UIAbilityContext;
  abilityCtx.terminateSelf()        // ← 杀掉整个应用进程
}
```

`terminateSelf()` 结束 UIAbility，也就是**整个应用**。用户的预期是「退出游戏，回到库页」。

### 为什么当初这么写

`Index.ets:240` 的注释说了原因：

> The kernel reclaims the GPU context, the threads and the descriptors on the way out, which is why this is safer than cancelling a detached thread inside the guest.

意思是：guest 线程是 detached 的，没法安全取消，只能靠杀进程让内核回收。

**这是真实的架构约束，不是偷懒。**

### 修复方案

需要给 host 层加一个真正的 `stopGame`：

```c
// demo_ohos.c
void dm_ohos_demo_stop(void) {
  atomic_store(&g_stop, 1);
  // 新增：等线程真正退出（最多 2 秒），再允许下一次 start
  for (int i = 0; i < 200 && atomic_load(&g_run); i++) usleep(10 * 1000);
}
```

但光这样不够——`demo_body` 有 **5 处 `return NULL` 直接退出**（`:162`、`:177`、`:179` 等），**都不销毁 Vulkan 资源**：

```c
:177  if (!sup) { LOG(...); return NULL; }     // 泄漏 surface
:179  if (!nf || nf > 32) { LOG(...); return NULL; }  // 泄漏 surface
```

下次启动会创建第二个 surface/swapchain 在同一窗口上，**必然失败**。

### 真正的修复（三步）

1. **给 `demo_body` 加统一的 cleanup 标签**：所有 `return NULL` 改成 `goto cleanup`，在 cleanup 里销毁 `fbs/views/swap/rp/pool/surf`
2. **`dm_ohos_demo_stop` 等线程退出**：轮询 `g_run` 到 0，超时则记日志
3. **`Index.ets` 的 `exitToLibrary` 改成**：先 `dmhost.stopGame()`，等确认停止，再 `router.replaceUrl('pages/LibraryPage')`

第 3 步还要解决一个问题：`Index.ets:295` 的 `if (this.isGame) return;` 会阻止再次启动。需要在 stop 后重置 `isGame`。

---

## 问题 3：退出游戏后无法再次进入

### 根因（问题 2 的直接后果）

因为问题 2 是靠 `terminateSelf()` 杀进程，理论上重开应用应该干净。但用户说「无法再次进入」，说明：

1. `terminateSelf()` 后进程没真正退出（还有 detached 线程活着），或者
2. 重开后 `g_run` 仍是 1（`dm_ohos_demo_start` 在 `:302` 直接 `return`）

`demo_ohos.c:302`：

```c
if (atomic_load(&g_run)) { LOG("[demo] already running"); return; }
```

**如果上一个进程的 detached 线程还持有 GPU 资源，或者 `g_run` 没清 0，新启动就被这一行挡掉，静默失败。**

### 修复

与问题 2 同源，修好 2 就修好 3。额外需要：
- `dm_ohos_demo_start` 失败时要**返回错误码给 JS**，不能静默 `return`
- `Index.ets` 的 `launchGame` 要检查返回码，失败时给用户提示

---

## 问题 4：进入设置有时卡死退出

### 根因（高置信）

`SettingsPage.ets:70` 的 `aboutToAppear()` 同步调用，其中 `:124`：

```ts
const names: string[] = fs.listFileSync(filesDir + '/games');
```

`listFileSync` 是**同步阻塞 IO**，在 UI 线程上跑。

而 `SettingsPage.ets:45` 注释提到「Bytes on disk; -1 while the walk runs」——说明有个**目录遍历在算磁盘占用**。14.7 GB 的游戏目录（41000 个文件）在 UI 线程上遍历，**必然卡死**。

### 修复

1. 把 `listFileSync` 和目录遍历移到 `taskpool` 或异步 API
2. 遍历期间显示「计算中…」，不要阻塞 UI
3. 加超时：超过 3 秒就放弃精确统计，显示「约 XX GB」

---

## 问题 5：Celeste 进入实际游戏后偶尔黑屏

### 可能的三个原因

| 原因 | 证据 | 可能性 |
|---|---|---|
| **surface 尺寸为 0** | `demo_ohos.c:188` 有 `if (!ext.width \|\| !ext.height)` 的处理，说明这种情况真实发生 | 高 |
| **swapchain 重建时机** | `demo_ohos.c:276` `VK_ERROR_OUT_OF_DATE_KHR` 触发 rebuild，rebuild 期间黑屏 | 中 |
| **首次 present 前的空白** | XComponent 创建到第一帧之间 | 低（短暂） |

### 最可能的原因

`demo_ohos.c:193-195` 的注释透露了关键信息：

> The M0 probe's finding: the XComponent's native window can still lack its buffer geometry when the surface is handed over

**XComponent 交出 surface 时，窗口可能还没有 buffer 几何尺寸。** 代码用 `SET_BUFFER_GEOMETRY` 补救，但如果补救失败就黑屏。

### 修复方向

1. 黑屏时不要静默：在画面上叠一个「正在初始化…」的 ArkTS 覆盖层，直到第一帧真正 present
2. `demo_body` 在 `ext.width/height` 为 0 时**重试而不是 continue 空转**，并记日志
3. 需要真机复现才能确诊——目前是推断

---

## 修复优先级

| 序 | 问题 | 影响 | 工作量 |
|---|---|---|---|
| **P0** | 2 + 3：退出杀应用、无法重进 | 游戏完全不可用 | 大（要改 C + ArkTS） |
| **P1** | 4：设置卡死 | 设置页不可用 | 中（异步化） |
| **P1** | 1：点击不弹详情 | 交互误导 | 小（改绑定） |
| **P2** | 5：偶发黑屏 | 体验差但可恢复 | 中（需真机复现） |

---

## 我建议的做法

**先修 P0（问题 2+3）**，因为它让游戏完全不可用。具体：

1. 改 `demo_ohos.c`：加 `cleanup` 标签，所有 `return NULL` 走统一销毁
2. 改 `dm_ohos_demo_stop`：等 `g_run` 归零，返回成功/失败
3. 改 `Index.ets`：`exitToLibrary` 调 stopGame 而非 terminateSelf；stop 后重置 `isGame`，允许重新启动
4. 加 NAPI 导出 `stopGame`
5. 真机验证：启动 → 退出 → 再启动，循环 5 次

要我按这个顺序动手吗？还是你想先修哪个？
