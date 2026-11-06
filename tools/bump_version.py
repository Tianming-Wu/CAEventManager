#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推进版本号。

    python tools/bump_version.py             # 只推进第四段（构）——每次上线都动它
    python tools/bump_version.py patch       # 第三段（修）+1
    python tools/bump_version.py minor       # 第二段（次）+1
    python tools/bump_version.py major       # 第一段（主）+1
    python tools/bump_version.py --show      # 只看现在是几

版本号写成四段 ``主.次.修.构``：

* **主** 架构或兼容性变了（老的模块、预设、数据库要跟着动）
* **次** 一批新功能落地
* **修** 行为变化或一批修复
* **构** **只增不减**：任何一段推进它都跟着 +1，永远不会被清零。所以它是这台机器上
  "第几次发布"，可以当作唯一的部署序号（大致等于提交数，但不依赖版本控制系统）

规矩：每次提交并上线都要推进，至少推进「构」。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "app.py")
# VERSION 那一行里最后一个 "主.次.修.构" 才是版本号：它前面可能还有
# os.environ.get("LOTTERY_VERSION") or 这样的写法
PATTERN = re.compile(r"""^(VERSION = .*?)(\d+)\.(\d+)\.(\d+)\.(\d+)(".*)$""", re.M)


def current():
    with open(APP, encoding="utf-8") as fh:
        text = fh.read()
    match = PATTERN.search(text)
    if match is None:
        raise SystemExit('app.py 里没有四段版本号：VERSION = "1.2.3.4"')
    return text, [int(p) for p in match.groups()[1:5]]


def main(argv):
    text, parts = current()
    if "--show" in argv or "-s" in argv:
        print(".".join(str(p) for p in parts))
        return 0
    which = (argv[1] if len(argv) > 1 else "build").lower()
    # 第四段只增不减：它是"第几次发布"，推进任何一段都要 +1
    parts[3] += 1
    if which in ("build", "b", "构"):
        pass
    elif which in ("patch", "p", "修"):
        parts[2] += 1
    elif which in ("minor", "m", "次"):
        parts[1] += 1
        parts[2] = 0
    elif which in ("major", "M", "主"):
        parts[0] += 1
        parts[1] = parts[2] = 0
    else:
        raise SystemExit("推进哪一段？major / minor / patch / build")
    new = ".".join(str(p) for p in parts)
    text = PATTERN.sub(lambda m: "%s%s%s" % (m.group(1), new, m.group(6)), text, count=1)
    with open(APP, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("版本号 ->", new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
