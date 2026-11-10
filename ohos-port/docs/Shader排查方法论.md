# Shader 排查方法论（本会话实战提炼）

> **文档性质**：参考手册 / 操作规范，不是进度报告。
> **适用范围**：`nx2android` 的 NVN → Vulkan replay 移植（HarmonyOS / Android），尤其是原始 `shaders.dmsv` 在 Maleoon 910 等设备上出现 pipeline 编译失败或画面异常时。
> **使用约定**：后续 shader / 画面异常排查一律按本文的分层验证与单变量 A/B 执行；每次只改一个变量、保留 PID / 日志 / 帧证据，实际代码改动先经只读审核。
> **当前分支**：`fix/ohos-scene-color-blocks`
> **核心文件**：
>
> | 文件 | 职责 |
> |---|---|
> | `nxnative/dm/android/gfx_vk_shader.c` | SPIR-V 变体生成、capability 裁剪、variant 缓存 |
> | `nxnative/dm/android/gfx_vk_exec.c` | pipeline key、`build_pipeline()`、replay 状态与 `g_vkst` 统计 |
> | `nxnative/dm/android/gfx_vk_tex.c` | 格式表、image view、BC 解码、packaged 上传 |
> | `nxnative/dm/ohos/front_ohos.c` | `env.txt` 加载与运行时诊断通道 |

**速查一句话**：改 shader 靠「定位 - 注入 - variant 分发」的宿主侧改写框架；验证靠「字节 → 驱动 → 计数 → 像素」四层证据链；测试靠「单变量 A/B + 缓存键隔离 + 最小重制」收敛根因。

---

## 一、怎么修改 shader

### 1. 宿主侧 SPIR-V 二进制改写（正式修复手段）

在 `gfx_vk_shader.c` 中直接操作 SPIR-V 字节（`spv_clip_depth()` 的实现经验）。

**先定位再注入**：按指令流扫描定位目标，不要按固定偏移写死。

```
OpEntryPoint
  → OpMemberDecorate … BuiltIn Position
  → OpTypeStruct
  → OpTypePointer Output
  → OpVariable
```

**SPIR-V 布局三条铁律（踩过坑）**：

| # | 规则 | 违反后的后果 |
|---|---|---|
| 1 | `OpTypeStruct` 的 member0 在 `w[i+2]`，**不是** `w[i+3]` | 定位到错误 member，注入到非 `Position` 成员 |
| 2 | `OpConstant` 布局是 `(result type, result id, literal)`，与 `OpType*` 不同，需专用 helper `spvi_const()` | 常量 id / 类型错配，`spirv-val` 失败 |
| 3 | 新增类型 / 常量必须插在**首个全局 `OpVariable` 之前**，不能插在 `OpFunction` 前 | 模块结构非法，驱动直接拒绝 |

**stage 差异**：

- vertex：在每个 `OpReturn` 前注入；
- geometry：在每次 `OpEmitVertex` / `OpEmitStreamVertex` 前映射，**emit 后恢复原值**（否则后续 emit 读到已被改写的 z）。

**variant 分发**：用 `fd` 位标记（bit0 = fragment depth，bit1 = dual source，bit2 = clip map）；失败返回 `VK_NULL_HANDLE` 让 pipeline 跳过，**不留 fallback**，否则会掩盖真实失败。

> 背景实例：NVN 的 near = −w 产生 `−w..w` 的 clip z，Vulkan 默认 `0..w`；Maleoon 910 无 `VK_EXT_depth_clip_control`，因此在最终位置输出阶段注入
> `gl_Position.z = gl_Position.z * 0.5 + gl_Position.w * 0.5`。
> `gfx_vk_exec.c::build_pipeline()` 只对最终位置输出阶段请求 clip variant，`pkey.clip_map` 控制是否请求；`DM_VK_X_NOCLIPMAP=1` 只是 A/B 诊断开关，不是修复。

### 2. 替代 SPV 外部注入（诊断手段，不是修复）

| 开关 | 作用 |
|---|---|
| `DM_VK_SPV_DIR=<dir>` + `DM_VK_SPV_ALT=1` | 按文件存在性替换指定 key 的 shader |
| `DM_VK_SPV_SEED` | 让应用运行时自建空占位文件，绕过「沙盒不允许 hdc 新建文件」；hdc 覆盖后**必须重启进程**（shader module / pipeline 有缓存） |
| `DM_VK_SPV_DUMP=<dir>` | 导出驱动**实际收到**的 variant 字节（文件名含 key / vflags 防覆盖），用于确认「我改的字节 = 驱动编译的字节」 |

> **红线**：替代 SPV、常量色输出、direct present、任意非黑图都只是缩小范围，**不能作为修复结论**。只有原始 shader + 正式 env + 正常 present 出正确画面才算修复。

### 3. 工具链

| 工具 | 用途 |
|---|---|
| `spirv-dis` | 反汇编读指令，核对 `OpEntryPoint`、BuiltIn `Position`、Location 接口 |
| `spirv-val --target-env vulkan1.2` | 验证原始 SPV **和** 设备导出的 variant SPV 的合法性 |
| `spirv-opt -O` | 排除「体积过大 / 常见优化问题」这一类因素 |
| `devtools/dmsv_extract.py` | 从 `shaders.dmsv` 按 16 进制 key 抽取原始 SPIR-V |
| `analyze_spv.py` | 对多个失败 shader 取 opcode 交集，找驱动不兼容指令 |

---

## 二、怎么验证

### 分层验证：每层只回答一个问题

| 层 | 手段 | 回答的问题 |
|---|---|---|
| 字节合法 | `spirv-val` 过 DUMP 出的 variant | **我**是否生成了非法 SPIR-V |
| 驱动接受 | `vkCreateGraphicsPipelines` 返回值 + `graphics pipeline failed (-1): shaders <key>` 日志 | 驱动能否编译 |
| 真在跑 | `g_vkst` 统计（`draws` / `passes` / `dispatches` / `no pipeline` / `no program` / `no target`） | pipeline 是否真的被使用 |
| 画面正确 | `DM_VK_SHOT` 窗口纹理帧抓取 / `hdc shell snapshot_display` | 最终像素是否正确 |

> `-1` = `VK_ERROR_OUT_OF_HOST_MEMORY`，**不等于**瞬时内存不足；Maleoon 910 上可由非法 vertex input state（如零顶点流硬编码 `binding=16` 越界，而 `maxVertexInputBindings=16`、合法为 `0..15`）触发。遇到它先查状态合法性，不要先加 sleep 重试——本会话曾加 4 次重试，5 条管线共 20 次全部 `-1 → -1`，反而证明是确定性非法状态。

### 关键原则

1. **只有原始 shader + 正常 blit 出正确画面才算修复** —— 替代 SPV、常量色只能缩小范围，不能作为结论。
2. **关闭开关 ≠ 干净对照**：A/B 开关必须进缓存键（如 `pkey.clip_map`），否则同进程切换会复用旧管线缓存，得出假阴性。改了键布局要同步升级 `PIPE_MAGIC`（`PPE1` → `PPE2`），让旧 prewarm 记录失效；`vk_pipelines.bin` 的 header 同时保存 `sizeof(pkey)`，若改动只是填了 padding 导致 `sizeof` 不变，magic 是**唯一**能让旧记录失效的手段。
3. **画面自身会变**：本会话实测同一场景的色块密度会在两个量级间自行跳变（黄色像素约 1.5k ↔ 9.5k，截图文件大小 218 KB ↔ 320 KB）。因此 A/B 必须做 `off → on → off` **三段对照**并量化像素；单次截图或肉眼判断会得出假结论。
4. **验证日志用单 pattern 查询**（hdc shell 对组合引号敏感）；设备侧先 `grep` 再回传，避免 `DFX_SignalHandler` / `[longframe]` 刷屏把关键行挤出缓冲。
5. **四层证据链缺一不可**：字节 → 驱动 → 计数 → 像素。任一层缺失都不能宣布修复。

---

## 三、怎么测试（单变量 A/B）

### 核心纪律：每次实验只改变一个变量

本会话的排除链示范：

1. 关掉注入（`DM_VK_X_NOCLIPMAP=1`）→ 原始 vertex 仍失败 → **排除「是我的变体导致」**
2. 换 fragment（常量 3 输出替代）→ 仍失败 → **排除 fragment**
3. 换 vertex（`simple4.vert`，保留同样的输出 / layout / descriptor）→ 成功 → **锁定原 vertex**
4. 缩体积（`spirv-opt -O`）→ 仍失败 → **排除体积 / 常见优化问题**
5. 比反射（nbind / 结构相同）→ 初步排除 descriptor 数量

> 补充判据：验证「是否真的进入目标场景」要用 `dispatches > 0` 与 `no pipeline` 是否增长，不能只看等待时间或截图。本会话曾因 `dispatches = 0`（画面还在加载 / 黑屏）把「未进入场景」误判为「修复生效」。

### 测试操作要点

- **设备状态管理**
  - WiFi hdc 固定 target：`hdc -t 192.168.2.158:42213 ...`；断线先 `hdc tconn 192.168.2.158:42213`。
  - 重装包 `versionCode` 必须递增：`DM_VERSION_CODE=200000000N python nxnative/dm/ohos/mkhap.py --game 01007EF00011E000`，否则报 `9568263 install version downgrade`（`mkhap.py` 默认用 Unix 秒，可能低于设备已装版本）。
  - 确认日志里的 PID 是本次进程（`hdc shell pidof dev.eden_emu.ohos`）；旧进程残留会误导结论。
  - 设备 env 真实可写路径：`/data/app/el2/100/base/dev.eden_emu.ohos/haps/entry/files/dm/env.txt`（应用内视图为 `/data/storage/el2/base/haps/entry/files/dm/`）。
  - Git Bash 下 `hdc file send/recv` 会被路径转换搞坏，改用 PowerShell 并写绝对路径。
- **输入验证先行**：进入游戏前先确认虚拟手柄到达（`M2E2E publish/consume` 日志、`uitest uiInput keyEvent 2054`；A 键也可用 `uitest uiInput click 2446 882`）。
- **回归意识**：每轮改动后跑 subagent 只读审核（历史上拦下 3 处 SPIR-V 阻断问题，以及退化分支重复 binding、`vk_tex_prepare` 被跳过等）；真机验证覆盖「新场景修复 + 旧场景不退化」（菜单 + 游戏内都要看）。
- **正式验收前恢复配置**：关掉所有诊断 / 替代路径，推送正式 `env.txt`，使用原始 `shaders.dmsv` 与原始 shader，再验证菜单、存档选择、加载转场、实际 3D 场景、输入与持续运行。

### 通用排查漏斗

```
画面异常
 → 分层定位：pipeline 创建？（日志）→ 提交？（g_vkst 计数）→ 采样 / 绑定？（直通色实验）
             → 深度 / 坐标？（UV / w 直显）→ pass 分层？（PASSDUMP 找最早出错的 RT）
 → 缩到单 shader：逐 stage 替换
 → 缩到单因素：最小重制（simple4）→ 二分指令块
 → 修复后：字节合法 → 驱动接受 → 计数正常 → 像素正确
```

---

## 附录 A：运行时诊断通道（本会话补充）

> 目的：在**场景帧**精确下发开关 / 抓 GPU 现场，避免「改 env → 重启 → 猜帧号」的循环。

**启用**：`nxnative/dm/ohos/front_ohos.c` 原本无条件 `unsetenv("DM_CTL")`，而 Android 只在 `#ifdef DM_RELEASE` 下禁用；已改为一致的条件包裹（release 仍禁用）。`ctl_start()` 由 `hid_keys_init()` → `main.c` 无条件启动，平台无关。

`env.txt`：

```
DM_CTL=/data/storage/el2/base/haps/entry/files/dm/fs.tsv
```

**三个实测约束**：

1. `hdc file send` **只能覆盖应用沙盒里已存在的文件**，新建会 permission denied；`/data/local/tmp` 应用进程读不到。用目录里原本存在的空文件（如 `fs.tsv`）作载体。
2. 本地 `ctl.txt` **保持前缀不变**、只追加新行（`env KEY=VALUE` / `A` / `shot` 等），再整体覆盖推送；控制线程按文件偏移增量读取，前缀不变才能只消费新行。每行必须以 `\n` 结尾，且只用追加方式。
3. OHOS Vulkan 的 `passdump` 命令是空实现，必须走 `env DM_VK_PASSDUMP=1`；**参数行要先于触发行下发**。

**常用开关**（`gfx_vk_exec.c::x_read()` 每次 submission 读取，均为 live）：

| 类别 | 开关 |
|---|---|
| pass / 帧抓取 | `DM_VK_PASSDUMP`, `DM_VK_PASSDUMP_SKIP`, `DM_VK_PASSDUMP_SIZE`, `DM_VK_PASSDUMP_PASS`, `DM_VK_PASSDUMP_FRAMES`, `DM_VK_PASSDUMP_TARGET`, `DM_VK_PASSDUMP_DEPTH`, `DM_VK_PASSDUMP_FMT` |
| 纹理 / 采样 | `DM_VK_TEXDUMP=<pool>+<off>`, `DM_VK_TEXDUMP_LEVEL`, `DM_VK_WATCH=<off>[#n]`, `DM_VK_LOG_UPLOADS`, `DM_VK_LOG_FS=<fs key>`, `DM_VK_SAMP_LOG`, `DM_VK_UBO_LOG` |
| 追踪 | `DM_VK_TRACE_PASS`, `DM_VK_TRACE_SIZE=WxH[:fmt[:fmt]][#n]`, `DM_VK_TRACE_FRAME`, `DM_VK_LOG_CLEAR`, `DM_VK_LOG_TID` |
| 单变量 A/B | `DM_VK_X_*`（`NOCLIPMAP` / `SKIPVS` / `COLOR` / `NOSETC` / `NOSETMEMO` / `NOSHC` / `NOKEEP` / `NODRVC` / `LANDCUT` / `RDKEEP` / `NOGS` …）、`DM_VK_PASS_SPLIT`, `DM_VK_ASYNC_PIPE`, `DM_VK_NOPREWARM`, `DM_NVN_SAMP_SNAP` |

> `DM_VK_TRACE_SIZE` 命中一次后会自动清空，只 trace 一个 pass；用 `#n` 指定第 n+1 个同尺寸 pass。
> `DM_NVN_FB_SNAP` / `DM_NVN_ZBLOCK` / `DM_NVN_PASSDUMP_ALL` 等只在 Metal `nvn_mtl.m` 生效，Android / OHOS 不要误用。
> 启动期才读、需重启才生效：`DM_VK_UBWC`、`DM_NVN_ASYNC`、`DM_VK_X_BC_CPU`、`DM_VK_NOPCACHE` 等（`static` 缓存）；`DM_NVN_ASYNC` 默认关闭。

## 附录 B：pass 分层定位手法

1. `env DM_VK_TRACE_PASS=-2` 列出整帧所有 pass 的尺寸 / 格式，先画出帧结构（例：BOTW 一帧 126 个 pass，0–16 亮度金字塔、17–28 为 1280×720 主 3D、29–118 为 bloom 多级、119+ 回到全尺寸）。
2. `env DM_VK_PASSDUMP_PASS=<n,n,...>` + `env DM_VK_PASSDUMP=1` 按编号，或 `DM_VK_PASSDUMP_SIZE=WxH[,WxH...]` 按尺寸抓 RT；产物为 `DM_ROOT/pass_NN.ppm`，用 `hdc file recv` 拉回。
3. 对每个 RT 做**像素统计**（黄色 / 品红计数）而不是只看缩略图，二分出「色块最早出现在哪个 pass」。
4. 结合 `DM_VK_LOG_CLEAR=1` 看 clear color、`DM_VK_TRACE_SIZE` 看绑定、`DM_VK_TEXDUMP` 看被采样纹理，区分「画进去的」与「采样出来的」。

> 注意：pass 编号**每帧会变**，跨帧对比要用尺寸 / 格式而不是编号；`DM_VK_PASSDUMP_SKIP=WxH` 可以按尺寸定位起点。

---

**一句话**：改 shader 靠「定位 - 注入 - variant 分发」的宿主侧改写框架；验证靠「字节 → 驱动 → 计数 → 像素」四层证据链；测试靠「单变量 A/B + 缓存键隔离 + 最小重制」收敛根因。
