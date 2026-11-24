# 记忆条目 Schema 约定（v1.2）

> 适用范围：L2 记忆层 `*.md` 条目文件（不含 MEMORY.md 索引与日期日志）。
> 目的：让文件式记忆可被机器校验，把一致性从"靠自觉"变成"靠脚本"。
> v1.2：新增 owner（协作档写权分区）与轻量档退化规则（无 L1/L3 时 source 承担唯一真相）。

## 1. frontmatter 字段

```yaml
---
name: my-topic                       # 必填，与文件名一致（去 .md）
description: 一句话摘要               # 必填，会进索引，写清"是什么 + 最新状态"
scope: project:myproject/moduleA     # 必填，作用域；取值见下表
type: fact | decision | progress | rule | procedure   # 必填，五枚举
status: candidate | active | historical | revoked # 必填；candidate=待复核，revoked=结论作废，禁止再引用
confidence: high | medium | low      # 选填，默认 medium
source:                              # 必填，至少一条，用于回溯与纠错
  - "docs/design.md#§7.3"
  - "commit:f6508da"
valid_from: 2026-01-01               # 选填，事实生效时间
revisit_when: "某外部条件到位"        # 选填，触发复核的条件（比日期更适合挂起事项）
supersedes: [old-topic-v0]           # 选填，被本条取代的旧条目
owner: alice                         # 选填（协作档建议填），条目负责人；改动有 owner 的条目先过 owner
updated: 2026-01-01                  # 必填，最后人工确认日期（非文件 mtime）
metadata:                            # 选填，工具自带的嵌套块原样保留
  node_type: memory
---
```

**scope 取值**（建议格式）：`project:<项目>/<域>`、`project:<项目>/common`、`project:<项目>/env`、`process:collab`。

**status 语义**：

| 值 | 含义 | 行为 |
|---|---|---|
| candidate | 待复核（新结论先落此状态，带出处，下次会话复核或复发才转正） | 可检索，注入时必须标注"未复核"；超过 7 天未处理报 WARN |
| active | 当前有效，可依赖 | 默认参与检索与注入 |
| historical | 已被后续轮次覆盖，仅留痕 | 可检索不主动注入，引用时必须标注 |
| revoked | 结论作废（误报/错误推断） | 禁止引用，保留原文仅供追溯 |

**candidate→active 转正规则**（借鉴 agent-memory「晋升靠复发不靠断言」）：单次会话产生的新结论先落 candidate；满足以下任一条件即可人工转 active——① 下次会话复核确认属实；② 在 ≥2 个独立轮次中出现；③ 用户当场拍板（拍板内容可直接 active）。这把"一次判断"变成"两次确认"，拦截单次误判进常驻记忆。

## 2. 解析约定

- frontmatter 支持一层嵌套：顶层键不缩进，`metadata:` 等块下子键缩进 2 空格——顶层 `type` 与 `metadata.type` 互不冲突。
- **校验器只认顶层字段**。注意：部分工具写文件时会把字段规整收进 `metadata:` 块，需手工改回「顶层字段 + metadata 块并存」结构。
- 顶层 `type` 只认五枚举 `fact / decision / progress / rule / procedure`，写 project/user/feedback 会报 ERROR。

## 3. 索引（MEMORY.md）格式

文件头部约定 + 条目行：

```markdown
# <项目名> 项目记忆索引

> 索引与状态只维护在本文件（派生侧只留指针，不复制内容）。
> 每条格式：`- [主题](文件.md) — 摘要 ｜ 状态 · 最后更新`
> 读取顺序：先读本索引 → 按摘要判断相关 → 只读命中文件，勿整目录全读；历史轮次在 archive/（不进索引、默认不读）。

- [本地开发环境](local-dev-env.md) — JDK/数据库/运行入口/工具链 ｜ 有效 · 10-08
```

- 状态取值：**有效**（可依赖）/ **历史**（仅留痕）/ **已撤销**（勿引用）。
- 条目形态：【快照】现状与待办、【台账】现行口径一行一条、【规则】流程与红线、【事实】环境与工具。
- 索引只做摘要**不复制内容**；每轮有状态变化当轮同步该行。

## 4. 档位退化规则

| 档位 | 有 L1 | 有 L3 | 唯一真相落在哪 |
|---|---|---|---|
| 标准三层 | ✅ | ✅ | L1 权威源文档，条目 source 指向它 |
| 仅 L1（无执行侧工具） | ✅ | ❌ | L1；无 L3 即无指针层，索引直接消费 |
| **轻量档（仅 L2）** | ❌ | ❌ | **条目 `source` 字段**（文件路径/commit/URL），每条必须可回溯 |

轻量档适用：单工具、条目预计 <10 条、单人。条目变多或出现权威文档源后用 `--upgrade` 补层，不动已有条目。

## 5. memcheck 检查项一览

| # | 检查 | 级别 |
|---|---|---|
| 1 | 条目超 8000 字（L2 未归档的） | WARN |
| 2 | 索引对齐：孤条目/死链、索引行缺状态或缺日期 | ERROR / WARN |
| 3 | frontmatter 必填字段缺失（scope/type/status/source/updated）、枚举非法 | WARN / ERROR |
| 4 | archive/ 文件 status 非 historical、或被索引引用 | WARN |
| 5 | 关键数字跨文件不一致（每文件取首次出现值：约定"现状置顶，首次即最新"） | WARN |
| 5.5 | 重复主题：条目 description 相似度 ≥80%（同 scope 内）、不同台账文件出现完全相同行；完全同文必报，模糊相似报前 20 对 | WARN |
| 6 | 含待办/挂起事项但无 revisit_when、30 天未复核 | WARN |
| 6.5 | owner 字段存在但为空（协作档写权分区，见 §1 owner 字段） | WARN |
| 7 | 敏感值明文（password/token/口令形态，占位符排除） | ERROR |
| 8 | L3 MEMORY.md 缺 L2 指针、超 2000 字（轻量档跳过） | ERROR / WARN |

报告生成到 `memory-kit/MEMORY-REPORT.md`（`--quiet` 只输出一行统计不写文件，适合每轮末尾快速门禁）；**ERROR=0 才算落盘完成**。
性能：每文件单次读取缓存，纯 stdlib 单进程，<1 万条目秒级。每轮跑一次即可，勿逐文件跑。

## 6. 关键数字一致性（可配置）

数字漂移是最常见的记忆腐化形态。做法：在 `memory_paths.json` 的 `num_patterns` 中登记本项目的关键数字正则（如"XX 项用例"、"领先 N 个提交"，格式 `[标签, 正则]`），校验器比对所有 L2 条目中的取值；多值即 WARN，以 L1 权威源（轻量档以 source）为准修正记忆里的旧值。

## 7. L3 派生层（标准档/协作档）——只留索引

- **记忆主体（条目正文/台账/归档）只存共享 L2 一处**；L3 是各工具（Claude/Codex/WorkBuddy）原生记忆里的**索引指针**：
  - 一行"本项目记忆见 `<共享L2>/MEMORY.md`"；
  - 工具本地路径指针（memory-kit、L1 位置等）；
  - **禁止复制条目内容**（含摘要句）——需要"执行必备"（环境/命令等浓缩事实）也写成 L2 的 env 条目，L3 只留指针。
- 日期日志 `YYYY-MM-DD.md`：append-only，只记当轮过程与踩坑，不进索引。
- memcheck 会校验：指针缺失=ERROR、L3 MEMORY.md 超 2000 字=WARN（索引指针写不出 2000 字，超了说明在往里堆内容）。
