# Memory Expert

**File-based long-term memory for AI coding agents — consistency enforced by a script, not by willpower.**

> 给 AI 编码助手（Claude Code / Codex / WorkBuddy 等）的**文件式长期记忆体系**，把"记忆不漂移"从靠自觉变成靠校验：`memcheck` 跑到 `ERROR=0` 才算一轮写入完成。

![layer model](https://img.shields.io/badge/layers-L1%20%2F%20L2%20%2F%20L3-blue) ![python](https://img.shields.io/badge/python-3.8%2B%20stdlib-green) ![license](https://img.shields.io/badge/license-MIT-black)

---

## The problem / 解决什么问题

An agent's memory decays in predictable ways: the same number gets recorded with three different values, a decision gets overruled but the old note stays "current", the index lists files that no longer exist, a token ends up in a plaintext markdown file. Prompt-level instructions don't stop this, because the failure is not in the model's reasoning — it's in the **files**.

Memory Expert attacks it with three things:

1. **A fixed layer contract** — every fact has exactly one home.
2. **A strict entry schema** — frontmatter fields, status enum, append-only ledgers.
3. **A validator you cannot argue with** — `memcheck.py`, 8 check families, `ERROR=0` as the gate.

AI 记忆的腐化方式是固定的：同一个数字被记成三个值、结论被推翻但旧条目还是"有效"、索引指向已删除的文件、口令明文躺在 markdown 里。这些问题不是提示词能解决的，因为腐化发生在**文件层**。

---

## Three layers / 三层模型

| Layer | Role | Holds |
|---|---|---|
| **L1** — source of truth | The only truth | Specs, design docs, delivered artifacts, organized by business domain |
| **L2** — memory body | **The single place memory lives** | `MEMORY.md` index + entry files + `archive/` |
| **L3** — derived pointers | **Index pointers only** | One line per tool: "this project's memory lives at `<L2>/MEMORY.md`" + local log |

**Hard rule**: entry bodies, ledgers and archives exist **only in L2**, no matter which tool produced them. Each tool's native memory file keeps a *pointer*, never a copy — including summaries. Two copies means two truths, and two truths mean drift.

**Three invariants**

1. Write-back is one-directional — validation reports fix L1/L2; L3 never flows back.
2. Put it on the right layer — facts → L1, memory body → L2 (only there), pointers → L3.
3. Secrets, tokens and test credentials never enter any memory file.

---

## Quick start / 快速开始

```bash
git clone https://github.com/ATRIISK/memory-expert.git
cd memory-expert
```

**Minimal tier** — single tool, fewer than ~10 entries, one person:

```bash
python scripts/init_memory.py --l2 <project>/.memory --name myproject
python <project>/.memory/memory-kit/memcheck.py --quiet   # expect: ERROR=0
```

**Standard tier** — multi-session project where conventions drift:

```bash
python scripts/init_memory.py \
  --l1 <project>/docs \
  --l2 <project>/.memory \
  --l3 ~/.claude/memory \
  --l3 ~/.codex/memory \
  --name myproject
python <project>/.memory/memory-kit/memcheck.py          # writes MEMORY-REPORT.md
```

`init_memory.py` installs a copy of the validator plus `memory_paths.json` into `<L2>/memory-kit/`. **Run the validator from there, not from this repo** — that copy reads the config sitting next to it, so every project member and every tool validates against the same paths. Commit `memory-kit/` to the project repo.

**Upgrade later** — lightweight → standard, without touching existing entries:

```bash
python scripts/init_memory.py --l2 <project>/.memory --l1 <project>/docs --upgrade
```

Requirements: Python 3.8+, standard library only. No dependencies, no network, no LLM calls.

---

## The round-trip loop / 每轮落盘四步

When a new conclusion appears, write it **in the same round** — not "before delivery":

1. **Write L1** (skip on minimal tier; cite path or commit in the entry).
2. **Update L2** — sync frontmatter `status` / `updated` / `revisit_when`; append new conclusions to ledgers **one line per fact, never edit in place**.
3. **Update the index row** — summary and date.
4. **Run `memcheck`** — `ERROR=0` is the only definition of "written".

---

## What memcheck catches / 检查项

| # | Check | Level |
|---|---|---|
| 1 | Entry over 8 KB without archiving | WARN |
| 2 | Index alignment — orphan entries, dead links, missing status or date | ERROR / WARN |
| 3 | Missing required frontmatter fields, illegal enum values | WARN / ERROR |
| 4 | Archived file not marked `historical`, or still referenced by the index | WARN |
| 5 | **A key number that differs across files** (first occurrence wins per file) | WARN |
| 5.5 | Near-duplicate topics — description similarity ≥ 80%, or byte-identical ledger lines | WARN |
| 6 | Open TODO / blocked item without `revisit_when`, or untouched for 30 days | WARN |
| 7 | **Plaintext secret** (password / token forms; placeholders excluded) | ERROR |
| 8 | L3 missing its L2 pointer, or L3 exceeding 2000 chars | ERROR / WARN |

Number drift (check 5) is configurable: register your project's own numeric patterns in `memory_paths.json` → `num_patterns`, and the validator compares every occurrence across entries. Pure stdlib, single process, single read per file — tens of thousands of entries validate in seconds.

---

## Entry schema / 条目 Schema

```yaml
---
name: payment-timeout-policy
description: 订单超时取消走 TTL+DLX，30 分钟；已按生产配置调优
scope: project:shop/order
type: decision            # fact | decision | progress | rule | procedure
status: active            # candidate | active | historical | revoked
confidence: high
source:
  - "docs/design.md#§4.2"
  - "commit:f6508da"
valid_from: 2026-01-01
revisit_when: "MQ 集群扩到 5 节点后重新压测"
supersedes: [payment-timeout-policy-v0]
owner: alice              # collaboration tier only
updated: 2026-01-01
---
```

**`status` is the load-bearing field**

| Value | Meaning | Behaviour |
|---|---|---|
| `candidate` | New, unverified | Searchable, must be labelled "unverified" when injected; WARN after 7 days |
| `active` | Currently valid | Default for retrieval and injection |
| `historical` | Superseded | Searchable, not injected; must be labelled when quoted |
| `revoked` | Wrong (misjudgement) | Never cite; kept only for traceability |

**Promotion is by recurrence, not by assertion**: a fresh conclusion lands as `candidate`. It becomes `active` only when (a) a later session re-confirms it, (b) it shows up in ≥ 2 independent rounds, or (c) the user explicitly rules on it. One judgement call never enters permanent memory — it takes two.

Full spec: [`references/schema.md`](references/schema.md). Collaboration write-partitioning and archiving: [`references/playbook.md`](references/playbook.md).

---

## Use it as a skill / 作为 skill 安装

The repo layout is a standard agent-skill directory (`SKILL.md` + `references/` + `scripts/`):

```bash
# Claude Code
cp -r memory-expert ~/.claude/skills/

# WorkBuddy
cp -r memory-expert ~/.workbuddy/skills/

# Codex / other agents that read a skills directory
cp -r memory-expert ~/.agents/skills/
```

The agent then loads `SKILL.md` on demand — when you ask it to plan a memory system, design an entry schema, or run a consistency check.

---

## Reading discipline / 召回侧纪律

Memory is **an assertion about the past, not a fact about the present**:

- Environment, version and path facts older than 30 days: probe them live before relying on them.
- When memory contradicts reality, reality wins — fix the entry in that same round.
- Label provenance in reports: "archived memory (date)" vs "verified just now".

---

## Repository layout

```
memory-expert/
├── SKILL.md              # agent-facing instructions: tiers, invariants, the 4-step loop
├── references/
│   ├── schema.md         # entry schema, index format, status enum, check catalogue
│   └── playbook.md       # collaboration write zones, archiving, size control
└── scripts/
    ├── init_memory.py    # scaffold L1/L2/L3 + memory-kit/ (validator + config), or --upgrade
    └── memcheck.py       # validator source; init copies it into <L2>/memory-kit/
```

---

## License

MIT — see [LICENSE](LICENSE).
