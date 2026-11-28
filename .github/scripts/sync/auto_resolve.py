#!/usr/bin/env python3
"""自动解决"双方均只新增内容"的 XML 冲突：两侧新增内容全部保留（并集）。

仅在以下条件全部成立时才改写文件，否则保持冲突并留给人工处理：
  * 文件是 AndroidManifest.xml 或 res/values*/ 下的资源 XML；
  * 每个冲突块都带空的 base 段（diff3），即上游与本仓库都只是在同一位置插入了内容，
    没有任何一方修改或删除了共同祖先的行；
  * 按"上游在前 / 自定义在前"两种顺序生成的候选文件中，至少一个通过结构校验
    （XML 良构；manifest 的权限声明必须直接位于 <manifest> 下；资源名不得重复）。

自动合并的结果仍需通过后续的保全校验、编译和单元测试闸门后才会进入 main。
"""
import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ALLOWED_PATH = re.compile(r"(^|/)(AndroidManifest\.xml|res/values[^/]*/[^/]+\.xml)$")
OURS, BASE, SEP, THEIRS = "<<<<<<< ", "||||||| ", "=======", ">>>>>>> "
UPSTREAM, CUSTOM = "upstream", "custom"
PREFERENCE = [(UPSTREAM, CUSTOM), (CUSTOM, UPSTREAM)]
EXIT_REMAIN = 3
PERMISSION_TAGS = ("uses-permission", "permission")


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True)


def unmerged_paths():
    out = git("diff", "--name-only", "--diff-filter=U", "-z").stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


def parse(text):
    """把带 diff3 冲突标记的文本拆成 (行列表, 冲突块列表)；行列表中的 None 表示冲突块位置。

    结构不符合预期（缺少 base 段、标记嵌套、标记未闭合）时返回 None。
    """
    lines, hunks, hunk, state = [], [], None, "outside"
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        if state == "outside":
            if body.startswith(OURS):
                hunk, state = {"ours": [], "base": None, "theirs": []}, "ours"
            elif body.startswith((BASE, SEP, THEIRS)):
                return None
            else:
                lines.append(raw)
        elif state == "ours":
            if body.startswith(BASE):
                hunk["base"], state = [], "base"
            else:
                hunk["ours"].append(raw)
        elif state == "base":
            if body == SEP:
                state = "theirs"
            else:
                hunk["base"].append(raw)
        elif state == "theirs":
            if body.startswith(THEIRS):
                hunks.append(hunk)
                lines.append(None)
                hunk, state = None, "outside"
            else:
                hunk["theirs"].append(raw)
    if state != "outside":
        return None
    return lines, hunks


def structure_error(path, text):
    """返回结构校验错误描述；None 表示通过。"""
    try:
        root = ET.fromstring(text.lstrip("\ufeff").encode("utf-8"))
    except ET.ParseError as error:
        return f"XML 解析失败: {error}"
    if path.endswith("AndroidManifest.xml"):
        for parent in root.iter():
            if parent is not root and any(c.tag.startswith(PERMISSION_TAGS) for c in parent):
                return "权限声明出现在 <application> 等非 <manifest> 直接子节点的位置"
        if root.find("application") is None:
            return "缺少 <application>"
    if "/res/values" in path:
        names = [(e.tag, e.get("name")) for e in root.iter() if e.get("name")]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            return f"资源名重复: {duplicates[:5]}"
    return None


def render(lines, hunks, order):
    # 合并方向是 `git merge upstream/master`：ours = 本仓库（自定义），theirs = 上游
    side_key = {UPSTREAM: "theirs", CUSTOM: "ours"}
    first, second = order
    out, index = [], 0
    for line in lines:
        if line is None:
            out.extend(hunks[index][side_key[first]])
            out.extend(hunks[index][side_key[second]])
            index += 1
        else:
            out.append(line)
    return "".join(out)


def resolve(path, text):
    """返回 (说明, 新内容 或 None)。"""
    if not ALLOWED_PATH.search(path):
        return "不属于可自动处理的 XML 文件", None
    parsed = parse(text)
    if parsed is None:
        return "冲突标记结构不符合预期", None
    lines, hunks = parsed
    if any(h["base"] is None or h["base"] for h in hunks):
        return "存在修改或删除共同祖先行的冲突块，需要人工判断", None
    failures = []
    for order in PREFERENCE:
        label = " + ".join("上游" if side == UPSTREAM else "自定义" for side in order)
        merged = render(lines, hunks, order)
        error = structure_error(path, merged)
        if error is None:
            return f"保留双方新增内容，顺序: {label}", merged
        failures.append(f"{label}: {error}")
    return "所有合并顺序均未通过结构校验", None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="写回文件并 git add；默认只演练不改文件")
    args = parser.parse_args()
    remaining = []
    for path in unmerged_paths():
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError) as error:
            print(f"KEEP  {path}: 无法读取 ({error})")
            remaining.append(path)
            continue
        note, merged = resolve(path, text)
        if merged is None:
            print(f"KEEP  {path}: {note}")
            remaining.append(path)
            continue
        print(f"SOLVE {path}: {note}")
        if args.apply:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(merged)
            git("add", "--", path)
    if remaining:
        print("::error::仍存在无法自动解决的冲突: " + ", ".join(remaining))
        sys.exit(EXIT_REMAIN)
    sys.exit(0)


if __name__ == "__main__":
    main()
