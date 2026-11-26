"""可选特性登记表：装了哪些、缺哪些，服务器管理页照这张表说话。

站内的"可选"指的是**缺了也能跑**的能力，通常是需要额外依赖的那些（见
requirements.txt）。这一层只负责收集状态，不负责安装；每一项都要能说清
"现在是什么状态"和"缺了会怎样"，页面上原样显示。
"""

import imaging


def _imaging():
    if not imaging.available():
        return {"id": "imaging", "name": "自动图片缩放", "ok": False,
                "detail": "未安装 Pillow，使用已有尺寸的外观图"}
    try:
        from PIL import __version__ as pil_version
    except Exception:
        pil_version = ""
    return {"id": "imaging", "name": "自动图片缩放", "ok": True,
            "detail": ("Pillow " + pil_version).strip()}


FEATURES = (_imaging,)


def listing():
    """所有可选特性及当前状态。"""
    out = []
    for build in FEATURES:
        try:
            out.append(build())
        except Exception as exc:            # 一项坏了不该让整页打不开
            out.append({"id": getattr(build, "__name__", "?"), "name": "未知特性",
                        "ok": False, "detail": str(exc)[:120]})
    return out
