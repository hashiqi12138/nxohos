# BOTW（塞尔达：旷野之息）HarmonyOS 移植性能优化全记录

> 目标：真机 Shrine of Resurrection（初始神庙）**≥30fps 且画面正常**。
> 设备：HUAWEI Pura 70 Pro+（Kirin 9010 / Maleoon 910，Vulkan 1.2.309）。
> 游戏：`01007EF00011E000`（BOTW 1.9.0，build cd57b23f）；包名 `dev.eden_emu.ohos`。
> 工程：`f:\新建文件夹\nx2ohos\nxnative`（nx2android 核心 dm/{host,gfx,tools} 复用 + dm/ohos 适配层）。
>
> **最终结论：30fps 在该设备确证不可达。** 全部微优化与结构性杠杆试尽后，shrine 结构性上限约 **7-8fps（帧时间 p50 ≈128-142ms）**；状态好（关后台、凉机、预热后）的基线版本可摸到 **10-11fps**。本文按时间顺序记录每一轮优化的动机、做法、gate/版本、真机效果与证伪原因，不遗漏。

---

## 0. 版本线（HAP versionCode）

| 版本 | 内容 |
|---|---|
| 2000786123 | 诊断探针：GPU split（sub/pass/gap/unaccounted）+ present blit GPU avg |
| 2000786131 | shrine descriptor alloc/write 拆分插桩 |
| 2000786134 | XEngine 超分集成闭环 |
| 2000786135-139 | desc 跨帧缓存 6 轮真机 A/B（证伪收尾） |
| 2000786140 | copy-back 压缩 gate（DM_VK_CB_RANGE，证伪）+ per-pool copy-back 探针 |

---

## 1. 平台/驱动背景（Maleoon 910 已知缺陷，全部真机实测）

1. **zero-copy 不可**：AHardwareBuffer 的 dma-buf mmap 失败（`-1`），所有 NVN 内存池退化为 shadow 拷贝（"copied"，zero-copy 0.0MB）。早期 5.5s/帧的根因即 cached 池每提交整池 memcpy 回拷。
2. **bindless descriptor-array 不可**：32768/4096 槽池均 GPU ~1200ms/帧、无几何（黑/蓝屏）、时间戳 -1。NonUniform 动态索引不支持。
3. **UBWC 压缩纹理直接 blit 到 swapchain 崩溃**（SIGSEGV）：需先 1:1 拷贝到无压缩普通图像再 blit。
4. **阻塞式 semaphore wait 自旋/迟醒**：`vkWaitSemaphores` 曾出现 GPU 1ms 工作等 5.5s；`DM_VK_POLLWAIT=1` 改轮询 `vkGetSemaphoreCounterValue` 规避。
5. **descriptor 操作效率极低**：同套 descriptor set 代码 Adreno 能高效吸收、Maleoon 不行；iOS Metal argument buffer+bindless 同场景 CPU replay 仅 ~8ms，OHOS ~70-80ms。这是平台卡顿核心。
6. **华为官方 Vulkan 文档只列接口存在性**，不保证驱动实际支持/性能；任何文档优化路径落地前必须真机查能力+单变量对照。
7. **W^X 0/6**：JIT/运行时补丁/落盘 .so dlopen 全不可行 → 重写器必须 AOT。
8. 其它工程坑：`VK_OHOS_surface`（非 android_surface）；bm 安装大 HAP 需 `-w 600`；hilog 域须 0x0000-0xFFFF；fork 子进程禁 Vulkan/dlopen；`DM_CTL` 在 OHOS release 被 `unsetenv`（不能 live 调参）；`fprintf(stderr)`（DM_SLOWFRAME）不进 hilog；app 沙箱写路径须 `/data/storage/el2/base/...`（全局 `/data/app/...` fopen 失败）。

---

## 2. 帧预算与测量体系

shrine 一帧 ≈ **CPU replay ~70ms + 游戏自身阻塞 SyncWait ~86ms**（GPU ~83ms + completion copy-back ~25-36ms），近串行相加。

| 成分 | 耗时 | 细分 |
|---|---|---|
| CPU replay | ~70ms | desc ~40（含 collect/alloc/write）、memo ~15、cuts ~9、其余 ~6 |
| GPU | ~83ms | sub span 85 / pass spans 60 / inter-pass gap 14 / unaccounted 10；128 pass |
| 游戏 SyncWait | ~86ms | BOTW 每帧阻塞等自己 GPU（语义依赖） |
| completion copy-back | ~25-36ms | GPU 完成后 memcpy 回 guest，压在 g_done 关键路径 |

测量插桩（均纯测量、不改行为）：
- `DM_VK_PASS_TIMES=1`：GPU split + 每 pass GPU 时间 + present blit GPU avg。
- `DM_VK_PROF_SLOW=1`：slow-replay 块 + CPU split per frame（pipe/tex/poolsync/desc/cuts/…/memo）。
- sets 拆分：`g_set_memo / g_ns_set_hash / g_ns_set_new`、`g_memo_miss[8]`。
- `DM_NVN_TSV=<沙箱路径>` + `DM_NVN_TSV_CALLS=0`：逐帧 `@frame_ms/@work_ms/@nvn_ms/@sync_ms/@wait_ms/@pq_wait_ms/@gpu_beg/end`。
- completion 线程：`completion: fence N total X (wait Y, pool copy-back Z)`；`copied pools … copy-back … sub-begin wait`；per-pool `copy-back pool … whole/range … bytes … ms`。
- 方法论：**单变量对照**（一次一个变量且进 pipeline 缓存键）；**四层漏斗**（g_vkst draws/passes → 窗口纹理 → 源纹理 → 最终合成）；证据纪律（force-stop 清旧 PPM、记 pidof、只取对应 PID、等 PPM 写完、系统截图不能单独证明 XComponent）。

---

## 3. 优化路径全记录（按阶段）

### 阶段 A：画面正确性前置（先能画对，再谈快）
- **数据包**：1.6.0（8e9978d5）无 CUTS/OBS 补丁集→加载期稳定空指针崩溃；换 **1.9.0（cd57b23f）** 解决。第三方重打包 XCI/NSP 解包失败（RomFS 偏移/哈希非标准），须原始 dump。
- **黑屏根因**：顶点着色器 `vp_c3` 动态 UBO 路径——`drv_segment()` 只填少数字段、其余 4KB 清零→顶点矩阵错、覆盖为零。排除 descriptor 冲突/fragment/坐标/render pass/光栅化后收敛于此并修复。
- **UBWC blit 崩溃**规避：1:1 拷贝到无压缩图像再 blit（见 §1.3）。
- **SPIR-V 改写方法论**：宿主侧二进制改写（OpTypeStruct member0=w[i+2]、OpConstant 布局、新类型插首个 OpVariable 前）+ 外部注入诊断（DM_VK_SPV_DIR/ALT/SEED）；验证四层（spirv-val → vkCreateGraphicsPipelines → g_vkst → DM_VK_SHOT 像素）；fd 位标记变体分发、失败返 VK_NULL_HANDLE 不留 fallback。
- 效果：从黑屏/崩溃到**画面正确**，为后续所有性能实验的前提。

### 阶段 B：系统/调度/呈现层（早期最大收益）
- **关后台**（抖音/微信等复活吃 CPU）：shrine **6 → 10fps**。
- **present 线程 QoS 提权**（P3.1/P3.3，`QOS_USER_INTERACTIVE`；DEADLINE_REQUEST 实测 300/300 被降级弃用）：present 路径从 **9-31ms 摆动**变稳定。
- **OpenGTX + GamePerformance 声明游戏类别**：GPU 从固定 **308MHz 解锁到 530-750MHz** 动态、峰值 750 满频。
- **swapchain 加到 6 images**（min+3）：缓解 FIFO acquire 等待（曾 submit+present p50 18.3ms 而 GPU 14.1ms，多为 acquire  wait）。
- **present blit 改 NEAREST**（线性放大在 tile renderer 是真实开销）：present GPU **18-20ms → ~1.4ms**。
- **present 独立线程 + 4 深队列**、replay 在 nq worker：游戏线程不在 vsync 内等。
- **copy-back 跳过无 GPU 写的提交**（早期修复）：5.5s/帧 → **32-90ms/帧**（加载期）。
- `DM_VK_POLLWAIT=1` 规避驱动阻塞 wait 自旋。
- 效果：呈现/调度开销基本榨干，present GPU ~1.4ms、GPU 满频。

### 阶段 C：CPU 微优化（不破坏画面，2026-11-18）
- `vk_ubd_overlaps` 线性扫描 → **排序索引 + prefix-max 二分**。
- `vk_ubd_shadow` 缓存**仅在重叠更新/land/新提交时失效**。
- `stage_set` 逐写 → **整 set 一次 vkUpdateDescriptorSets**（`DM_VK_X_BATCHW`）。
- 效果：重场景 **6-7 → ~8fps**。单变量诊断确认瓶颈是单线程 CPU replay 60-70ms + GPU 69ms 的**串行墙**，单一安全微调仅 ~10%，不够 30fps。

### 阶段 D：结构性探索（多路证伪）
- **bindless descriptor-array**（32768/4096 槽）：GPU ~1200ms/帧、黑/蓝屏 → **证伪**（§1.2）。bindless 仅占 CPU submit ~12% 且 BOTW 无对应池绑定、需 translator 大改 → 降级非优先。遗留：`SHVK_BINDLESS` 默认 '1' 应改 '0'。
- **descriptor update template**：证伪（无收益）。
- **synchronization2 全局屏障窄化**（NOBAR）：评估无收益。
- **render scale 0.7**（`DM_RENDER_SCALE`）：仅 GPU **-7%**、gap 反升、fps 不变 → **证伪**（GPU 非 fill-bound）。
- **desc 跨帧缓存**（`DM_VK_DESC_CACHE`，6 轮真机 135-139）：shrine set 内容每帧唯一（cbuf range 对齐余数烤进 set、ubd shadow 每 submission 重建）→ 复用≈0、arena 涨 118k、pers alloc 12µs/set 比 submission 池慢、flush 循环反劣化 → **shrine 证伪**；轻场景（对话框）有效 CPU **69→15ms**。gate 默认0 保留。
- **多线程 replay / render pass 合并裁剪 / 雾·history MRT 随 render scale / 帧流水线**：列为结构性候选（见阶段 F/H 与 §6 遗留）。

### 阶段 E：XEngine 超分（可行性→集成→中性）
- 对照华为官方文档+真机确认 XEG 空域超分在 P70 Pro+ 可用（dlopen/dlsym，NDK 无导入库）。
- 集成 present 路径（`DM_VK_XEG_UPSCALE`，默认0），任何拒绝逐帧回退 NEAREST；swapchain 按需加 SAMPLED usage。
- 真机 A/B（134）：XEG present GPU **1.5ms ≈ NEAREST 1.4ms**，**性能中性**、画质正常。窗口本就 1080p、屏幕≈1080p 无真实放大倍率 → 无收益。保留为可选画质 gate。
- 结合阶段 D render scale 证伪：**超分/降分辨率不是该设备杠杆**（GPU 非 fill-bound）。

### 阶段 F：帧流水线化调查（不可行）
- 后端**已流水线化**：submission 环 NSLOT=6、present 异步 4 深；实测 `sub-begin wait=0`、`@pq_wait_ms=0`。
- 逐帧 TSV 证实串行来自**游戏自身每帧 SyncWait**（`@sync_ms p50≈86ms ≈ GPU 83ms`），非后端 stall。
- 要让 replay(N+1) 与 GPU(N) 重叠须游戏在 sync 返回前提前发命令——它不会。改之会读脏/画错。**不可行**。

### 阶段 G：completion copy-back 压缩（证伪且有害，140）
- 设想：cached 池整池 memcpy（~30ms）改 range-only（`DM_VK_CB_RANGE`），并补 draw/ssbo/texel 的 GPU-write 登记 + per-pool 探针；subagent 审核后真机 A/B。
- 结果：gate=1 与基线**持平**（frame 142.5 vs 142.3、sync 86.1 vs 85.8）。
- 根因：shrine 被拷回池全为 **uncached（flags 0x22）** 本就 range-only；cached→range 无作用对象。保守 draw-SSBO 登记反使 GPU-write 范围合并膨胀 → copy-back 尖峰 **240-313ms**（更差）。
- 画面 gate=1 正常（无读脏）但性能证伪。gate 默认0 保留。

### 阶段 H：GPU pass 切分粒度（唯一正收益）
- 机制：Maleoon 默认 `DM_VK_PASS_SPLIT=384` 是刻意调优——shrine 主 pass ~2000 draw，切分后早提交部分让 GPU 与 replay 重叠（**204→150ms**）；"合并=关切分(0)"会**倒退**。同 target keep 已开。可调的是粒度。
- 单变量 A/B（shrine frame p50 / sync p50）：

  | PASS_SPLIT | frame p50 | sync p50 | 画面 |
  |---|---|---|---|
  | 384（默认/基线） | 142.3ms | 85.8ms | 正常 |
  | 768 | 128.4ms | 78.8ms | 正常 |
  | 1024 | 163.5ms | 98.8ms | 异常风险 |

- 768 减少 tiler store/reload 又保留足够重叠 → **~+10% fps**；1024 切太少丢失重叠倒退，且本次会话末 1024 会话出现画面异常（见 §5 恢复）。

---

## 4. 效果汇总

| 杠杆 | 效果 | 状态 |
|---|---|---|
| 关后台 | 6→10fps | 采用（状态依赖） |
| QoS/OpenGTX/swapchain/NEAREST | present 18-20→1.4ms；GPU 308→750MHz | 采用 |
| copy-back 跳过无写提交 | 5.5s→32-90ms（加载） | 采用 |
| CPU 微优化（ubd 二分/shadow/整 set update） | 6-7→8fps | 采用 |
| bindless / template / sync2 / render scale | 证伪 | 弃 |
| desc 跨帧缓存 | shrine 证伪；轻场景 69→15ms | gate 默认0 |
| XEG 超分 | 中性（1.5≈1.4ms） | gate 可选 |
| 帧流水线化 | 不可行（游戏 SyncWait） | 弃 |
| copy-back 压缩 | 证伪且有害 | gate 默认0 |
| PASS_SPLIT 768 | 142→128ms（+10%） | 可选最优 |

累计：早期 155-175ms（6-8fps）→ 基线 p50 ~142ms、状态好 10-11fps；768 可再至 ~128ms。**30fps 不可达**。

---

## 5. 本次恢复记录（画面异常 + "比 10fps 慢"）

- 现象：会话末设备仍跑在 `PASS_SPLIT=1024` 会话（发了 768 env 但未 relaunch），画面异常、6fps。
- 恢复：env 回 **`env_xeg1.txt` 基线**（XEG=1、PASS_SPLIT 回默认 384、去掉 SLOWFRAME/TSV/CB_RANGE 探针）+ relaunch。**画面已正常**（shrine 正确渲染）。
- "10fps 版本"= **384 基线**（PID 9093 那轮，关后台/凉机/预热后 good seconds 10-11fps）。恢复即回到该配置。
- 当前 7-8fps 与 10fps 的差异是**状态变量**：发热、swap 已用 5GB、刚启动预热、hilogd 持续抓取 ~30% CPU；top 确认并无抖音/微信在跑（安卓包名在 OHOS 不存在）。非配置倒退。
- 若要在画面正常前提下再要 ~10%：可重设 `DM_VK_PASS_SPLIT=768`（其单独截图画面正常）；但 1024 已证画面风险，勿用。

---

## 6. 上限论证与遗留

**为什么 30fps 不可达**：帧 = CPU replay ~70 + 游戏 SyncWait ~86 串行；即便 GPU 全消，CPU 70ms 也只 ~14fps；GPU 非 fill-bound（降分辨率仅 -7%）；Maleoon descriptor 效率低（iOS 同场景 8ms）。串行是 BOTW 同步渲染语义，不可改。
**遗留/未做**：
- `SHVK_BINDLESS` 默认 '1'→'0'（shader_vk.py ~L106），卫生项。
- clear-only pass #19-22 ~15ms：clear 折叠逻辑存在但未生效，需 `DM_VK_TRACE_PASS=-2` 真机 trace 定位（不可离线猜改）。
- 雾/history MRT 随 render scale 尺寸适配（若重启 render scale 方向）。
- 多线程 replay（结构性，风险高，未做）。
- 证伪 gate 保留默认0：`DM_VK_DESC_CACHE`、`DM_VK_CB_RANGE`、`DM_VK_XEG_UPSCALE`(中性可选)。

---

## 7. 复现 / 调参 / 采集命令

构建+打包+安装（cwd `f:\新建文件夹\nx2ohos\nxnative`；PowerShell 用 `;`）：
```
python dm/ohos/build_ohos.py --lib --game 01007EF00011E000
$env:DM_VERSION_CODE='<ver+1>'; python dm/ohos/mkhap.py --game 01007EF00011E000 --lib dm/ohos/out/01007EF00011E000/libdmhost.so --out f:\新建文件夹\.tmp\hap_cap
hdc file send f:\新建文件夹\.tmp\hap_cap\dmhost-signed.hap /data/local/tmp/nx2a-e/entry-default-signed.hap
hdc shell 'bm install -p /data/local/tmp/nx2a-e -r -w 600'
```
env 覆盖（改 env 需 relaunch；DM_CTL 不可 live）：
```
hdc file send <local env.txt> /data/app/el2/100/base/dev.eden_emu.ohos/haps/entry/files/games/01007EF00011E000/env.txt
```
导航 shrine：force-stop + `aa start -b dev.eden_emu.ohos -a EntryAbility`；+8s；tap(746,600)+3s；Play(1855,702)+40s；A(2430,882)+25s；A+25s；A+45~75s（加载慢时第三 A 后或停 Load save 对话框需再 A）。
截图：`hdc shell 'snapshot_display -f /data/local/tmp/x.jpeg'` + `hdc file recv`。
TSV：env 加 `DM_NVN_TSV=/data/storage/el2/base/haps/entry/files/nvn.tsv`+`DM_NVN_TSV_CALLS=0`，pull 后分析 `@frame_ms/@sync_ms/@pq_wait_ms`。
日志关键行：`fps:`、`GPU split:`、`CPU split per frame:`、`copied pools … copy-back … sub-begin wait`、`completion: fence …`、`copy-back pool …`、`wait: fence …`。
状态监测：`hdc shell 'top -b -n 1 | head -25'`（看游戏/系统/hilogd CPU、Mem/Swap）；fps 受发热/swap/预热影响，A/B 需同状态对照。