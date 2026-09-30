#!/usr/bin/env python3
"""todo 任务系统调度器（`.agents/docs/todo/` 的唯一入口）。

索引文件 `.agents/docs/todo/README.md` 是**唯一调度真源**：
找任务、排优先级、决定下一个做什么，都只读它一个文件，不要 ls 目录、不要逐个读任务文档。

用法:
    python taskmgr.py list [--root R]                         # 一屏看完：激活任务 + 排队清单
    python taskmgr.py add "<标题>" [--priority 高|中|低] [--root R]
    python taskmgr.py activate <编号|关键字> [--root R]        # 激活（原激活任务自动退回排队）
    python taskmgr.py priority <编号|关键字> <高|中|低> [--root R]
    python taskmgr.py link <编号|关键字> <checkpoint 相对路径> [--root R]
    python taskmgr.py done <编号|关键字> [--reason 原因] [--no-next] [--root R]   # 完成→归档→出表→激活队首
    python taskmgr.py drop <编号|关键字> [--reason 原因] [--root R]               # 废弃，其余同上
    python taskmgr.py sync [--root R]                          # 扫描任务文档重建索引（修漂移）
    python taskmgr.py check [--root R]                         # 校验索引与任务文档一致性

「继续任务」时先跑 `list`，它给出的激活任务 + 关联 checkpoint 就是恢复点。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from newdoc import (  # noqa: E402
    DEFAULT_PRIORITY,
    PRIORITY_ORDER,
    DocError,
    archive_doc,
    create_doc,
    ensure_index,
    find_task,
    index_check,
    index_first_queued,
    index_set_active,
    index_sync,
    parse_index,
    read_meta,
    set_meta,
    todo_dir,
    write_index,
)

TODO_REL = ".agents/docs/todo"


class TaskError(Exception):
    """任务操作的可预期错误。"""


def rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def require_index(root: Path) -> dict:
    """取索引；不存在则生成空的，todo 目录不存在直接报错。"""
    if not todo_dir(root).exists():
        raise TaskError(f"目录不存在: {todo_dir(root)}（先跑 scaffold.py）")
    if parse_index(root) is None:
        ensure_index(root)
    idx = parse_index(root)
    if idx is None:  # pragma: no cover - 兜底
        raise TaskError(f"无法读写任务索引 {TODO_REL}/README.md")
    return idx


def require_task(root: Path, key: str) -> Path:
    p = find_task(root, key)
    if p is None:
        raise TaskError(f"todo/ 下找不到任务: {key}（用 list 看现有任务）")
    return p


def is_active_num(idx: dict, num: str) -> bool:
    return bool(idx["active"]) and idx["active"][0]["num"] == num


def pool_of(idx: dict) -> list[dict]:
    return idx["active"] + idx["queue"]


def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    idx = require_index(root)
    print(f"[todo 索引] {TODO_REL}/README.md")
    if idx["active"]:
        a = idx["active"][0]
        print("\n激活中（1，同时最多一个）")
        print(f"  {a['num']}  [{a['priority']}]  {a['title']}")
        print(f"       任务文档: {TODO_REL}/{a['num']}_{a['title']}.md")
        print(f"       关联快照: {a['checkpoint']}")
    else:
        print("\n激活中：（空）")
    print(f"\n排队中（{len(idx['queue'])}，按 优先级 → 编号）")
    if idx["queue"]:
        for r in idx["queue"]:
            print(
                f"  {r['num']}  [{r['priority']}]  {r['title']}   checkpoint: {r['checkpoint']}"
            )
    else:
        print("  （空）")

    print("\n下一步：")
    if idx["active"]:
        a = idx["active"][0]
        cp = a["checkpoint"]
        extra = f"，再读 .agents/docs/{cp}" if cp not in ("-", "") else ""
        print(
            f"  读 {TODO_REL}/{a['num']}_{a['title']}.md{extra}，从「进行中 / 下一步」继续。"
        )
    elif idx["queue"]:
        print(
            f"  激活位为空：先 activate {idx['queue'][0]['num']}，再读它的任务文档继续。"
        )
    else:
        print("  队列是空的——没有排队任务，去问用户要做什么，不要自己从代码里猜一个。")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    require_index(root)
    path, num = create_doc(root, "todo", args.title, priority=args.priority)
    print(f"[新建] {rel(root, path)}")
    print(f"[索引] 已登记进「排队中」（优先级 {args.priority}）")
    print(
        "[提醒] 去补全任务文档的「背景」与「目标（验收标准）」——空壳任务等于没有任务。"
    )
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    idx = require_index(root)
    path = require_task(root, args.key)
    num = path.name[:4]

    if is_active_num(idx, num):
        print(f"[跳过] {num} 已经是激活任务")
        return 0
    if idx["active"]:
        old = idx["active"][0]["num"]
        old_path = find_task(root, old)
        if old_path:
            set_meta(old_path, 状态="排队")
        print(f"[退回] {old} → 排队中")

    row = index_set_active(root, num)
    if row is None:
        raise TaskError(f"索引里没有 {num}，跑 taskmgr.py sync 重建索引")
    set_meta(path, 状态="进行中")
    print(f"[激活] {num}_{row['title']}（优先级 {row['priority']}）")
    print(f"  任务文档: {TODO_REL}/{path.name}")
    if row["checkpoint"] not in ("-", ""):
        print(f"  关联快照: .agents/docs/{row['checkpoint']}")
    else:
        print("  关联快照: 无（中/大任务记得建 checkpoint 并 link 过来）")
    return 0


def cmd_priority(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if args.value not in PRIORITY_ORDER:
        raise TaskError(f"优先级必须是 高 / 中 / 低，收到: {args.value}")
    idx = require_index(root)
    path = require_task(root, args.key)
    num = path.name[:4]

    set_meta(path, 优先级=args.value)
    pool = pool_of(idx)
    target = next((r for r in pool if r["num"] == num), None)
    if target is None:
        raise TaskError(f"索引里没有 {num}，跑 taskmgr.py sync 重建索引")
    target["priority"] = args.value
    rest = [r for r in pool if r["num"] != num]
    if is_active_num(idx, num):
        write_index(root, [target], rest)
    else:
        write_index(root, idx["active"], rest + [target])
    print(f"[优先级] {num} → {args.value}（文档与索引均已更新，排队区已重排）")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    idx = require_index(root)
    path = require_task(root, args.key)
    num = path.name[:4]

    cp = args.checkpoint.replace("\\", "/").replace(".agents/docs/", "")
    if cp.startswith("./"):
        cp = cp[2:]
    set_meta(path, **{"关联 checkpoint": cp})

    pool = pool_of(idx)
    target = next((r for r in pool if r["num"] == num), None)
    if target is not None:
        target["checkpoint"] = cp
        rest = [r for r in pool if r["num"] != num]
        if is_active_num(idx, num):
            write_index(root, [target], rest)
        else:
            write_index(root, idx["active"], rest + [target])
    print(f"[关联] {num} → {cp}")
    return 0


def _finish(args: argparse.Namespace, status: str) -> int:
    root = Path(args.root).resolve()
    idx = require_index(root)
    path = require_task(root, args.key)
    num = path.name[:4]

    if not is_active_num(idx, num):
        active = idx["active"][0]["num"] if idx["active"] else "（无）"
        print(f"[警告] {num} 不是当前激活任务（激活位：{active}），仍按指定完成任务。")

    set_meta(path, 状态=status, 完成=args.date)
    dst = archive_doc(root, path, args.reason)
    print(f"[归档] → {rel(root, dst)}（状态：{status}）")
    print(f"[出表] 已从 {TODO_REL}/README.md 删除 {num} 这一行")

    if args.no_next:
        return 0
    # 只有当没有其他任务处于「进行中」时才自动接档（防双激活）
    others = [
        p
        for p in sorted(todo_dir(root).glob("[0-9][0-9][0-9][0-9]_*.md"))
        if read_meta(p).get("状态") == "进行中"
    ]
    if others:
        print(
            "[提示] 仍有「进行中」任务："
            + ", ".join(p.name[:4] for p in others)
            + "——不自动接档。"
        )
        return 0
    nxt = index_first_queued(root)
    if nxt is None:
        print("[提示] 队列已空，没有下一个任务。")
        return 0
    npath = todo_dir(root) / f"{nxt['num']}_{nxt['title']}.md"
    if npath.exists():
        set_meta(npath, 状态="进行中")
        index_set_active(root, nxt["num"])
        print(f"[自动激活] {nxt['num']}_{nxt['title']}（优先级 {nxt['priority']}）")
        cp = nxt["checkpoint"]
        extra = f" 与 .agents/docs/{cp}" if cp not in ("-", "") else ""
        print(f"  下一步：读 {TODO_REL}/{npath.name}{extra}")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    return _finish(args, "已完成")


def cmd_drop(args: argparse.Namespace) -> int:
    return _finish(args, "已废弃")


def cmd_sync(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    p, n, skipped = index_sync(root)
    print(f"[同步] {rel(root, p)} 已按 todo/ 下的任务文档重建，登记 {n} 条")
    print("  以**任务文档文首元信息**为准，保留「说明」小节，覆盖两张表。")
    if skipped:
        print(f"[警告] {len(skipped)} 个任务标了完成/废弃却还赖在 todo/ 下，未进索引：")
        for s in skipped:
            print(f"  - {s}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    problems = index_check(root)
    if not problems:
        print(f"[通过] {TODO_REL}/README.md 与任务文档一致。")
        return 0
    print(f"[问题] 共 {len(problems)} 项：")
    for p in problems:
        print(f"  ✗ {p}")
    print("\n  修法：按提示逐条补；搞不清就 taskmgr.py sync 以任务文档为准重建索引。")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="todo 任务系统调度器")
    ap.add_argument("--root", default=".", help="项目根目录（默认当前目录）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="一屏输出激活任务与排队清单").set_defaults(
        func=cmd_list
    )

    p_add = sub.add_parser("add", help="新建任务并登记进索引")
    p_add.add_argument("title")
    p_add.add_argument(
        "--priority", default=DEFAULT_PRIORITY, choices=list(PRIORITY_ORDER)
    )
    p_add.set_defaults(func=cmd_add)

    p_act = sub.add_parser("activate", help="激活任务（旧的自动退回排队）")
    p_act.add_argument("key", help="编号（0007）或标题关键字")
    p_act.set_defaults(func=cmd_activate)

    p_pri = sub.add_parser("priority", help="改优先级并重排索引")
    p_pri.add_argument("key")
    p_pri.add_argument("value", choices=list(PRIORITY_ORDER))
    p_pri.set_defaults(func=cmd_priority)

    p_link = sub.add_parser("link", help="关联 checkpoint（如 checkpoint/0004_x.md）")
    p_link.add_argument("key")
    p_link.add_argument("checkpoint")
    p_link.set_defaults(func=cmd_link)

    for name, helptext, reason in (
        ("done", "完成任务：归档 + 出表 + 自动激活队首", "任务已完成"),
        ("drop", "废弃任务：归档 + 出表 + 自动激活队首", "任务已废弃"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("key")
        p.add_argument("--reason", default=reason)
        p.add_argument(
            "--date", default=date.today().isoformat(), help="完成日期（默认今天）"
        )
        p.add_argument("--no-next", action="store_true", help="完成后不自动激活下一个")
        p.set_defaults(func=cmd_done if name == "done" else cmd_drop)

    sub.add_parser("sync", help="从任务文档重建索引").set_defaults(func=cmd_sync)
    sub.add_parser("check", help="校验索引与任务文档一致性").set_defaults(
        func=cmd_check
    )

    args = ap.parse_args()
    try:
        return args.func(args)
    except (DocError, TaskError) as e:
        print(f"[错误] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
