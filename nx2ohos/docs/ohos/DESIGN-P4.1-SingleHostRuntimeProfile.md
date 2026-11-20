# P4.1：一个宿主跑多个游戏——把游戏 profile 移出二进制

起草于 2026-11-21。状态：设计稿，尚未实现。

## 1. 问题

现在每个游戏都单独构建一个 `libdmhost.so`：

```
python dm/ohos/build_ohos.py --lib --game 01007EF00011E000
python dm/ohos/mkhap.py --game 01007EF00011E000 --lib dm/ohos/out/01007EF00011E000/libdmhost.so
```

这与移植版已经确立的产品形态相矛盾：一个 HAP、内置游戏库，每个游戏由自己的数据包提供（AGENTS.md：*lib-only HAP，游戏数据以 HTTP pack 下发*）。今天要支持第二个游戏，就必须重新构建并重装应用；而一个旧的 HAP 会在不同游戏之间悄悄改变行为。

把 `--game` 在整条工具链里追一遍，它只用来选 profile：

| 使用方 | 用这个 title 做什么 |
|---|---|
| `dm/ohos/build_ohos.py` | 输出目录、`gen/<title>/`、`-I gen/<title>` |
| `dm/games/game.py header <id> <out.h>` | 生成 `game_profile.h`：约 50 个 `#define DM_GAME_*` 常量 |
| `dm/ohos/mkhap.py` | HAP 构建目录名；从 profile 读 `pad_layout` / `op_mode` 写进 `pad_default.json` |

除此之外全是**随数据包走、从不编进二进制**的 per-title 数据：

- 重写后的模块（`mods/main.patched.bin`）、着色器包（`shaders.dmsv`）、`RomFS/`、`env.txt`；
- 重写器的 fixups（`main.fixups.tsv`）、手柄默认布局（`pad_default.json`）。

也就是说，per-game 二进制**只差一份生成头文件里的常量**。实测：运行时源码里 `DM_GAME_*` 共 218 处引用，分布在约 20 个文件；其中只有 5 处出现在预处理条件里（`#ifdef DM_GAME_DISPLAY_VERSIONS`、`DM_GAME_MULTI`、`DM_GAME_SHARED_FONT_TTF`、`DM_GAME_NVN_API_MAJOR`）。

上游其实已经有把它变成运行时值的机制：`game.py header_multi`（为多程序应用加的，例如 Super Mario 3D All-Stars）会生成

```c
#define DM_GAME_X (dm_game_cur()->x)
static const dm_game_t dm_games[] = { ... };
```

并在 `main.c` 里由 `dm_game_cur()` 按 `DM_PROGRAM` 选行。这正是运行时 profile 需要的形状；唯一要改的是**表的来源**：从编译期常量改为启动时读入的值。

## 2. 目标与非目标

**目标**

1. 一份 `libdmhost.so` 能跑所有数据包带 profile 的游戏。
2. 新增游戏或更新 profile 只发数据——不重建 HAP、不重装应用。
3. BOTW 行为逐位不变：模块加载顺序、内存分区、加载哈希、帧时间（在噪声范围内）都一致。
4. Mac 与 iOS 目标继续用当前生成的同一份源码编译。

**非目标**

- 把 `.so` 放进数据包。没好处：映射库的是沙箱加载器，而且每次同步上游仍然要为每个游戏重编。相比一个纯文本 profile 文件，这条路严格更差。
- 让用户在启动器里编辑 profile。它是 pack 制作数据；填错会改变内存布局，必须由数据包自身的校验拦住。
- 重做着色器包。`shaders.dmsv` 本来就是 per-game 数据。

## 3. 设计

### 3.1 一个生成器，两种模式

`game.py header` 增加 `--runtime`：

| 模式 | 生成的 `game_profile.h` | 使用方 |
|---|---|---|
| `builtin`（默认，现状） | `#define DM_GAME_X <字面量>` | Mac、iOS、OHOS 回退路径 |
| `runtime` | `#define DM_GAME_X (dm_game_cur()->x)` + `dm_game_cur()` 声明 | 单一 OHOS 宿主 |

两种模式共用同一个 `dm_game_t` 结构，调用点无需改动。

### 3.2 profile 文件

数据包把 profile 放在 `env.txt` 旁边：

```
<game root>/profile.tsv
```

它就是游戏的 `dm/games/<id>/game.tsv`，由 `pack_game.py` 原样复制（这本来就是制作格式，且 `mkhap.py` 今天已经在读它的两个键，证明这个模式可行）。解析规则沿用现有约定：制表符分隔，`#` 开始注释，空行忽略。

C 侧只读白名单里的键，因此文件可以保留它的说明注释：

```
title_id, program_id, name, modules, build, display versions（取自 build）, pad_layout, op_mode,
render_base, fps_target, fps_max, fps_hold, present_interval,
mem_reserve, mem_alias, mem_stack, app_pool, mem_used, sys_resource, stdalloc_cap, mem_map_limit,
romfs_marker, screen_aspect, present_snap, pso_mtl4, enc_thread, perf_boost,
draw_barriers_game, shader_key_skip（off/len）, tex_flush_upload, tex_dirty_upload, tex_dyn_slots,
args, env, launch_param, ue_texture_pool, ue_fps_cvar, upscaler_keys, vconv_range
```

**必需**键（身份、模块、内存）缺失时，启动失败并打印键名与文件路径——与今天“没有 profile 就编不过”等价。可选键保持结构体零默认值。

### 3.3 表的结构

`dm_game_t` 除数组字段外原样复用，数组改为带边界检查：

```c
enum { DM_GAME_MAX_MODULES = 8, DM_GAME_MAX_BUILDS = 4, DM_GAME_MAX_VERSIONS = 4 };
typedef struct {
    int program;
    const char *id, *name, *romfs_marker, *args, *env, *launch_param;
    const char *modules[DM_GAME_MAX_MODULES]; int nmod;
    struct { const char *build, *mods; } builds[DM_GAME_MAX_BUILDS]; int nbuilds;
    struct { const char *build, *display; } versions[DM_GAME_MAX_VERSIONS]; int nversions;
    /* 标量：mem_*、fps_*、各开关，均为 int / uint64_t */
} dm_game_t;
```

`header_multi` 现有的定长行已经证明这些上界够用（它的数组也是按 profile 大小定长）。

### 3.4 读取时机

`main.c host_preload()` 是唯一“足够早、又足够晚”的位置：

```
main(argc, argv)
  host_preload(argc, argv)
    argv[1] = 游戏根目录  →  game_profile_load(root + "/profile.tsv")   ← 新增，第一条
    …… 现有：load_modules / dm_cheats_init / dm_sdk_major / ……
  front_run(guest_main) → nnMain
```

`DM_GAME_*` 最早的使用者是 `profile_modules()`（模块列表）以及它之前的 `LOG` 行；内存分区宏（`hle_svc.c` 19 处）要到访客 `SetHeapSize` 时才读，晚得多。加载器的 `DM_LOADHASH` 在 `load_modules` 之后运行，所以每个使用方都能看到填好的表。

`dm_host_preload_start()`（iOS 的提前加载）调用同一个函数，两条路径看到同一张表。

### 3.5 按类别列出的调用点改动

| 类别 | 数量 | 改动 |
|---|---|---|
| 标量值（`MEM_*`、`FPS_*`、开关） | 约 150 处 | 无：宏变成 `dm_game_cur()->字段` 表达式 |
| 字符串值（`ID`、`NAME`、`ARGS`、`ROMFS_MARKER`） | 约 20 处 | 无 |
| 静态数组初始化（`DM_GAME_MODULES`、`DM_GAME_BUILD_MODULES`、`DM_GAME_DISPLAY_VERSIONS`） | 3 处 | 启动时从表填入 |
| 数组维度（`DM_GAME_NMOD` 作 VLA） | 2 处（`main.c`） | 改为固定上界 `DM_GAME_MAX_MODULES` |
| `#ifdef` 条件 | 5 处 | 始终定义；运行时判空决定 |

5 处 `#ifdef` 改为：

```c
#ifdef DM_GAME_MULTI            →  if (dm_game_program_count() > 1)
#ifdef DM_GAME_DISPLAY_VERSIONS →  if (dm_game_cur()->nversions)
#ifdef DM_GAME_SHARED_FONT_TTF  →  char *f = dm_game_cur()->shared_font; if (f)
#ifdef DM_GAME_NVN_API_MAJOR    →  if (dm_game_cur()->nvn_api_major)
```

在 `builtin` 构建里，同样的表达式求值结果与原来的常量一致，因此一份源码同时服务两种模式。

### 3.6 多程序应用

`game.tsv` 已用 `programs` 键标识这类应用。运行时加载器把列出的子 profile 读成第 0..n 行，`dm_game_cur()` 对 `DM_PROGRAM` 的语义保持不变。`header_multi` 今天拒绝的两个键（`shared_font`、`device_api`）变成运行时字段——这消除了该限制，而不是复制它。

### 3.7 坏 profile 的拦截

- `pack_game.py` 从当前构建所编译的同一份 `game.tsv` 写出 `profile.tsv`，profile 与包的 `mods/` 出自同一份制作记录。
- 包的 `.stamp` 已覆盖内容变更；`profile.tsv` 纳入戳记集合，编辑它就会像其他文件一样触发重新导入。
- `game_profile_load` 拒绝以下情况：必需键缺失、模块数超上界、数字无法解析、`title_id` 与目录名不符。每种拒绝都打印文件与键名。

## 4. 逐文件工作清单

| 文件 | 工作 |
|---|---|
| `dm/games/game.py` | 新增 `header --runtime`；输出带边界的 `dm_game_t`；多程序继续走 `header_multi` |
| `dm/host/game_profile_rt.c`（新增） | `game_profile_load()`、`dm_game_cur()`、`dm_game_program_count()`、tsv 解析（约 150 行） |
| `dm/host/main.c` | 在 `host_preload` 第一条调用加载器；修正两处 VLA；填入模块数组 |
| `dm/host/hle_svc.c` | 标量无需改；确认 19 处在两种模式下都能编译 |
| `dm/host/hle_cheats.c`、`hle_oe.c`、`hle_sdk16.c`、`gfx/nvn/hle_nvn.c` | 上述 `#ifdef` 改写 |
| `dm/games/<id>/game.tsv` | 不变（本来就是来源） |
| `dm/tools/pack_game.py` | 把 `game.tsv` 复制进包作为 `profile.tsv`；纳入戳记输入 |
| `dm/ohos/build_ohos.py` | 新增 `--profile builtin\|runtime`；`runtime` 时给 `game.py header` 传 `--runtime` |
| `dm/tools/test_game_profile.py`（新增） | 解析 profile，与 `game.py` 的 builtin 常量逐字段比对 |
| `dm/ohos/mkhap.py` | 打 runtime 宿主时不再查 profile（由数据包负责） |

## 5. 风险

| 风险 | 缓解 |
|---|---|
| 某个内存布局常量在 profile 载入前被读取 | 加载器是 `host_preload` 的第一条语句；`dm_game_cur()` 内部断言非空并以明确信息 die |
| 两种模式随时间漂移 | 唯一来源是 `game.tsv`：builtin 与 runtime 由它生成；`test_game_profile.py` 比对二者 |
| iOS/Mac 回归 | `game.py header` 默认仍是 builtin；共享源码形状不变 |
| 必须保持常量的静态初始化 | 已审计：只有 `main.c` 的三处数组，均由表填入 |
| 不带 profile 的旧数据包 | runtime 宿主回退到编译内置的默认表，并记录日志；包校验报告缺失文件 |

## 6. 验证

三层，由便宜到贵。

1. **单元**：`test_game_profile.py` 从 BOTW 的 `game.tsv` 生成 profile，用 C 测试桩（或解析器的 Python 镜像）解析，再与 `game.py header` 的 builtin 输出逐字段比对。任何差异即失败。
2. **加载一致性**：分别构建 `--profile builtin` 与 `--profile runtime` 两个 HAP，用同一份 BOTW 数据加 `DM_LOADHASH=1` 运行，比对哈希、模块顺序行、SDK 行与内存分区日志。出现差异即说明 profile 不等价，改动到此为止。
3. **真机**：runtime HAP 在 Pura 70 Pro+ 上跑 Shrine 基线。帧时间须与 builtin（P3.5 方法：`measure.py --scene shrine --samples 3`，取中位数）在噪声范围内一致，截图一致。然后用同一构建跑第二个游戏、不重编——这才是本次改动的意义。

## 7. 落地步骤

| 步骤 | 内容 | 通过标准 |
|---|---|---|
| 0 | 本文档 | 评审通过 |
| 1 | `game_profile_rt.c` + `test_game_profile.py` | BOTW profile 解析结果等于 builtin 常量 |
| 2 | `game.py --runtime`、调用点改写、`--profile` 开关 | 两个 HAP 都能构建；加载哈希相等 |
| 3 | `pack_game.py` 携带 `profile.tsv`；OHOS 默认 runtime | shrine 帧时间等于 builtin 基线 |
| 4 | 对已发布游戏停用 per-game OHOS 构建 | 一个 HAP 跑两个游戏、二者之间无需重编 |

第 4 步保留 builtin 路径用于适配新游戏：profile 尚未就绪的游戏，仍可按今天 experimental 的方式单独构建宿主。

## 8. 实现状态（分支 `feat/single-host-profile`）

第 1–3 步的代码已落地，第 1 层验证通过，等待真机做第 2、3 层。

| 项 | 状态 |
|---|---|
| `game.py profile <id> <out.tsv>`：把 `header_one` 的常量写成 `<字段>\t<值…>` 行 | 完成 |
| `game.py header <id> <out.h> --runtime`：`dm_game_t` + 字段 X-macro + 宏读 `dm_game_cur()` | 完成 |
| `dm/host/game_profile_rt.c`：解析、填表、`dm_game_cur()`、拒绝规则 | 完成 |
| `main.c`：`host_preload` 第一条调用 `game_profile_load(argv[1]/profile.tsv)` | 完成 |
| 5 处 `#ifdef` 改为运行时判断（`DM_GAME_HAS_SHARED_FONT`、`DM_GAME_HAS_DEVICE_API`、`DM_GAME_NVERSIONS`、`DM_GAME_NGAMES`） | 完成 |
| `build_ohos.py --profile builtin\|runtime`（runtime 输出到 `out/<id>-rt`，并生成 `profile.tsv`） | 完成 |
| `pack_game.py`：包内写 `profile.tsv`，并把它纳入 stamp | 完成 |
| `dm/tools/test_game_profile.py`：runtime 行 vs builtin 常量逐字段比对 | 完成：**217 个程序 0 差异** |
| `test_ohos_patches.py`：补丁队列 | 完成：22 个补丁 0 失败 0 漂移 |

顺带补上的上游缺口（合并 185 提交后 `-Wl,--no-undefined` 暴露）：`mtl_tex_cpu_rewritten`、
`mtl_link_mode_set` 两个 Metal 后端符号的 OHOS stub，位于 `dm/ohos/gfx_mtl_compat_ohos.c`。

真机验证命令（runtime 库 + 该游戏的 profile）：

```
hdc file send f:\新建文件夹\.tmp\hap_rt\dmhost-signed.hap /data/local/tmp/nx2a-e/entry-default-signed.hap
hdc shell 'bm install -p /data/local/tmp/nx2a-e -r -w 600'
hdc file send dm/ohos/out/01007EF00011E000-rt/profile.tsv \
    /data/app/el2/100/base/dev.eden_emu.ohos/haps/entry/files/games/01007EF00011E000/profile.tsv
```

验收：启动日志出现 `game profile: <名字> (1 program, 3 modules)`；`DM_LOADHASH=1` 与 builtin 构建一致；shrine 帧时间
与 builtin 基线在噪声内一致。

## 9. 待定问题

1. ArkTS 侧是否直接读 `profile.tsv` 里的 `pad_layout` / `op_mode`，而不是 `mkhap.py` 生成的 `pad_default.json`？现有包布局两种都支持；维持现状可以把本次改动挡在 UI 层之外。
2. 包格式里 profile 用独立文件（本文方案）还是并入 `nx2a-data.json`？独立文件与 `game.tsv` 逐字节一致，戳记也简单。
3. 多程序应用用“一个 `profile.tsv` + 子文件”，还是“一个文件 + `program` 列”？前者可以直接复用 `programs`。
