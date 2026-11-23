# UI设计师

> 路由：gemini/gemini-web-pro ｜ 60121ms ｜ tokens: 0 ｜ 预注入文件: 5

## 结论

经核查仓库事实，`pages/GameLibrary.ets` 与 `ImportPanel` 的具体源码在**仓库中未找到**，但已提供的核心组件已暴露导入失败无重试入口的直接 UI 断层：
1. **顶部导入横幅缺乏错误态与操作入口**：`ImportBanner.ets:5-6` 注释明确将其设为非交互组件（`Non-interactive`），且 `ImportBanner.ets:15-46` 仅接收 `percent` 和 `gameName`，`importBannerVisible()`（`ImportBanner.ets:50-52`）仅绑定 `ImportStatus.active`，一旦导入异常终止便直接消失，无失败态或重试按钮。
2. **卡片级导入残缺态阻断点击**：导入中断导致包不完整时，`GameBadges.ets:55-68` 虽在 `help` 文案中提示“重新导入这个游戏的 .nxpk 可以补上”，但 `GameCard.ets:247-250` 在 `CardState.Incomplete` 下直接 `return` 拦截点击，导致卡片上既无视觉修复指示也无一键重试入口。

改造核心：扩展 `Tokens.ets` 危险态背景令牌；将 `ImportBanner` 升级为支持 `error` 态的可交互横幅（内嵌“重试”主按钮与“重选包”次按钮）；同时解除 `GameCard` 在 `Incomplete` 状态下的点击死锁，支持直达重导动作。

---

## 交付物

### 1. Design Token 变更表（`theme/Tokens.ets`）

| 类别 | Token 路径 | 变更动作 | 数值 / HEX | 用途说明 |
| :--- | :--- | :--- | :--- | :--- |
| **背景/语义色** | `Semantic.dangerSoft` | **新增** | `rgba(239,68,68,0.18)` | 参照 `Semantic.warningSoft`（`Tokens.ets:37`）与 `Semantic.danger`（`#EF4444`，`Tokens.ets:32`），用于失败横幅底色与标签底色 |
| **背景/语义色** | `Semantic.dangerBorder` | **新增** | `rgba(239,68,68,0.40)` | 用于失败态 `ImportBanner` 底部描边与异常 `GameCard` 边框高亮 |
| **浅色预留** | 浅色模式令牌 | **未定义** | 仓库中未找到 | 当前 `Tokens.ets:7-42` 仅为深色单套令牌，新增令牌保持一致的深色高对比度规范 |
| **字号** | `Font.label` / `Font.caption` | **复用** | `14` / `12`（`Tokens.ets:99,101`） | 横幅错误摘要用 `Font.label`（行高 `20`），错误码与卡片角标用 `Font.caption`（行高 `16`） |
| **间距与尺寸** | `Space.s2` / `Space.s4` / `Size.buttonSm` / `Size.touchTarget` | **复用** | `8` / `16` / `32` / `48`（`Tokens.ets:83,85,144-145`） | 横幅内边距 `Space.s4`、按钮间距 `Space.s2`；触控热区高 `48dp`（视觉按钮高 `Size.buttonSm=32dp`） |

### 2. 页面结构与关键态改造方案

#### 页面层级结构（游戏库页）
* **顶栏区**（`Size.topBar = 64dp`，`Tokens.ets:138`）：保持原有导航与添加入口。
* **导入状态横幅区**（`ImportBanner`，`Z.sticky = 10`，`Tokens.ets:131`）：吸顶于顶栏下方，由纯展示条升级为状态容器（加载态非交互 / 失败态可聚焦交互）。
* **主内容区**：空库时渲染 `EmptyState`（`EmptyState.ets:38-75`）；有数据时渲染 `GameCard` 网格（`GameCard.ets:139-260`）。

#### 关键态视觉、动效与无障碍规范
1. **空态（Empty）**：
   * 沿用 `EmptyState.ets:43-68` 结构（标题 `Font.display` + 副标题 `Font.body` + 主按钮 `Size.buttonLg=48dp` + 引导链接），无导入记录时不展示 `ImportBanner`。
2. **加载态（Loading）**：
   * `ImportBanner` 保持 `4dp` 进度条（`Semantic.accent` 至 `Semantic.accentHover` 渐变，`ImportBanner.ets:20-35`）与 `Font.caption` 文案，焦点不抢占网格。
   * 卡片内执行任务时，底部渲染 `3dp` 进度条（`GameCard.ets:186-200`）。
3. **错误态（Error - 核心改造）**：
   * **顶部 `ImportBanner` 失败态**：容器高度自适应（最小 `48dp`），底色 `Semantic.dangerSoft`，底边框 `1dp` `Semantic.dangerBorder`；左侧显示 `Font.label`（`TextToken.primary`）失败原因（如“导入失败：包校验中断”），右侧放置**主操作按钮「重试」**（高 `Size.buttonSm=32dp`、外扩热区至 `Size.touchTarget=48dp`、圆角 `Radius.sm=8dp`、底色 `Semantic.danger`、文字 `TextToken.primary`）及**次操作「重选文件」**（描边 `Semantic.borderStrong`）。
   * **卡片 `CardState.Incomplete` 态**：保留 `0.42` 透明度（`GameCard.ets:217`）与红色角标（`Semantic.danger`，`GameBadges.ets:58,65`），移除 `GameCard.ets:248` 对 `Incomplete` 的点击屏蔽，改为触发 `onRetryImport` 回调（弹窗或直达重选 `.nxpk`），副标题明确为 `${sizeLabel} · 导入失败（点击重试）`。
   * **动效与无障碍**：横幅展开/切换使用 `Motion.duration(Motion.base)`（`200ms`，`Curve.FastOutSlowIn`，`Tokens.ets:117,121,124`）；`ImportBanner` 失败态设 `accessibilityGroup(true)` 与 `accessibilityLiveRegion` 主动播报错误，重试按钮手柄焦点环宽 `Size.focusRing=2dp`（`Semantic.borderFocus`）。
4. **禁用态（Disabled）**：
   * 点击「重试」触发异步任务期间，按钮置灰（`opacity(0.4)`）并阻止重复点击；`GameCard` 在 `CardState.Launching` 时保持点击与长按禁用（`GameCard.ets:248,254`）。

---

## 证据

* `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/theme/Tokens.ets:32-37`（定义 `Semantic.danger` 与 `warningSoft`/`successSoft`，缺失 `dangerSoft`）
* `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/theme/Tokens.ets:115-127`（定义 `Motion.base = 200` 与 `Motion.duration()` 无障碍动效开关）
* `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/theme/Tokens.ets:137-153`（定义 `Size.buttonSm = 32`、`Size.touchTarget = 48`、`Size.focusRing = 2`）
* `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/components/ImportBanner.ets:5-6`（注释声明横幅 `Non-interactive`，无交互能力）
* `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/components/ImportBanner.ets:15-46`（组件仅定义 `percent` 与 `gameName` 属性，无错误状态与重试按钮）
* `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/components/ImportBanner.ets:50-52`（`importBannerVisible()` 仅判断 `ImportStatus.active`，失败后横幅直接隐藏）
* `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/components/GameBadges.ets:55-68`（`GameState.Extracted` 与未解压态仅在 `help` 文本中提示重新导入，无直接操作）
* `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/components/GameCard.ets:16-29`（`CardState` 枚举仅有 `Incomplete`，无 `ImportFailed` 状态）
* `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/components/GameCard.ets:247-250`（`onClick` 明确拦截 `CardState.Incomplete`，阻断用户点击卡片修复）
* `nx2ohos/nxnative/dm/ohos/hap/entry/src/main/ets/components/EmptyState.ets:38-75`（空态页面结构与无障碍配置基线）

---

## 风险与待确认

1. **缺失文件待读取**：`pages/GameLibrary.ets`（含 `ImportStatus` 结构定义与导入调度逻辑）及 `ImportPanel` 组件源码在仓库事实中未提供，需读取后确认错误码字段（如 `ImportStatus.error`）及失败后源文件路径（URI/沙箱缓存）是否仍有效；若原始文件句柄已失效，重试按钮需降级拉起系统文件选择器（Picker）而非静默重跑。
2. **浅色模式适配**：当前 `Tokens.ets` 仅包含深色模式色值，若后续需支持系统深浅色切换，需补充对应资源限定符颜色表。
