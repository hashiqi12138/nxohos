# 需求分析师

> 路由：trae/DeepSeek-V4.1-Flash ｜ 104943ms ｜ tokens: 45562 ｜ 工具调用: 19

## 结论

失败处理位于 `LibraryPage.ets` 的 `runImport` catch 分支：它只做三件事——把错误写进 `ImportStatus.error`、关闭面板、弹 3 秒 toast，然后丢弃上下文（uri/info 不保留），因此用户没有任何重试入口。仓库里已有现成的重试范式可复用：远程下载失败后按钮变「重试」（`RemotePacksSection.ets:244-252`）。本地导入缺这一环。

## 交付物

### 需求条目

**REQ-1 失败后保留重试上下文（P0）**
- 动机：`LibraryPage.ets:355-365` catch 后 `showPanel=false`（359）、只 `showToast`（363），`pendingUri/pendingInfo` 在 `412-413` 已被清空，失败即失忆。
- 验收：新增 `@State importError: string`、私有 `failedUri: string`、`failedInfo: PackInfo | null`；在 catch 中写入三者并 `showPanel = true`；重试时无需重新选文件。
- 不做：不持久化到 preferences（跨进程恢复重试不在本次范围）。

**REQ-2 面板错误态与「重试」按钮（P0）**
- 动机：`ImportPanel.ets:37` 恒显示「正在导入」，按钮只有「后台继续/取消导入」（98-118），没有失败态。
- 验收：`ImportPanel` 增 `@Prop error: string`、`onRetry: () => void`；`error` 非空时标题改「导入失败」、正文显示错误、按钮行改为「关闭 + 重试」；`LibraryPage.ets:780-789` 传入新属性。
- 不做：不改动进度条与剩余时间逻辑。

**REQ-3 重试动作与降级（P0）**
- 动机：需要一条幂等、可重复执行的路径。
- 验收：新增 `retryImport()`：若 `failedUri` 非空且 `readPackHeader(failedUri)` 成功 → 直接 `runImport(files, failedUri, info)`；否则回退 `pickPack()`。`importPack` 会先清 `mods/RomFS/aoc`（`PackImport.ets:135-137`），半成品可被覆盖，重试幂等。
- 不做：不做断点续传式重试（tar 流式写入无断点语义，`PackImport.ets:108-109`）。

**REQ-4 失败原因常驻可读（P1）**
- 动机：toast 3000 ms 自动消失（`LibraryPage.ets:655-662`），错误文本 `humanError`（394-401）用户来不及读。
- 验收：失败后错误文本同时进入面板常驻区；`ImportStatus.error`（`GameLibrary.ets:178-179` 注释写「banner clears on read」但全仓库无人读）明确由面板消费，消除死字段。
- 不做：不新增顶部常驻错误条（留作 P2，见风险）。

**REQ-5 空间不足时改导向清理（P2）**
- 动机：`humanError` 对 `2300023/ENOSPC` 返回「存储空间不足，清理后重试」（396），此时重试必然再失败。
- 验收：错误命中空间不足时，面板按钮改为「去清理」，跳 `pages/SettingsPage`（复用 `LibraryPage.ets:705-708` 写法）。
- 不做：不在本页内做删除缓存的实现。

### 冲突与依赖

| 类型 | 条目 | 说明 |
|---|---|---|
| 冲突 | `pendingUri` 复用 | `pendingUri` 在 `onConfirm`（412-413）与 `onCancelConfirm`（421-425）被清空，不能拿来当重试句柄，必须独立字段 |
| 冲突 | toast vs 面板 | 失败同时关面板（359）+ 3 秒 toast（363），两处都留不住信息；REQ-2 后需决定是否仍弹 toast，避免重复 |
| 冲突 | 文案重复 | `doPick:308` 与 `humanError:399` 都输出「这不是游戏包」 |
| 依赖 | 远程重试范式 | `RemotePacksSection.ets:244-252` + `RemoteDownloads.ets:213-218`（Failed 态 + note），按钮文案与配色直接对齐 |
| 依赖 | 状态重置 | `runImport:332-341` 已重置 `importPct` 等，重试不需额外清理 |

## 证据

- `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/pages/LibraryPage.ets:330-366`（runImport）、`:355-365`（失败分支）、`:359`（关面板）、`:363`（仅 toast）、`:394-401`（humanError）、`:396`、`:400`
- `.../LibraryPage.ets:655-662`（toast 3000ms）、`:281-283`（pickPack）、`:285-327`（doPick）、`:299-302`、`:305-310`、`:321-323`、`:404-419`、`:412-413`、`:421-425`、`:777-793`、`:780-789`
- `.../components/ImportPanel.ets:37`、`:98-118`
- `.../pages/GameLibrary.ets:165`、`:178-179`、`:242-245`、`:247-250`
- `.../components/RemotePacksSection.ets:211-213`、`:244-252`
- `.../components/RemoteDownloads.ets:213-218`
- `.../pages/PackImport.ets:105-106`、`:108-109`、`:135-137`

## 风险与待确认

1. **picker uri 生命周期**：`failedUri` 在同一进程内是否仍可 `fs.openSync`（`PackImport.ets:55`）——需真机确认；REQ-3 的降级分支就是为此兜底。
2. **「后台继续」后失败**：用户点过「后台继续」（`ImportPanel.ets:108`）后 `showPanel=false`，失败时若强行 `showPanel=true` 会打断浏览。需产品决策：是重开面板还是只弹 toast + 卡片角标。建议先按「重开面板」实现，真机看体验再定。
3. **同类缺陷未覆盖**：`LibraryPage.ets:233-236`（数据不完整）、`:646-651`（存档导入失败）、`:299-302`（选择器失败）同样是「只有提示无重试」，本次按范围外处理，建议另开需求。
4. 仓库中未找到本页导入失败的任何自动化测试，`ohos-port/tools/verify/` 下的闸门是否需要新增用例待确认。
