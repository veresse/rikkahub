#!/usr/bin/env python3
"""校验合并上游后，本仓库的自定义修改仍然完整。

两类检查：
  1. 挂载点标记（fork-markers.tsv）：自定义功能接入上游代码的关键位置仍然存在。
     上游重构了这些位置时，自定义代码即使还在也可能不再生效，必须人工重新接入。
  2. 自定义新增行逐行保留：把合并前 fork 相对共同祖先新增的每一行，与合并结果逐一比对。
     合并（含自动解决冲突）不得丢失任何一行自定义内容。

用法：
  verify_fork.py --markers <tsv> --merged <rev> [--base <rev> --fork <rev>]
退出码：0 通过；1 存在丢失，必须人工处理。
"""
import argparse
import re
import subprocess
import sys

MAX_REPORT = 20


def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True)


def load_markers(path):
    markers = []
    with open(path, encoding="utf-8-sig") as handle:
        for number, raw in enumerate(handle, 1):
            line = raw.rstrip("\r\n")
            if not line.strip() or line.startswith("#"):
                continue
            columns = line.split("\t")
            if columns[0] == "exists" and len(columns) == 2:
                markers.append((number, columns[0], columns[1], None))
            elif columns[0] == "contains" and len(columns) == 3:
                markers.append((number, columns[0], columns[1], columns[2]))
            else:
                raise SystemExit(f"{path}:{number}: 格式应为 exists\\t路径 或 contains\\t路径\\t标记")
    return markers


def blob(rev, path):
    result = git("show", f"{rev}:{path}", check=False)
    return result.stdout.decode("utf-8", "replace") if result.returncode == 0 else None


def check_markers(markers, merged):
    problems = []
    for number, kind, path, needle in markers:
        content = blob(merged, path)
        if content is None:
            problems.append((path, f"标记表第 {number} 行：自定义文件不存在于合并结果"))
        elif needle is not None and needle not in content:
            problems.append((path, f"标记表第 {number} 行：找不到挂载点标记 {needle!r}"))
    return problems


def significant(line):
    text = line.strip()
    return len(text) >= 5 and re.search(r"[0-9A-Za-z\u4e00-\u9fff]", text) is not None


def fork_added_lines(base, fork):
    """返回 {路径: [自定义新增的有效行]}（按 rstrip 后的文本比较）。"""
    diff = git("-c", "core.quotePath=false", "diff", "--no-color", "--no-abbrev", "-U0", base, fork).stdout.decode("utf-8", "replace")
    added, current = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = None if line == "+++ /dev/null" else line[6:]
            if current is not None:
                added.setdefault(current, [])
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            text = line[1:].rstrip()
            if significant(text):
                added[current].append(text)
    return added


def check_preserved(added, merged):
    problems = []
    for path, lines in sorted(added.items()):
        content = blob(merged, path)
        if content is None:
            problems.append((path, f"自定义修改过的文件在合并结果中不存在（上游删除或重命名），涉及 {len(lines)} 行自定义内容"))
            continue
        present = {text.strip() for text in content.splitlines()}
        missing = [text for text in lines if text.strip() not in present]
        if missing:
            sample = "; ".join(text.strip() for text in missing[:5])
            problems.append((path, f"{len(missing)}/{len(lines)} 行自定义新增内容丢失，例如: {sample}"))
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--markers", required=True, help="fork-markers.tsv 路径")
    parser.add_argument("--merged", required=True, help="合并结果版本（rev）")
    parser.add_argument("--base", help="共同祖先（与 --fork 同时提供时启用逐行保留检查）")
    parser.add_argument("--fork", help="合并前的本仓库版本")
    args = parser.parse_args()
    merged = git("rev-parse", "--verify", f"{args.merged}^{{commit}}").stdout.decode().strip()

    problems = check_markers(load_markers(args.markers), merged)
    checked = 0
    if args.base and args.fork:
        added = fork_added_lines(args.base, args.fork)
        checked = sum(len(lines) for lines in added.values())
        problems += check_preserved(added, merged)

    if problems:
        print(f"::error::合并后自定义修改校验失败，共 {len(problems)} 处问题，禁止进入 main")
        for path, message in problems[:MAX_REPORT]:
            print(f"::error file={path}::{message}")
        for path, message in problems:
            print(f"  ✗ {path}: {message}")
        print("提示：若上游删除或重命名了自定义文件，或重构了挂载点，需要人工把自定义功能重新接入后再合并。")
        sys.exit(1)
    print(f"✓ 自定义修改校验通过：挂载点标记齐全，逐行保留检查覆盖 {checked} 行自定义新增内容")
    sys.exit(0)


if __name__ == "__main__":
    main()
