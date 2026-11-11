# -*- coding: utf-8 -*-
"""服务安全模式：把服务收起来，只读，但平台自己能改。

出配置或数据问题时用得上：外部资源一律不加载（内置模块也算外部资源），活动内的写入全部拒绝，
而账号、模块启用状态、短链、通知这些平台自己的东西照常可改——否则进了安全模式就再也没法自救。

开关是静态设置（`config.json` 的 `safe_mode`，改完重启生效）；测试与特殊部署可用
`LOTTERY_SAFE_MODE=1` 覆盖。读到什么就是什么，进程内不再变。
"""
import os

import errors
import localconfig

_state = {"enabled": None}

MESSAGE = "服务处于安全模式：活动内的数据暂不开放，平台设置仍可修改"
LOGIN_MESSAGE = "Service is under maintenance"


def enabled():
    if _state["enabled"] is None:
        raw = os.environ.get("LOTTERY_SAFE_MODE")
        if raw is None:
            _state["enabled"] = bool(localconfig.load().get("safe_mode"))
        else:
            _state["enabled"] = raw.strip().lower() in ("1", "true", "yes", "on")
    return _state["enabled"]


def reset():
    """测试用：清掉缓存，下次重新读开关。"""
    _state["enabled"] = None


def refuse():
    """活动内的写入被拒：503 + 一句说明。"""
    return errors.ApiError(MESSAGE, 503)


def login_blocked(account):
    """安全模式下只让站长进：别的账号返回要抛的错误，站长返回 None。"""
    if not enabled():
        return None
    role = ""
    try:
        role = str(account["role"] or "")
    except Exception:
        role = str((account or {}).get("role") or "")
    return None if role == "owner" else refuse_login()


def refuse_login():
    """安全模式下别的账号登录被拒；提示只写一句英文，登录页原样显示。"""
    return errors.ApiError(LOGIN_MESSAGE, 503)
