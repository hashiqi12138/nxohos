# P3.4：BOTW 游戏内帧率瓶颈实测（replay 的 47% 在 descriptor set）

> 方法：设备实测 + profiler，不靠推断。
> 设备：HBN-AL80 / Maleoon 910 / HarmonyOS 7.0.0.111
> 游戏：Breath of the Wild `01007EF00011E000`，进游戏内（不是标题界面）

---

## 0. 一句话结论

**BOTW 游戏内是 CPU-bound 于 replay，不是 GPU-bound。** OpenGTX 让 GPU 从 12.99 ms 降到 4.76 ms，帧时间没变——因为瓶颈从来不在 GPU。replay 里 **descriptor set 占 47%**，而其中 **95.4% 是「内容全新」而不是「哈希冲突」**，所以加关联度这类缓存优化救不了。

---

## 1. 怎么测的

### 1.1 进入游戏内

标题界面和游戏内是完全不同的负载。标题约 20 fps，游戏内 7-9 fps。

虚拟手柄的按键坐标是相对比例（`Index.ets:115`）：

```
A 键: cx=0.86, cy=0.70  →  x = 0.86 × 2844 = 2446,  y = 0.70 × 1260 = 882
```

连点 A 约 8 次（每次间隔 1.5 s）可跳过标题与开场。

### 1.2 打开 profiler

`env.txt` 里加：

```
DM_VK_PROF_MS=20      # 阈值 20 ms（默认 4000 us），抓所有帧
DM_VK_PROF_SLOW=1
DM_VK_X_SETDIAG=1     # 本文新增的 miss 分类开关
```

**坑**：`hdc shell 'echo >> env.txt'` 报 `Permission denied`。必须 `hdc file recv` 拉到本地改，再 `hdc file send` 推回。

---

## 2. 实测数据

### 2.1 replay 分解（游戏内，75 ms 帧）

```
slow replay (present 1721): 72.1 ms
  descriptor sets   40.7 ms   ← 47-56%
  cuts               7.6 ms   ← 10-25%（波动）
  other             19.9 ms   ← 25%
  textures           2.4 ms
  pipeline binds     1.6 ms
  pipelines built    0.0 ms   ← 缓存已满，不重建
```

### 2.2 GPU 不是瓶颈（OpenGTX 前后对比）

| | 之前 | 之后 |
|---|---|---|
| `frame work p50` | 33.37 ms | 40.19 ms |
| **`GPU p50`** | **12.99 ms** | **4.76 ms** |
| 帧率 | 20-34 fps | 20-34 fps（无变化） |

GPU 降到 4.76 ms 说明**时钟提示生效了**，但帧时间没变——瓶颈在 CPU。

### 2.3 descriptor set 的真实分布

```
sets (present 1762): 278,917 reused, 327,687 new
  hash        169.7 ms
  collect    4191.0 ms
  alloc+write 3765.3 ms
```

**53% 是新建**。

### 2.4 miss 分类（本文核心结论）

新增 `DM_VK_X_SETDIAG` 后测得：

```
set miss why: 800 collision, 234,877 slot expired/free, 10,437 other layout, 0 same-content
of 246,114 misses; distinct slots touched 234,877
```

| 原因 | 数量 | 占比 |
|---|---|---|
| **slot expired/free** | 234,877 | **95.4%** |
| other layout | 10,437 | 4.2% |
| **collision** | 800 | **0.3%** |
| same-content | 0 | 0% |

`distinct slots touched 234,877` 恰好等于 `slot expired` 数量 → **每个 set 都是全新的，槽位从没被填过第二次**。

### 2.5 为什么 set 这么多

```
replay up to present 1200: draws 115,503, passes 62,257
```

- **11.5 万 draw 产生 32.6 万新 set** → 平均 **2.8 set/draw**（vertex/fragment/compute，符合预期）
- **62,257 pass 对 115,503 draw** → **每 pass 只有 1.85 draw**

这是 BOTW 的真实特征：**大量小 draw，每个频繁切渲染目标**。

---

## 3. 结论：哪些方向无效，哪些有效

### 3.1 无效：descriptor set 关联度（组相联）

冲突只占 **0.3%**。把单槽改成 4 路组相联，最多救回 800 次 miss（占 0.3%），**对 95.4% 的「内容全新」完全无效**。

**这条要写下来，因为它看起来是最直观的优化。**

### 3.2 无效：XEG_spatial_upscale

P3.3 文档把它列为「最值得接」，但它只降低 GPU 的 blit 开销。当前 GPU 只用 4.76 ms 而帧时间 40 ms，**GPU 侧优化没有意义**。

### 3.3 有效方向（按预期收益）

| # | 方向 | 依据 | 预期 |
|---|---|---|---|
| 1 | **跨帧 descriptor set 缓存** | 95.4% 是 `slot expired`，`g_set_epoch` 每次 submission 递增导致缓存只在一帧内有效 | 最高 |
| 2 | 减少 draw 数 | 11.5 万 draw / 62k pass，每 pass 1.85 draw | 高，但需改 pass 结构 |
| 3 | 减少 cuts | `cuts` 7.6-19.8 ms 波动，每 15 draw 切一次（`gfx_vk_exec.c:784`） | 中等 |
| 4 | 多线程 replay | `queue replay` 单线程 12.4 ms（占 72 ms 的 17%） | 中等 |

---

## 4. 下一步：跨帧 descriptor set 缓存

### 4.1 当前逻辑

```c
// gfx_vk_exec.c:1503
if (g_setc[k].epoch == g_set_epoch && g_setc[k].h == h && g_setc[k].l == s->dsl && ...) { hit }
```

`g_set_epoch` 在 `vk_exec_reset`（每次 submission）递增，所以**缓存跨帧失效**。

### 4.2 为什么可以跨帧

哈希已经包含：

```c
h = fnv(h, &s->dsl, sizeof s->dsl);                        // layout
h = fnv(h, &x->dstBinding, 4);                             // binding
h = fnv(h, &x->descriptorType, 4);
h = fnv(h, &x->descriptorCount, 4);
if (x->pBufferInfo) h = fnv(h, x->pBufferInfo, sizeof *x->pBufferInfo);   // buffer + offset + range
if (x->pImageInfo)  h = fnv(h, x->pImageInfo, sizeof *x->pImageInfo * count);  // view + sampler
```

**buffer 指针、offset、range、imageView、sampler 全在哈希里**。同一个 buffer 同一 offset → 内容相同。dynamic uniform buffer 的 offset 已单独走 `dofs`（`gfx_vk_exec.c:1478-1481`），不参与哈希。

所以**哈希相同 = 内容相同**，跨帧复用是安全的。

### 4.3 风险

- `g_setc` 只有 8192 槽，一帧 32 万 set → **必须扩容**，否则跨帧缓存立刻被冲掉
- 扩容到 2^20（104 万）约 32 MB（每槽 32 字节），可接受
- 需要在 submission 边界做**代际清理**（太老的条目淘汰），否则无限增长

---

## 5. 复现命令

```powershell
# 1. 编译
python dm/ohos/build_ohos.py --lib --game 01007EF00011E000

# 2. 打包（DM_VERSION_CODE 必须高于设备当前值）
$env:DM_VERSION_CODE = '2000785960'
python dm/ohos/mkhap.py --game 01007EF00011E000 --lib dm/ohos/out/01007EF00011E000/libdmhost.so --out <dir>

# 3. 安装
hdc shell 'aa force-stop dev.eden_emu.ohos'
hdc file send <dir>/dmhost-signed.hap /data/local/tmp/nx2a-e/entry-default-signed.hap
hdc shell 'bm install -p /data/local/tmp/nx2a-e -r'

# 4. 打开 profiler（env.txt 必须 recv 改再 send）
hdc file recv <sandbox>/games/01007EF00011E000/env.txt env.txt
# 加 DM_VK_PROF_MS=20 / DM_VK_PROF_SLOW=1 / DM_VK_X_SETDIAG=1
hdc file send env.txt <sandbox>/games/01007EF00011E000/env.txt

# 5. 启动并进游戏
hdc shell 'aa start -b dev.eden_emu.ohos -a EntryAbility'
# 等 12 s，点 BOTW 卡片（968,739），等 30 s，连点 A（2446,882）8 次

# 6. 取数据
hdc shell 'hilog -x -T dmhost' | Select-String 'slow replay|sets \(present|set miss why'
```

---

## 6. 试过并回滚：跨帧 descriptor set 缓存

### 6.1 改动

按 §4 的推理，把缓存从「一帧内有效」改成「内容哈希跨帧有效」：

```c
// 之前
if (g_setc[k].epoch == g_set_epoch && g_setc[k].h == h && ...) { hit }

// 改成
if (g_setc[k].h == h && g_setc[k].l == s->dsl && g_setc[k].set != VK_NULL_HANDLE && ...) {
    g_set_hits++; set = g_setc[k].set; g_setc[k].epoch = g_set_epoch; }
```

并把 `SETC` 从 8192 扩到 `1 << 20`（32 MB），加代际淘汰（每 600 次 submission 扫一遍，丢弃 600 帧未用的）。

### 6.2 结果：复用率达成，但性能崩溃

```
sets (present 50): 380 reused, 38 new      ← 复用率 90%（之前 47%）
                    collect 245.0 ms       ← 之前约 4 ms
帧率: 1 fps, 每帧 1200 ms                   ← 之前 20-28 fps
```

**复用率目标达到了，但帧率崩到 1 fps。**

### 6.3 为什么

`g_setc` 变成 **32 MB 的全局数组**（104 万 × 32 字节）。每次 `stage_set` 的 `g_setc[k]` 访问都是**随机内存访问**，而 32 MB 远超 CPU 缓存。之前 8192 × 32 = 256 KB 正好在 L2 里。

`collect` 从 4 ms 涨到 245 ms，**60 倍的退化**，全部来自缓存未命中。

### 6.4 结论：这条路需要换实现

跨帧复用的**想法是对的**（95.4% miss 确实浪费），但**不能靠扩大直接映射表**。可行的替代：

| 方案 | 说明 |
|---|---|
| **保留 8192 槽，但不清空** | epoch 只做 LRU 标记，不失效。表小、局部性好，代价是高冲突率下复用率有限 |
| **两级缓存** | 小表（热）+ 大表（冷），热表常驻 L1/L2 |
| **按 layout 分桶** | 每个 `VkDescriptorSetLayout` 一个独立小表，访问局部性天然好 |
| **不缓存 set，缓存「写操作序列」** | 记录 `vkUpdateDescriptorSets` 的参数，命中时直接重放，不查表 |

### 6.5 记录这个失败的理由

它看起来是最直接的优化（复用率从 47% 到 90%），但**实测是 60 倍的性能退化**。没有设备实测，这个改动会被当成改进提交。
