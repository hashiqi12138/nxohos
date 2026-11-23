# 需求分析师

> 路由：trae/DeepSeek-V4.1-Flash ｜ 43905ms ｜ tokens: 33067 ｜ 工具调用: 18

## 结论

失败处理有两条，但都不会让用户重新发起导入：

1. `runImport` 的 catch 里**先关面板、再弹 3 秒 toast**，然后函数返回：面板关了，用户手上没有任何「再试一次」的控件。
2. `ImportStatus.fail()` 把错误写进 `ImportStatus.error`，但**全仓没有任何地方读它**（只有 3 处赋值）。所以失败态既没驻留，也没被 UI 消费。

结论：改造点不是「加一个按钮」，而是**让失败成为一个有生命周期的 UI 状态**（驻留 + 可重入），而不是一条会自己消失的消息。

## 交付物

### 需求条目

| ID | 需求 | 验收条件 | 明确不做 |
|---|---|---|---|
| R1 | 导入失败后 `ImportPanel` 不关闭，转为失败视图：标题改为失败、显示 `humanError` 文案、进度条置灰 | 失败后面板仍在屏上；toast 3 秒消失后面板仍在；`ImportPanel.ets:37` 的「正在导入」不再出现 | 不做新的全屏错误页 |
| R2 | 失败视图提供「重试」与「关闭」两个动作；重试直接重跑同一个包，不要求用户重新选文件 | 点重试 → `ImportStatus.begin` 被再次调用、`importCancelled` 复位、进度从 0 重新计时；连点两次只产生一次导入 | 不做自动重试 / 退避重试 |
| R3 | 错误按可重试性分类：`2300028`/`2300018`/`2300023` 显示「重试」；`NXPK` 类错误不给重试，改为「重新选择文件」 | 选错文件时失败视图只有「重新选择文件」；空间不足时给「重试」且文案含「清理后重试」 | 不改 `humanError` 的错误码映射表本身 |
| R4 | `pendingUri`/`pendingInfo` 在 `runImport` 入口保存，供重试复用（当前只在 `replaceData` 分支保存，且 `onConfirm` 里会被清空） | 首次导入（非替换路径）失败后点重试，uri 仍有效 | 不把 uri 持久化到 preferences |
| R5 | 面板被「后台继续」关掉后失败，库页仍有一条可点的失败条 | 失败时 `ImportStatus.error` 非空 → 库页出现一条带「重试」的条，重试后消失 | 不做通知栏 / 系统通知 |

### 冲突与依赖清单

- **C1 `showPanel` 时序**：`LibraryPage.ets:359` 在 catch 里无条件 `showPanel = false`，与 R1 直接冲突，必须改为「失败时保留」。
- **C2 `pendingUri` 被清空**：`LibraryPage.ets:410-413` 的 `onConfirm` 在调用 `runImport` 前就把 `pendingUri`/`pendingInfo` 清成空，R4 若沿用这对字段必须调整清空时机，否则替换路径的重试必然拿到空 uri。
- **C3 banner 可见条件**：`LibraryPage.ets:714` 用 `importPct > 0 && < 100` 判断，失败时 pct 停在中间值，banner 会继续显示「正在导入 N%」——失败态需显式排除 banner，否则同一个屏幕同时说「正在导入」和「失败」。
- **C4 双通道重复提示**：`showToast`（`LibraryPage.ets:655-662`）3 秒自动清空，若同时保留 toast + 驻留面板，同一错误会说两遍。建议失败态只走面板，toast 仅留给「取消」。
- **C5 静态状态跨页残留**：`ImportStatus` 是 static 类，`error`/`active` 在离开库页后仍存在；重进页面时 `aboutToAppear`（`LibraryPage.ets:101-113`）没有重置它们，R5 需要在这里做一次清理。
- **C6 视觉一致性依赖**：远程下载已有「失败 → 重试」范式（`RemotePacksSection.ets:211-217` 显示失败原因、`:244` 按钮文案切「重试」、`:249` 用 `Semantic.coral`），本地导入的重试按钮应对齐这套文案与配色，否则同一应用里两种「重试」长得不一样。
- **D1 依赖 `importPack` 的续传语义**：`LibraryPage.ets:361`、`:805` 的文案承诺「下次从断点继续」。重试能否直接复用旧 uri 并续传，取决于 `PackImport.ets` 的 `importPack` 实现，我本轮未读该文件。

## 证据

- `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/pages/LibraryPage.ets:355-365` — catch 分支：`ImportStatus.fail` → `showPanel = false` → `showToast`，无重试
- `LibraryPage.ets:394-401` — `humanError` 错误码到文案的映射（唯一分类依据）
- `LibraryPage.ets:285-327` — `doPick`，picker 与 header 校验路径
- `LibraryPage.ets:410-416` — `onConfirm` 清空 `pendingUri`/`pendingInfo` 后调 `runImport`
- `LibraryPage.ets:655-662` — `showToast` 3 秒自动清除
- `LibraryPage.ets:763-771` — toast 是纯文本 `Text`，不可点
- `LibraryPage.ets:714-724` — banner 可见条件基于 `importPct`
- `LibraryPage.ets:777-793` — `ImportPanel` 实例化处，props 无失败/重试字段
- `components/ImportPanel.ets:27-118` — 面板只有进度与「后台继续 / 取消导入」，无失败态
- `pages/GameLibrary.ets:165-251` — `ImportStatus` 定义，`fail()` 在 `:242-245` 只赋值不通知
- `GameLibrary.ets:243` / `LibraryPage.ets:383` — `error` 的全部写入点；全仓无读取点
- `components/RemotePacksSection.ets:211-217, 244-252` — 可参照的重试按钮范式

## 风险与待确认

- **picker 授权 URI 的有效期**：`doPick`（`LibraryPage.ets:285-327`）内未见持久化授权调用，重试复用旧 uri 是否仍可读，需真机确认；若不可读，R2 退化为「重试即重新拉起 picker」。
- **`importPack` 是否幂等/续传**：需读 `pages/PackImport.ets` 确认重试时不会在已解包目录上产生半写文件；这是 R2 正确性的前提，我本轮未读该文件。
- **`.tmp/ohos-refactored/` 同名文件行号不同**（如 `LibraryPage.ets:400` vs `:396`），改造前需确认构建实际取的是 `nx2ohos/nxnative/dm/ohos/hap/` 这一份，而非 `.tmp` 快照。
- 真机可见性（toast 与面板的层叠、失败视图在小屏是否被底部遮挡）只能真机确认。
