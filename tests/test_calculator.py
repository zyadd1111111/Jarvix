from __future__ import annotations

import pytest

from tools.calculator import CalculationError, SafeCalculator


def test_calculates_basic_arithmetic() -> None:
    result = SafeCalculator().try_calculate("calculate 2 + 3 * 4")

    assert result is not None
    assert result.response == "2 + 3 * 4 = 14"


def test_calculates_percent_of() -> None:
    result = SafeCalculator().try_calculate("what is 15% of 200?")

    assert result is not None
    assert result.display_value == "30"


def test_calculates_word_operators() -> None:
    result = SafeCalculator().try_calculate("what is 2 plus 2?")

    assert result is not None
    assert result.display_value == "4"


def test_calculates_word_percent() -> None:
    result = SafeCalculator().try_calculate("calculate 20 percent of 80")

    assert result is not None
    assert result.display_value == "16"


def test_calculates_math_functions() -> None:
    result = SafeCalculator().try_calculate("sqrt(81)")

    assert result is not None
    assert result.display_value == "9"


def test_ignores_non_math_question() -> None:
    assert SafeCalculator().try_calculate("what is the best plan today?") is None


def test_blocks_unsafe_expression() -> None:
    with pytest.raises(CalculationError):
        SafeCalculator().calculate("__import__('os').system('date')")
