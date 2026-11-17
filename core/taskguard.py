# -*- coding: utf-8 -*-
"""后台任务的护栏。

服务里没有别的线程，这一层是给"要自己定时干活"的任务用的（眼下只有备份调度）。
它做三件事：**吞掉异常**（一个任务抛异常不该把进程带走，也不该悄悄死掉）、
**记账**（连续失败几次）、**放弃**（连续失败到数就停掉这个任务、发条通知、记审计）。

用法：

    handle = taskguard.start("备份调度", 300, step)      # step 是一个普通函数
    ...
    taskguard.stop_all()

``step`` 自己不该睡长觉：它每次只判断"现在该不该干活"，睡的事交给这一层。
"""

import threading
import time
import traceback

import panel

MAX_FAILS = 3
_tasks = {}          # name -> {"fails": n, "alive": bool, "stopped": bool, "last": msg}
_lock = threading.Lock()


def status():
    """给「服务器管理」与体检看：每个任务还活着没有、失败过几次。"""
    with _lock:
        return [dict(name=name, **info) for name, info in sorted(_tasks.items())]


def _note(name, **fields):
    with _lock:
        info = _tasks.setdefault(name, {"fails": 0, "alive": False, "stopped": False,
                                        "last": ""})
        info.update(fields)


def _give_up(name, exc):
    """连续失败到数：停掉这个任务，并在看得见的地方留痕。"""
    detail = "%s: %s" % (type(exc).__name__, exc)
    print("任务「%s」连续 %d 次失败，已停掉：%s" % (name, MAX_FAILS, detail))
    try:
        with panel.tx() as tx:
            panel.audit(tx, "system", "task_stopped", name,
                        panel.audit_note(reason=detail[:200]))
            panel.notice(tx, "critical", "后台任务已停止：%s" % name,
                         "它连续 %d 次出错（最后一次：%s）。服务本身照常，"
                         "查清原因后重启服务即可恢复。" % (MAX_FAILS, detail),
                         actor="system", obj=name)
    except Exception:                                   # noqa: BLE001 - 记事失败也别抛
        pass


def start(name, interval, step, run_now=True):
    """起一个后台任务：每 ``interval`` 秒跑一次 ``step``。重复调用同名任务是安全的。"""
    with _lock:
        if name in _tasks and _tasks[name]["alive"]:
            return _tasks[name]
    stop = threading.Event()

    def loop():
        _note(name, alive=True, stopped=False, fails=0, last="")
        fails = 0
        first = True
        while not stop.is_set():
            if first and not run_now:
                first = False
                if stop.wait(interval):
                    break
                continue
            first = False
            try:
                step()
            except Exception as exc:                    # noqa: BLE001 - 任务代码什么都可能抛
                fails += 1
                _note(name, fails=fails, last="%s: %s" % (type(exc).__name__, exc))
                print("任务「%s」第 %d 次失败：\n%s"
                      % (name, fails, traceback.format_exc()))
                if fails >= MAX_FAILS:
                    _give_up(name, exc)
                    _note(name, alive=False, stopped=True)
                    return
            else:
                if fails:
                    _note(name, fails=0, last="")
                fails = 0
            if stop.wait(interval):
                break
        _note(name, alive=False)

    thread = threading.Thread(target=loop, name="task:" + name, daemon=True)
    _note(name, alive=True, stopped=False, fails=0, last="")
    thread.start()
    with _lock:
        _tasks[name]["stop"] = stop
        _tasks[name]["thread"] = thread
        return dict(_tasks[name])


def stop(name):
    with _lock:
        info = _tasks.get(name) or {}
        event = info.get("stop")
    if event is not None:
        event.set()


def stop_all():
    with _lock:
        events = [info.get("stop") for info in _tasks.values() if info.get("stop")]
    for event in events:
        event.set()
