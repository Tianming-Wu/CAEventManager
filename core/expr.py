# -*- coding: utf-8 -*-
"""A tiny arithmetic language for values that depend on the live state.

Used where a fixed number is too rigid but running author code is too much:
the ticket pool is ``signed_in * 3 + 20`` rather than a constant, and it is
re-evaluated every time the state changes.

No ``eval``.  The source is parsed into an AST and walked; only numbers, names
from the supplied context, arithmetic and a handful of functions are allowed,
so nothing can reach an attribute, a subscript, a comprehension or an import.

    >>> evaluate("signed_in * 3 + 20", {"signed_in": 12})
    56
"""
import ast
import operator

MAX_LENGTH = 200
MAX_NODES = 200

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

FUNCTIONS = {
    "min": min,
    "max": max,
    "round": round,
    "abs": abs,
    "int": int,
}


class ExprError(Exception):
    """Something the operator has to fix, with a message meant for them."""


def names(src):
    """Every identifier used, so the UI can say what is available."""
    try:
        tree = ast.parse(src or "", mode="eval")
    except SyntaxError:
        return []
    return sorted({node.id for node in ast.walk(tree)
                   if isinstance(node, ast.Name)})


def _check(src):
    if not src or not src.strip():
        raise ExprError("表达式是空的")
    if len(src) > MAX_LENGTH:
        raise ExprError("表达式太长（上限 %d 个字符）" % MAX_LENGTH)
    try:
        return ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ExprError("表达式语法不对：%s" % (exc.msg,))


def evaluate(src, context, as_int=True):
    """Evaluate ``src`` with ``context`` as the only source of names."""
    tree = _check(src)
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ExprError("表达式太复杂")

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ExprError("只能用数字")
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in context:
                raise ExprError("不认识的名字：%s" % node.id)
            value = context[node.id]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ExprError("%s 不是数字" % node.id)
            return value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            left = walk(node.left)
            right = walk(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 8:
                raise ExprError("幂次太大了")
            try:
                return _BIN[type(node.op)](left, right)
            except ZeroDivisionError:
                raise ExprError("除零了")
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](walk(node.operand))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise ExprError("只能用 %s" % "、".join(sorted(FUNCTIONS)))
            if node.keywords:
                raise ExprError("函数不支持关键字参数")
            args = [walk(arg) for arg in node.args]
            try:
                return FUNCTIONS[node.func.id](*args)
            except TypeError:
                raise ExprError("%s 的参数不对" % node.func.id)
        raise ExprError("表达式里有不支持的写法")

    value = walk(tree)
    if as_int:
        return int(value)
    return float(value)


def check(src, context_names):
    """Validate without a live context; returns the names it needs."""
    used = names(src)
    unknown = [n for n in used if n not in context_names]
    if unknown:
        raise ExprError("不认识的名字：%s" % "、".join(unknown))
    return used
