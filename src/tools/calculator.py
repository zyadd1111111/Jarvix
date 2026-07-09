from __future__ import annotations

import ast
from dataclasses import dataclass
import math
import operator
import re
from typing import Callable


class CalculationError(ValueError):
    """Raised when a calculation request is unsafe or invalid."""


ALLOWED_BINARY_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

ALLOWED_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

ALLOWED_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

ALLOWED_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "ceil": math.ceil,
    "cos": math.cos,
    "floor": math.floor,
    "log": math.log,
    "log10": math.log10,
    "max": max,
    "min": min,
    "round": round,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "tan": math.tan,
}


@dataclass(frozen=True)
class CalculationResult:
    expression: str
    value: float

    @property
    def display_value(self) -> str:
        if math.isfinite(self.value) and self.value.is_integer():
            return str(int(self.value))
        return f"{self.value:.10g}"

    @property
    def response(self) -> str:
        return f"{self.expression} = {self.display_value}"


class SafeCalculator:
    """Evaluates small arithmetic expressions without using eval."""

    _leading_words = re.compile(
        r"^(?:jarvix[, ]+)?(?:calculate|calc|compute|solve|what(?:'s| is))\s+", re.IGNORECASE
    )

    def try_calculate(self, text: str) -> CalculationResult | None:
        expression = self._extract_expression(text)
        if expression is None:
            return None
        value = self.calculate(expression)
        return CalculationResult(expression=expression, value=value)

    def calculate(self, expression: str) -> float:
        if len(expression) > 160:
            raise CalculationError("Calculation is too long.")
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise CalculationError("That calculation is not valid.") from exc
        value = self._evaluate(tree.body)
        if not math.isfinite(value):
            raise CalculationError("Calculation did not produce a finite number.")
        return float(value)

    def _extract_expression(self, text: str) -> str | None:
        value = text.strip().rstrip("?")
        if not value:
            return None

        lowered = value.lower()
        explicit = bool(self._leading_words.match(value))
        if explicit:
            value = self._leading_words.sub("", value).strip()

        value = self._replace_percent_of(value)
        value = self._replace_word_operators(value)
        value = value.replace("^", "**").replace("×", "*").replace("÷", "/")

        if not explicit and not self._looks_like_math(value, lowered):
            return None

        if re.search(r"[A-Za-z_]", value):
            names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", value))
            if not names.issubset(ALLOWED_NAMES.keys() | ALLOWED_FUNCTIONS.keys()):
                return None

        return value

    @staticmethod
    def _replace_percent_of(value: str) -> str:
        value = re.sub(
            r"(\d+(?:\.\d+)?)\s*%\s+of\s+(\d+(?:\.\d+)?)",
            r"(\1 / 100) * \2",
            value,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"(\d+(?:\.\d+)?)\s+percent\s+of\s+(\d+(?:\.\d+)?)",
            r"(\1 / 100) * \2",
            value,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _replace_word_operators(value: str) -> str:
        replacements = [
            (r"\bplus\b", "+"),
            (r"\bminus\b", "-"),
            (r"\btimes\b", "*"),
            (r"\bmultiplied\s+by\b", "*"),
            (r"\bdivided\s+by\b", "/"),
            (r"\bover\b", "/"),
            (r"\bto\s+the\s+power\s+of\b", "**"),
            (r"\bsquared\b", "** 2"),
            (r"\bcubed\b", "** 3"),
        ]
        for pattern, replacement in replacements:
            value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
        return value

    @staticmethod
    def _looks_like_math(value: str, lowered_original: str) -> bool:
        has_digit = bool(re.search(r"\d", value))
        has_operator = bool(re.search(r"[+\-*/%^()]", value))
        starts_with_math_function = lowered_original.startswith(
            ("sqrt(", "sin(", "cos(", "tan(", "log(", "log10(", "round(", "abs(")
        )
        return has_digit and has_operator or starts_with_math_function

    def _evaluate(self, node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)

        if isinstance(node, ast.Name) and node.id in ALLOWED_NAMES:
            return float(ALLOWED_NAMES[node.id])

        if isinstance(node, ast.UnaryOp):
            operator_type = type(node.op)
            if operator_type not in ALLOWED_UNARY_OPERATORS:
                raise CalculationError("Unsupported unary operator.")
            return float(ALLOWED_UNARY_OPERATORS[operator_type](self._evaluate(node.operand)))

        if isinstance(node, ast.BinOp):
            operator_type = type(node.op)
            if operator_type not in ALLOWED_BINARY_OPERATORS:
                raise CalculationError("Unsupported operator.")
            left = self._evaluate(node.left)
            right = self._evaluate(node.right)
            if operator_type is ast.Pow and abs(right) > 12:
                raise CalculationError("Exponent is too large.")
            return float(ALLOWED_BINARY_OPERATORS[operator_type](left, right))

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                raise CalculationError("Unsupported function.")
            args = [self._evaluate(arg) for arg in node.args]
            if len(args) > 4:
                raise CalculationError("Too many function arguments.")
            return float(ALLOWED_FUNCTIONS[node.func.id](*args))

        raise CalculationError("Unsupported calculation syntax.")
