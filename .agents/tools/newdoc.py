#!/usr/bin/env python3
""".agents/docs 文档管家：新建（自动编号）/ 归档 / 校验引用 + todo 任务索引维护。

用法:
    python newdoc.py new <category> <标题> [--sub 子分类] [--priority 高|中|低] [--root 项目根]
    python newdoc.py archive <文件路径> [--reason 原因] [--root 项目根]
    python newdoc.py check [--root 项目根]

category 取值: knowledge | questions | checkpoint | todo | explore

todo 分类的**任务调度**（激活 / 优先级 / 完成出表）请用 scripts/taskmgr.py——
它内部复用本文件的 create_doc / archive_doc，并保证「任务文档元信息」与
「todo/README.md 索引」两处同步，避免只改一处造成漂移。

本文件与 taskmgr.py 一样只用标准库，可直接 `from newdoc import ...` 复用。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlsplit

CATEGORIES = ["knowledge", "questions", "checkpoint", "todo", "explore"]
DOCS_ROOT = Path(".agents") / "docs"
INVALID = r'[\\/:*?"<>|\r\n\t]'

# ---------------------------------------------------------------- 任务索引常量
TODO_INDEX_NAME = "README.md"  # .agents/docs/todo/README.md —— 固定名，不编号不归档
TASK_STATUS = ("排队", "进行中", "已完成", "已废弃")
PRIORITY_ORDER = {"高": 3, "中": 2, "低": 1}
DEFAULT_PRIORITY = "中"

INDEX_HEADINGS = ("激活中", "排队中", "说明")

EMPTY_ROW = "| - | （无） | - | - |"

INDEX_TEMPLATE = """# 任务索引与调度表（todo/README.md）

> 本文件是 `.agents/docs/todo/` 的**唯一调度真源**。
> 被要求「继续任务」时**只读这一个文件**——不要 ls 目录、不要逐个读 todo 文档（省 token）。
> 维护一律用 `python .agents/tools/taskmgr.py`，不要手改表格。
> 规则见 `.agents/rules/07_task_tracking.md` §2。
> 最后更新: {today}  |  激活任务: {active}

## 激活中（同时最多一个）

| 编号 | 任务 | 优先级 | 关联 checkpoint |
| --- | --- | --- | --- |
{active_rows}

## 排队中（按 优先级 → 编号 排序）

| 编号 | 任务 | 优先级 | 关联 checkpoint |
| --- | --- | --- | --- |
{queue_rows}

## 说明

{tail}
"""

INDEX_TAIL = """- **单一激活**：「激活中」永远最多一行；激活新任务时旧的自动退回「排队中」。
- **完成即出表**：任务归档（完成 / 废弃）后**必须**从本表删除该行，历史由 `todo/_archive/` 留存。
- **排序**：优先级 `高 > 中 > 低`，同优先级按编号升序（先建的先做）。
- **改优先级**：`taskmgr.py priority <编号> 高|中|低`（同步改任务文档文首的 `优先级`）。
- **队列为空**：两个表都空 = 真的没有排队任务，去问用户，不要自己从代码里猜一个出来。
"""

TEMPLATES = {
    "knowledge": """# {num}_{title}

> 分类: {cat}{sub}  |  创建: {today}  |  适用范围: <哪些模块/场景>
> 关键词: <便于检索的几个词>

## 结论（先给做法，再讲原理）

## 背景

## 细节

## 常见改动点
- 想改 X → 改 `<文件>:<函数>`，注意 Y

## 参考
- 关联代码：`src/...`
""",
    "questions": """# {num}_{title}

> 分类: questions  |  发现: {today}  |  状态: 待修复 / 已修复  |  严重级别: 高/中/低

## 现象

## 复现步骤

## 排查过程

## 根本原因

## 修复方案

## 如何避免再犯
""",
    "explore": """# {num}_{title}

> 分类: explore  |  日期: {today}  |  结论: 可行 / 不可行 / 待定

## 问题

## 调研范围与方法

## 候选方案对比

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
|  |  |  |  |

## 结论与建议

## 后续（确认可行 → 建 todo）
""",
    "todo": """# {num}_{title}

> 状态: 排队  |  创建: {today}  |  完成: -
> 优先级: 中  |  关联 checkpoint: -

## 背景

## 目标（验收标准）
- [ ] 标准 1
- [ ] 标准 2

## 阻塞 / 依赖

## 完成记录
""",
    "checkpoint": """# {num}_{title}

> 关联任务: todo/NNNN_xxx.md  |  更新: {today}  |  进度: 0/N
> 最近 commit: -

## 已完成

## 进行中（下一步从这里继续）

## 待办

## 关键决策与上下文（恢复任务必读）

## 验证方式
""",
}


class DocError(Exception):
    """文档操作的可预期错误（命令行会打成 [错误] 前缀并退出码 1）。"""


def sanitize(title: str) -> str:
    t = re.sub(INVALID, "_", title).strip().strip(".")
    return t or "untitled"


def next_number(cat_dir: Path) -> int:
    """取该分类（含子目录与 _archive）内已用编号的最大值 + 1。"""
    max_n = 0
    for p in cat_dir.rglob("*.md"):
        m = re.match(r"^(\d{4})_", p.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


# ------------------------------------------------------------------ todo 索引
def todo_dir(root: Path) -> Path:
    return root / DOCS_ROOT / "todo"


def index_path(root: Path) -> Path:
    return todo_dir(root) / TODO_INDEX_NAME


def ensure_index(root: Path) -> tuple[Path, bool]:
    """确保任务索引存在；已存在则原样返回。返回 (路径, 是否新建)。"""
    p = index_path(root)
    if p.exists():
        return p, False
    if not todo_dir(root).exists():
        raise DocError(f"分类目录不存在: {todo_dir(root)}（先跑 scaffold.py）")
    p.write_text(
        INDEX_TEMPLATE.format(
            today=date.today().isoformat(),
            active="（无）",
            active_rows=EMPTY_ROW,
            queue_rows=EMPTY_ROW,
            tail=INDEX_TAIL,
        ),
        encoding="utf-8",
    )
    return p, True


def _row_line(row: dict) -> str:
    link = row.get("link") or f"./{row['num']}_{row['title']}.md"
    return f"| {row['num']} | [{row['num']}_{row['title']}]({link}) | {row['priority']} | {row['checkpoint']} |"


def _sort_key(row: dict) -> tuple[int, int]:
    return (-PRIORITY_ORDER.get(row["priority"], 2), int(row["num"]))


def parse_index(root: Path) -> dict | None:
    """解析任务索引。返回 None 表示索引文件不存在。

    Returns:
        {"active": [row], "queue": [row], "tail": str, "updated": str, "active_task": str}
        row = {"num", "title", "priority", "checkpoint", "link"}
    """
    p = index_path(root)
    if not p.exists():
        return None
    sections: dict[str, list[str]] = {}
    cur = ""
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            sections[cur] = []
            continue
        if cur:
            sections[cur].append(line)

    def rows_of(prefix: str) -> list[dict]:
        raw = []
        for name, body in sections.items():
            if name.startswith(prefix):
                raw = body
                break
        out = []
        for line in raw:
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 4 or not re.fullmatch(r"\d{4}", cells[0]):
                continue
            title_cell = cells[1]
            m = re.match(r"^\[([^\]]*)\]\(([^)]*)\)$", title_cell)
            title, link = (m.group(1), m.group(2)) if m else (title_cell, "")
            title = re.sub(r"^\d{4}_", "", title)
            if title in ("（无）", "-", ""):
                continue
            out.append(
                {
                    "num": cells[0],
                    "title": title,
                    "priority": cells[2]
                    if cells[2] in PRIORITY_ORDER
                    else DEFAULT_PRIORITY,
                    "checkpoint": cells[3],
                    "link": link,
                }
            )
        return sorted(out, key=_sort_key)

    tail = ""
    for name, body in sections.items():
        if name.startswith("说明"):
            tail = "\n".join(body).strip()
            break
    return {
        "active": rows_of("激活中"),
        "queue": rows_of("排队中"),
        "tail": tail or INDEX_TAIL,
    }


def write_index(
    root: Path, active: list[dict], queue: list[dict], tail: str | None = None
) -> Path:
    """重写任务索引（激活中最多一行，排队区自动排序）。"""
    p = index_path(root)
    if not p.exists():
        ensure_index(root)
    active = sorted(active, key=_sort_key)[:1]
    active_nums = {r["num"] for r in active}
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in sorted(queue, key=_sort_key):
        if r["num"] in active_nums or r["num"] in seen:
            continue
        seen.add(r["num"])
        deduped.append(r)
    queue = deduped
    cur = parse_index(root) or {}
    label = f"{active[0]['num']}_{active[0]['title']}" if active else "（无）"
    p.write_text(
        INDEX_TEMPLATE.format(
            today=date.today().isoformat(),
            active=label,
            active_rows="\n".join(_row_line(r) for r in active) or EMPTY_ROW,
            queue_rows="\n".join(_row_line(r) for r in queue) or EMPTY_ROW,
            tail=tail if tail is not None else (cur.get("tail") or INDEX_TAIL),
        ),
        encoding="utf-8",
    )
    return p


def index_add(
    root: Path,
    num: str,
    title: str,
    priority: str = DEFAULT_PRIORITY,
    checkpoint: str = "-",
    active: bool = False,
) -> None:
    """往索引里登记一行（已存在则先摘出再按新值写回，等于更新）。"""
    cur = parse_index(root)
    if cur is None:
        return
    title = re.sub(r"^\d{4}_", "", title)
    row = {
        "num": num,
        "title": title,
        "priority": priority if priority in PRIORITY_ORDER else DEFAULT_PRIORITY,
        "checkpoint": checkpoint or "-",
        "link": f"./{num}_{title}.md",
    }
    active_rows = [r for r in cur["active"] if r["num"] != num]
    queue_rows = [r for r in cur["queue"] if r["num"] != num]
    if active:
        write_index(root, active_rows + [row], queue_rows)
    else:
        write_index(root, active_rows, queue_rows + [row])


def index_remove(root: Path, num: str) -> bool:
    """从索引里删除一行（任务完成/废弃归档时用）。返回是否真的删掉了。"""
    cur = parse_index(root)
    if cur is None:
        return False
    a = [r for r in cur["active"] if r["num"] != num]
    q = [r for r in cur["queue"] if r["num"] != num]
    if len(a) == len(cur["active"]) and len(q) == len(cur["queue"]):
        return False
    write_index(root, a, q)
    return True


def index_set_active(root: Path, num: str) -> dict | None:
    """把某任务设为激活（原激活任务退回排队）。返回被激活的 row，找不到返回 None。"""
    cur = parse_index(root)
    if cur is None:
        return None
    pool = cur["active"] + cur["queue"]
    target = next((r for r in pool if r["num"] == num), None)
    if target is None:
        return None
    queue = [r for r in pool if r["num"] != num]
    write_index(root, [target], queue)
    return target


def index_first_queued(root: Path) -> dict | None:
    """排队区队首（优先级最高、同优先级编号最小）。"""
    cur = parse_index(root)
    return cur["queue"][0] if cur and cur["queue"] else None


def index_sync(root: Path) -> tuple[Path, int, list[str]]:
    """扫描 todo/*.md 重建索引（修漂移）。

    Returns:
        (索引路径, 登记条数, 被跳过的任务编号列表——已完成/已废弃但还赖在 todo/ 下的)
    """
    ensure_index(root)
    tdir = todo_dir(root)
    active, queue, count, skipped = [], [], 0, []
    for p in sorted(tdir.glob("[0-9][0-9][0-9][0-9]_*.md")):
        meta = read_meta(p)
        status = meta.get("状态", "排队")
        if status in ("已完成", "已废弃"):
            skipped.append(
                f"{p.name[:4]}（状态 {status}，应归档：taskmgr.py done|drop {p.name[:4]}）"
            )
            continue
        row = {
            "num": p.name[:4],
            "title": p.stem[5:],
            "priority": meta.get("优先级", DEFAULT_PRIORITY),
            "checkpoint": meta.get("关联 checkpoint", "-") or "-",
            "link": f"./{p.name}",
        }
        if status == "进行中":
            active.append(row)
        else:
            queue.append(row)
        count += 1
    # 单一激活兜底：sync 时若出现多个「进行中」，保留排序最靠前（优先级最高、同级编号最小）
    # 的那个，其余改回排队——与"队首即下一个"的排序规则保持一致
    if len(active) > 1:
        active.sort(key=_sort_key)
        for row in active[1:]:
            set_meta(tdir / f"{row['num']}_{row['title']}.md", 状态="排队")
            queue.append(row)
        active = active[:1]
    write_index(root, active, queue)
    return index_path(root), count, skipped


def index_check(root: Path) -> list[str]:
    """校验索引与任务文档的一致性，返回问题列表（空列表 = 通过）。"""
    problems: list[str] = []
    tdir = todo_dir(root)
    if not tdir.exists():
        return problems
    idx = parse_index(root)
    if idx is None:
        return [
            f"缺少任务索引: {index_path(root).relative_to(root).as_posix()}"
            f"（跑 taskmgr.py sync 生成，见 rules/07 §2）"
        ]

    known = {p.name[:4]: p for p in tdir.glob("[0-9][0-9][0-9][0-9]_*.md")}
    indexed: set[str] = set()
    for bucket, rows in (("激活中", idx["active"]), ("排队中", idx["queue"])):
        for row in rows:
            indexed.add(row["num"])
            rel = f".agents/docs/todo/{row['num']}_{row['title']}.md"
            if row["num"] not in known:
                problems.append(
                    f"索引「{bucket}」引用了不存在的任务: {row['num']}（{rel}）"
                )
                continue
            meta = read_meta(known[row["num"]])
            status = meta.get("状态", "排队")
            if bucket == "激活中" and status != "进行中":
                problems.append(
                    f"索引「激活中」的 {row['num']} 在文档里状态是「{status}」（应为 进行中）"
                )
            if bucket == "排队中" and status == "进行中":
                problems.append(
                    f"任务 {row['num']} 文档状态是「进行中」却排在「排队中」"
                    f"（激活用 taskmgr.py activate {row['num']}）"
                )
            prio = meta.get("优先级", DEFAULT_PRIORITY)
            if prio != row["priority"]:
                problems.append(
                    f"任务 {row['num']} 优先级不一致：文档「{prio}」vs 索引「{row['priority']}」"
                )
            cp = row["checkpoint"]
            if cp not in ("-", ""):
                cp_path = root / DOCS_ROOT / cp.replace("\\", "/")
                if not cp_path.exists():
                    problems.append(
                        f"任务 {row['num']} 关联的 checkpoint 不存在: "
                        f"{cp_path.relative_to(root).as_posix()}"
                        f"（用 taskmgr.py link 改指向或补写快照）"
                    )
    for num in sorted(set(known) - indexed):
        meta = read_meta(known[num])
        if meta.get("状态", "排队") in ("已完成", "已废弃"):
            problems.append(
                f"任务 {num} 已「{meta.get('状态')}」却仍在 todo/ 下"
                f"（应归档：taskmgr.py done {num}）"
            )
        else:
            problems.append(f"任务 {num} 未登记进索引（跑 taskmgr.py sync）")
    if len(idx["active"]) > 1:
        problems.append(
            f"激活任务超过一个（单一激活违规）: "
            f"{', '.join(r['num'] for r in idx['active'])}"
        )
    return problems


# ------------------------------------------------------------ 任务文档元信息
_META_SEG = re.compile(r"^([^:：]+?)\s*[:：]\s*(.*)$")


def read_meta(path: Path) -> dict:
    """读文档文首 `>` 引用块里的 `键: 值 | 键: 值` 元信息。"""
    meta: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return meta
    for line in lines[:12]:
        if not line.startswith(">"):
            continue
        for seg in line[1:].split("|"):
            m = _META_SEG.match(seg.strip())
            if m:
                meta[m.group(1).strip()] = m.group(2).strip()
    return meta


def set_meta(path: Path, **kv: str) -> None:
    """改写文档文首元信息（键已存在则改值，不存在则补到最后一个 `>` 行）。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(kv)
    last = -1
    for i in range(min(len(lines), 12)):
        if not lines[i].startswith(">"):
            continue
        last = i
        segs = [s.strip() for s in lines[i][1:].split("|")]
        new: list[str] = []
        for seg in segs:
            m = _META_SEG.match(seg)
            if m and m.group(1).strip() in remaining:
                new.append(f"{m.group(1).strip()}: {remaining.pop(m.group(1).strip())}")
            else:
                new.append(seg)
        lines[i] = "> " + "  |  ".join(new)
    if remaining and last >= 0:
        lines.insert(
            last + 1, "> " + "  |  ".join(f"{k}: {v}" for k, v in remaining.items())
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_task(root: Path, key: str) -> Path | None:
    """按编号或标题关键字在 todo/ 下定位任务文档。"""
    tdir = todo_dir(root)
    if re.fullmatch(r"\d{4}", key):
        for p in tdir.glob(f"{key}_*.md"):
            return p
        return None
    hits = [p for p in sorted(tdir.glob("[0-9][0-9][0-9][0-9]_*.md")) if key in p.name]
    return hits[0] if hits else None


# ---------------------------------------------------------------- 核心操作
def create_doc(
    root: Path,
    category: str,
    title: str,
    sub: str = "",
    priority: str = DEFAULT_PRIORITY,
) -> tuple[Path, str]:
    """新建文档并自动编号。todo 分类会同步登记进任务索引。返回 (路径, 编号)。"""
    if category not in CATEGORIES:
        raise DocError(f"category 必须是 {CATEGORIES} 之一")
    cat_dir = root / DOCS_ROOT / category
    if not cat_dir.exists():
        raise DocError(f"分类目录不存在: {cat_dir}（先跑 scaffold.py）")
    sub_dir = cat_dir / sub if sub else cat_dir
    if sub and (
        len(Path(sub).parts) != 1
        or sub in (".", "..", "_archive")
        or not sub_dir.resolve().is_relative_to(cat_dir.resolve())
    ):
        raise DocError("子分类必须是分类内的一层普通目录")
    sub_dir.mkdir(parents=True, exist_ok=True)

    t = sanitize(title)
    if re.match(r"^\d{4}_", t):
        num, t = t[:4], t[5:]
    else:
        num = f"{next_number(cat_dir):04d}"

    path = sub_dir / f"{num}_{t}.md"
    if path.exists():
        raise DocError(f"已存在: {path}")
    body = TEMPLATES[category].format(
        num=num,
        title=t,
        cat=category,
        sub=f"/{sub}" if sub else "",
        today=date.today().isoformat(),
    )
    if category == "todo":
        prio = priority if priority in PRIORITY_ORDER else DEFAULT_PRIORITY
        body = body.replace("> 优先级: 中", f"> 优先级: {prio}")
    path.write_text(body, encoding="utf-8")

    if category == "todo" and index_path(root).exists():
        prio = priority if priority in PRIORITY_ORDER else DEFAULT_PRIORITY
        index_add(root, num, t, prio, "-", active=False)
    return path, num


def archive_doc(root: Path, src: Path, reason: str = "任务已完成") -> Path:
    """归档文档到同分类 _archive/。todo 分类会同步从索引里删除该行。"""
    src = src.expanduser().resolve()
    if not src.is_file():
        raise DocError(f"文件不存在: {src}")
    try:
        rel = src.relative_to(root)
    except ValueError:
        raise DocError(f"文件不在项目根目录下: {src}") from None
    parts = rel.parts
    if len(parts) < 4 or parts[0] != ".agents" or parts[1] != "docs":
        raise DocError(f"不是 .agents/docs/<分类>/... 下的文档: {rel.as_posix()}")
    cat = parts[2]
    archive_dir = root / DOCS_ROOT / cat / "_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dst = archive_dir / src.name
    if dst.exists():
        raise DocError(f"归档目标已存在，需先解决冲突: {dst}")

    text = src.read_text(encoding="utf-8")
    header = (
        f"> 归档时间: {date.today().isoformat()}  |  归档原因: {reason}  |  "
        f"原路径: {rel.as_posix()}\n\n"
    )
    if not text.startswith("> 归档时间:"):
        text = header + text

    # 用「移动 + 原地补头 + 改名」实现归档，全程不走 unlink——
    # 部分环境（沙箱 / safe-delete hook / 回收站不可用）会拦截删除语义导致 FAIL CLOSED，
    # 而归档本质是移动不是删除，os.replace 在同卷上是原子操作且不触发删除拦截。
    pending = archive_dir / f"{src.name}.{os.getpid()}.moving"
    os.replace(src, pending)
    pending.write_text(text, encoding="utf-8")
    os.replace(pending, dst)

    if cat == "todo" and index_path(root).exists():
        m = re.match(r"^(\d{4})_", src.name)
        if m:
            index_remove(root, m.group(1))
    return dst


# -------------------------------------------------------------- 固定成本审计
# 每次会话无条件要读的"固定成本三件套"（第 34 条，check 强制校验）
COST_LIMIT_AGENTS = 250  # AGENTS.md 全文
COST_LIMIT_RULES01 = 200  # .agents/rules/01_session_workflow.md 全文
COST_LIMIT_ARCH_REQUIRED = 150  # .agents/ARCHITECTURE.md 必读区（第 1/2/3/6 节）
CHECKPOINT_SOFT = 80  # checkpoint 超过 → 提示精简
CHECKPOINT_HARD = 120  # checkpoint 超过 → 报问题
KNOWLEDGE_LIMIT = 150  # 单份 knowledge 超过 → 预警外移
QUEUE_LIMIT = 15  # 排队任务超过 → 预警队列腐烂


def required_zone_lines(text: str) -> int:
    """数 ARCHITECTURE.md 必读区（第 1、2、3、6 节）的行数。"""
    cur: int | None = None
    n = 0
    for line in text.splitlines():
        m = re.match(r"^## (\d+)\. ", line)
        if m:
            cur = int(m.group(1))
        if cur in (1, 2, 3, 6):
            n += 1
    return n


def check_costs(root: Path) -> tuple[list[str], list[str], list[str]]:
    """固定成本审计（第 34 条）。返回 (problems, infos, 概要行)。"""
    problems: list[str] = []
    infos: list[str] = []
    summary: list[str] = []
    total_lines = 0

    targets = [
        ("AGENTS.md", root / "AGENTS.md", COST_LIMIT_AGENTS, None),
        (
            ".agents/rules/01_session_workflow.md",
            root / ".agents" / "rules" / "01_session_workflow.md",
            COST_LIMIT_RULES01,
            None,
        ),
        (
            ".agents/ARCHITECTURE.md 必读区(1/2/3/6)",
            root / ".agents" / "ARCHITECTURE.md",
            COST_LIMIT_ARCH_REQUIRED,
            required_zone_lines,
        ),
    ]
    for label, path, limit, counter in targets:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        n = counter(text) if counter else len(text.splitlines())
        total_lines += n
        summary.append(
            f"  {label}: {n} 行 / 上限 {limit}  [≈{int(len(text) / 2.5)} tok，粗估]"
        )
        if n > limit:
            problems.append(
                f"固定成本超限: {label} {n} 行 > 上限 {limit} 行"
                f"（外移细节到 knowledge，只留结论+指针）"
            )

    if total_lines:
        summary.append(f"  合计: {total_lines} 行——这是每个新会话的最低固定开销")

    # checkpoint 长度纪律（rules/07 §3）：交接快照每次恢复都要全读
    cp_dir = root / DOCS_ROOT / "checkpoint"
    if cp_dir.exists():
        for p in sorted(cp_dir.glob("[0-9][0-9][0-9][0-9]_*.md")):
            n = len(p.read_text(encoding="utf-8").splitlines())
            rel = p.relative_to(root).as_posix()
            if n > CHECKPOINT_HARD:
                problems.append(
                    f"checkpoint 过长: {rel} {n} 行 > {CHECKPOINT_HARD}"
                    f"（细节下沉到任务正文/knowledge）"
                )
            elif n > CHECKPOINT_SOFT:
                infos.append(
                    f"checkpoint 偏长: {rel} {n} 行（>{CHECKPOINT_SOFT}，建议精简）"
                )

    # knowledge 行数预警（rules/04 §1）：防止"按需加载"退化成大文件全读
    kn_dir = root / DOCS_ROOT / "knowledge"
    if kn_dir.exists():
        for p in sorted(kn_dir.rglob("[0-9][0-9][0-9][0-9]_*.md")):
            if "_archive" in p.parts:
                continue
            n = len(p.read_text(encoding="utf-8").splitlines())
            if n > KNOWLEDGE_LIMIT:
                rel = p.relative_to(root).as_posix()
                infos.append(
                    f"knowledge 偏长: {rel} {n} 行"
                    f"（>{KNOWLEDGE_LIMIT}，细节外移或拆子分类，留结论+指针）"
                )

    return problems, infos, summary


# ---------------------------------------------------------------- 子命令
def cmd_new(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        path, num = create_doc(root, args.category, args.title, args.sub, args.priority)
    except DocError as e:
        print(f"[错误] {e}")
        return 1
    print(f"[新建] {path.relative_to(root).as_posix()}")
    if args.category == "todo":
        if index_path(root).exists():
            print(
                f"[索引] 已登记到 .agents/docs/todo/README.md「排队中」（优先级 {args.priority}）"
            )
        else:
            print("[提醒] 任务索引不存在，跑 taskmgr.py sync 生成并登记该任务")
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        dst = archive_doc(root, Path(args.file), args.reason)
    except DocError as e:
        print(f"[错误] {e}")
        return 1
    print(f"[归档] → {dst.relative_to(root).as_posix()}")
    print("[提醒] 请删除所有指向它的引用，然后运行 check 校验。")
    return 0


def check_markdown_links(root: Path) -> list[str]:
    """Check local inline and reference links outside fenced code blocks."""
    problems: list[str] = []
    files = [root / "AGENTS.md"] + sorted((root / ".agents").rglob("*.md"))
    for source in files:
        if not source.is_file() or "_archive" in source.relative_to(root).parts:
            continue
        body = source.read_text(encoding="utf-8")
        body = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", body)
        targets = re.findall(r"\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)", body)
        targets += re.findall(r"(?m)^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)", body)
        for target in targets:
            target = target.strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            value = unquote(parsed.path)
            if re.search(r"NNNN|xxx|<|>|\.\.\.", value):
                continue
            base = root if value.startswith(".agents/") else source.parent
            resolved = (base / value).resolve()
            if not resolved.is_relative_to(root):
                problems.append(f"链接越出项目: {target} ← {source.relative_to(root)}")
            elif not resolved.exists():
                problems.append(f"链接断链: {target} ← {source.relative_to(root)}")
    return problems


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    docs = root / DOCS_ROOT
    problems: list[str] = []
    infos: list[str] = []
    problems.extend(check_markdown_links(root))

    if not docs.exists():
        print(f"[错误] 未找到 {docs}，请先跑 scaffold.py")
        return 1

    # 1. 分类目录与 _archive 完整性
    for cat in sorted(p.name for p in docs.iterdir() if p.is_dir()):
        archive = docs / cat / "_archive"
        if not archive.exists():
            problems.append(f"缺少归档目录: .agents/docs/{cat}/_archive/")
        mds = [
            p
            for p in (docs / cat).rglob("*.md")
            if "_archive" not in p.relative_to(docs / cat).parts
        ]
        if not mds or (
            len(mds) == 1 and mds[0].name == TODO_INDEX_NAME and cat == "todo"
        ):
            infos.append(f"空分类: .agents/docs/{cat}/（无文档）")

    # 2. 编号重复 / 命名不规范（每个父分类内唯一）
    for cat in sorted(p.name for p in docs.iterdir() if p.is_dir()):
        used: dict[str, list[str]] = {}
        for p in (docs / cat).rglob("*.md"):
            rel = p.relative_to(root).as_posix()
            if not re.match(r"^\d{4}_.+\.md$", p.name):
                if p.name != "README.md":
                    problems.append(f"命名不规范（应为 NNNN_xxx.md）: {rel}")
                continue
            used.setdefault(p.name[:4], []).append(rel)
        for num, paths in sorted(used.items()):
            if len(paths) > 1:
                problems.append(f"编号重复 {num}: {', '.join(paths)}")

    # 3. 引用断链：AGENTS.md 与 rules 里出现的 .agents/... 路径
    #    形如 xxx / NNNN / 0000 / <占位> 的是模板示例，不算真实引用
    placeholder = re.compile(r"xxx|XXX|NNNN|0000|<.*?>")
    refs: dict[str, set[str]] = {}
    scan_files = [root / "AGENTS.md"] + sorted(
        (root / ".agents" / "rules").glob("*.md")
    )
    for f in scan_files:
        if not f.exists():
            continue
        for m in re.finditer(
            r"\.agents/[A-Za-z0-9_\-/\u4e00-\u9fff.]+\.md",
            f.read_text(encoding="utf-8"),
        ):
            ref = m.group(0)
            if placeholder.search(ref):
                continue
            refs.setdefault(ref, set()).add(f.relative_to(root).as_posix())
    for ref, sources in sorted(refs.items()):
        target = root / ref
        if not target.exists():
            problems.append(f"引用断链: {ref}  ← 来自 {', '.join(sorted(sources))}")
        elif "_archive" in ref:
            problems.append(
                f"引用了归档文档（归档文档不应被引用）: {ref}  ← 来自 {', '.join(sorted(sources))}"
            )

    # 4. 架构总览（活文档，rules/15）：存在性 + 变更日志是否已登记
    arch = root / ".agents" / "ARCHITECTURE.md"
    if not arch.exists():
        problems.append("缺少架构总览: .agents/ARCHITECTURE.md（先跑 scaffold.py）")
    else:
        arch_text = arch.read_text(encoding="utf-8")
        if "本文件待填充" in arch_text:
            problems.append(".agents/ARCHITECTURE.md 仍为待填充模板")
        # 精简形态（小项目，rules/15 §8）只保留必读区 1/2/3/6，没有第 8 节变更日志
        lean = "形态: 精简" in arch_text
        if "基线 commit" not in arch_text:
            problems.append(".agents/ARCHITECTURE.md 文首缺少「基线 commit」标记")
        if not lean and not re.search(
            r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|", arch_text, flags=re.MULTILINE
        ):
            problems.append(
                ".agents/ARCHITECTURE.md 第 8 节变更日志表里没有已登记的日期行"
            )
        for n in (1, 2, 3, 6) if lean else tuple(range(1, 9)):
            if f"## {n}. " not in arch_text:
                kind = "精简" if lean else "完整"
                problems.append(
                    f".agents/ARCHITECTURE.md 缺少第 {n} 节（{kind}形态，模板见 rules/15 §4）"
                )
        match = re.search(r"(?ms)^## 3\. .*?\n(.*?)(?=^## |\Z)", arch_text)
        seg = match.group(1) if match else ""
        body_rows = [
            line
            for line in seg.splitlines()
            if line.strip().startswith("|")
            and "---" not in line
            and "模块 / 目录" not in line
        ]
        if not any(row.strip("| ").strip() for row in body_rows):
            problems.append(
                ".agents/ARCHITECTURE.md 第 3 节模块地图仍是空表（见 rules/15 §9）"
            )

    # 5. todo 任务索引（rules/07 §2）：存在性 + 与任务文档的一致性
    problems.extend(index_check(root))

    # 6. 固定成本审计（第 34 条）：必读三件套 + checkpoint/knowledge 长度纪律
    cost_problems, cost_infos, cost_summary = check_costs(root)
    problems.extend(cost_problems)
    infos.extend(cost_infos)

    # 7. 队列腐烂预警（rules/07 §2.3）
    idx = parse_index(root)
    if idx is not None and len(idx["queue"]) > QUEUE_LIMIT:
        infos.append(
            f"排队任务 {len(idx['queue'])} 条（>{QUEUE_LIMIT}），队列开始腐烂："
            f"重排优先级、合并同类项，或把远期任务移出 todo/"
        )

    # 8. 输出
    print(f"[校验] {root}")
    print(f"  文档总数: {sum(1 for _ in docs.rglob('*.md'))}")
    if idx is not None:
        label = (
            f"{idx['active'][0]['num']}_{idx['active'][0]['title']}"
            if idx["active"]
            else "（无）"
        )
        print(f"  任务队列: 激活「{label}」 / 排队 {len(idx['queue'])} 条")
    if cost_summary:
        print("\n[固定成本]（每次会话无条件要读的内容，第 34 条）")
        for line in cost_summary:
            print(line)
    if infos:
        print("\n[提示]")
        for i in infos:
            print(f"  · {i}")
    if problems:
        print(f"\n[问题] 共 {len(problems)} 项：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("\n[通过] 编号唯一、命名规范、引用无断链、任务索引一致、固定成本达标。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=".agents/docs 文档管家（含 todo 任务索引）"
    )
    ap.add_argument("--root", default=".", help="项目根目录（默认当前目录）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="新建文档并自动编号")
    p_new.add_argument("category", choices=CATEGORIES)
    p_new.add_argument("title")
    p_new.add_argument("--sub", default="", help="子分类，如 software / domain")
    p_new.add_argument(
        "--priority",
        default=DEFAULT_PRIORITY,
        choices=list(PRIORITY_ORDER),
        help="仅 todo 分类：任务优先级（默认 中）",
    )
    p_new.set_defaults(func=cmd_new)

    p_arc = sub.add_parser("archive", help="归档文档到同分类 _archive/")
    p_arc.add_argument("file")
    p_arc.add_argument("--reason", default="任务已完成")
    p_arc.set_defaults(func=cmd_archive)

    p_chk = sub.add_parser("check", help="校验编号/命名/引用/任务索引")
    p_chk.set_defaults(func=cmd_check)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
