# NXOHOS 0.2.0

面向 HarmonyOS 的 Switch 游戏移植运行环境。本版本把「一个游戏一个宿主」改成**一个 HAP 跑所有游戏**：应用只负责运行环境，每个游戏的常量、着色器、资源都以数据包形式下发。

- 版本：0.2.0（versionCode `2000786201`，arm64，API 12+）
- 安装包：[HAP/nxohos-0.2.0-signed.hap](HAP/nxohos-0.2.0-signed.hap)
- 设备实测：HUAWEI Pura 70 Pro+（Kirin 9010 / Maleoon 910）
- 详细性能数据：[BOTW 鸿蒙性能优化经验](../../nx2ohos/BOTW鸿蒙性能优化经验.md)

## 目录

| 文件 | 内容 |
|---|---|
| `HAP/nxohos-0.2.0-signed.hap` | 可安装的应用（单一宿主，支持多游戏） |
| `docs/game-packaging.md` | 游戏分发制作方式：从 porter 产物到 `.nxpk` |
| `docs/library-and-download.md` | 游戏库启动方式、连接下载的操作说明 |
| `screenshots/library.jpeg` | 游戏库界面（含"可下载"列表与卡片状态） |
| `screenshots/gameplay-botw.jpeg` | 实机运行画面（塞尔达传说：旷野之息） |

## 快速开始

1. 安装（设备已开启无线/有线调试）：

```bash
hdc file send HAP/nxohos-0.2.0-signed.hap /data/local/tmp/nx2a-e/entry-default-signed.hap
hdc shell 'bm install -p /data/local/tmp/nx2a-e -r -w 600'
```

2. 打开 **NXOHOS**。首次启动没有游戏时显示"添加游戏"。
3. 在库页底部「可下载」填入游戏包服务器地址（例如 `192.168.2.2:28899`），刷新后点「下载」。
4. 下载完成后点游戏卡片——**直接进入游戏**，不需要再点一次列表。
5. 退出游戏：游戏内返回库（进程结束，下次启动回到库）。

## 0.2 的变化

1. **单一宿主（P4.1）**：游戏常量不再编进 `libdmhost.so`。数据包携带 `profile.tsv`，宿主启动时读取；没有 `profile.tsv` 的旧包自动回退到内置默认配置，仍然可玩。
2. **包格式**：`.nxpk` 改用 GNU tar，支持超过 100 字节的文件名（《王国之泪》有 48 个这样的路径）。
3. **卡片状态可信**：按磁盘实况判断"可运行 / 不完整"，数据缺失的游戏不会再把玩家带进模拟器画面。
4. **增量包**只替换包内实际包含的目录，不再整目录清空。
5. **下载可更新**：服务端包变化（stamp 不同）时，已安装的游戏会重新变为可下载。
6. **启动更稳**：呈现线程提权、GPU 提频（OpenGTX）、NEAREST 放大；可选 XEngine 超分。

## 已知限制

- 《旷野之息》在 Pura 70 Pro+ 上约 **7–10 fps**（Shrine 场景，帧时间 ≈128–142 ms）。30 fps 在此设备不可达，原因与全部实测数据见性能文档。
- "不完整"的卡片表示游戏目录缺少 `mods/main.patched.bin` 或 `RomFS/`，需要重新导入数据包。
- 首次下载大型包（如《王国之泪》≈17.4 GB）需要较长传输时间；应用按 stamp 跳过已安装的同版本数据。
- 音频、视频解码仍为桩实现（H.264/AV1 影片黑屏），不影响 3D 主体运行。

## 复现构建

```bash
# 宿主（单一 HAP，运行时 profile）
python dm/ohos/build_ohos.py --lib --game 01007EF00011E000 --profile runtime
$env:DM_VERSION_CODE='2000786201'
python dm/ohos/mkhap.py --game 01007EF00011E000 \
    --lib dm/ohos/out/01007EF00011E000-rt/libdmhost.so --out <输出目录>

# 数据包
python dm/tools/pack_game.py --pack <porter 数据包目录> \
    --title <标题ID> --out <标题ID>.nxpk
```
