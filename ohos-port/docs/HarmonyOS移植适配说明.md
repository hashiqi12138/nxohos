# nxohos HarmonyOS 真机移植与适配说明

> 以《塞尔达传说：旷野之息》1.9.0（BOTW，Title ID `01007EF00011E000`）为例，总结 nx2android 宿主移植到 HarmonyOS 的架构、需要调整的位置、实际移植步骤和验收方法。
>
> 更新日期：2026-11-09。本文区分“已正式验证”和“诊断路径已出图”，不把替代 shader 或 direct present 当作原始渲染修复。

## 1. 当前项目状态

### 1.1 已完成

| 项目 | 当前结果 |
|---|---|
| HarmonyOS 系统能力 | 已验证 Vulkan、`VK_OHOS_surface`、大地址空间、寄存器/TLS/futex 等关键能力 |
| NSO 执行方式 | 已改为构建期 AOT 重写；不依赖真机运行时 JIT 或 RW→RX |
| 宿主构建 | OHOS SDK clang 可构建 AArch64 `libdmhost.so` |
| BOTW 数据包 | 1.9.0 数据已拆成 6 个签名 HAP，总计约 14.68 GB |
| 安装与首启释放 | 多 HAP 安装成功；跨模块 rawfile 可读取；首次 staging 成功 |
| 引擎启动 | guest 主循环、RomFS、存档挂载、NVN HLE、compute pipeline、音频和 present 均已运行 |
| 着色器加载 | 已加载 19,592 个 shader；运行期 shader bundle 和 pipeline 创建链路已工作 |
| 最终上屏链路 | XComponent → OHNativeWindow → VkSurfaceOHOS → swapchain → present 已跑通 |
| 严重性能问题 | 每帧约 5.5 秒的问题已修复，通常降至约 32–90 ms/帧 |
| 游戏画面 | 当前诊断基线可正确显示 BOTW 1.9.0 主菜单，见[真机截图](verify/20261109-BOTW-menu.png) |

### 1.2 尚未收口

1. **原始 shader 渲染路径尚未正式修复。** 当前正确菜单使用“固定全屏顶点 + 简单 texture fragment”的诊断组合，不能作为最终交付状态。
2. Maleoon 910 不支持 `VK_EXT_depth_clip_control`。NVN 默认深度约定可能产生 `z=-1, w=1`，Vulkan 默认裁剪范围却是 `0≤z≤w`，需要通用的深度映射 variant。
3. 原始最终 vertex/fragment 必须在相同游戏阶段、相同帧序号下重新验收；启动早期 `frame0` 为黑不能用于判断最终路径好坏。
4. 目前通常约 32–90 ms/帧，尚未达到 60 fps 的 16.7 ms/帧目标。
5. 诊断开关、替代 SPV、PPM、临时构建目录仍需清理；虚拟手柄、完整生命周期和长时间实玩还需按游戏逐项验收。

## 2. 总体移植架构

```text
游戏 dump / 更新 / DLC
        │
        ├─ 解包：ExeFS、RomFS、AOC
        │
        ├─ NSO 构建期重写（DM_TARGET=ohos）
        │    main/sdk/其他模块 → mods/*.patched.bin + fixups
        │
        ├─ shader 提取与转换 → shaders.dmsv
        │
        ├─ OHOS 宿主构建 → libdmhost.so
        │
        └─ 数据包 → env.txt / settings.txt / mods / RomFS / aoc / shaders.dmsv
                         │
                         ▼
              entry + data1...dataN 多模块 HAP
                         │
                         ▼
              首启 ArkTS staging 到 files/dm
                         │
                         ▼
 ArkTS XComponent → NAPI → dm_main → guest 线程
                         │
                         ▼
              NVN HLE → Vulkan replay
                         │
                         ▼
       OHNativeWindow → VkSurfaceOHOS → swapchain → present
```

核心原则是：**保留 nx2android 的 guest、HLE、NVN 和 Vulkan replay，只替换平台边界。** 不重写游戏业务，不把 BOTW 特殊分支散落到通用后端。

## 3. Android 与 HarmonyOS 需要调整的地方

| 边界 | Android | HarmonyOS / nxohos 的调整 |
|---|---|---|
| 工具链 | Android NDK | OHOS SDK clang，`--target=aarch64-linux-ohos` |
| 可执行代码 | 部分平台可运行时处理代码页 | 真机 W^X 限制严格，采用构建期 AOT 重写并随 HAP 交付 |
| UI 入口 | Activity / ANativeWindow | ArkTS 页面 + XComponent，NAPI 传 surfaceId |
| Vulkan WSI | `VK_KHR_android_surface` | `VK_OHOS_surface` + `vkCreateSurfaceOHOS` |
| 窗口生命周期 | Android surface 回调 | `onLoad/onDestroy` 上报和释放；每次新 surface 都重建 swapchain |
| GPU 内存池 | AHardwareBuffer 零拷贝路径 | 无等价 AHardwareBuffer 路径，使用 guest/shadow copied pools 和同步回拷 |
| 数据分发 | 外部文件目录或 adb 推送 | rawfile 内置于 HAP，首启释放到应用沙盒 |
| 大包 | 单 APK/外部数据 | 签名工具不支持超过 4 GB 的普通 zip，拆 entry + feature HAP |
| 安装 | adb install | 单 HAP 用 `hdc install -r`；多 HAP 推送目录后用 `bm install -p` |
| 日志 | logcat / 文件 | hilog 为主；stderr 另重定向到 `files/dm/log.txt` |
| 音频/媒体 | AAudio / MediaCodec | OH_Audio 与 OHOS 对应实现 |
| 调试 | adb | 本任务只使用 WiFi hdc 或 AGC 云调试，不依赖 USB |

## 4. 移植前的准备

### 4.1 工具与目录

需要：

- DevEco Studio、OHOS SDK、hvigor、DevEco 自动签名材料；
- OHOS SDK 中的 AArch64 clang 和 sysroot；
- Vulkan SDK 的 `glslc` 或 `glslangValidator`，调试 SPIR-V 时还需要 `spirv-dis/spirv-val`；
- 可连接真机的 `hdc`；
- 已正确解包的游戏、本体对应更新和 DLC，以及匹配版本的 profile。

环境要求：

- `OHOS_SDK` 指向包含 `native/llvm/bin/clang` 的 SDK 目录；不传时构建脚本也会尝试 DevEco 默认目录。
- `DEVECO_HOME` 指向 DevEco Studio；自动签名材料必须与模板 bundle `dev.eden_emu.ohos` 匹配。
- hvigor 构建目录必须是 ASCII 路径。可用 `DM_HAP_BUILD_DIR` 指定，例如 `F:\ohos_build_dmhost_fix`。
- 签名使用 DevEco 自带 JBR。系统 Java 若不支持所需算法，应将 `JAVA_HOME` 指向 `<DevEco>\jbr`，并把其 `bin` 放到 PATH 前面。
- 构建盘需容纳约两份数据包及签名产物；15 GB 级游戏不能用空间紧张的系统盘临时凑合。

### 4.2 游戏 profile

每款游戏先检查 `nxnative/dm/games/<titleID>/`：

- `game.tsv`：模块、显示版本、内存和帧率等配置；
- `patches.py`：必须按 NSO build ID 限定重写修正，不能把固定地址套到别的版本；
- shader 输入表和已有平台约束；
- 是否依赖 BCn、transform feedback、geometry shader、特殊内存布局或运行期 shader 捕获。

新版本游戏不能只改显示版本号。NSO build ID 变化后，需要重新核对重写位置和 shader 输入。

## 5. 实际移植步骤

### 5.1 解包并生成 OHOS NSO 模块

porter 的前两段与 Android 共用，但重写阶段设置 `DM_TARGET=ohos`：

```text
extract → rewrite（DM_TARGET=ohos）→ shaders
```

OHOS 重写产物放入数据包的 `mods/`，宿主启动时读取 `mods/main.patched.bin` 及 profile 声明的其他模块。

需要调整的典型位置：

- `dm/tools/patch_nso.py` 及平台 thread-slot/TLS 约定；
- `dm/ohos` 的加载和线程入口；
- 每款游戏按 build ID 设置的 `patches.py`；
- 生成目录与 Android 分开，避免错误复用另一平台的缓存。

**不要在设备上依赖匿名 RW 页面重新改为 RX，也不要把运行时下载的 `.so` 再 `dlopen` 当通用方案。** 当前真机结论要求构建期完成重写。

### 5.2 生成 shader bundle

shader 阶段把游戏 shader 转成 `shaders.dmsv`，包含 SPIR-V 和反射信息。宿主在 `gfx_vk_shader.c` 中完成：

- vertex/fragment/compute 等 stage 的 descriptor set 分配；
- UBO、纹理、 sampler、storage 等绑定；
- 设备不支持能力的声明清理；
- 整数纹理、额外输出、fragment depth 等 variant；
- 缺失 shader 的运行期捕获和下一次打包。

如果日志出现 shader missing，应把 `captured_shaders/` 重新交给转换链，而不是长期使用替代 shader 绕过。

### 5.3 构建 `libdmhost.so`

在 `nx2android` 仓库根目录执行：

```powershell
python nxnative/dm/ohos/build_ohos.py --game 01007EF00011E000 --lib --jobs 8
```

需要独立输出目录时：

```powershell
python nxnative/dm/ohos/build_ohos.py --game 01007EF00011E000 --lib --out <构建目录> --jobs 8
```

主要源码复用关系：

- `host/*.c`：guest 与系统 HLE；
- `gfx/*.c`、`gfx/nvn/*.c`、`gfx/tex/*.c`：图形公共部分；
- `android/gfx_vk_*.c`：共享 Vulkan replay；
- `android/compat`：可共用的 POSIX/Darwin 兼容层；
- `ohos/*.c`：OHOS 前端、音频、媒体、窗口和 present。

编译关键点包括 `-ffixed-x18`、`-DDM_OHOS=1`、AArch64 OHOS target、16 KB max-page-size，以及链接时 `--no-undefined`。

### 5.4 准备统一数据包

设备沙盒内的数据布局保持与 Android 一致：

```text
dm/
├── mods/
│   ├── main.patched.bin
│   └── 其他模块及 fixups
├── RomFS/
├── aoc/                  # 有 DLC 时
├── shaders.dmsv
├── env.txt
└── settings.txt          # 按游戏需要
```

`front_ohos.c` 设置：

- `DM_ROOT=<files>/dm`
- `DM_ROMFS=<root>/RomFS`
- `DM_SAVE`、`DM_TEMP`
- `DM_VK_SHADERS=<root>/shaders.dmsv`

OHOS 包内的路径型 env 值应写相对路径，例如 `DM_AOC_DIR=aoc`。启动时由 `env_under_root()` 放到沙盒根目录下，不能把 PC 或其他设备的绝对路径写进包。

### 5.5 打 HAP：小数据与大数据分别处理

完整 porter 入口：

```powershell
python -m porter port <游戏目录> -k <prod.keys路径> --platform hap -o <输出目录>
```

先看阶段图，不实际构建：

```powershell
python -m porter port <游戏目录> -k <prod.keys路径> --platform hap --dry-run
```

阶段图为：

```text
rewrite → shaders → host → datapack → hap → install
```

也可以对已有宿主和数据包直接调用：

```powershell
python nxnative/dm/ohos/mkhap.py --game 01007EF00011E000 --lib <libdmhost.so路径> --data <数据包目录> --out <输出目录>
```

#### 单模块

小数据包只生成 `dmhost-signed.hap`。`libdmhost.so` 必须先放到 `entry/libs/arm64-v8a`，rawfile 必须位于模块的 `src/main/resources/rawfile`，放错目录不会被打进 HAP。

#### 多模块

`mkhap.py` 默认按 3500 MiB 拆分：

- `entry` 保存 `env.txt`、`settings.txt`、`shaders.dmsv`、`mods/` 等固定小文件；
- `RomFS/` 和 `aoc/` 按文件大小分配到 `data1、data2...`；
- 每个 data 模块是随安装交付的 feature module；
- 每个模块分别执行 `assembleHap` 并签名；
- 最终输出 `dmhost-haps/`，安装时必须整组安装。

限制：

- 单个签名 HAP 必须低于 4 GB，默认预留到约 3.5 GB；
- rawfile 不能拆分单个文件，因此单文件本身也不能超过分包上限；
- 不要改用 `assembleApp` 规避多 HAP：当前链路会产生额外大 `.app`，且其中可能携带未签名副本，不是安装所需产物。

### 5.6 多 HAP 安装

推荐使用 porter 的 `--install`，其多模块流程为：

1. 将所有签名 HAP 推到 `/data/local/tmp/nx2a-install`；
2. 执行 `bm install -p <目录> -u 100 -r -w 600`；
3. 设备不接受 `-u 100` 时去掉该参数重试；
4. 停止旧实例并启动 `EntryAbility`。

核心命令形式：

```powershell
hdc shell bm install -p /data/local/tmp/nx2a-install -u 100 -r -w 600
hdc shell aa force-stop dev.eden_emu.ohos
hdc shell aa start -b dev.eden_emu.ohos -a EntryAbility
```

`-w 600` 是必须的：十几 GB 数据解包可能远超 bm 默认等待时间。多 HAP 不能用只适合单文件的 `hdc install -r <单个HAP>` 代替。

WiFi 连接示例：

```powershell
hdc tconn 192.168.2.158:42213
hdc list targets
```

### 5.7 首启 staging：rawfile 到沙盒

`hap/entry/src/main/ets/pages/Index.ets::stageData()` 是数据链路的关键适配点。

`dm.files.tsv` 格式：

```text
# DM_DATA <数据集stamp>
相对路径<TAB>字节数<TAB>所在模块
```

释放流程：

1. 从 entry 读取 manifest；
2. 比较沙盒 `.stamp`，相同则跳过；
3. stamp 变化时，先清理数据包拥有的旧 `mods/RomFS/aoc`；
4. entry 文件用当前 context，dataN 文件用 `createModuleContext(module).resourceManager`；
5. `getRawFd()` 后先 `lseek` 到 rawfd 起始位置，再用固定 1 MiB 缓冲循环读写；
6. 每个文件必须核对实际复制长度；
7. 全部成功后才能写 `.stamp` 并启动游戏。

不能改成整文件 `getRawFileContent()`：大文件会造成额外内存峰值。复制失败时不能把半份数据当完整游戏启动。

### 5.8 XComponent、窗口和 present

调用链：

```text
Index.ets XComponent
  → getXComponentSurfaceId/getXComponentSurfaceRect
  → napi_bridge.c::setSurfaceId
  → front_ohos.c::dm_ohos_front_set_surface
  → OH_NativeWindow_CreateNativeWindowFromSurfaceId
  → gfx_vk_present_ohos.c::swap_make
  → vkCreateSurfaceOHOS + swapchain
```

`swap_make()` 需要按真机能力设置：

- surface format 与 colorSpace 必须成对采用驱动报告值；
- 优先 IDENTITY transform；
- extent 来自 XComponent surface 像素尺寸，并夹到驱动 min/max；
- 建 swapchain 前设置 `SET_BUFFER_GEOMETRY`；
- 每帧 clear + blit，因此 `TRANSFER_DST` 是硬要求；
- composite alpha 从 supported 值中选择；
- 使用 FIFO 由合成器做 vsync 节流；
- acquire/fence 等待必须有界，避免 surface 消失时持锁卡死 UI 线程；
- 新 surface 到来时先销毁旧 swapchain，再释放旧 OHNativeWindow。

最终画面比例使用 aspect-fit/letterbox，不能为铺满屏幕而拉伸。

### 5.9 OHOS 内存池和 Vulkan replay

Android 的 AHardwareBuffer 零拷贝路径不能直接照搬到 OHOS。当前使用 copied/shadow pool：

- 提交前把本次需要的 guest 数据同步到 GPU shadow；
- GPU 完成后把本次实际写入范围回拷到 guest；
- deferred uniform update 可经 `vk_ubd_shadow()` 使用 scratch/shadow buffer；
- descriptor、image layout、pool serial 和 texture view 必须保持同一版本。

已修复的性能根因是：`pools_copy_back()` 曾经对每个 cached pool 整池回拷，单帧复制量可达约 1.5 GB。现在只处理与本次提交 GPU 写入范围相交的 pool，cached pool 也只复制相交区间，帧时间从约 5.5 秒降到通常约 32–90 ms。

适配其他游戏时应优先查看真实复制量和 `g_vkst`，不要用整池 `memcpy` 作为长期默认路径。

## 6. BOTW 当前图形问题与下一步修哪里

### 6.1 已排除的方向

- guest 主循环没有死锁，游戏持续产生 draw 和 present；
- 最终 HDR 源纹理可以包含正确菜单；
- image→buffer 回读可用；
- 最终 `vp_c3` 的 guest 和 GPU shadow 数据有效；
- `vp_c3` binding1 直通实验输出绿色，与 `c[2]=(0,2,0,0)` 一致，描述符和 UBO 读取路径可用；
- `VK_FORMAT_R8G8B8A8_UNORM` 与其 sRGB view twin 不是天然冲突；
- 设备 `maxBoundDescriptorSets=6`、动态 UBO 数量和 64 KB UBO range 不是当前根因。

### 6.2 已确认的深度裁剪差异

设备日志为：

```text
Maleoon 910, Vulkan 1.2, depth clip control no
```

原最终顶点可得到 `gl_Position=(2x,2y,-1,1)`。没有 `VK_EXT_depth_clip_control` 时，当前后端保留 NVN 的负一到一约定，但 Vulkan 默认会裁掉 `z<0` 的图元。原顶点加入：

```glsl
gl_Position.z = gl_Position.z * 0.5 + gl_Position.w * 0.5;
```

后可稳定绘制品红，证明该裁剪差异真实存在。

建议的正式修复位置：

- `android/gfx_vk_shader.c`：增加通用 SPIR-V 深度映射 variant，在最终位置输出阶段的 entry point return 前执行 `z'=z/2+w/2`；
- `android/gfx_vk_exec.c::build_pipeline()`：仅在设备无 depth clip control 且 NVN 当前为 near=-w 时请求该 variant；
- vertex 后还有 geometry 时只改最后的位置输出阶段，避免重复映射；
- variant 必须进入模块缓存键，因为游戏可在运行期切换 depth mode。

不能用 `depthClampEnable=true` 冒充修复，它会改变 3D 深度语义。也不应只对 BOTW 的最终 blit 写死补丁。

### 6.3 当前可用基线与正式验收的差别

当前已保存的正确菜单来自：

- 固定全屏顶点，`z=0,w=1`；
- 简单 `texture(tex,uv)` fragment；
- 菜单加载完成后的 `frame5`，而非启动早期 `frame0`。

这证明纹理和 present 基线可用，但还没有证明原始 shader 已恢复。下一步应在同一段菜单、同一类后帧上，对原始 bundle 与深度映射 variant 做单变量比较，成功后关闭所有替代 SPV 再验收。

### 6.4 当前诊断绘制方式的风险

“固定全屏顶点 `z=0,w=1` + 简单 `texture(tex,uv)` fragment”只能作为对照实验，不能作为最终绘制方案。它只替换最终合成 pass，上游游戏仍按原路径渲染；菜单正确不代表原始顶点、原始 fragment 或整个 3D 场景已恢复。

主要风险：

1. **绕过原始顶点变换**：固定 `pos.xy*2` 忽略原矩阵和分辨率适配。输入范围、视口或转场变化后，可能出现缩放、偏移、比例错误、局部显示或越界裁剪。
2. **掩盖而不是修复深度问题**：`z=0` 满足 Vulkan 默认裁剪范围，但其他 NVN near=-w 的 3D shader 仍可能被裁剪，表现为菜单正常而实际场景缺模型、近处物体消失或部分 pass 无内容。
3. **UV 与透视插值差异**：原 fragment 会结合 `gl_FragCoord.w` 做透视修正并防护 NaN；简单采样在 `w=1` 时可用，恢复原矩阵后可能出现 UV 非线性、拉伸、越界采样、黑边或闪烁。
4. **丢失原 fragment 语义**：如果同一 shader 用于其他目标或版本变化，替代 fragment 可能丢失 alpha、混合、通道处理、色彩处理和边界防护。
5. **性能数据偏乐观**：替代 shader 减少了矩阵读取和 fragment 运算，诊断期间的帧时间不能代表原始路径性能，正式优化后必须重新测量。
6. **验收误判**：该基线只能证明 HDR 源纹理、纹理绑定、采样和 present 可用，不能证明原始渲染正确，也不能作为发布配置保留。

因此，最终必须恢复原始 shader，以通用深度映射 variant 解决裁剪差异，并在菜单、实际场景、转场和不同分辨率下重新验收；诊断 env 和替代 SPV 应全部关闭。

## 7. 真机调试方法

### 7.1 防止旧日志和旧截图污染

每次实验：

1. `aa force-stop` 停止旧实例；
2. 清理旧 `frame*.ppm/pass_*.ppm`；
3. 启动后记录当前 `pidof dev.eden_emu.ohos`；
4. 只接受该 PID 的 `shot:` 日志对应文件；
5. 等 PPM 完整写完再拉取，避免读到截断文件。

当前 PID 日志示例：

```powershell
hdc shell hilog -x -P <PID> -e "shot:"
```

`mtl_stats()` 在共享 `gfx_common.c` 中是空 stub，TSV 里的 `@draws/@passes` 不能用于判断是否绘制。真实 replay 统计看 `g_vkst`。

### 7.2 帧抓取与系统截图

`DM_VK_SHOT` 抓的是游戏窗口纹理，适合区分“游戏没生成画面”和“present 没上屏”。系统 `snapshot_display` 不一定可靠反映 XComponent 内容，不能单独作为黑屏结论。

启动最初几帧可能本来就是黑或加载图。必须继续抓菜单出现后的帧，不能只分析 `frame0`。

### 7.3 替代 SPV 仅作诊断

`DM_VK_SPV_DIR` + `DM_VK_SPV_ALT=1` 可按 shader key 加载替代模块。GLSL 应生成与当前设备兼容的 SPIR-V，例如：

```powershell
glslangValidator -V --target-env vulkan1.2 -S vert -o <key>.spv <shader.vert>
```

每次只替换 vertex 或 fragment 一个变量。替代 SPV、direct present 和常量色 fragment 都是定位工具，最终验收必须全部关闭。

## 8. 适配一款新游戏的推荐顺序

1. **确认版本和 profile**：Title ID、更新版本、NSO build ID、模块列表、内存布局。
2. **跑通解包和 dry-run**：先确认阶段图与输入，不急着上设备。
3. **生成 OHOS 重写模块**：核对模块加载和线程入口，处理缺失 SVC/HLE。
4. **生成完整 shader bundle**：先消除 missing shader，再调画面。
5. **构建宿主**：所有符号在链接时解析，不用弱符号碰巧带过。
6. **先打小数据包**：验证签名、安装、staging、RomFS 读取和存档挂载。
7. **大数据再拆多 HAP**：检查每个模块签名、manifest 第三列和整组安装。
8. **先验证自测场景和 present**：再进入真实游戏，区分窗口问题与游戏问题。
9. **验证引擎推进**：guest 线程、RomFS、存档、音频、NVN draw、compute、present 逐项确认。
10. **验证真实游戏画面**：必须看游戏阶段后的真实帧，不能用常量色或任意非黑图验收。
11. **关闭诊断路径**：原始 shader、原始最终 blit、正常 present 下重新验证。
12. **做完整游戏验收**：输入、音频、存档、前后台、长时间运行和性能分别留档。

## 9. 关键文件索引

| 文件 | 作用 |
|---|---|
| `nxnative/dm/ohos/build_ohos.py` | OHOS SDK 构建宿主和 `libdmhost.so` |
| `nxnative/dm/ohos/mkhap.py` | 单/多模块 HAP 打包、签名、安装入口 |
| `porter/platform_ohos.py` | rewrite→shaders→host→datapack→hap→install 流水线 |
| `nxnative/dm/ohos/hap/entry/src/main/ets/pages/Index.ets` | 跨模块 manifest staging、XComponent 生命周期 |
| `nxnative/dm/ohos/napi_bridge.c` | ArkTS/NAPI 到 native 的桥接与游戏线程 |
| `nxnative/dm/ohos/front_ohos.c` | surfaceId、OHNativeWindow、环境变量和 `dm_main` 启动 |
| `nxnative/dm/ohos/gfx_vk_present_ohos.c` | VkSurfaceOHOS、swapchain、blit、present、帧抓取 |
| `nxnative/dm/ohos/ohos_vk_platform.h` | OHOS Vulkan 平台声明及无 AHardwareBuffer 的回退 |
| `nxnative/dm/android/gfx_vk_core.c` | device、memory pools、submission、copy-back |
| `nxnative/dm/android/gfx_vk_exec.c` | NVN 状态到 Vulkan pipeline/descriptor/draw 的 replay |
| `nxnative/dm/android/gfx_vk_shader.c` | DMSV、反射、SPIR-V module 与 variant |
| `nxnative/dm/android/gfx_vk_tex.c` | NVN texture/image/view 映射 |
| `ohos-port/docs/verify/20261107-数据包与present.md` | 早期数据包、present 和真机验证记录 |

## 10. 当前后续工作清单

按优先级：

1. 实现并审核无 `VK_EXT_depth_clip_control` 时的通用深度映射 variant；
2. 在菜单后帧重新比较原始 shader，确认是否还存在独立问题；
3. 移除替代 shader/direct present，仅保留必要通用修复验收；
4. 保存正式真机截图、当前 PID 日志、性能统计和版本哈希；
5. 清理诊断 env、临时 SPV/PPM 和构建目录；
6. 继续降低 replay、pool 同步和 GPU 成本，目标 16.7 ms/帧；
7. 完成输入、音频、存档、生命周期和长时间实玩验收。

---

**当前结论：nxohos 的平台、打包、数据、宿主和 present 主链路已经打通，BOTW 也已证明能够在真机上显示正确菜单；但项目仍处于“原始渲染修复与正式验收阶段”，还不能把当前诊断基线标记为最终可发布版本。**
