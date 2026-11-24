#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init_memory.py — 为任意项目初始化三层记忆体系骨架

用法:
    # 标准档（三层）
    python init_memory.py --l1 <L1权威源目录> --l2 <L2记忆目录> --l3 <L3派生目录> [--name <项目名>]
    # 轻量档（只有 L2，适合小项目；后续可 --upgrade 补层）
    python init_memory.py --l2 <L2记忆目录> [--name <项目名>]
    # 轻量档升级为三层（只补缺的层与配置，不动已有条目）
    python init_memory.py --l2 <L2目录> --l1 <L1> --l3 <L3> --upgrade

动作:
    1. 创建目录（L2 含 archive/；L1/L3 按档位）
    2. L2 生成 MEMORY.md 索引模板 + SCHEMA.md（已存在则跳过，不覆盖）
    3. 生成 <L2 同级>/memory-kit/：memcheck.py + memory_paths.json（--upgrade 时合并更新 json，不覆盖已有 keys）
    4. L3 生成 MEMORY.md 指针模板（有 L3 时）
"""
import argparse
import json
import os
import shutil
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_SCHEMS = os.path.join(SKILL_DIR, "references", "schema.md")
MEMCHECK_SRC = os.path.join(SKILL_DIR, "scripts", "memcheck.py")

INDEX_TEMPLATE = """# {name} 项目记忆索引

> **索引与状态只维护在本文件**（派生侧只留指针，不复制内容）。
> 每条格式：`- [主题](文件.md) — 摘要 ｜ 状态 · 最后更新`
> 状态取值：**候选**（待复核）/ **有效**（可直接依赖）/ **历史**（已被后续轮次覆盖，仅留痕）/ **已撤销**（结论作废，勿引用）。
> 条目形态：【快照】现状与待办、【台账】现行口径一行一条、【规则】流程与红线、【事实】环境与工具。
> 读取顺序：先读本索引 → 按摘要判断相关 → 只读命中文件，**勿整目录全读**；历史轮次在 `archive/`（不进索引、默认不读）。

- [记忆与文档同步规范](keep-docs-in-sync.md) — 每轮落盘四步 + 写入五关 + L2 三段式控长 ｜ 有效 · {today}
"""

KEEP_DOCS_TEMPLATE = """---
name: keep-docs-in-sync
description: 记忆与文档同步流程规范（每轮落盘四步 + 写入五关 + 控长归档），每次更新记忆必须按此流程执行
scope: process:collab
type: rule
status: active
source:
  - "用户指示（项目启动时确认）"
updated: {today}
---

## 每轮落盘后的固定动作

1. **写 L1**{l1_line}：事实与交付物进权威源文档，唯一真相{no_l1_hint}。
2. **更新 L2**：条目 frontmatter 的 `status / updated / revisit_when` 同步；新结论进台账（`*-ledger.md`）一行一条，**不就地涂改旧结论**，被取代的改 `status: historical` 或移入 `archive/`。
3. **更新索引**：`MEMORY.md` 那一行的摘要与日期。
4. **跑校验**：`python {memcheck}` → **ERROR 必须为 0** 才算落盘完成（报告见同目录 `MEMORY-REPORT.md`；赶时间可用 `--quiet` 只看一行统计）。

## 写入五关（硬规则，缺一不可）

1. **核准事实再写**——不凭记忆或推测；对外汇报内容尤其如此。
2. **落对层**——事实/交付物 → L1{no_l1_hint2}，索引/主题条目 → L2{no_l3_hint}，不跨层堆放不重复。
3. **格式合规**——frontmatter 必填字段置顶层（见 SCHEMA.md）；索引行 `- [主题](文件.md) — 摘要 ｜ 状态 · 日期`；台账只追加不涂改。
4. **写后跑 memcheck，ERROR 必须为 0**。
5. **不并发改同一处**——后写者以已核准的最新事实为准。

## 控长与归档

- 单条目 ≤ 8000 字；超长拆三段：`<topic>.md`（快照，active）/ `<topic>-ledger.md`（台账，active）/ `archive/<topic>-archive.md`（historical，不进索引）。历史原文逐字搬运不改写。
- schema 全量约定见 `SCHEMA.md`。

## 红线

- 不得改动用户在交付文档中的手工编辑内容；疑似异常段落先问再动。
- 敏感值（口令/token/测试数据）不进任何记忆文件。
- 流程层面新增约定先与用户讨论再固化。
"""

L3_TEMPLATE = """# {name}（{tool_label}侧索引）

> 本文件**只留索引指针**，记忆主体在共享 L2：`{l2}/MEMORY.md`（开工先读它，勿在此复制条目内容）。
> 环境/命令等执行必备 → 写成 L2 的 env 条目，此处只留指针。

## 指针

- 共享记忆索引: {l2}/MEMORY.md
- 校验工具: 用 L2 同级 memory-kit/memcheck.py，ERROR=0 才算落盘完成

## 本侧日志

- `memory/YYYY-MM-DD.md`：append-only 执行日志，只记当轮过程与踩坑，不进索引。
"""


def write_if_missing(path, content):
    if os.path.exists(path):
        print(f"  [跳过] 已存在: {path}")
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [创建] {path}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--l1", default=None, help="L1 权威源目录（缺省=轻量档）")
    ap.add_argument("--l2", required=True, help="L2 记忆层目录（索引+条目所在）")
    ap.add_argument("--l3", action="append", default=None,
                    help="L3 派生层目录；跨应用时每个工具传一次（--l3 <工具A> --l3 <工具B>），缺省=轻量档")
    ap.add_argument("--name", default="项目", help="项目名（用于模板标题）")
    ap.add_argument("--upgrade", action="store_true",
                    help="升级已有轻量档为三层：只补缺的层与配置，不覆盖不搬移已有条目")
    args = ap.parse_args()

    import datetime
    today = datetime.date.today().strftime("%Y-%m-%d")
    l2 = os.path.abspath(args.l2)
    l1 = os.path.abspath(args.l1) if args.l1 else None
    l3_list = [os.path.abspath(d) for d in args.l3] if args.l3 else []
    lite = not (l1 and l3_list)

    print(f"初始化记忆体系：{args.name}（{'轻量档' if lite else '标准三层'}{' · 升级' if args.upgrade else ''}）"
          + (f" · 跨应用 {len(l3_list)} 个 L3" if len(l3_list) > 1 else ""))
    for d in [l2, os.path.join(l2, "archive")] + ([l1] if l1 else []) + l3_list:
        os.makedirs(d, exist_ok=True)
        print(f"  [目录] {d}")

    # L2 索引 + 规范条目 + SCHEMA（upgrade 时已存在则全部跳过）
    write_if_missing(os.path.join(l2, "MEMORY.md"), INDEX_TEMPLATE.format(name=args.name, today=today))
    no_l1_hint = "（轻量档无 L1：出处写文件路径/commit，靠 source 字段承担唯一真相）" if not l1 else ""
    write_if_missing(os.path.join(l2, "keep-docs-in-sync.md"),
                     KEEP_DOCS_TEMPLATE.format(
                         today=today,
                         l1_line=f"（{l1}）" if l1 else "",
                         no_l1_hint=no_l1_hint,
                         no_l1_hint2="" if l1 else "（轻量档：出处进条目 source 字段）",
                         no_l3_hint="，指针 + 执行必备 → L3" if l3_list else "（轻量档无 L3）",
                         memcheck=os.path.join((os.path.dirname(l2) if os.path.basename(l2) == "memory" else l2), "memory-kit", "memcheck.py")))
    if os.path.exists(TEMPLATE_SCHEMS):
        write_if_missing(os.path.join(l2, "SCHEMA.md"), open(TEMPLATE_SCHEMS, encoding="utf-8").read())

    # memory-kit：memcheck + 路径配置（upgrade 时合并 json，不覆盖已有 keys）
    # 落点约定：l2 目录名是 memory 则 kit 放其同级（.claude/memory -> .claude/memory-kit）；
    # 否则（轻量档自定义目录名）kit 放 l2 内部，自包含可整体拷走。
    if os.path.basename(l2) == "memory":
        kit = os.path.join(os.path.dirname(l2), "memory-kit")
    else:
        kit = os.path.join(l2, "memory-kit")
    os.makedirs(kit, exist_ok=True)
    if os.path.exists(MEMCHECK_SRC):
        dst = os.path.join(kit, "memcheck.py")
        shutil.copy(MEMCHECK_SRC, dst)
        print(f"  [更新] {dst}")
    paths_file = os.path.join(kit, "memory_paths.json")
    cfg = {}
    if os.path.exists(paths_file):
        with open(paths_file, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.setdefault("name", args.name)
    if l1:
        lst = cfg.setdefault("l1", [])
        if l1 not in lst:
            lst.append(l1)
    cfg["l2"] = l2
    if l3_list:
        cfg_l3 = cfg.setdefault("l3", [])
        if not isinstance(cfg_l3, list):
            cfg_l3 = [cfg_l3]
        for d in l3_list:
            if d not in cfg_l3:
                cfg_l3.append(d)
        cfg["l3"] = cfg_l3
    with open(paths_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"  [创建/更新] {paths_file}")

    # L3 指针文件（跨应用：每工具一个 L3，各放指向共享 L2 的指针）
    def l3_label(d):
        base = os.path.basename(os.path.normpath(d))
        if base == "memory":
            parent = os.path.basename(os.path.dirname(os.path.normpath(d)))
            return f"{parent}/{base}" if parent else base
        return base

    for d in l3_list:
        write_if_missing(os.path.join(d, "MEMORY.md"),
                         L3_TEMPLATE.format(name=args.name, l2=l2, tool_label=l3_label(d)))

    print("\n完成。落点：")
    if l1:
        print(f"  L1 权威源: {l1}")
    print(f"  L2 记忆层: {l2}{'（跨应用共享，唯一索引）' if len(l3_list) > 1 else ''}")
    for d in l3_list:
        print(f"  L3 派生层: {d}")
    print(f"  校验工具:  {os.path.join(kit, 'memcheck.py')}（读同目录 memory_paths.json 取路径）")
    if lite:
        print("  （轻量档提示：条目变多或出现权威文档源后，可 --upgrade --l1/--l3 补建，不动已有条目）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
