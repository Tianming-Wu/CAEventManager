# -*- coding: utf-8 -*-
"""姓氏读音：多音字作姓时的读法。

拼音表（`pinyintable.py`，pypinyin 生成）给的是"单字最常用读音"，对姓氏系统性出错：
`曾` 变 `ceng`、`单` 变 `dan`、`解` 变 `jie`。名字的第一个字是姓，所以这里单独有一张表，
算拼音时先查它，查不到再退回常用读音。

只收**读音与常用读音不同**的姓氏：同音的不必进来，进来的每一条都要真的会用上。
"""

SURNAME_READING = {
    "曾": "zeng",
    "单": "shan",
    "解": "xie",
    "仇": "qiu",
    "区": "ou",
    "查": "zha",
    "朴": "piao",
    "乐": "yue",
    "折": "she",
    "种": "chong",
    "祭": "zhai",
    "宓": "mi",
    "繁": "po",
    "员": "yun",
    "能": "nai",
    "阚": "kan",
    "秘": "bi",
    "洗": "xian",
    "黑": "he",
    "句": "gou",
    "过": "guo",
    "华": "hua",
    "任": "ren",
    "燕": "yan",
    "召": "shao",
    "术": "zhu",
    "说": "shui",
    "贲": "ben",
    "胺": "an",
    "莘": "shen",
    "谌": "chen",
    "覃": "qin",
    "郇": "huan",
    "靳": "jin",
    "翟": "zhai",
    "牟": "mou",
    "盖": "ge",
    "藏": "zang",
    "尉": "yu",
    "长": "chang",
}


def surname_reading(name):
    """名字第一个字的姓氏读音；不是多音姓氏就返回空串。"""
    text = (name or "").strip()
    if not text:
        return ""
    return SURNAME_READING.get(text[0], "")
