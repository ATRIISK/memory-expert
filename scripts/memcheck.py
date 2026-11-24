#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memcheck.py — 三层记忆体系一致性校验（只读）

用法:
    python memcheck.py                  # 用脚本同目录 memory_paths.json 的路径，报告写 MEMORY-REPORT.md
    python memcheck.py --paths X.json   # 指定路径配置
    python memcheck.py --report X.md    # 指定报告输出路径
    python memcheck.py --quiet          # 只输出一行统计，不写报告文件（适合每轮末尾快速门禁）

只读保证: 本脚本不写入任何记忆或文档文件，仅读取 + 生成独立报告。
退出码: ERROR>0 返回 1，否则 0。ERROR=0 才算落盘完成。
性能: 纯 stdlib 单进程文件扫描；每个文件只读一次并缓存（<1 万条目秒级）。
支持形态: 三层（L1/L2/L3）或轻量档（仅 L2）——路径配置缺 l1/l3 即跳过对应检查。
"""
import json
import os
import re
import sys
import datetime
from collections import defaultdict

KIT_DIR = os.path.dirname(os.path.abspath(__file__))
PATHS_FILE = os.path.join(KIT_DIR, "memory_paths.json")

LONG_LIMIT = 8000          # L2 单条目上限
L3_LIMIT = 2000            # L3 指针文件上限
STALE_DAYS = 30            # 过期复核天数
NEW_FIELDS = ["scope", "type", "status", "source", "updated"]
STATUS_VALUES = {"candidate", "active", "historical", "revoked"}  # candidate=待复核（借鉴 agent-memory 晋升制）
CANDIDATE_STALE_DAYS = 7   # candidate 超过 7 天未复核转正/撤销，避免积压
TYPE_VALUES = {"fact", "decision", "progress", "rule", "procedure"}
IDX_STATUS_WORDS = re.compile(r"(有效|候选|历史|已撤销)")  # 索引行状态词（与 STATUS_VALUES 对应）

# 关键数字一致性正则（按项目自定义，追加到 memory_paths.json 的 num_patterns）
# 格式: [标签, 正则]，正则含至少一个捕获组；每个文件取首次匹配值（约定"现状置顶，首次即最新"）
DEFAULT_NUM_PATTERNS = []

findings = {"ERROR": [], "WARN": [], "INFO": []}
def add(level, section, text):
    findings[level].append((section, text))

_CACHE = {}
def read(p):
    """每文件只读一次（本次运行内缓存），重复扫描零 IO。"""
    if p not in _CACHE:
        with open(p, encoding="utf-8", errors="replace") as f:
            _CACHE[p] = f.read()
    return _CACHE[p]

def parse_fm(text):
    """解析 frontmatter：顶层键 + 缩进子键（metadata 等嵌套块）分开，只取顶层。
    顶层键后跟缩进列表（- item）视为有值（如 source: 多行列表）。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm = {}
    lines = text[3:end].splitlines()
    for i, line in enumerate(lines):
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent > 0:
            continue  # 嵌套块子键不参与校验
        if line.lstrip().startswith("- "):
            continue  # 列表项，归属上一个顶层键
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if not v and i + 1 < len(lines):
            nxt = lines[i + 1]
            nxt_indent = len(nxt) - len(nxt.lstrip())
            if nxt_indent > indent and nxt.lstrip().startswith("- "):
                v = "[]"  # 多行列表，视为有值
        fm[k] = v
    return fm, text[end + 4:]

def collect_md(d, recursive=False):
    out = []
    if not os.path.isdir(d):
        return out
    if recursive:
        for root, _, names in os.walk(d):
            for n in names:
                if n.endswith(".md"):
                    out.append(os.path.join(root, n))
    else:
        for name in sorted(os.listdir(d)):
            if name.endswith(".md") and os.path.isfile(os.path.join(d, name)):
                out.append(os.path.join(d, name))
    return out

def is_daily_log(name):
    return bool(re.match(r"\d{4}-\d{2}-\d{2}\.md$", name))

def rel(p):
    for label, base in BASES:
        if p.startswith(base):
            return f"{label}/{os.path.relpath(p, base)}"
    return p

# ---------- 1. 规模 ----------
def check_size(files):
    rows = []
    for p in files:
        t = read(p)
        n = len(t)
        fm, _ = parse_fm(t)
        base_dir = os.path.dirname(p)
        if n > LONG_LIMIT and p.startswith(P["l2"]) \
                and os.path.basename(os.path.dirname(p)) != "archive" \
                and fm.get("status") != "historical" and not is_daily_log(os.path.basename(p)):
            add("WARN", "超长", f"{rel(p)} = {n} 字 > {LONG_LIMIT}（L2 条目，需拆 snapshot/ledger/archive 三段）")
        elif n > LONG_LIMIT and not p.startswith(P["l2"]):
            add("INFO", "超长", f"{rel(p)} = {n} 字（L1 交付文档/日志，不建议拆；可在文首加按节导航）")
        rows.append((rel(p), n))
    return rows

# ---------- 2. 索引对齐 ----------
def parse_index_lines(text):
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            continue
        m = re.match(r"- \[(.+?)\]\((.+?\.md)\)(.*)$", line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out

def check_index():
    index = os.path.join(P["l2"], "MEMORY.md")
    if not os.path.exists(index):
        add("ERROR", "索引对齐", "L2 索引 MEMORY.md 不存在")
        return []
    text = read(index)
    rows = parse_index_lines(text)
    linked = {os.path.basename(f) for _, f, _ in rows}
    actual = {os.path.basename(p) for p in collect_md(P["l2"])
              if os.path.basename(p) not in ("MEMORY.md", "SCHEMA.md") and not is_daily_log(os.path.basename(p))}
    for f in sorted(actual - linked):
        add("ERROR", "索引对齐", f"条目文件未被索引引用（孤条目）: {f}")
    for f in sorted(linked - actual):
        add("ERROR", "索引对齐", f"索引指向的文件不存在（死链）: {f}")
    for title, _, tail in rows:
        if not IDX_STATUS_WORDS.search(tail):
            add("WARN", "索引对齐", f"索引行缺状态标记: {title}")
        if not re.search(r"\d{1,2}-\d{1,2}|\d{4}-\d{2}-\d{2}", tail):
            add("WARN", "索引对齐", f"索引行缺更新日期: {title}")
    return rows

# ---------- 3. 字段完备性 ----------
def check_schema(files):
    for p in files:
        base = os.path.basename(p)
        if base == "MEMORY.md" or base == "SCHEMA.md" or is_daily_log(base):
            continue
        if os.path.basename(os.path.dirname(p)) == "archive":
            continue
        fm, _ = parse_fm(read(p))
        missing = [k for k in NEW_FIELDS if k not in fm or not fm.get(k)]
        if missing:
            add("WARN", "字段完备", f"{base}: 缺 {', '.join(missing)}")
        st = fm.get("status")
        if st and st not in STATUS_VALUES:
            add("ERROR", "字段完备", f"{base}: status='{st}' 非法（应为 candidate/active/historical/revoked）")
        ty = fm.get("type")
        if ty and ty not in TYPE_VALUES:
            add("ERROR", "字段完备", f"{base}: type='{ty}' 非 v1 枚举（fact/decision/progress/rule/procedure）")

def check_archives(archives):
    index = os.path.join(P["l2"], "MEMORY.md")
    idx_text = read(index) if os.path.exists(index) else ""
    for p in archives:
        base = os.path.basename(p)
        fm, _ = parse_fm(read(p))
        if fm.get("status") != "historical":
            add("WARN", "归档规范", f"archive/{base}: status 应为 historical，实为 '{fm.get('status')}'")
        if base in idx_text:
            add("WARN", "归档规范", f"archive/{base}: 被索引引用（归档不应出现在索引里）")

# ---------- 4. 关键数字一致性 ----------
def check_numbers(files):
    patterns = []
    for pat in P.get("num_patterns", DEFAULT_NUM_PATTERNS):
        if isinstance(pat, (list, tuple)) and len(pat) == 2:
            patterns.append(tuple(pat))
    if not patterns:
        return {}
    table = defaultdict(dict)
    for p in files:
        base = os.path.basename(p)
        if is_daily_log(base) or os.path.basename(os.path.dirname(p)) == "archive" \
                or base == "MEMORY.md":
            continue
        t = read(p)
        for label, pat in patterns:
            try:
                for m in re.finditer(pat, t):
                    groups = [g for g in m.groups() if g]
                    if groups:
                        table[label][rel(p)] = groups[0]
                        break
            except re.error as e:
                add("ERROR", "数字一致", f"num_patterns 中 '{label}' 正则非法: {e}")
                return table
    for label, per_file in sorted(table.items()):
        vals = set(per_file.values())
        if len(vals) > 1:
            detail = "; ".join(f"{f}={v}" for f, v in sorted(per_file.items()))
            add("WARN", "数字一致", f"{label} 存在多个取值 -> {detail}")
    return table

# ---------- 5. 过期与复核缺口 ----------
def check_staleness(files):
    today = datetime.date.today()
    for p in files:
        base = os.path.basename(p)
        if base in ("MEMORY.md", "SCHEMA.md") or is_daily_log(base):
            continue
        if os.path.basename(os.path.dirname(p)) == "archive":
            continue
        fm, body = parse_fm(read(p))
        if fm.get("status") == "historical":
            continue
        d = None
        for key in ("updated", "modified"):
            m = re.search(r"(\d{4})-(\d{2})-(\d{2})", fm.get(key, ""))
            if m:
                d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                break
        if d and (today - d).days > STALE_DAYS:
            add("WARN", "过期复核", f"{base}: 最后更新 {d}，已 {(today - d).days} 天未复核")
        if fm.get("status") == "candidate" and d and (today - d).days > CANDIDATE_STALE_DAYS:
            add("WARN", "candidate 积压", f"{base}: status=candidate 已 {CANDIDATE_STALE_DAYS} 天未复核"
                                         f"（复核后改 active，或撤销/归档；借鉴 agent-memory 晋升制，候选不该长期滞留）")
        if re.search(r"(挂起|待办|待确认|待拍板|待.*到位)", body) and "revisit_when" not in fm:
            add("WARN", "过期复核", f"{base}: 含待办/挂起事项但无 revisit_when 字段（无法自动提醒复核）")
        if "revisit_when" in fm and not fm.get("revisit_when"):
            add("INFO", "过期复核", f"{base}: revisit_when 为空，等同无复核条件")

# ---------- 5.5 重复主题检测（借鉴 memory-health） ----------
DUP_SIM_THRESHOLD = 0.8   # description 相似度阈值（difflib ratio）
DUP_MAX_REPORT = 20       # 相似对报告上限：模板化 description 会产生 O(n²) 噪音，只报前 N 对 + 统计

def check_duplicates(files):
    """跨条目 description 查重 + 跨台账行查重：同一结论写两处是记忆腐化最常见形态。"""
    import difflib
    descs = []   # (文件, description)
    ledger_rows = defaultdict(list)  # ledger 文件 -> [(行内容, 行号)]
    for p in files:
        base = os.path.basename(p)
        if base in ("MEMORY.md", "SCHEMA.md") or is_daily_log(base):
            continue
        if os.path.basename(os.path.dirname(p)) == "archive":
            continue  # 归档原文与主文件天然重复，不查
        if base.endswith("-ledger.md"):
            for i, ln in enumerate(read(p).splitlines(), 1):
                s = ln.strip()
                if s and not s.startswith("#") and not s.startswith(">") and not s.startswith("|--") \
                        and not s.startswith("---") and not s.startswith("```"):
                    ledger_rows[base].append((re.sub(r"[\s\|]", "", s)[:80], i))
            continue
        fm, _ = parse_fm(read(p))
        d = fm.get("description", "")
        if d:
            descs.append((base, d))
    # description 相似度（同 scope 内才比——跨域条目即使句式雷同也不是同一结论；
    # 超 60 条时先按词项倒排粗筛再精算，避免 O(n²) 全对比）
    pairs = []
    by_scope = defaultdict(list)
    for base, d in descs:
        fm = {}
        for p2 in files:
            if os.path.basename(p2) == base:
                fm, _ = parse_fm(read(p2))
                break
        by_scope[fm.get("scope", "")].append((base, d))
    for scope, items in by_scope.items():
        if len(items) > 60:
            # 粗筛：任意 3-gram 重叠才进入精算
            grams = defaultdict(set)
            for idx, (base, d) in enumerate(items):
                for k in range(len(d) - 2):
                    grams[d[k:k+3]].add(idx)
            cand = set()
            for s, idxs in grams.items():
                il = sorted(idxs)
                for a in range(len(il)):
                    for b in range(a + 1, len(il)):
                        cand.add((il[a], il[b]))
            for a, b in cand:
                ratio = difflib.SequenceMatcher(None, items[a][1], items[b][1]).ratio()
                if ratio >= DUP_SIM_THRESHOLD:
                    pairs.append((scope, items[a], items[b], ratio))
        else:
            for a in range(len(items)):
                for b in range(a + 1, len(items)):
                    ratio = difflib.SequenceMatcher(None, items[a][1], items[b][1]).ratio()
                    if ratio >= DUP_SIM_THRESHOLD:
                        pairs.append((scope, items[a], items[b], ratio))
    # 完全同文（ratio=1.0）是真重复，全部列出不受上限限制；其余按相似度降序截断
    exact = [p for p in pairs if p[3] >= 0.999]
    fuzzy = sorted([p for p in pairs if p[3] < 0.999], key=lambda x: -x[3])
    for scope, (f1, _), (f2, _), ratio in exact:
        add("WARN", "重复主题", f"{f1} 与 {f2}（{scope}）description 完全相同（真重复：同一结论两处维护，必须合并或一处留指针）")
    for scope, (f1, _), (f2, _), ratio in fuzzy[:DUP_MAX_REPORT]:
        add("WARN", "重复主题", f"{f1} 与 {f2}（{scope}）description 相似度 {ratio:.0%}（疑似同一主题两处维护，应合并或一处留指针）")
    if len(fuzzy) > DUP_MAX_REPORT:
        add("WARN", "重复主题", f"…另有 {len(fuzzy) - DUP_MAX_REPORT} 对相似未列出（多为模板化句式撞车：description 只差序号/日期。"
                                f"建议让 description 带上具体主题词，而不是调整阈值）")
    # 台账行完全相同（不同文件间的重复行）
    seen_rows = {}
    for fname, rows in sorted(ledger_rows.items()):
        for text, lineno in rows:
            if text in seen_rows:
                add("WARN", "重复主题", f"{fname}:{lineno} 与 {seen_rows[text]} 存在相同台账行（同一结论写两处，只应保留一处带出处）")
            else:
                seen_rows[text] = f"{fname}:{lineno}"

# ---------- 6. 敏感值线索（脱敏输出） ----------
SECRET_PATTERNS = [
    (r"password\s*[:=]\s*[\"']?(\S{6,})", "password 赋值"),
    (r"token\s*[:=]\s*[\"']?(\S{8,})", "token 赋值"),
    (r"(?:Bearer|Authorization)\s+([A-Za-z0-9_\-\.]{12,})", "鉴权凭据"),
]

def mask(v):
    return v[:2] + "*" * max(3, len(v) - 2)

def placeholder_like(tok):
    if any(c in tok for c in "<>*…"):
        return True
    if any("\u4e00" <= c <= "\u9fff" for c in tok):
        return True
    return tok.lower().startswith(("your", "xxx", "example", "***", "$", "{"))

def check_secrets(files):
    n = 0
    for p in files:
        for i, line in enumerate(read(p).splitlines(), 1):
            for pat, why in SECRET_PATTERNS:
                for v in re.findall(pat, line):
                    v = v.strip("\"'")
                    if placeholder_like(v) or v.lower() in ("password", "token"):
                        continue
                    if re.fullmatch(r"[A-Za-z0-9!@#$%^&*_\-\.]{8,}", v):
                        add("ERROR", "敏感值", f"{rel(p)}:{i} {why} 明文（值 {mask(v)}，建议改占位符并指向配置文件）")
                        n += 1
    if n == 0:
        add("INFO", "敏感值", "未发现明文口令/token（占位符与中文描述已排除）")

# ---------- 7. L3 派生层指针（轻量档无 L3 时跳过；跨应用支持多个 L3，每工具一个） ----------
def l3_label(d):
    """L3 展示标签：目录名是 memory 时带上父目录（wb/memory、codex/memory），避免多工具撞名。"""
    base = os.path.basename(os.path.normpath(d))
    if base == "memory":
        parent = os.path.basename(os.path.dirname(os.path.normpath(d)))
        return f"{parent}/{base}" if parent else base
    return base

def check_pointer():
    for l3_dir in P["l3"]:
        label = l3_label(l3_dir)
        p = os.path.join(l3_dir, "MEMORY.md")
        if not os.path.exists(p):
            add("WARN", "派生层", f"[{label}] L3 MEMORY.md 不存在（派生层应有一个指针文件）")
            continue
        t = read(p)
        if "MEMORY.md" not in t:
            add("ERROR", "派生层", f"[{label}] L3 MEMORY.md 未包含指向共享 L2 索引的指针"
                                   f"（记忆主体只存 L2 一处，工具记忆只留索引；自成一体=第二真相）")
        if len(t) > L3_LIMIT:
            add("WARN", "派生层", f"[{label}] L3 MEMORY.md = {len(t)} 字——索引指针写不出这个量，"
                                  f"疑似在往 L3 堆条目内容（记忆主体只能进 L2），请压缩为纯指针")

# ---------- 8. 多人写权（owner 登记，选填字段——有值才校验） ----------
def check_owners(files):
    """协作档：owner 值合法即可（非空字符串）；owner 缺失不报错（单人文档不用填）。"""
    for p in files:
        base = os.path.basename(p)
        if base in ("MEMORY.md", "SCHEMA.md") or is_daily_log(base):
            continue
        if os.path.basename(os.path.dirname(p)) == "archive":
            continue
        fm, _ = parse_fm(read(p))
        o = fm.get("owner", "")
        if "owner" in fm and not o:
            add("WARN", "写权分区", f"{base}: owner 字段为空（要么删掉要么填负责人）")

# ---------- 报告 ----------
def build_report(size_rows, links, num_table):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("# 记忆体系一致性报告（memcheck）")
    L.append("")
    L.append(f"> 生成时间：{now}　模式：**只读扫描**（本报告不改动任何记忆或文档）")
    L.append("> 判定：ERROR 必须修；WARN 建议修；INFO 仅记录。报告只驱动 L1/L2 修正，L3 永不反向写入。")
    L.append("")
    L.append("## 0. 总览")
    L.append("")
    L.append("| 级别 | 数量 |")
    L.append("|---|---|")
    for lv in ("ERROR", "WARN", "INFO"):
        L.append(f"| {lv} | {len(findings[lv])} |")
    L.append("")
    scan_parts = [f"L2 记忆 {len(collect_md(P['l2']))} 个",
                  f"归档 {len(collect_md(os.path.join(P['l2'], 'archive')))} 个"]
    for d in P["l3"]:
        scan_parts.append(f"L3[{l3_label(d)}] 记忆 {len(collect_md(d))} 个")
    if P["l1"]:
        scan_parts.append(f"L1 文档 {sum(len(collect_md(d)) for d in P['l1'])} 个")
    L.append("- 扫描：" + "、".join(scan_parts))
    L.append(f"- 索引条目：{len(links)} 条")
    L.append("")
    L.append("## 1. 规模（按字数降序，前 8）")
    L.append("")
    L.append("| 文件 | 字数 |")
    L.append("|---|---|")
    for name, n in sorted(size_rows, key=lambda x: -x[1])[:8]:
        L.append(f"| {name} | {n} |")
    L.append("")
    L.append("## 2. 关键数字一致性")
    L.append("")
    if num_table:
        for label, per_file in sorted(num_table.items()):
            vals = sorted(set(per_file.values()))
            mark = "❌ 不一致" if len(vals) > 1 else "✅"
            L.append(f"**{label}** {mark}：{', '.join(vals)}")
            for f, v in sorted(per_file.items()):
                L.append(f"  - {f} → {v}")
            L.append("")
    else:
        L.append("（未配置 num_patterns，无数字比对）")
        L.append("")
    for lv, sec_no in (("ERROR", 3), ("WARN", 4), ("INFO", 5)):
        L.append(f"## {sec_no}. {lv} 明细（{len(findings[lv])}）")
        L.append("")
        if not findings[lv]:
            L.append("（无）")
            L.append("")
            continue
        grouped = defaultdict(list)
        for sec, txt in findings[lv]:
            grouped[sec].append(txt)
        for sec, items in sorted(grouped.items()):
            L.append(f"### {sec}")
            L.append("")
            for it in items:
                L.append(f"- {it}")
            L.append("")
    L.append("## 6. 建议动作")
    L.append("")
    L.append("1. 修 ERROR：索引对齐（孤条目/死链）、枚举非法、敏感值明文")
    L.append("2. 补 frontmatter：scope / type / status / source / updated")
    L.append("3. 统一数字：以 L1 权威源为准，改记忆里的旧值")
    L.append("4. 拆超长文件：snapshot（主文件）+ ledger（台账）+ archive/（归档，逐字搬运）")
    L.append("5. 复查过期项：补 revisit_when 或归档")
    L.append("6. 清理 candidate：复核后转 active 或撤销，候选不滞留超过 7 天")
    L.append("7. 合并重复主题：同一结论只在一处维护，别处留指针")
    return "\n".join(L) + "\n"


def load_paths():
    if "--paths" in sys.argv:
        pf = sys.argv[sys.argv.index("--paths") + 1]
    elif os.path.exists(PATHS_FILE):
        pf = PATHS_FILE
    else:
        print("错误：未找到 memory_paths.json（首次使用请先跑 init_memory.py，或用 --paths 指定）")
        sys.exit(2)
    cfg = json.loads(read(pf))
    l3_raw = cfg.get("l3")
    # l3 兼容两种写法：单字符串（旧配置）或列表（跨应用多工具，每工具一个 L3）
    if isinstance(l3_raw, str):
        l3_list = [os.path.abspath(l3_raw)]
    elif isinstance(l3_raw, list):
        l3_list = [os.path.abspath(d) for d in l3_raw]
    else:
        l3_list = []
    return {
        "name": cfg.get("name", "项目"),
        # 轻量档：l1/l3 可缺省，缺哪个跳过哪层检查
        "l1": [os.path.abspath(d) for d in cfg.get("l1", [])] if cfg.get("l1") else [],
        "l2": os.path.abspath(cfg["l2"]),
        "l3": l3_list,
        "num_patterns": cfg.get("num_patterns", []),
    }


def main():
    global P, BASES
    P = load_paths()
    archive_dir = os.path.join(P["l2"], "archive")
    BASES = [("L2", P["l2"])] + [(f"L3:{l3_label(d)}", d) for d in P["l3"]] \
        + [("L1", d) for d in P["l1"]]

    l2_files = collect_md(P["l2"])
    archives = collect_md(archive_dir)
    l3_files = []
    for d in P["l3"]:
        l3_files += collect_md(d)
    l1_docs = collect_md(P["l1"][0], recursive=True) if P["l1"] and os.path.isdir(P["l1"][0]) else []
    for d in P["l1"][1:]:
        l1_docs += collect_md(d)
    all_files = l2_files + archives + l3_files + l1_docs

    size_rows = check_size(all_files)
    links = check_index()
    check_schema(l2_files)
    check_archives(archives)
    num_table = check_numbers(all_files)
    check_staleness(l2_files)
    check_duplicates(l2_files + l3_files)
    check_owners(l2_files)
    check_secrets(all_files)
    if P["l3"]:
        check_pointer()

    quiet = "--quiet" in sys.argv
    out = os.path.join(KIT_DIR, "MEMORY-REPORT.md")
    if "--report" in sys.argv:
        out = sys.argv[sys.argv.index("--report") + 1]
    if not quiet:
        with open(out, "w", encoding="utf-8") as f:
            f.write(build_report(size_rows, links, num_table))
    summary = "ERROR=%d WARN=%d INFO=%d" % (
        len(findings["ERROR"]), len(findings["WARN"]), len(findings["INFO"]))
    print(summary if quiet else summary + f" -> {out}")
    return 1 if findings["ERROR"] else 0

if __name__ == "__main__":
    sys.exit(main())
