# -*- coding: utf-8 -*-
"""平台残留审计：模块搬出去之后，平台上还剩哪些玩法的东西。

    python tools/audit_platform.py            # 只看报告
    python tools/audit_platform.py --strict   # 有残留就返回 1（将来接 CI 用）

两件事：

1. **共用件里谁在用哪个导出**。`assets/js/common.js`（主站）与 `assets/js/event.js`
   （活动族）是运行时库：只有模块在用的，说明它是玩法的东西，该搬进模块；谁都不用的，
   **只作提示**——很可能是刚写下还没接上的 WIP 或给模块预留的钩子，要不要删由人判断。
2. **平台代码里的玩法词**。`app.py`、`core/`、`assets/js/`、`pages/` 里出现奖票、签到、
   抽奖、学生这类词，就是模块化还没收干净的地方。模块自己的目录不在扫描范围内。

模块离不开平台，所以没法靠"把模块拿走再跑"来验证；这个脚本就是那条替代的验证线。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LIBS = {
    "LT": "assets/js/event.js",
    "PX": "assets/js/common.js",
}

PLATFORM_JS = ("admin.js", "panel.js", "login.js", "register.js",
               "signup.js", "print.js", "screen.js")
PLATFORM_DIRS = ("pages",)
THEME_DIR = "theme"
MODULE_DIRS = ("builtin/modules", "modules")
SCAN_EXT = (".js", ".html")

# 玩法词：出现在平台代码里就说明这块还没搬干净
# 只收名词：draw / ticket / student 这类词在平台里另有含义（画图、工单、名单），
# 收进来只会把真正的残留淹掉
WORDS = ("奖票", "抽奖", "签到", "学生", "任务一", "任务二",
         "nwuca.lottery", "KIND_LABEL")

# 这几个是搜索引擎与格式化部件：平台提供、模块给词表，只有模块在用是正常的
KEEP = {"fmtTime", "score", "parseQuery", "queryRecords", "tagsFromSpec"}
WORD_SCAN = (
    ("app.py", None),
    ("core", (".py",)),
    ("assets/js", (".js",)),
    ("pages", (".html",)),
)


def read(path):
    try:
        return io.open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        return ""


def exported(lib, path):
    text = read(os.path.join(ROOT, path))
    match = re.search(r"window\.%s\s*=\s*\{(.*?)\n  \};" % lib, text, re.S)
    if not match:
        return []
    return re.findall(r"^\s{4}([A-Za-z_][A-Za-z0-9_]*)\s*:", match.group(1), re.M)


def surface_files(kind):
    out = []
    if kind == "platform":
        for d in PLATFORM_DIRS:
            base = os.path.join(ROOT, d)
            if os.path.isdir(base):
                out += [os.path.join(base, n) for n in sorted(os.listdir(base))
                        if n.endswith(SCAN_EXT)]
        for name in PLATFORM_JS:
            full = os.path.join(ROOT, "assets", "js", name)
            if os.path.isfile(full):
                out.append(full)
    elif kind == "theme":
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, THEME_DIR)):
            out += [os.path.join(dirpath, n) for n in files if n.endswith(SCAN_EXT)]
    else:
        for d in MODULE_DIRS:
            for dirpath, _dirs, files in os.walk(os.path.join(ROOT, d)):
                out += [os.path.join(dirpath, n) for n in files if n.endswith(SCAN_EXT)]
    return out


def audit_symbols():
    print("== 共用件：谁在用哪个导出 ==")
    surfaces = {k: surface_files(k) for k in ("platform", "theme", "module")}
    print("   扫描：平台 %d、外观 %d、模块 %d 个文件"
          % (len(surfaces["platform"]), len(surfaces["theme"]), len(surfaces["module"])))
    leftover = 0
    for lib, path in LIBS.items():
        names = exported(lib, path)
        print("\n   %s（%s）%d 个导出" % (lib, path, len(names)))
        if not names:
            print("     ！读不出导出表")
            leftover += 1
            continue
        only_module, unused = [], []
        for name in names:
            pattern = re.compile(r"\b%s\.%s\b" % (lib, name))
            used = {}
            for kind, files in surfaces.items():
                hits = []
                for f in files:
                    rel = os.path.relpath(f, ROOT).replace("\\", "/")
                    if rel == path:
                        continue
                    if pattern.search(read(f)):
                        hits.append(rel)
                if hits:
                    used[kind] = hits
            if not used:
                unused.append(name)
            elif set(used) == {"module"} and name not in KEEP:
                only_module.append((name, used["module"]))
        if only_module:
            print("     只有模块在用（平台不该提供）：")
            for name, files in only_module:
                print("       %-14s %s" % (name, ", ".join(sorted(files)[:2])))
            leftover += len(only_module)
        if unused:
            # 没人用不等于死：可能是刚写下还没接上的 WIP，所以只提示、不算"待收"
            print("     暂时没有引用（可能是 WIP 或给模块预留，需人工判断）：%s"
                  % "、".join(unused))
        if not only_module and not unused:
            print("     干净")
    return leftover


def audit_words():
    print("\n== 平台代码里的玩法词 ==")
    hits = 0
    for where, exts in WORD_SCAN:
        full = os.path.join(ROOT, where)
        paths = []
        if os.path.isfile(full):
            paths = [full]
        elif os.path.isdir(full):
            for dirpath, _dirs, files in os.walk(full):
                for name in files:
                    if exts and not name.endswith(exts):
                        continue
                    paths.append(os.path.join(dirpath, name))
        for path in sorted(paths):
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            found = []
            for lineno, line in enumerate(read(path).splitlines(), 1):
                for word in WORDS:
                    if word in line:
                        found.append((lineno, word, line.strip()[:70]))
                        break
            if not found:
                continue
            print("\n   %s（%d 处）" % (rel, len(found)))
            for lineno, word, line in found[:5]:
                print("     %4d  [%s] %s" % (lineno, word, line))
            if len(found) > 5:
                print("           … 还有 %d 处" % (len(found) - 5))
            hits += len(found)
    print("\n   合计 %d 处" % hits)
    return hits


def main():
    strict = "--strict" in sys.argv
    left = audit_symbols()
    words = audit_words()
    total = left + words
    print("\n结论：共用件 %d 处、玩法词 %d 处，共 %d 处待收" % (left, words, total))
    print("       暂时没有引用的导出不计入：那是给人看的提示，不是待办")
    if strict and total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
