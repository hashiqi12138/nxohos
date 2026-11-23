# 隔离方案：guest 跑子进程，主进程只留 UI

> 用户要求（2026-11-23 00:11）：回库页后能换任意游戏，不关应用，加一层隔离。
> 本文只做方案，不动代码。等技术验证通过再实施。

---

## 一、为什么这个方案成立

### 核心 API（已核实存在）

**鸿蒙原生子进程**：`native_child_process.h`

```c
Ability_NativeChildProcess_ErrCode OH_Ability_StartNativeChildProcess(
    const char* entry,                    // "libdmhost.so:Main"
    NativeChildProcess_Args args,          // entryParams + fdList
    NativeChildProcess_Options options,    // isolationMode
    int32_t *pid);

Ability_NativeChildProcess_ErrCode OH_Ability_RegisterNativeChildProcessExitCallback(
    OH_Ability_OnNativeChildProcessExit onProcessExit);   // (pid, signal)
```

**执行模型**（头文件原文）：

```
子进程：dlopen(libName) → dlsym("Main") → Main(args) → 返回即退出
```

**这就是隔离的关键**：`Main()` 返回，子进程自己退出，主进程完全不受影响。

### surface 跨进程传递（已核实）

```c
// 主进程：从 XComponent 的 window 拿 id
int32_t OH_NativeWindow_GetSurfaceId(OHNativeWindow *window, uint64_t *surfaceId);

// 子进程：用 id 重建 window
int32_t OH_NativeWindow_CreateNativeWindowFromSurfaceId(uint64_t surfaceId, OHNativeWindow **window);
```

`surfaceId` 是 `uint64_t`，**可以塞进 `entryParams` 字符串传过去**。

而且现有代码已经在用第二个函数：`front_ohos.c:32`。

---

## 二、架构

```
┌─────────────────────────────────────────┐
│ 主进程（ArkTS + ArkUI）                  │
│  ├─ LibraryPage / SettingsPage          │
│  ├─ XComponent（surface 提供者）         │
│  └─ 起停子进程、收退出回调                │
└──────────────┬──────────────────────────┘
               │ OH_Ability_StartNativeChildProcess
               │ entryParams = "{surfaceId}|{root}|{save}|{cache}"
               ▼
┌─────────────────────────────────────────┐
│ 子进程（libdmhost.so:Main）              │
│  ├─ 解析 entryParams                     │
│  ├─ CreateNativeWindowFromSurfaceId      │
│  ├─ dm_ohos_game_start(root, save, cache)│
│  └─ nnMain() 循环 —— 死循环，无所谓      │
│     （杀进程就全没了）                     │
└─────────────────────────────────────────┘
```

**退出流程**：

1. 用户点「退出游戏」
2. 主进程 `kill(childPid, SIGTERM)` 或直接 `kill(childPid, SIGKILL)`
3. 子进程死，所有 GPU/内存/线程随之回收（内核保证）
4. `OH_Ability_RegisterNativeChildProcessExitCallback` 收到 `(pid, signal)`
5. 主进程 `router.replaceUrl('pages/LibraryPage')`
6. **应用活着，库页在，可以点任何游戏**

**换游戏**：回到第 1 步，起一个新子进程。

---

## 三、改动清单

### 3.1 子进程入口（新增）

`dm/ohos/child_entry.c`：

```c
#include "native_child_process.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// 子进程入口：鸿蒙按 "libdmhost.so:Main" 找这个符号
void Main(NativeChildProcess_Args args) {
    // entryParams 格式：surfaceId|root|save|cache
    char *p = args.entryParams;
    if (!p) return;
    uint64_t surfaceId = strtoull(p, &p, 10);
    if (*p == '|') p++;
    // ... 解析 root / save / cache

    dm_ohos_front_set_surface(surfaceId, w, h);
    dm_ohos_game_start(root, save, cache);   // 内部 nnMain 死循环
    // 返回即退出进程
}
```

### 3.2 主进程 NAPI（改 `napi_bridge.c`）

```c
static napi_value js_startChild(napi_env env, napi_callback_info info) {
    // 参数：surfaceId, root, save, cache
    NativeChildProcess_Args a = {0};
    snprintf(g_entry, sizeof g_entry, "%llu|%s|%s|%s", surfaceId, root, save, cache);
    a.entryParams = g_entry;

    NativeChildProcess_Options o = {0};
    o.isolationMode = NCP_ISOLATION_MODE_NORMAL;   // 共享沙箱，能读 games/

    int32_t pid = 0;
    int rc = OH_Ability_StartNativeChildProcess("libdmhost.so:Main", a, o, &pid);
    // 返回 pid 给 ArkTS
}
```

### 3.3 退出回调（新增）

```c
static void on_child_exit(int32_t pid, int32_t signal) {
    LOG("child %d exited (signal %d)", pid, signal);
    // 通知 ArkTS：通过 napi thread-safe function 或轮询 pid
}

// 初始化时注册
OH_Ability_RegisterNativeChildProcessExitCallback(on_child_exit);
```

### 3.4 ArkTS（改 `Index.ets`）

```ts
private launchGame(filesDir: string, cache: string, titleId: string): void {
  const surfaceId = this.controller.getXComponentSurfaceId();
  this.childPid = dmhost.startChild(surfaceId, root, filesDir + '/save', cache);
}

private exitToLibrary(): void {
  dmhost.stopChild(this.childPid);       // kill(SIGTERM)
  router.replaceUrl({ url: 'pages/LibraryPage' });
}
```

**关键**：`Index.ets` 不再需要 `isGame` 闩，因为每次都是新进程。

---

## 四、必须先验证的三件事

在动手前，这三条任一失败方案就不成立。

### V1：子进程能否建 Vulkan 实例并 present 到父进程的 surface

**为什么存疑**：`CreateNativeWindowFromSurfaceId` 在**同进程**用过（`front_ohos.c:32`），但跨进程的 surface 共享在内核里是 bufferqueue 的跨进程句柄，理论上支持（`surfaceId` 是全局唯一标识）。

**怎么验**：写一个最小子进程，只做 `CreateNativeWindowFromSurfaceId` + `vkCreateSwapchainKHR` + 清屏，看父进程 XComponent 是否变红。

**风险**：中。这是整个方案的地基。

### V2：`libdmhost.so` 里的 guest 能否在子进程里跑起来

**为什么存疑**：guest 需要读 `games/<title>/mods/*.patched.bin`，子进程要能访问同一沙箱路径。

**怎么验**：`NCP_ISOLATION_MODE_NORMAL` 下，子进程 `fopen(root + "/mods/main.patched.bin")` 看能否成功。

**风险**：低。NORMAL 模式共享沙箱（头文件明确说 "parent process shares the same sandbox"）。

### V3：杀子进程后 GPU 资源真的全回收

**为什么存疑**：Vulkan 上下文、显存、surface 都是内核对象，进程死应该全回收，但 Maleoon 驱动有过 abort 记录。

**怎么验**：起子进程 → 跑 30 秒 → `kill -9` → 立刻起第二个子进程 → 看是否正常。

**风险**：中低。内核回收进程资源是硬保证，但驱动层可能有泄漏。

---

## 五、与当前实现的关系

### 已经做好的（可复用）

- `dm_host_teardown()`（我刚加的）—— 子进程正常退出时仍可调用，做优雅收尾
- `stopGame` NAPI —— 可以保留，改为「优雅退出后 `_exit`」

### 要废弃的

- `g_game_started` 一次性闩 —— 子进程模型下每个进程只跑一次，天然满足
- `terminateSelf` 作为退出手段 —— 改成杀子进程

### 要保留的

- `dm_ohos_game_start` 的全部逻辑 —— 子进程里照常调
- `front_ohos.c` 的 surface 处理 —— 子进程里照常调

---

## 六、工作量估算

| 项 | 文件 | 行数 |
|---|---|---|
| 子进程入口 | 新增 `child_entry.c` | ~80 |
| NAPI 起停子进程 | `napi_bridge.c` | ~60 |
| 退出回调 + 通知 ArkTS | `napi_bridge.c` | ~30 |
| ArkTS 改造 | `Index.ets` | ~40 |
| 构建脚本（编入 libchild_process.so） | `build_ohos.py` | ~5 |
| **合计** | | **~215 行** |

不含验证时间。

---

## 七、我的建议

**先做 V1 验证**（surface 跨进程 present）。这是唯一可能推翻方案的点。

如果 V1 通过，剩下的都是工程量，风险可控。

如果 V1 失败，退路是：
- 子进程只跑游戏逻辑，渲染仍由主进程做（数据通过共享内存传帧）—— 性能损失大，不推荐
- 或者接受「关应用重开」（已实现）

**要我先写 V1 的验证程序吗？** 大概 100 行 C，能跑就说明方案成立。
