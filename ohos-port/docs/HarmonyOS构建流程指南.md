# nxohos HarmonyOS 构建流程指南

> 适用于 nx2android/nxohos 游戏宿主的 HarmonyOS 真机构建、打包、签名、安装和验证。示例游戏为 BOTW 1.9.0，Title ID `01007EF00011E000`。
>
> 当前验证基线：`main@50463c7`。本文命令以 Windows PowerShell 为例；路径按实际环境替换，不要在命令或文档中写入密钥内容、证书密码。

## 1. 构建链路

```text
游戏目录 + prod.keys
        │
        ▼
porter port --platform hap
        │
        ├─ extract  解包 ExeFS / RomFS / AOC
        ├─ rewrite  DM_TARGET=ohos，构建期 NSO/AOT 重写
        ├─ shaders  生成 shaders.dmsv
        ├─ host     OHOS SDK 构建 libdmhost.so
        ├─ datapack 生成 mods/RomFS/aoc/env.txt 等统一数据包
        ├─ hap      mkhap.py 构建并签名单模块或多模块 HAP
        └─ install  hdc / bm 安装并启动
```

有两种常用构建方式：

- **完整构建**：游戏、NSO、shader、数据或 profile 发生变化时，使用 porter 全流程。
- **增量构建**：只修改 `libdmhost.so` 的宿主源码时，重新构建宿主并覆盖 ASCII HAP 工程，再执行 `assembleHap`，不必重复拆分十几 GB 数据。

## 2. 环境准备

### 2.1 必需工具

| 工具 | 用途 |
|---|---|
| Python 3 | porter、NSO 重写、宿主和 HAP 构建脚本 |
| DevEco Studio | hvigor、Node、JBR、自动签名材料 |
| OHOS SDK | AArch64 clang、sysroot、Native Window/Vulkan 等库 |
| Vulkan SDK / glslc | 后端内置 shader 和诊断 shader 编译 |
| hdc | WiFi 真机安装、启动、文件和日志操作 |
| 游戏 dump、更新、DLC、profile | 生成匹配版本的数据包 |

### 2.2 推荐环境变量

标准 DevEco 安装目录可这样设置：

```powershell
$deveco = "D:\dev-echo\DevEco Studio"
$env:DEVECO_HOME = $deveco
$env:OHOS_SDK = "$deveco\sdk\default\openharmony"
$env:DEVECO_SDK_HOME = "$deveco\sdk"
$env:JAVA_HOME = "$deveco\jbr"
$env:PATH = "$deveco\tools\node;$env:JAVA_HOME\bin;$env:PATH"
```

`build_ohos.py` 要求 `$OHOS_SDK` 下存在：

```text
native/llvm/bin/clang
native/sysroot/
```

本机 `hdc` 也可能来自另一套 DevEco 安装，应单独指定：

```powershell
$hdc = "E:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe"
$target = "192.168.2.158:42213"
```

设备有多个 hdc target 时，所有命令都应加 `-t $target`，否则会出现 `need connect-key`。

### 2.3 路径要求

- 工作区：`f:\新建文件夹`
- 主源码：`f:\新建文件夹\nx2android`
- HAP 构建目录必须是 **ASCII 路径**，例如 `F:\ohos_build_dmhost_01007EF00011E000` 或 `F:\ohos_build_dmhost_fix`。
- 大数据构建盘需要约两份数据包及签名产物的空间。
- `libdmhost.so` 必须在 hvigor 构建前复制到 `entry/libs/arm64-v8a/`。

可显式指定自动打包目录：

```powershell
$env:DM_HAP_BUILD_DIR = "F:\ohos_build_dmhost_01007EF00011E000"
```

该目录会被 `mkhap.py` 清空并重新复制，目录名必须以 `ohos_build` 开头，不能指向手工维护的重要工程。

### 2.4 构建前检查

```powershell
python --version
node -v
& $hdc list targets
git -C "f:\新建文件夹" status --short --branch
```

确认 DevEco 自动签名材料与以下 bundle 匹配：

```text
dev.eden_emu.ohos
```

签名密码保留在 DevEco 安全存储中，不要导出到脚本或文档。

## 3. 方式 A：完整一键构建

### 3.1 先查看阶段和输入

```powershell
Set-Location "f:\新建文件夹\nx2android"
python -m porter port <游戏目录> -k <prod.keys路径> --platform hap -o <输出目录> --dry-run
```

重点检查：

- Title ID、游戏版本和 NSO build ID；
- profile 中的模块、内存和 shader 配置；
- 输出目录是否有足够空间；
- 是否误用了其他平台的缓存。

### 3.2 构建并安装

```powershell
python -m porter port <游戏目录> -k <prod.keys路径> --platform hap -o <输出目录> --install
```

不加 `--install` 时只生成产物：

```powershell
python -m porter port <游戏目录> -k <prod.keys路径> --platform hap -o <输出目录>
```

完整流程自动完成 NSO/AOT、`shaders.dmsv`、宿主、数据包、HAP 和安装，不需要分别手工执行这些阶段。

## 4. 方式 B：分阶段或增量构建

### 4.1 构建 `libdmhost.so`

在源码目录执行：

```powershell
Set-Location "f:\新建文件夹\nx2android"
python nxnative/dm/ohos/build_ohos.py --game 01007EF00011E000 --lib --jobs 8
```

默认输出：

```text
nxnative/dm/ohos/out/01007EF00011E000/libdmhost.so
```

自定义输出目录：

```powershell
python nxnative/dm/ohos/build_ohos.py --game 01007EF00011E000 --lib --out <宿主输出目录> --jobs 8
```

只做编译检查、不链接：

```powershell
python nxnative/dm/ohos/build_ohos.py --game 01007EF00011E000 --lib --only-compile
```

Release 宿主：

```powershell
python nxnative/dm/ohos/build_ohos.py --game 01007EF00011E000 --lib --release
```

注意：debug 和 release 的默认输出位置不同，后续复制时必须选择本次实际生成的 SO。

### 4.2 使用现有数据包重新打 HAP

如果数据包已经由 porter 生成，可直接执行：

```powershell
python nxnative/dm/ohos/mkhap.py --game 01007EF00011E000 --lib <libdmhost.so路径> --data <数据包目录> --out <HAP输出目录>
```

数据包布局必须是：

```text
mods/
RomFS/
aoc/                  # 有 DLC 时
shaders.dmsv
env.txt
settings.txt          # 按游戏需要
```

`mkhap.py` 会自动：

- 复制 HAP 模板到 ASCII 构建目录；
- 按当前时间更新 `versionCode`；
- 放置宿主 SO 和 rawfile；
- 按默认约 3500 MiB 拆分大数据；
- 分别构建、签名每个模块；
- 输出单 HAP 或 `dmhost-haps/`。

### 4.3 只更新现有 entry 工程

适用于真机反复调试宿主、设备上已经有完整数据的情况。

复制新宿主：

```powershell
Copy-Item -LiteralPath "f:\新建文件夹\nx2android\nxnative\dm\ohos\out\01007EF00011E000\libdmhost.so" -Destination "F:\ohos_build_dmhost_fix\entry\libs\arm64-v8a\libdmhost.so" -Force
```

检查 `F:\ohos_build_dmhost_fix\AppScope\app.json5` 的 `versionCode`，必须高于设备已安装版本。可获取当前 Unix 时间作为新版本号：

```powershell
[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
```

然后构建 entry：

```powershell
Set-Location "F:\ohos_build_dmhost_fix"
$env:JAVA_HOME = "$deveco\jbr"
$env:PATH = "$env:JAVA_HOME\bin;$deveco\tools\node;$env:PATH"
& "$deveco\tools\hvigor\bin\hvigorw.bat" --mode module -p module=entry@default -p product=default -p buildMode=debug assembleHap --no-daemon
```

输出：

```text
entry/build/default/outputs/default/entry-default-signed.hap
```

## 5. 单 HAP 与多 HAP 规则

### 5.1 单 HAP

小数据包生成一个签名 HAP，可直接安装：

```powershell
& $hdc -t $target install -r <entry-default-signed.hap路径>
```

### 5.2 多 HAP

大数据生成：

```text
dmhost-haps/
├── entry-default-signed.hap
├── data1-default-signed.hap
├── data2-default-signed.hap
└── ...
```

规则：

- 单个签名 HAP 必须小于 4 GB，默认按约 3500 MiB 预留；
- `entry` 携带宿主、shader、mods 和小文件；
- `RomFS/aoc` 分配到 dataN feature 模块；
- rawfile 不能拆分单个文件，单文件本身不能超过分包上限；
- 每个模块都要分别签名；
- 不用 `assembleApp` 代替多 HAP，它会额外生成很大的 `.app`，并可能包含未签名副本。

多模块安装：

```powershell
$haps = "<dmhost-haps目录>"
$remote = "/data/local/tmp/nx2a-install"
& $hdc -t $target shell "rm -rf $remote; mkdir -p $remote"
Get-ChildItem -LiteralPath $haps -Filter "*.hap" | ForEach-Object {
    & $hdc -t $target file send $_.FullName "$remote/$($_.Name)"
}
& $hdc -t $target shell "bm install -p $remote -u 100 -r -w 600"
```

设备不接受 `-u 100` 时：

```powershell
& $hdc -t $target shell "bm install -p $remote -r -w 600"
```

`-w 600` 用于避免十几 GB 数据解包时触发 bm 默认超时。

## 6. 启动与验证

### 6.1 启动应用

```powershell
& $hdc -t $target shell aa force-stop dev.eden_emu.ohos
& $hdc -t $target shell aa start -b dev.eden_emu.ohos -a EntryAbility
```

启动 entry，不要直接启动 dataN；dataN 只是 rawfile 容器。

### 6.2 首启 staging

首次启动或数据 stamp 变化时，ArkTS 会按 `dm.files.tsv` 把各模块 rawfile 释放到：

```text
/data/app/el2/100/base/dev.eden_emu.ohos/haps/entry/files/dm/
```

必须确认：

- 能读取 entry 的 manifest；
- dataN 通过 `createModuleContext(module).resourceManager` 读取；
- 分块复制长度正确；
- 全部文件成功后才写 `.stamp`；
- `mods/main.patched.bin` 和 `RomFS` 存在后才会启动游戏。

dataN 本身没有游戏 Ability，不能通过点击其入口验证 staging。

### 6.3 查看当前进程和日志

```powershell
& $hdc -t $target shell pidof dev.eden_emu.ohos
& $hdc -t $target shell hilog -x -P <PID> -e "starting the game|staged|shader bundle|window:|shot:"
```

判断画面时：

- 只采用当前 PID 的日志和帧；
- 启动早期 `frame0` 可能是正常黑帧；
- 等菜单或实际场景后的帧；
- 最终验收关闭 `DM_VK_SPV_ALT`、direct present 和其他替代路径。

## 7. 清理与重建

宿主构建会依据源码和编译参数自动增量。需要清理 HAP 构建时：

```powershell
Set-Location "F:\ohos_build_dmhost_fix"
& "$deveco\tools\hvigor\bin\hvigorw.bat" clean --no-daemon
```

停止 hvigor daemon：

```powershell
& "$deveco\tools\hvigor\bin\hvigorw.bat" --stop-daemon
```

完整重打数据包时优先重新运行 `mkhap.py`，不要在它的自动构建目录中长期存放手工修改；需要反复修改时应使用单独的 `ohos_build_dmhost_fix` 工程。

## 8. 常见错误

| 错误 | 原因与处理 |
|---|---|
| hvigor 在中文路径失败 | 把 HAP 工程复制到 `F:\ohos_build...` 等 ASCII 目录后构建 |
| HAP 中没有 `libdmhost.so` | 在 hvigor 前放入 `entry/libs/arm64-v8a`，不能用 HAP `rawfile` 代替 native libs |
| `RSASSA-PSS`、签名算法或 keystore 错误 | 将 `JAVA_HOME` 和 PATH 切到 DevEco 自带 `jbr`，不要使用过旧系统 Java |
| bundleName 与 Profile 不匹配 | 使用与签名材料匹配的 `dev.eden_emu.ohos`，或重新生成对应 Profile |
| `find zip central directory failed` | 单 HAP 超过签名工具的普通 zip 限制，改用 entry + dataN |
| project 模式找不到 `assembleHap` | 使用 `--mode module`，每个模块分别执行 `assembleHap` |
| 多 HAP 安装后缺少数据 | 必须整组 `bm install -p`，不能只安装 entry 或某个 dataN |
| bm `operation time out` | 增加 `-w 600`，并检查设备剩余空间 |
| `install version downgrade` | 提高 `AppScope/app.json5` 的 `versionCode` 后重新签名 |
| `need connect-key` | 当前有多个 hdc target，为所有 hdc 命令添加 `-t <target>` |
| dataN 启动无入口 | 这是正常的；从 entry 启动并完成 staging |
| staging 后游戏未启动 | 检查 stamp、文件长度、`mods/main.patched.bin` 和 `RomFS`，不要直接进入 shader 调试 |
| 原始画面验收仍加载替代 SPV | 清理 env 中的 `DM_VK_SPV_ALT/DM_VK_SPV_DIR` 和 direct present 开关后重启 |

## 9. 构建验收清单

- [ ] NSO/AOT 和 `shaders.dmsv` 来自同一个游戏版本与 profile；
- [ ] OHOS 宿主编译、链接无失败；
- [ ] `libdmhost.so` 位于正确的 HAP native libs 目录；
- [ ] 所有 HAP 都已签名，单 HAP 均小于 4 GB；
- [ ] 多 HAP 整组安装成功，staging 完整；
- [ ] 设备运行的是本次 `versionCode` 和新宿主，而不是旧实例；
- [ ] guest、RomFS、存档、shader、draw/compute、音频和 present 均有日志证据；
- [ ] 原始 shader、正常最终 blit 下验证菜单和实际场景；
- [ ] 保存版本哈希、设备型号、PID、日志和真实帧截图。

## 10. 当前已验证示例

`main@50463c7` 的验证结果：

- OHOS 138 个源码编译、链接通过；
- entry HAP 签名成功；
- 提高 `versionCode` 后 WiFi hdc 安装成功；
- 原始 shader 深度 variant 在 Maleoon 910 上创建成功；
- PID 38933 的菜单后帧正确，1,800 次 present 时 `no program=0/no pipeline=0`。

移植原理和平台适配细节见[《HarmonyOS 移植适配说明》](HarmonyOS移植适配说明.md)，真机证据见[数据包与 present 验证记录](verify/20261107-数据包与present.md)。
