# -*- coding: utf-8 -*-
"""应用根目录：入口 ``app.py`` 所在的那一层。

程序模块放在 ``core/`` 里，但配置、数据目录、模板、内置模块与文档都在仓库根目录，
所以路径一律从这里算，不用各自的 ``__file__``。
"""
import os

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
