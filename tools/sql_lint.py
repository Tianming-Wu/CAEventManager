# -*- coding: utf-8 -*-
"""SQL 拼接审计：任何一处用变量拼出来的 SQL 都必须显式标记。

    python tools/sql_lint.py          # 报告
    python tools/sql_lint.py --strict # 有未标记的拼接就返回 1

参数一律走 `?` 占位符；只有"列名、表名、SET 片段"这类**结构**才可能被拼进语句。
这些地方必须是项目内的常量，并且在这一行标上 `sql-ok`，让 review 一眼看到。
新写一处没有标记的拼接，这个脚本就会报出来。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"tmp", "tools", ".git", "__pycache__", "data"}

SQL_WORD = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|REPLACE|PRAGMA|ALTER|CREATE|DROP)\b",
                      re.I)
LITERAL = re.compile(r"""([rubfRUBF]*)(["'])((?:\\.|(?!\2).)*)\2""")


def py_files():
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def is_interpolated(line, end):
    """字面量结束之后（同一行里）还有没有拼接。"""
    rest = line[end:].lstrip()
    if rest.startswith("%"):
        return True
    if rest.startswith("+"):
        return True
    if rest.startswith(".format("):
        return True
    return False


def main():
    strict = "--strict" in sys.argv
    problems = []
    marked = 0
    for path in py_files():
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        try:
            text = io.open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if "sql-ok" in line:
                if SQL_WORD.search(line):
                    marked += 1
                continue
            hit = False
            for match in LITERAL.finditer(line):
                prefix, _quote, body = match.group(1), match.group(2), match.group(3)
                if not SQL_WORD.search(body):
                    continue
                if "f" in prefix.lower():
                    hit = True
                    break
                if is_interpolated(line, match.end()):
                    hit = True
                    break
            if hit:
                problems.append((rel, lineno, line.strip()[:110]))

    if problems:
        print("未标记的 SQL 拼接（%d 处）：" % len(problems))
        for rel, lineno, text in problems:
            print("  %s:%d  %s" % (rel, lineno, text))
        print("\n如果拼进去的是项目内的常量（列名、表名、SET 片段），在这一行末尾加 `# sql-ok`；"
              "\n如果拼进去的是用户给的值，改成 ? 占位符。")
    else:
        print("SQL 拼接全部有标记（%d 处标记过的）。" % marked)
    if strict and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
