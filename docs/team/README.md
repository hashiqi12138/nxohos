# 10 角色产品团队 · 搭建与运行手册

> 目标：角色分工 + 每角色独立模型路由。
> 建立时间：2026-11-22 ｜ 适用项目：nx2ohos（Switch → HarmonyOS NEXT 原生移植）

---

## 0. 当前路由配置（2026-11-22 按 YG 指定调整）

| 角色 | 路由 |
|---|---|
| 全部 9 个角色 | `trae/DeepSeek-V4.1-Flash` |
| UI 设计师 | `gemini/gemini-web-pro` |

`roster.yml` 中 `route_policy: shared` —— 允许共用路由。
改成 `distinct` 则恢复「每角色一条互不重复路由」的强制校验。

**共用路由的代价（已知并接受）**

1. 角色差异只剩提示词，模型能力差异消失 → 同源共识盲区变大。
2. 全部请求压到 trae 网关，503 限流概率显著上升 → 并发压到 **2**。
3. UI 设计师保留 gemini 路由，正是为了留一个真正的异源视角。

---

## 1. 为什么是这套技术

环境里存在 4 种多智能体编排机制，能否"每角色独立模型"是硬指标。实测结论：

| 机制 | 独立 provider/model | 实测证据 | 采用 |
|---|---|---|---|
| **workflow 工具** | ✅ 支持 | `agent(prompt,{provider,model})` 实测 8 条路由全部按指定模型返回 | 交互式多角度分析 |
| **subagent / subagent_fork** | ⚠️ 需开启 | 需 profile 配 `modelSelectionSettings: true` + `allowedModels` 白名单 | 单点深挖 |
| Agent Teams `spawn_teammate` | ❌ 不支持 | 参数仅 `name/description/prompt/context`，无模型位 | 不用 |
| ralph | ❌ 不支持 | 单一 objective | 不用 |

**最终选型：自建 Node 编排器（`tools/team/run_team.mjs`）**，理由：

1. 需要**真实的文件读取能力**（见第 3 节），workflow 的 `agent()` 无法给子智能体挂自定义工具。
2. 需要**三阶段闸门 + 人类评审卡**，这是产品流程而非一次性问答。
3. 需要**可审计的产物落盘**（每角色一份 .md + `_meta.json`）。
4. 零第三方依赖，换机器不用 `npm install`。

---

## 2. 网关能力矩阵（决定调用方式）

关键发现：**不是所有网关都支持 tool calling**，这直接决定该角色能否自己读代码。

| provider | 端点 | 工具调用 | 实测证据 | 调用方式 |
|---|---|---|---|---|
| `trae` | `127.0.0.1:8791/v1` | ✅ 支持 | `finish=tool_calls`，正确返回 `read_file` 调用 | 工具循环 |
| `deepseek-official` | `api.deepseek.com/v1` | ✅ 支持 | 21 次连续工具调用成功 | 工具循环 |
| `gemini` | `127.0.0.1:8792/v1` | ❌ **不支持** | `finish=stop` 且 `tool_calls=null`，模型回「当前环境没有文件读取工具」 | **上下文预注入** |
| `ollama` | `127.0.0.1:11434/v1` | 未测（服务未起） | `/v1/chat/completions` 404 | 按预注入处理 |
| `beichen` | 远程 | — | TLS 连接被关闭 | 不启用 |

`GATEWAYS[provider].supportsTools` 就是这张表的代码化，编排器据此自动选择调用方式。

### gemini 网页端转发的三个坑（实测）

1. **不支持 tool_calls** —— 给了 `tools` 也当没看见。必须宿主预读文件拼进提示词。
2. **`system` 角色可用** —— 实测 `ZEBRA-77` 代号能正确继承，不需要合并进 user。
3. **PowerShell 发中文会乱码** —— 是 PowerShell 的 body 编码问题，不是网关问题。用 Node `fetch` 发同一段中文正常。**排查此类问题一律用 Node，别用 `Invoke-RestMethod`。**

---

## 3. 关键教训：没有工具的角色会编造

**第一版实现是错的。** 第一轮跑完，两份产物都写着"无法读取 `F:\新建文件夹`，以下均未验证"。

根因：纯 HTTP `/chat/completions` 调用**没有文件系统能力**，而提示词却写着"请实际读取"。模型读不到文件，只能写"未验证"来免责——这等于用 10 个模型产出了 10 份正确的废话。

**修复分两条路：**

### 3a. 支持工具的路由（trae / deepseek-official）→ 工具循环

`tools/team/repo-tools.mjs` 提供三个只读工具：

| 工具 | 作用 |
|---|---|
| `list_dir` | 列目录（含文件大小），摸清结构 |
| `read_file` | 按行范围读文本，单文件上限 120KB |
| `grep` | 正则搜索，返回 `文件:行号: 内容` |

防护：路径解析后必须仍在仓库内（防目录穿越）；跳过 `.git/node_modules/二进制`；
ENOENT 时自动给出近似路径建议（实测 Kimi-K3 会拼错目录，无提示时会反复空转）。

### 3b. 不支持工具的路由（gemini）→ 上下文预注入

`buildPinnedPrompt()` 由宿主替角色读好文件，拼成一份完整提示词：

```
# 角色        角色定义 + 硬性规则
# 任务        人类的需求
# 仓库事实     顶层目录 + ArkTS 源码完整索引 + must_read/context_files 全文
# 输出要求     必须给 路径:行号；索引中存在的文件不得声称「未找到」
```

角色用 `must_read`（必读入口）和 `context_files`（补充上下文）声明要预读的文件。
UI 设计师当前预注入 5 个文件 ≈ 26KB + 27 项源码索引 ≈ 30KB 提示词，gemini 单次约 28-80s 返回。

**「完整源码索引」是踩坑后加的，不能省。**
第一版只塞了角色声明的 5 个文件，gemini 看不到其余文件，就把「我没被给到」误判成「仓库里不存在」，
断言 `pages/GameLibrary.ets` 未找到——而它实际存在（23939 字节，`ImportStatus` 类在 `:165`）。
这个错误被交叉评审抓出。加入源码索引 + 明确措辞后，同一提示词下的输出变为
「位于索引中的 `pages/GameLibrary.ets`（目前未预读全文，需进一步读取确认）」，误判消除。

评审环节同理：`buildReviewerPrompt()` 会把被评者引用的 `路径:行号` 真的读出来附给评审人，
否则无工具的评审人无法判断行号是否属实。

### 修复前后对比（同一需求）

| | 修复前 | 修复后 |
|---|---|---|
| 工具/注入 | 0 | 19 次工具调用 / 5 文件 + 27 项索引 |
| 产出 | "所有路径未验证" | 精确到 `LibraryPage.ets:355-365` 等 10-32 处 |
| 事实性 | 复述人类需求 | **纠正了人类需求的事实错误** |

修复后的真实发现（需求分析师，trae 路由）：需求说"红色提示"，代码里那条 toast 是 `TextToken.primary`（白色 `#F1F5F9`），真正的红色失败态属于远程下载路径，而**那条路径已有重试按钮**。缺口只在本地 picker 导入路径。

UI 设计师（gemini 路由）独立发现的另一条：`CardState` 枚举只有 `Idle/Focus/Press/Incomplete/Launching/Verifying`，**没有导入失败态**，需要新增 `ImportFailed`。已核实属实（`GameCard.ets:16-29`）。

另一条被核实的发现：`GameCard.ets:248` 的 `.onClick` 对 `CardState.Incomplete` 直接 `return`，
即数据残缺的卡片点不动，而 `GameBadges.ets:59` 的 `help` 文案只说"重新导入 .nxpk 可以补上"却无入口。
这是"用户看到提示但无法行动"的确切代码证据。

---

## 4. 怎么用

```bash
# 1. 校验花名册 + 网关可达性（不发推理，不花钱）
node tools/team/run_team.mjs --selfcheck

# 2. 单独验证 gemini 预注入链路（不跑完整流程）
node tools/team/probe-gemini.mjs ui-designer "你的需求文本"

# 3. 小范围试跑（推荐：先 2-3 个角色）
node tools/team/run_team.mjs \
  --request "游戏库页导入失败没有重试入口，请定位实现位置并设计方案" \
  --roles requirements-analyst,ui-designer \
  --concurrency 2

# 4. 全团队跑
node tools/team/run_team.mjs --request "..." --all --concurrency 2
```

产物：

```
docs/team/runs/<runId>/
├── APPROVAL.md                 ← 给你的评审卡（先看这个）
├── _meta.json                  ← 机器可读的裁决记录
├── requirements-analyst.md     ← 各角色交付物
├── ui-designer.md
└── <role>.ERROR.txt            ← 失败时的堆栈
```

---

## 5. 流程闸门

```
人类需求
   ↓
[阶段1] 各角色产出（支持工具的走工具循环，不支持的走预注入）
   ↓
[阶段2] 机器预检（证据 < 3 处 路径:行号 → 直接判返工，不消耗评审预算）
   ↓
[阶段3] 交叉评审（矩阵指定，reviewer ≠ 被评者，每份 2 人）
   ↓
[阶段4] 生成 APPROVAL.md  ← 唯一的闸门在你手上
   ↓
你批准 → 才进入实现 → 测试签核 + 运维签核 → 发布
```

机器预检阈值来自 `roster.yml` 的 `gates.precheck`，不硬编码：

| 检查项 | 默认 |
|---|---|
| `min_evidence_refs` | 3 处 `路径:行号` |
| `require_evidence_section` | 必须有 `## 证据` 段 |
| `reject_zero_tool_calls` | 零工具调用直接打回 |

评审矩阵：

| 产物类型 | 评审人 |
|---|---|
| 需求 | 架构师、体验官、测试 |
| 设计 | 架构师、体验官、鸿蒙开发 |
| UI | 体验官、鸿蒙开发、产品设计 |
| 实现 | 架构师、反编译、测试 |
| 测试计划 | 架构师、鸿蒙开发、Switch 专家 |
| 发布 | 架构师、体验官、测试 |

---

## 6. 已知问题与下一步

**已修**
- ✅ YAML 解析器把 `- ` 列表误判为对象 → 重写为带前瞻的递归下降解析器
- ✅ `trae` 模型 id 大小写敏感（`deepseek-v4-pro` 全小写）→ 按 `/v1/models` 实际返回值登记
- ✅ 凭据读取：自动回落到 `$DSH_HOME/.credentials.yaml` 的 `refs` 段
- ✅ 503 限流 → 指数退避 + 抖动
- ✅ 空响应（推理模型吃光 max_tokens）→ 判为可重试
- ✅ 角色无文件能力 → 工具循环 / 上下文预注入双路径
- ✅ `read_file` 的 `offset/limit` 字符串入参 → 显式 `Number()`
- ✅ 路径拼错 → 自动给出近似路径建议
- ✅ 产物质量 → 机器预检拦截无证据产物
- ✅ 预注入盲区 → 附完整源码索引，消除「没给我 = 不存在」的误判

**待办**
- ⬜ 预注入提示词目前无 token 预算控制，文件多了会超模型窗口 → 应按 `context_files` 总字节数设上限并告警
- ⬜ 单角色实测耗时：需求分析师 19 次工具调用；gemini 28-80s / 30k 字符提示词。全团队跑一次成本不低 → 可加 `--dry-run` 估算
- ⬜ 未接入 git：实现阶段目前靠人类手动执行，可加 `--implement` 走 worktree 隔离
- ⬜ gemini 网页端有会话级上下文，长提示词可能被截断，目前无校验 → 应比对注入文件数与模型确认收到的文件数
- ⬜ 索引只覆盖 `ets` 源码树（depth 3，上限 200 项），C 侧 `dm/ohos/*.c` 未索引 → 涉及 NAPI/宿主层的角色需扩大索引范围

---

## 7. 维护规则

1. 花名册是唯一真相源，改角色/路由/评审矩阵只改 `docs/team/roster.yml`，不改代码。
2. 新增角色若走不支持工具的网关，**必须**声明 `must_read` 或 `context_files`，否则该角色等于没读代码。
3. 换模型前先跑 `--selfcheck`；模型 id 以网关 `/v1/models` 返回值为准，别凭记忆写。
4. 团队的写入权限为**零**：工具全部只读，写动作只在人类批准后执行。
5. 排查网关问题用 Node `fetch`，不要用 PowerShell 的 `Invoke-RestMethod`（中文会乱码）。

