from __future__ import annotations

import ast
import math
import re
from typing import Any

import numexpr
import numpy as np
from numpy.typing import NDArray

ALLOWED_FUNCTIONS = {"where", "abs", "minimum", "maximum", "sqrt", "log", "exp"}
VARIABLE_PATTERN = re.compile(r"^r[1-9][0-9]*_b[1-9][0-9]*$")
ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.USub,
    ast.UAdd,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.BitAnd,
    ast.BitOr,
    ast.BitXor,
    ast.Invert,
)


class ExpressionError(ValueError):
    pass


class _MinMaxTransformer(ast.NodeTransformer):
    def visit_Call(self, node: ast.Call) -> Any:
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id in {"minimum", "maximum"}:
            if len(node.args) != 2:
                raise ExpressionError(f"{node.func.id} requires two arguments")
            left, right = node.args
            operator: ast.cmpop = ast.Lt()
            if node.func.id == "maximum":
                operator = ast.Gt()
            comparison = ast.Compare(left=left, ops=[operator], comparators=[right])
            return ast.Call(
                func=ast.Name(id="where", ctx=ast.Load()),
                args=[comparison, left, right],
                keywords=[],
            )
        return node


def _validated_source(expression: str, variable_names: set[str]) -> str:
    if len(expression) > 4096:
        raise ExpressionError("expression exceeds 4096 characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError("expression syntax is invalid") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 256:
        raise ExpressionError("expression exceeds 256 AST nodes")
    for node in nodes:
        if not isinstance(node, ALLOWED_NODES):
            raise ExpressionError(f"expression node is not allowed: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                raise ExpressionError("expression function is not allowed")
            if node.keywords:
                raise ExpressionError("keyword arguments are not allowed")
        if (
            isinstance(node, ast.Name)
            and node.id not in ALLOWED_FUNCTIONS
            and (not VARIABLE_PATTERN.fullmatch(node.id) or node.id not in variable_names)
        ):
            raise ExpressionError(f"expression variable is not available: {node.id}")
        if isinstance(node, ast.Constant) and (
            not isinstance(node.value, int | float)
            or (isinstance(node.value, float) and not math.isfinite(node.value))
        ):
            raise ExpressionError("only finite numeric constants are allowed")
    transformed = _MinMaxTransformer().visit(tree)
    return ast.unparse(ast.fix_missing_locations(transformed))


def evaluate_expression(
    expression: str,
    variables: dict[str, NDArray[np.generic]],
) -> NDArray[np.generic]:
    source = _validated_source(expression, set(variables))
    try:
        return np.asarray(numexpr.evaluate(source, local_dict=variables, global_dict={}))
    except (KeyError, TypeError, ValueError) as exc:
        raise ExpressionError("expression evaluation failed") from exc
