# -*- coding: utf-8 -*-
"""请求是谁发起的：TCP 对端地址，或者前面那一跳代理写在请求头里的地址。

审计、日志、报名记录与按来源的限速都从这里取地址。默认只认 TCP 对端——连接从哪儿来
就是谁；服务前面挂反向代理时，对端永远是代理自己，这时由 ``config.json`` 的
``trust_forwarded_ip`` 决定要不要改读请求头。

一个前提：打开这个开关之前，端口不能被外部直接访问。请求头是客户端可以随便写的，
只有前面那一跳（nginx / frp / 学校外层入口）会把它覆盖成真实地址。
"""

import ipaddress


def _clean(value):
    """一段候选地址：合法的 IPv4 / IPv6 才认，其余（含换行、脚本、空值）一律丢掉。

    X-Forwarded-For 会被原样写进审计与日志，掺进换行就等于伪造日志行，所以这里
    先按地址解析一遍，只放行解析得通的那一个。
    """
    text = (value or "").strip()
    if not text:
        return ""
    # 允许带端口（"1.2.3.4:5678"）与方括号（"[2001:db8::1]:443"）
    if text.startswith("["):
        text = text[1:].split("]", 1)[0]
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return ""


def forwarded(headers):
    """请求头里的客户端地址，取不到就返回空串。

    ``X-Forwarded-For`` 是一串「客户端, 代理1, 代理2」，**最右边那一段**是紧挨着我们的
    那一跳代理写上去的，也就是它看到的对端地址；左边几段可能来自客户端自己填的头，
    照抄它们等于让发请求的人自己决定记成谁。没有这个头再看 ``X-Real-IP``。
    """
    if not headers:
        return ""
    chain = headers.get("X-Forwarded-For") or ""
    for part in reversed(chain.split(",")):
        got = _clean(part)
        if got:
            return got
    return _clean(headers.get("X-Real-IP") or "")


def resolve(peer, headers, trust=False):
    """最终用的地址：信任代理时优先请求头，取不到再回落到 TCP 对端。"""
    if trust:
        got = forwarded(headers)
        if got:
            return got
    return peer or ""
