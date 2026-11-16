# P3.2：BOTW 帧率追踪 — OHOS 与 Metal 后端的结构差异

分支 `perf/botw-framerate`。起点是用户报告"塞尔达帧率低"，要求参考 iOS / 安卓端找性能问题。

## 先说一个与帧率无关但更严重的问题：多游戏数据互相覆盖

调试中发现设备上只有一个游戏目录 `01007EF00011E000`（BOTW 的 title ID），
里面装的却是 **Celeste** 的数据：

```
目录名:           01007EF00011E000
nx2a-data.json:  "title_id": "01002B30028F6000", "name": "Celeste"
hilog:           01007EF00011E000 "Celeste" 1.2 GB
```

BOTW 的 1.1 GB 数据被 Celeste 覆盖，且 Celeste 自己的目录根本不存在。

### 根因

`Index.ets` 的 `stageData(rootArg)` 有两条数据来源：

| 来源 | 目标目录 | 是否正确 |
|---|---|---|
| 环回下载（devpush 的 `.nxpk`） | `gamesDir(filesD) + '/' + info.title` | ✅ 从容器头读 title |
| 内置 rawfile（HAP 打包数据） | 直接用调用方传入的 `root` | ❌ 从不看包的 title |

调用方第 1175 行传的是 `this.activeRoot(files)` —— **游戏库里当前选中的游戏**，
而不是这个 build 自带的游戏。所以"装 Celeste 的包、库里选着 BOTW"就写进了 BOTW 的目录。

第 1012 行的注释早就写明了正确做法（"The container names its own game: import into
games/<title>, whatever root the caller passed"），但只有环回路径实现了它。

### 修复

`stageData` 解压前先读包里自己的 `nx2a-data.json`，用它的 `title_id` 决定目标目录：

```typescript
let root: string = rootArg;
{
  let tid: string = '';
  try {
    const raw = await ctx.resourceManager.getRawFileContent('nx2a-data.json');
    const meta = JSON.parse(...) as Record<string, Object>;
    const t = meta['title_id'];
    if (typeof t === 'string' && /^[0-9A-Fa-f]{16}$/.test(t)) tid = t.toUpperCase();
  } catch (e) { /* 老包没有这个文件：用调用方的 root */ }
  if (tid.length) {
    const named: string = gamesDir(ctx.filesDir) + '/' + tid;
    if (named !== root) dmhost.log(`staging: the pack is ${tid}, not the selected ...`);
    root = named;
  }
}
```

函数参数改名为 `rootArg`，解压段用新的 `root`。环回分支里三处旧引用保持 `rootArg`
（那条路径自己已经从容器头取 title 了）。

### 另一个坑（GAMEDATA.md 已记录，这次又踩到）

`bm install -p <只含 entry 的目录>` **会卸载已装的数据模块**。`devpush.py` 只装 entry，
所以用它更新之后设备上只剩 `entry`，应用显示"还没有游戏"。

装数据必须 entry + data1..N 一起 `bm install -p <目录>`，且目录里所有 HAP 的
versionCode 必须相同（否则 `9568278 install version code not same`）。

---

## 帧率：两个后端的结构差异

用户要求对比 iOS/Android。实测两个后端的规模：

| 后端 | 文件 | 大小 |
|---|---|---|
| Metal（iOS / Mac） | `dm/gfx/nvn/nvn_mtl.m` 单文件 | **1.7 MB** |
| Vulkan（OHOS / Android） | `gfx_vk_exec.c` + `gfx_vk_core.c` + `gfx_vk_tex.c` + `gfx_vk_shader.c` + `hle_vk.m` | 248 + 90 + 74 + 52 + 153 KB |

### 根本差别：descriptor set

在 `nvn_mtl.m` 里搜 `descriptorSet` / `VkDescriptorSet`：**0 次**。

Metal 没有 descriptor set 概念——着色器资源直接按 buffer/texture 索引绑定。
Vulkan 必须为每次 draw 构造或查找 descriptor set，这是 replay 里最贵的环节之一。

实测（BOTW，`DM_VK_PROF_SLOW=1`）：

```
slow replay: descriptor sets 3.7-4.0 ms   (collect 1.25 + alloc+write 0.75 + hash 0.05)
             other           5.5-6.1 ms   (draw 录制本体)
```

已做的优化（本次之前）：8 槽 memo（collect 1.6→1.25 ms，命中 12→21 次/帧）。

### 下一步的候选方向

1. **descriptor set 池化**：现在每次 miss 都 `vk_alloc_set`。若改成按 layout 分桶的
   自由链表，alloc+write 的 0.75 ms 可大幅下降。
2. **`other` 5.5 ms**：87 draws/帧里每条要记 10 个动态状态调用。`set_dynamic` 已有
   `BC_SAME` 剪枝，但 pass 边界会重置。
3. **GPU 端 13-15 ms**：59 个 pass/帧，其中一个 1920x1080 深度 pass 独占 4.4-4.8 ms。

这些都要在设备实测确认收益后才动手，不靠推测。

---

## 待办

- [x] 定位多游戏数据覆盖 bug 并修复（`stageData` 读包内 title）
- [x] ArkTS 编译通过
- [ ] 重打整套 HAP（新 versionCode，含数据）并安装
- [ ] 验证 Celeste 进自己的目录、BOTW 数据不再被覆盖
- [ ] 恢复 BOTW 数据（需要重新 port 一次）
- [ ] 帧率优化（descriptor set 池化等）
