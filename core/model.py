# -*- coding: utf-8 -*-
"""活动的业务模型由模块提供，这一层只负责按活动所用的模块把它取出来。

调用方写的还是 ``model.xxx(...)``：先按当前活动的 ``kind`` 找到模块的 ``model.py``，
再把属性转过去。平台自己不认识任何玩法字段——那些词只出现在模块里。

模块代码跑在服务进程里，所以取出来的可调用对象一律套上保护层（``moduleguard``）：
它抛异常时先兜住并重载一次，连续三次失败就把模块停用。取属性本身也在保护里——
模块在导入期或属性访问时炸掉，同样按这个规矩处理。
"""
import db
import errors
import moduleguard
import modules

ApiError = errors.ApiError


def model_for(kind_id):
    """某个模块的业务模型对象。"""
    return modules.load_kind_model(kind_id)


def current():
    """当前活动的业务模型：按它自己的 kind 取。"""
    kind = str(db.get_config(db.connect(), "kind") or "").strip()
    if not kind:
        raise errors.ApiError("这个活动没有指定模块", 500)
    return model_for(kind)


def ensure_event(conn=None):
    """模块自己的初始化钩子（例如灌演示数据）；模块没装就跳过，平台照样能用。"""
    try:
        kind = str(db.get_config(db.connect(), "kind") or "").strip()
    except Exception:
        return False
    if not kind:
        return False
    try:
        mod = model_for(kind)
    except Exception:
        return False
    hook = getattr(mod, "ensure_seed", None)
    if callable(hook):
        moduleguard.call(kind, "ensure_seed", hook,
                         conn if conn is not None else db.connect())
    return True


def __getattr__(name):
    # 模块级 __getattr__：model.whatever 落到当前活动的模块上。
    # 可调用的一律过保护层：模块抛异常不该把请求打成 500，也不该让服务带病跑下去
    kind = str(db.get_config(db.connect(), "kind") or "").strip()
    if not kind:
        raise errors.ApiError("这个活动没有指定模块", 500)
    attr = getattr(model_for(kind), name)
    if callable(attr):
        return moduleguard.wrap(kind, name, attr)
    return attr
