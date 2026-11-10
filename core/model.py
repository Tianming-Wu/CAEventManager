# -*- coding: utf-8 -*-
"""活动的业务模型由模块提供，这一层只负责按活动所用的模块把它取出来。

调用方写的还是 ``model.xxx(...)``：先按当前活动的 ``kind`` 找到模块的 ``model.py``，
再把属性转过去。平台自己不认识任何玩法字段——奖票、任务链这些词都只出现在模块里。
"""
import db
import errors
import modules

ApiError = errors.ApiError


def model_for(kind_id):
    """某个模块的业务模型对象。"""
    return modules.load_kind_model(kind_id)


def current():
    """当前活动的业务模型：按它自己的 kind 取。"""
    cfg = db.get_config(db.connect(), "kind") or db.DEFAULT_CONFIG["kind"]
    return model_for(cfg)


def ensure_event(conn=None):
    """模块自己的初始化钩子（例如灌演示数据）；模块没装就跳过，平台照样能用。"""
    try:
        mod = current()
    except Exception:
        return False
    hook = getattr(mod, "ensure_seed", None)
    if callable(hook):
        hook(conn if conn is not None else db.connect())
    return True


def __getattr__(name):
    # 模块级 __getattr__：model.whatever 落到当前活动的模块上
    return getattr(current(), name)
