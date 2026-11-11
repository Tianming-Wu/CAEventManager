# -*- coding: utf-8 -*-
"""随机数：有硬件随机源就用它，没有就退回操作系统的 CSPRNG。

这台机器上可能挂着 `/dev/hwrng`（Linux 的硬件随机设备）。读得到就和 `os.urandom`
按字节异或混合——只用硬件的话，设备出问题时会悄悄退化成可预测的值；只用内核的话，
就等于浪费了手边那点真随机。混起来两头都不吃亏。

调用方拿到的仍然是字节与整数，不需要关心来源。测试里可以看 `source()`。
"""
import os
import random

HWRNG = "/dev/hwrng"

_state = {"source": None}


def _hw(n):
    """从硬件随机设备读 n 个字节；读不到返回 None。"""
    try:
        with open(HWRNG, "rb", buffering=0) as fh:
            blob = fh.read(n)
    except (OSError, ValueError):
        return None
    return blob if blob and len(blob) == n else None


def source():
    """这台机器上实际用的是哪一种：`hwrng` 或 `krng`。第一次调用时探测一次。"""
    if _state["source"] is None:
        _state["source"] = "hwrng" if _hw(16) is not None else "krng"
    return _state["source"]


def bytes(n):
    """n 个随机字节。硬件读得到就与内核的混在一起。"""
    n = int(n)
    if n <= 0:
        return b""
    blob = os.urandom(n)
    hw = _hw(n)
    if hw is not None:
        _state["source"] = "hwrng"
        blob = bytes(a ^ b for a, b in zip(blob, hw))
    return blob


def below(limit):
    """[0, limit) 的整数，无偏（拒绝采样，不取模）。"""
    limit = int(limit)
    if limit <= 0:
        raise ValueError("上限要大于 0")
    span = limit.bit_length()
    need = (span + 7) // 8
    while True:
        value = int.from_bytes(bytes(need), "big") >> (need * 8 - span)
        if value < limit:
            return value


def randrange(low, high):
    """[low, high) 的整数。"""
    return int(low) + below(int(high) - int(low))


def choice(items):
    """随机取一项；序列为空时抛 ValueError。"""
    items = list(items)
    if not items:
        raise ValueError("没有可选项")
    return items[below(len(items))]


def shuffle(items):
    """原地洗牌（Fisher-Yates），返回同一个列表。"""
    for i in range(len(items) - 1, 0, -1):
        j = below(i + 1)
        items[i], items[j] = items[j], items[i]
    return items


def sample(items, count):
    """不放回地取 count 项。"""
    pool = list(items)
    count = int(count)
    if count < 0 or count > len(pool):
        raise ValueError("取的个数超出范围")
    return [pool.pop(below(len(pool))) for _ in range(count)]


class Random(object):
    """给需要 rng 对象的旧代码：只提供用到的那几个方法。"""

    def random(self):
        return below(1 << 53) / float(1 << 53)

    def randrange(self, low, high=None):
        if high is None:
            return below(low)
        return randrange(low, high)

    def randint(self, low, high):
        return randrange(low, int(high) + 1)

    def choice(self, items):
        return choice(items)

    def shuffle(self, items):
        return shuffle(items)

    def sample(self, items, count):
        return sample(items, count)
