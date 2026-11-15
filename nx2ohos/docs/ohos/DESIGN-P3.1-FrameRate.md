# 帧率优化：BOTW 从 21 fps 到 26 fps，以及为什么还没到 30

> 状态：**进行中，未达标**。
> 目标：稳定 ≥30 fps。实测：中位 26 fps，26% 的秒达到 30 fps。
> 本文记录每一步的测量和结论，避免后来的人重走弯路。

---

## 1. 起点：没有测量

移植代码里**没有任何帧率输出**。唯一的信号是 `present_slow` 在单帧超过 50ms 时打一行。这意味着一开始无法回答"现在多少帧"，更无法判断某个改动是否有效。

**第一步必须是加测量**，否则后面全是猜。

### 实现

在 `gfx_vk_present_ohos.c` 的 `present_enqueue` 里计数（**不是** `present_window`）：

```c
static void fps_frame(void)   // 每秒输出 fps / avg / max / slow
```

**为什么在 `present_enqueue`**：那是**游戏线程**交出帧的地方，帧间隔就是玩家感受到的帧时间。`present_window` 是呈现线程，它可能丢帧或重复帧，在那里计数测的是合成器，不是游戏。

输出样例：
```
fps: 21 frames/s, avg 52.5 ms, max 72.5 ms, 21 slow (>33.3 ms)
```

`slow` 是关键指标：超过 33.3ms 的帧数 = 错过 30fps 截止期的帧数。

---

## 2. 三个真实缺陷（都是测量发现的）

### 2.1 线程调度类从未生效

`dm/compat/darwin_compat.c` 的 `thread_policy_set` 是空实现：

```c
int thread_policy_set(...) { return KERN_NOT_SUPPORTED; }
```

而 `hle_nvn.c` 的 `rt_declare` **每帧**都在申请 deadline 策略。日志证据：

```
acquire thread time-constraint policy (period 16.667 ms, computation 2.000 ms) failed to set
```

**HarmonyOS 有这个能力**：`OH_QoS_SetThreadQoS`（`qos/qos.h`，API 12）。

修复：把 `thread_policy_set` 映射到 OHOS QoS，链接 `-lqos`。

**踩的坑**：
- 第一次用了 `#if defined(__OHOS__)`，但编译只定义 `DM_OHOS=1`，分支没进去 → 改用 `DM_OHOS`
- 第二次比较 `thread == pthread_self()`，但 `rt_declare` 传的是 `pthread_mach_thread_np()` 的返回值 → **不比较，直接设当前线程**（`rt_declare` 总是在目标线程自己上运行）

### 2.2 呈现线程没有调度类

```c
uint32_t mtl_present_qos(void) { return 0; }   // 硬编码返回 0
```

日志证据：
```
queue replay thread ... demoted in 300 of 300 frames; present QoS 0
```

`present_thread` 是这个移植自己的线程，从未提过优先级。修复：在线程启动时调 `OH_QoS_SetThreadQoS(QOS_USER_INTERACTIVE)`。

**为什么用 `USER_INTERACTIVE` 而不是 `DEADLINE_REQUEST`**：实测 `DEADLINE_REQUEST` 下仍然是 `demoted in 300 of 300 frames`——设备调度器几乎立刻把类收回。`USER_INTERACTIVE` 是 UI 线程本身跑的类，能保持。

### 2.3 blit 用了 LINEAR 滤波

`record_frame` 里每帧把游戏窗口纹理放大到屏幕：

```c
vkCmdBlitImage(..., VK_FILTER_LINEAR);   // 1920x1080 → 2844x1260
```

**证据**：把渲染分辨率降到 0.5 和 0.75，GPU 时间**完全不动**（18-20ms）。说明成本在这个放大 blit，不在场景。

修复：`VK_FILTER_NEAREST`。GPU 时间 **19ms → 12-16ms**。

---

## 3. 试过但无效的

| 改动 | 结果 | 结论 |
|---|---|---|
| `DM_RENDER_SCALE=0.75` | 帧时间不变 | GPU 时间不受分辨率影响 → 瓶颈不在填充率 |
| `DM_RENDER_SCALE=0.5` | 帧时间不变 | 同上 |
| swapchain 深度 4 → 6 | `submit+present` 18.29 → 18.40ms | 不是合成器等待的问题 |

这些**都回退了**（render scale 保留为可用选项，但没有默认开启）。

---

## 4. 当前瓶颈（精确测量）

`timing up to frame 1800`（300 帧 p50/p95）：

```
frame time          p50 38.63 / p95 46.97 ms
frame work          p50 38.58 / p95 46.90 ms
NVN submit+present  p50 17.76 / p95 21.97 ms   ← 最大单项
GPU                 p50 13.92 / p95 17.43 ms
```

`submit+present 17.76` 是 **replay 线程录制 Vulkan 命令的 CPU 时间**，与 `queue replay thread CPU 17.06 ms/帧` 吻合。

### replay 内部再分解（`DM_VK_PROF_SLOW=1 DM_VK_PROF_MS=8`）

```
slow replay: 8.5 ms
  descriptor sets  3.5 ms   ← 41%
  other            4.5 ms   ← draw 录制本身
  pipeline binds   0.2 ms
  textures         0.3 ms
```

### descriptor set 统计

```
sets: 70708 reused, 41030 new    ← 37% 每帧新分配
collect      831.8 ms / 720 帧 = 1.16 ms/帧   ← 最大
alloc+write  401.5 ms / 720 帧 = 0.56 ms/帧
hash          24.6 ms / 720 帧 = 0.03 ms/帧
```

**`collect` 1.16ms/帧** 是收集 descriptor 内容的开销（每个 binding 走一次 switch）。这是下一步的目标。

---

## 5. 下一步（按预期收益排序）

1. **descriptor set 的 `collect`**（1.16 ms/帧）
   - 检查 `stage_set` 里 `s->nbind` 个 binding 是否有可跳过的重复工作
   - 检查 32-slot 纹理数组的填充（`ii[96*4]` 是 9KB 栈内存）

2. **`other` 4.5 ms/帧**（draw 录制）
   - 每帧 87 draws / 56 passes
   - 56 个 render pass 的开关成本值得查

3. **GPU 13.92 ms**
   - 已确认不是填充率
   - 可能是 1920x1080 窗口纹理的带宽，或 pass 之间的 load/store

---

## 6. 测量工具（已提交，可复用）

| 开关 | 作用 |
|---|---|
| 默认 | 每秒 `fps: N frames/s, avg X ms, max Y ms, N slow` |
| `DM_FPS_TARGET=30` | 把 frame step 锁在 30fps，不再在 20/30 之间震荡 |
| `DM_VK_PROF_SLOW=1` | 打开 replay 内部分解 |
| `DM_VK_PROF_MS=8` | 降低分解输出阈值（默认 40ms，本机 replay 只有 8.5ms 达不到） |
| `DM_LONGFRAME=0` | 关掉单帧慢日志 |

这些开关写在游戏目录的 `env.txt`：

```
DM_LANG=en-US
DM_FPS_TARGET=30
DM_VK_PROF_SLOW=1
DM_VK_PROF_MS=8
```

---

## 7. 教训

1. **先测量再优化**。这个移植跑了很久都没有帧率数字，所有性能讨论都是猜测。
2. **计时口径决定结论**。在 `present_window` 计数会测到合成器；在 `present_enqueue` 计数才是玩家感受的帧时间。
3. **`#if` 宏要确认真的定义了**。`__OHOS__` 没有，`DM_OHOS` 才有——第一次改动整个分支没编译进去，白测一轮。
4. **一个变量一次改动**。把 QoS、blit 滤波、swapchain 深度一起改，就分不清哪个有效。
5. **诚实地记录"无效"**。render scale 和 swapchain 深度的失败同样是信息——它们排除了两个方向。
