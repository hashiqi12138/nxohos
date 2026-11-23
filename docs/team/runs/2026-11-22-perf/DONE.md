# 性能指标展示：全链路打通（实测）

> 2026-11-23 01:16–01:34
> 设备 HBN-AL80 / HarmonyOS 7.0.0.111 / API 26
> HAP 3315414 B，versionCode 2099000024

---

## 一、最终效果（真机实测）

### 游戏库卡片

```
┌──────────────┐
│        60 fps│  ← 右上角，绿色（>=55）
│      Celeste │
│              │
└──────────────┘
  Celeste
  1.2 GB · 可运行
```

### 详情页

```
[启动游戏]                    ← 标题下方，不用滚动

大小        1.2 GB
版本        1.4.1
数据包      01c284224fde
状态        可运行
帧率        60 fps
帧时间      17.59 ms (p50) / 24.28 ms (p95) / 29.99 ms (max)
掉帧率      0.0%
GPU         3.81 ms
场景        celeste-title
测量时间    2026-11-22 23:03
```

截图：`.screenshots/perf-badge.jpeg`、`.screenshots/perf-detail.jpeg`

---

## 二、链路

```
perf_capture.py  →  <titleId>/perf.json  →  GamePerf.ets  →  GameCard / GameDetailSheet
   采集                 存储                    读取                展示
```

### 为什么 perf.json 放游戏根目录

`PackImport.ets:135-136` 只清理包里实际带的 `mods` / `RomFS` / `aoc` 三个目录。放根目录意味着**重导游戏数据不会丢测量记录**。

### 为什么没测到的字段是 -1 不是 0

写 0 会让「没测」看起来像「性能极好」。`GamePerf.ms()` / `pct()` 对 -1 返回「未测」。

---

## 三、怎么把 perf.json 送上设备（关键坑）

### 坑 1：shell 写不进应用沙箱

SELinux `debug_hap_data_file` 边界，`hdc file send` 到 `/data/app/el2/100/base/...` 返回 Permission denied。

**解法**：走 pack_server 下发，应用自己写。

### 坑 2：stamp 相等会完全跳过导入

`AGENTS.md` 明确写着：**Equal stamps skip the import entirely**。

所以「等 stamp 增量包」永远进不去。

**解法**：用**新 stamp** 标记为更新。因为包不带 `mods`/`RomFS`/`aoc`，`importPack` 一个都不删，只写 tar 里的文件。

### 坑 3：pack_game.py 会强制重算 stamp

`pack_game.py:109` 的 `ensure_manifest(force=write_profile(...) is not None)` —— 只要它写了 `profile.tsv` 就重算 `dm.files.tsv`，我手写的 stamp 被覆盖。

**解法**：绕过 pack_game.py，手写 NXPK：

```python
header = (b'NXPK' + struct.pack('<I', 1) + TITLE.encode('ascii')
          + stamp.encode('ascii').ljust(48, b'\x00') + struct.pack('<Q', len(tar_bytes)))
```

80 字节头 + GNU tar，tar 里放 `nx2a-data.json` / `profile.tsv` / `perf.json` / `dm.files.tsv`（元数据在前，应用从 tar 头部读 manifest）。

### 完整操作

```powershell
# 1. 用真实元数据 + 新 stamp 做包（10 KB）
# 2. 放进 games-dir，原包用符号链接不动
cmd /c mklink "G:\nxpk_out\perfserve\01007EF00011E000.nxpk" "G:\nxpk_out\library\01007EF00011E000.nxpk"

# 3. 起服务器（设备地址不用改，还是 28899）
python dm/tools/pack_server.py --games-dir G:\nxpk_out\perfserve --port 28899 --bind 0.0.0.0

# 4. 设备点「下载库」→ Celeste 显示 10 KB → 点「下载」→「✓ 已安装」
```

---

## 四、这轮改的代码

| 文件 | 改动 |
|---|---|
| `GamePerf.ets` | 加 `dmhost` import；`readPerf` 解析失败时打诊断日志（原来是静默 `[]`，和「从未测过」无法区分） |
| `GameDetailSheet.ets` | `facts()` 追加 6 行性能指标；import `PerfRecord, ms, pct, sceneLabel`；启动按钮移到标题下方 |
| `GameLibrary.ets` | 加 `dmhost` import + `listGames` 返回处日志；新增 `listGameNames()`（不遍历目录） |
| `SettingsPage.ets` | 改用 `listGameNames` 避免 `treeSize` 卡 UI |

---

## 五、踩到的两个 UI 定位错误（记录以免重犯）

1. **横滚 vs 纵滚**：游戏库是**纵向**列表，Celeste 在下面，我一开始横向 swipe 找不到它。用户指出后改成 `swipe 1400 1100 1400 350 400` 纵向滚。
2. **返回键会退到桌面**：详情页开着时按 Back 直接退到系统桌面（不是关弹窗），会跑到抖音去。关详情页要用 `✕` 按钮。

---

## 六、还没做的

1. **BOTW / TOTK 没有 perf.json**：只有 Celeste 测过（60 fps）。TOTK 测过（8 fps）但记录没下发。
2. **场景只测了标题画面**：`celeste-title`，没测游戏内实际场景。
3. **`device.width/height` 是 0**：`perf_capture.py` 那次抓的时候分辨率查询失败（后来修了 `hidumper` 方案，但这个记录是修复前生成的）。
