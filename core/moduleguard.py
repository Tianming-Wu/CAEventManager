# -*- coding: utf-8 -*-
"""模块代码的保护层。

模块的 ``model.py`` 跑在服务进程里（没有单独的进程可以杀），所以它抛的异常会直接打断
那一次请求。这一层做三件事：

1. **兜住**：模块自己抛的异常不再原样冒到 HTTP 层，而是记日志、换成一句能看懂的话；
2. **重来一次**：第一次失败时先把这个模块的 ``model.py`` 重新载一遍（模块内的全局状态
   就是它自己的"内存"，重载等于重启），再试一回；
3. **连续三次就停用**：同一个模块连着失败三次，就当成"这个模块坏了"——停用它、
   记一条审计、发一条全站通知（所有人），之后它的活动页会显示「此活动不可用」。

失败计数按模块记，成功一次就清零。停用是走 ``panel.set_module_enabled``，
和站长在「模块管理」里点 [停用] 是同一条路，所以重新启用也在那里。
"""

import traceback

import errors
import panel

MAX_FAILS = 3
_locks_ok = True


class ModuleCrashed(errors.ApiError):
    """模块出错。消息给用户看，细节在服务日志里。"""

    def __init__(self, kind_id, msg):
        errors.ApiError.__init__(self, msg, 503)
        self.kind_id = kind_id


_fails = {}


def fails(kind_id):
    return _fails.get(kind_id, 0)


def reset(kind_id=""):
    if kind_id:
        _fails.pop(kind_id, None)
    else:
        _fails.clear()


def _reload(kind_id):
    """把这个模块的 model.py 重新载一遍；载不动就当这次重试也失败。"""
    import modules
    return modules.reload_kind_model(kind_id)


def _shut_down(kind_id, exc):
    """连续失败到数：停用模块、记审计、发一条全站通知。"""
    detail = "%s: %s" % (type(exc).__name__, exc)
    try:
        conn = panel.connect()
        panel.set_module_enabled(conn, kind_id, False, actor="system")
    except Exception:                                  # noqa: BLE001 - 停用失败也别再抛
        pass
    try:
        with panel.tx() as tx:
            panel.audit(tx, "system", "module_auto_off", kind_id,
                        panel.audit_note(reason=detail[:200]))
            panel.notice(tx, "critical", "模块 %s 已被自动停用" % kind_id,
                         "它连续 %d 次抛出异常（最后一次：%s）。数据没有改动，"
                         "修好之后在「模块管理」里重新启用。" % (MAX_FAILS, detail),
                         actor="system", obj=kind_id)
    except Exception:                                  # noqa: BLE001
        pass
    reset(kind_id)


def call(kind_id, name, func, /, *args, **kwargs):
    """调模块里的一个函数。成功就返回结果，失败按上面的规矩处理。

    前三个参数是"位置专用"（``/`` 之前）：模块函数自己也常有个叫 ``name`` 的参数，
    不这样会被我们的形参抢走，报一句莫名其妙的 TypeError。
    """
    try:
        out = func(*args, **kwargs)
    except errors.ApiError:
        # 模块自己抛的"给用户看"的错误（例如参数不对）：照原样交给上层，不算崩溃
        reset(kind_id)
        raise
    except Exception as exc:                           # noqa: BLE001 - 模块代码什么都可能抛
        fails_now = _fails.get(kind_id, 0) + 1
        _fails[kind_id] = fails_now
        print("module %s.%s 出错（连续 %d 次）：%s"
              % (kind_id, name, fails_now, traceback.format_exc()))
        if fails_now >= MAX_FAILS:
            _shut_down(kind_id, exc)
            raise ModuleCrashed(
                kind_id, "模块 %s 连续出错，已自动停用；请联系管理员" % kind_id) from exc
        if fails_now == 1:
            # 第一次失败：把它重新载一遍再试一次，模块内的坏状态就此清掉
            try:
                _reload(kind_id)
                out = func(*args, **kwargs)
            except errors.ApiError:
                reset(kind_id)
                raise
            except Exception as again:                 # noqa: BLE001
                _fails[kind_id] = fails_now + 1
                print("module %s.%s 重载后再失败：%s"
                      % (kind_id, name, traceback.format_exc()))
                if _fails[kind_id] >= MAX_FAILS:
                    _shut_down(kind_id, again)
                    raise ModuleCrashed(
                        kind_id, "模块 %s 连续出错，已自动停用；请联系管理员"
                        % kind_id) from again
                raise ModuleCrashed(
                    kind_id, "模块 %s 这一步出错了；再试一次，或联系管理员" % kind_id) from again
            else:
                return out
        raise ModuleCrashed(
            kind_id, "模块 %s 这一步出错了；再试一次，或联系管理员" % kind_id) from exc
    else:
        reset(kind_id)
        return out


def wrap(kind_id, name, func):
    """把模块里的一个可调用对象包起来：调用方照旧 ``f(...)``。"""
    def guarded(*args, **kwargs):
        return call(kind_id, name, func, *args, **kwargs)
    return guarded


def status():
    """给体检与服务器管理页看：哪些模块正处在失败里。"""
    return [{"module": kind_id, "fails": count}
            for kind_id, count in sorted(_fails.items()) if count]
