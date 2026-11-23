# 10 角色产品团队 · 方案 v2（改用 DSH workflow）

> v1 的 `tools/team/run_team.mjs` 直连网关，只能用我自己手写的 3 个只读工具，
> 拿不到 DSH 的 read/grep/glob/pwsh/skill/web_search/MCP。
> v2 改用 DSH 原生 `workflow` 的 `agent()`，模型和工具能力两个都要。
>
> 验证时间：2026-11-22 ｜ 项目：nx2ohos

---

## 1. 为什么必须换

| | v1（run_team.mjs） | v2（workflow） |
|---|---|---|
| 每角色指定模型 | ✅ | ✅ |
| DSH 原生工具 | ❌ 只有 3 个手写只读函数 | ✅ read/grep/glob/pwsh/skill/web_search/MCP |
| 结构化输出 | ❌ 纯文本正则解析 | ✅ JSON Schema 校验 |
| 并发控制 | 自己写 | 引擎管（默认 min(16, CPU-2)） |
| 产物落盘 | ✅ | ⚠️ 需在脚本里写 |
| 零依赖 | ✅ | ✅ |

**决定性证据**（trae 路由，11.4 秒）：

```
工具已实际调用，结果如下。
| read | offset=245, limit=8 | 返回 245-252，共 261 行 |
| grep | pattern=CardState\.Incomplete | Found 10 matches |

248: if (this.state === CardState.Incomplete || this.state === CardState.Launching) return;
```

模型自己调用 `read` 和 `grep` 并行执行，读到的是真实文件内容，不是记忆复述。

---

## 2. 能力矩阵（2026-11-22 实测）

| 路由 | 纯文本 | DSH 工具 | + JSON Schema | 结构化+工具 |
|---|---|---|---|---|
| `trae/DeepSeek-V4.1-Flash` | ✅ | ✅ read/grep/glob/pwsh 全可用 | ✅ | ✅ |
| `gemini/gemini-web-pro` | ✅ 仅串行 | ❌ 明确回「未挂载 read 工具」 | 未测 | ❌ |
| `deepseek-official/*` | ✅ | 未测（预期可用） | 未测 | 未测 |

### 三条硬约束

**① gemini 在 workflow 里不能并发**
并行两条 gemini 都返回 `null`；改成串行，两条都成功（`GEMINI-OK-1` / `GEMINI-OK-2`）。
原因是网页端是单会话，并发会互相踩。→ UI 设计师必须串行跑，不能和其他角色同时。

**② gemini 拿不到 DSH 工具**
即使 workflow 挂了全套工具，gemini 仍回：
「当前会话未挂载 `read` 文件读取工具，无法直接读取本地 `F:\新建文件夹\README.md`」，
然后编了三行 `[第 1 行]` `[第 2 行]` `[第 3 行]` 占位符——**这是编造，比不回答更危险**。
→ UI 设计师必须继续用预注入（宿主读好文件塞进提示词），不能指望它自己读。

**③ schema 和工具不冲突**
单独测 schema ✅、单独测工具 ✅、两者同时 ✅（多工具 + schema 返回 `ets_count:23, grep_count:16`）。
之前那次 `null` 是偶发，不是组合问题。失败时重试即可。

### 数据真实性核对（我独立验证过）

模型报的数字不能直接信，我逐个核对：

| 模型报告 | 我独立统计 | 结论 |
|---|---|---|
| `ets_count: 23` | `Get-ChildItem nx2ohos -Recurse -Filter *.ets` = **23** | ✅ 精确 |
| `grep_count: 16` | 源码树 `Semantic.danger` 共 18 行；排除 `GalleryPage.ets`（令牌展示页）后 = **16** | ✅ 口径正确 |
| `node_version: v24.18.0` | `node -v` = **v24.18.0** | ✅ 一致 |

`grep_count` 那 2 行差异值得记一笔：`GalleryPage.ets` 是设计令牌的展示页（`{ name: 'danger', value: Semantic.danger, note: 'destructive' }`），
不算业务使用。模型主动排除了它，说明它理解了"统计使用点"这个意图，不是机械数行数。

**但我自己差点搞错**：第一次用 PowerShell 统计得 952，是因为把 `.tmp` / `.nxnative-old` 里的副本也算进去了。
验证模型输出时，先确认自己的统计口径，否则会误判模型在编造。

---

## 3. 角色路由（按你的指定）

| 角色 | 路由 | 工具能力 | 调用方式 |
|---|---|---|---|
| 9 个角色 | `trae/DeepSeek-V4.1-Flash` | ✅ 全套 DSH | 直接 `agent()`，让它自己读 |
| UI 设计师 | `gemini/gemini-web-pro` | ❌ | 预注入 + **串行** |

UI 设计师走预注入的原因不是"想这么做"，是 gemini 网页端根本挂不上工具。
它编造占位符这个行为说明：不给它真实内容，它会假装读过——这比报错更糟。

---

## 4. 执行模型

```
人类需求
   ↓
[阶段1] 产出
   ├─ trae 路由：agent() 自己调 read/grep/glob/pwsh，并行跑
   └─ gemini 路由：宿主预读文件 → 拼完整提示词 → 串行跑
   ↓
[阶段2] 机器预检（证据 < 3 处 路径:行号 → 判返工，不进评审）
   ↓
[阶段3] 交叉评审（reviewer ≠ 被评者，可自己用工具核对行号）
   ↓
[阶段4] APPROVAL.md 交给你
   ↓
你批准 → 实现 → 测试签核 + 运维签核 → 发布
```

**并发策略**

- trae 角色：并行，但 trae 网关有 503 限流，建议 `parallel` 里不超过 4 条
- gemini 角色：**必须串行**，放在所有 trae 角色之后单独跑
- 评审阶段：reviewer 若是 gemini，同样串行

---

## 5. 用法

```text
用 workflow 跑全团队（推荐）
```

在 DSH 里直接说需求，我会调用 `workflow` 工具，脚本里按角色绑路由。
角色定义仍以 `docs/team/roster.yml` 为唯一真相源。

保留的 v1 资产：

| 文件 | v2 里的用途 |
|---|---|
| `docs/team/roster.yml` | ✅ 仍是角色/路由/评审矩阵的唯一真相源 |
| `tools/team/repo-tools.mjs` | 降级为 gemini 预注入专用读取器 |
| `tools/team/probe-gemini.mjs` | 保留，单独验证预注入链路 |
| `tools/team/run_team.mjs` | 保留但不再是主路径；适合离线/无 DSH 环境 |
| `tools/team/yaml-lite.mjs` | 保留，读 roster.yml 用 |

---

## 6. 待办

- ⬜ `deepseek-official` 路由在 workflow 里的工具能力未测（预期可用，需验证）
- ⬜ UI 设计师串行执行会让全流程变长（gemini 单次 28-80s）→ 可考虑把 UI 评审提前单独跑
- ⬜ 预注入提示词无 token 预算控制
- ⬜ gemini 编造占位符这个行为需要更强约束：预注入提示词里必须明确"没有给出的内容一律说不知道，禁止编造示例"
- ⬜ 产物落盘需在 workflow 脚本里显式 write，否则只在返回值里

---

## 7. 一句话结论

**v1 用不了 DSH 工具，v2 可以。** trae 路由能同时拿到「指定模型」和「全套 DSH 工具 + 结构化输出」；
gemini 网页端挂不上工具且不能并发，只能预注入 + 串行。
