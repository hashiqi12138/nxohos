# 需求分析师

> 路由：deepseek-official/deepseek-v4-pro ｜ 244048ms ｜ tokens: 74972 ｜ 工具调用: 21

## 结论

「游戏库页导入失败」的实现位置是 **本地 picker 导入路径**（`.nxpk` 选择器 → `importPack`），失败处理在 `LibraryPage.runImport()` 的 catch 分支。当前失败时只弹一条 **3 秒自动消失的 toast**，无重试入口。需说明一个与描述不一致的事实：这条 toast 渲染为 `TextToken.primary`（白色 `#F1F5F9`），**不是红色**；代码里真正的红色失败态属于「可下载」远程路径（S4），而那条路径**已有重试按钮**。因此本次改造的缺口是 picker 导入路径的「持久失败态 + 重试」。

## 交付物

### REQ-1 导入失败持久化失败态（新增）
- **动机**：失败只有 3 秒 toast，用户来不及读、也没有动作可点（`LibraryPage.ets:355-365`、`655-662`）。
- **验收**：`importPack` 抛错后进入持久失败态——显示失败原因 + 「重试」按钮，不再以 toast 为唯一载体；失败态保持到用户点重试或重新发起导入才清除。
- **明确不做**：不做自动重试；不做失败历史/日志列表。

### REQ-2 重试入口（新增）
- **动机**：用户对同一 `.nxpk` 应能一键重试，而非重走 picker 流程。
- **验收**：点击「重试」用失败时保留的 `uri` + `PackInfo` 重新调用 `runImport`；重复可点。重试安全由 `importPack` 开头先清 owned 目录（`mods/RomFS/aoc`）保证（`PackImport.ets:135-137`），失败时 `.stamp` 未写，成功后才写 stamp/meta。
- **明确不做**：不做 tar 字节级断点续传——picker 源是本地文件，`importPack` 从头重读（`PackImport.ets:108-251`）；不重新弹 picker。

### REQ-3 失败原因人话 + 分类（复用）
- **动机**：技术错误码不能直接暴露（设计规范 `DESIGN-S1-DesignSystem.md:443-456`）。
- **验收**：沿用 `LibraryPage.ets:395-401` 的 `humanError` 映射；`2300023/ENOSPC` 归为「需清理后重试」，超时/断网归为「可重试」，其余兜底截断显示。
- **明确不做**：不新增错误码明细页；不改 `humanError` 现有文案语义。

### REQ-4 失败后清理残留进度态（修复）
- **动机**：catch 分支只 `showPanel=false`，没有重置 `importPct/importName`，而 banner 的显示条件是 `0 < importPct < 100`（`LibraryPage.ets:714-718`），失败后可能残留一条「正在导入 X%」的假进度条。
- **验收**：失败后进度 banner 消失，失败态 UI 接管；重试开始时重新初始化进度。
- **明确不做**：不改 `ImportPanel`/`ImportBanner` 的正常进度展示逻辑。

### 冲突与依赖
1. **状态双源**：`ImportStatus.error` 已记录失败但**没有任何 UI 读取**（仅 `GameLibrary.ets:242-245` 写入，grep 无渲染引用），与页面新增失败态会形成两处真相。应统一为页面失败态，或让失败 banner 直接消费 `ImportStatus.error`。
2. **作用域隔离**：S4 远程下载已有 `PackState.Failed` + 红色「重试」（`RemotePacksSection.ets:211-252`、`RemoteDownloads.ets:213-218`），勿重复实现；本次只覆盖 picker 导入。
3. **非模态约束**：`DESIGN-S3-Import.md:189` 要求导入期间可继续操作库，失败态 UI 也须非模态，不阻断网格浏览。
4. **并发防抖**：`runImport` 是异步的，重试按钮须在「已有导入进行中」时禁用，避免同 uri 并发重入（现有 `launchingTitle` 已有类似守卫思路，`LibraryPage.ets:249`）。
5. **文档同步**：`DESIGN-S3-Import.md:168` 状态机写的是「失败 → banner 消失 → Toast 错误文案」，且验收清单（181-190）无重试项；本需求落地需同步更新该设计。

## 证据

- `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/pages/LibraryPage.ets:355-365` 失败 catch：`ImportStatus.fail` + `showPanel=false` + `showToast(humanError(raw))`
- 同文件 `:655-662` toast 3 秒自清；`:763-771` toast 用 `TextToken.primary`（白）渲染
- 同文件 `:714-718` 进度 banner 显示条件 `0 < importPct < 100`
- 同文件 `:281-327` picker 流程；`:395-401` `humanError` 映射
- `pages/GameLibrary.ets:242-245` `ImportStatus.fail` 只写 `error`，无消费方
- `pages/PackImport.ets:108-137` `importPack` 先清 owned 目录再流式重读（重试幂等、无续传）
- `theme/Tokens.ets:18-23` 白字；`:32-33` danger 红字
- `components/RemotePacksSection.ets:211-252` S4 失败红字 + 「重试」按钮（已有重试）
- `docs/ohos/DESIGN-S3-Import.md:168`、`:181-190` 设计现状无重试
- `docs/ohos/DESIGN-S1-DesignSystem.md:443-456` 错误文案规范

## 风险与待确认

1. **「红色提示」与代码不一致**：当前失败 toast 是白字，不是红色。若真机截图确为红色，说明运行版本与仓库 HEAD 不一致，或另有入口；需用当前 HAP（`nxohos-0.2.0-signed.hap`）复现确认后再对号入座。
2. **重试语义**：picker 导入失败后部分文件已落盘但 `.stamp` 未写，重试会整包重读（14.75 GB 从头再来）。需产品确认是否接受「全量重来」而非断点续传；断点续传属 `RemoteDownloads` HTTP 路径能力，picker 本地流式路径当前无此实现。
3. **磁盘满场景**：`ENOSPC` 重试若不先清理空间必然再次失败，失败态文案需引导「清理后重试」而非机械重试。
4. **真机验证项**：重试后进度、stamp 写入、失败态消失等需真机/运行时确认，静态代码无法覆盖。
