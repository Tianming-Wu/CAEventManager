# -*- coding: utf-8 -*-
"""运维助手能做什么：**唯一的放行判定**。

助手调用的每个动作都要先问这里一句。今天它是一张白名单（默认拒绝），明天可以换成
自动审计引擎（复核模型、频率限制、危险参数识别）——换的只是这个函数的实现，
助手本身与业务接口都不用动。

白名单之外的硬线（不随这层变化）：

* 需要 sudo（重新输入密码）的动作永远不在名单里：助手背后没有人可以问密码；
* 助手用谁的会话就用谁的权限，这里只做"再收窄"，不做"放宽"。
"""

# 允许助手直接执行的动作：目前只有只读的。
# 键是工具名，值是允许的最小角色（None 表示"登录即可，权限仍由接口自己把关"）。
READ_ACTIONS = {
    "list_events": None,
    "event_board": None,
    "list_rosters": None,
    "roster_entries": None,
    "list_signups": None,
    "signup_entries": None,
    "search_people": None,
    "recent_audit": "superadmin",
    "host_stats": "owner",
}

# 允许助手代劳的写动作：**现在是空的**。
# 加动作时同时给一句人话说明——拒绝时要把这句话说给用户听。
# 例：{"signup_update": "改报名的时间窗与名额"}
WRITE_ACTIONS = {}

ROLE_RANK = {"admin": 1, "superadmin": 2, "owner": 3}


def verdict(account, action, params=None):
    """(allow, reason)。拒绝时 reason 是给人看的一句话。"""
    role = (account or {}).get("role") if isinstance(account, dict) else None
    role = role or "admin"
    if action in READ_ACTIONS:
        need = READ_ACTIONS[action]
        if need and ROLE_RANK.get(role, 0) < ROLE_RANK.get(need, 99):
            return False, "这个动作超出你的角色权限"
        return True, ""
    if action in WRITE_ACTIONS:
        return True, ""
    return False, "这类动作还没有对助手开放，请到对应页面操作"


def is_write(action):
    return action in WRITE_ACTIONS


def describe(action):
    return WRITE_ACTIONS.get(action, "")
