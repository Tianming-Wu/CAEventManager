# -*- coding: utf-8 -*-
"""平台与模块共用的错误类型。

模块也要抛这个：接口层把它翻成「一句话 + 状态码」，别的异常一律算 500。
"""


class ApiError(Exception):
    """一句能直接给用户看的话，带一个状态码。"""

    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg = msg
        self.code = code
