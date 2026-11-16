# S5 游戏库：NX Play 的设计模式，搬到 OHOS

> 方法：craft-anything 的「研究原版 → 提取模式 → 对照 → 构建 → 验证」
> 原版：`varyform/nx-native-wrapper`（NX Play，Mac/iOS 上的 nxnative 前端，约 100 个 Swift 文件）
> 目标：我们的 `LibraryPage`（`dm/ohos/hap/entry/.../pages/LibraryPage.ets`）

---

## 1. 研究原版：NX Play 到底怎么做的

### 1.1 结构：状态与视图彻底分离

```
Packages/NXPlayKit/
  Model/        数据与逻辑：Game, Job, BuildProgress, Artwork, GameScanner...
  Views/        SwiftUI 视图：GameCard, GameGrid, GameList, ConsoleView...
  Library.swift 52 KB —— 状态容器（@Observable）
```

关键点：**`Library.swift` 是 52 KB 的单例状态容器**，视图从 `@Environment(Library.self)` 读它。
视图本身几乎不持状态——`GameCard` 里没有 `@State`。

**对照我们**：`LibraryPage.ets` 有 30+ 个 `@State`，`Index.ets` 有 1693 行。
状态散在页面里，页面一销毁就丢（这就是 S4 下载状态必须移到模块级的原因）。

### 1.2 `Game` 模型：一个 struct 携带全部派生状态

```swift
public struct Game {
    public enum State { case notExtracted, extracted, ready }
    let folder, name, titleID, version, languages, iconURL, buildFolder, ipaURL
    var isSwitch2: Bool { titleID?.hasPrefix("04") == true }
    var canExtract: Bool { state == .notExtracted && hasPackages && !isCopying }
    var canBuildFromFiles: Bool { hasPackages && !isCopying }
}
```

**这是最重要的模式**：所有「能不能做某事」都是**计算属性**，不是散在视图里的 if。

`GameCard` 因此可以极简：

```swift
GameActionButton(game: game, job: job)   // 按钮自己知道该显示什么、能不能点
```

**对照我们**：`GameEntry` 只有 7 个字段，全是数据，没有派生状态。
`LibraryPage` 里到处是 `g.hasData ? '可运行' : '不完整'` 这样的判断。

### 1.3 徽章系统：一个游戏能同时有多个状态

```swift
struct BadgeSpec { let text: String; var symbol: String?; let color: Color; let help: String }

static func icons(_ game: Game, switch2: Bool) -> [BadgeSpec] {
    if game.isSwitch2 { badges.append(BadgeSpec(text: "Switch 2", color: .red, ...)) }
    if let link = game.nxnativeLink { badges.append(NXNativeBadge.spec(link)) }
    if game.isKept { badges.append(BadgeSpec(text: "Kept", symbol: "lock.fill", color: .orange, ...)) }
    if let app = game.macAppURL { badges.append(...) }
    if let ipa = game.ipaURL { badges.append(BadgeSpec(text: "IPA", symbol: "iphone", ...)) }
    return badges
}
```

设计要点：

1. **徽章是可组合的列表**，不是一个状态字段。一个游戏可以同时是「Switch 2 + Kept + 有 IPA」。
2. **每个徽章带 `help`**：一段完整解释，说明它意味着什么、怎么改变它。
3. **溢出处理**：卡片上只放得下的那几个，其余显示 `+N`。
4. **`help` 也是 VoiceOver 读的内容**——无障碍不是附加的。

**对照我们**：`CardState` 是**单值枚举**（Idle/Focus/Press/Incomplete/Launching/Verifying）。
「焦点」和「不完整」不能同时表达，而它们本来可以。

### 1.4 进度画在封面上，不加高卡片

```swift
GameIcon(url: library.icon(for: game))
    .overlay(alignment: .top) { CardBadges(game: game).padding(6) }
    .overlay(alignment: .bottom) {
        if let job, job.showsProgress {
            IconProgressBar(fraction: job.progressFraction)
        }
    }
```

注释原文：*"on the artwork, so a build doesn't make the card taller"*

**这是布局稳定的关键**：构建进度出现/消失时，卡片尺寸不变，网格不跳。

**对照我们**：`CardState.Launching` 会改卡片内容，网格可能跳动。

### 1.5 `Job` 模型：长任务是一等公民

```swift
struct Job { var showsProgress: Bool; var progressFraction: Double; var progressText: String }
```

构建、安装、传输存档都是 `Job`。`GameCard` 接一个 `Job?`，有就画进度，没有就不画。

**对照我们**：导入有 `ImportStatus`（全局单例），远程下载有 `RemoteDownloads`（另一个单例），
构建没有。三个东西三套机制。

---

## 2. 提取模式：五条可搬的规则

| # | NX Play 的做法 | 为什么 | 我们现在的差距 |
|---|---|---|---|
| 1 | 派生状态是计算属性（`canExtract`/`canBuildIPA`） | 视图不做判断，规则只有一处 | `GameEntry` 是纯数据，判断散在视图里 |
| 2 | 徽章是可组合列表 | 一个游戏能同时有多个状态 | `CardState` 是单值枚举 |
| 3 | 每个状态带完整 `help` | 用户能自己解决，不查文档 | 只有短标签（`可运行`/`不完整`） |
| 4 | 进度叠在封面上 | 卡片尺寸不变，网格不跳 | 启动状态改内容 |
| 5 | 长任务是 `Job` 对象 | 一套机制覆盖构建/安装/传输 | 三套独立机制 |

---

## 3. 对照：我们缺什么（具体到文件）

| 文件 | 现在 | NX Play 对应 | 差距 |
|---|---|---|---|
| `GameLibrary.ets` `GameEntry` | 7 个纯数据字段 | `Game` struct + 计算属性 | 缺派生状态 |
| `GameCard.ets` `CardState` | 单值枚举 6 个值 | `[BadgeSpec]` + `Job?` | 单值 vs 可组合 |
| `GameDetailSheet.ets` | 详情面板 | `GameActions` + `GameSettingsViews` | 动作与设置未分离 |
| `LibraryPage.ets` | 30+ `@State` | `Library` 52 KB 单例 | 状态在页面里 |
| — | 无 | `Job` 模型 | 缺 |

---

## 4. 构建计划：这次做什么，不做什么

### 做（本轮）

**4.1 `GameEntry` 加派生状态（纯函数，可单测）**

```typescript
// GameLibrary.ets
export interface GameEntry {
  // ...现有 7 个字段
}

/** 派生状态：与 NX Play 的 Game.State 与 can* 属性同构。 */
export enum GameState { NotExtracted, Extracted, Ready }

export function gameState(g: GameEntry): GameState
export function canLaunch(g: GameEntry): boolean
export function canRebuild(g: GameEntry): boolean
export function isComplete(g: GameEntry): boolean
```

规则（照抄 NX Play 的语义）：
- `NotExtracted`：没有 `RomFS/`，只有包
- `Extracted`：有 `RomFS/`，但 `mods/main.patched.bin` 缺
- `Ready`：两者都在（`hasData`）

**4.2 徽章系统（`GameBadges.ets`，新文件）**

```typescript
export interface BadgeSpec {
  text: string;          // 徽章文字，也是无障碍标签
  color: string;         // 令牌色
  help: string;          // 完整解释
  symbol?: string;       // 有图标时用图标
}

export function badgesFor(g: GameEntry, opts: BadgeOptions): BadgeSpec[]
```

徽章清单（对应我们的状态，不是照搬 Mac 的）：
- `不完整` — 缺 RomFS 或 patched.bin，help 说明缺什么、怎么补
- `可运行` — Ready
- `已锁定` — 用户标记为不重建（对应 NX Play 的 Kept）
- `有存档` — 存档目录非空
- `旧构建` — 构建时间早于 host 源码（对应 NX Play 的 "App out of date"）

**4.3 卡片改用徽章 + 进度叠加（`GameCard.ets`）**

- `CardState` 拆成两部分：
  - `interaction`：Idle / Focus / Press（交互态，互斥）
  - `badges: BadgeSpec[]`（状态徽章，可组合）
- 进度改为封面底部叠加（`Launching` 不再改卡片内容）

**4.4 `Job` 模型（`Job.ets`，新文件）**

统一导入/下载/构建的进度：

```typescript
export interface Job {
  kind: 'import' | 'download' | 'build';
  title: string;
  fraction: number;      // 0..1
  text: string;          // '2.1 MB/s · 剩余 8 分 30 秒'
  showsProgress: boolean;
}
```

`RemoteDownloads` 和 `ImportStatus` 各提供一个 `job(): Job | null`。

### 不做（本轮）

- **不动 `LibraryPage` 的 30+ `@State`**：那是大重构，风险高，本轮只让新代码用新模型
- **不做 `Library` 单例**：先把派生状态和徽章做对，状态容器是下一步
- **不搬 Mac 特有的徽章**（Switch 2 / IPA / Xcode / Mac App）：我们没有这些概念

---

## 5. 验证标准

- [ ] `gameState` / `canLaunch` / `isComplete` 有单测，覆盖三种状态
- [ ] `badgesFor` 有单测：一个游戏能同时返回多个徽章
- [ ] 卡片显示徽章，`help` 可读到（长按详情面板显示完整 help）
- [ ] 启动进度叠在封面底部，卡片高度不变（截图对比）
- [ ] 构建通过，设备上跑起来
- [ ] 覆盖率不降（当前 90.3%）

---

## 6. 为什么不全盘照搬

NX Play 是 Mac 应用，有 `NSWorkspace`、`Finder`、`Xcode`、`codesign`、`Tailscale`。
我们是 OHOS 平板，没有这些。**搬的是结构，不是功能**：

- 搬「派生状态是计算属性」→ 规则只有一处
- 搬「徽章可组合」→ 一个游戏能表达多个状态
- 搬「help 是内容不是提示」→ 用户能自己解决问题
- 搬「进度不改布局」→ 网格稳定
- 搬「Job 是一等公民」→ 一套机制覆盖所有长任务

不搬：Mac/iOS 特有的集成、`Library` 52 KB 单例（下一步再说）、`ConsoleView`（我们没有遥控器场景）。
