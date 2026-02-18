import ast
from pathlib import Path

import pytest

PARTNER_ROUTE_PATH = Path(__file__).resolve().parents[1] / "bloobcat" / "routes" / "partner.py"


def _get_profit_node() -> ast.AsyncFunctionDef:
    module = ast.parse(PARTNER_ROUTE_PATH.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_profit":
            return node
    raise AssertionError("get_profit() not found in partner.py")


def test_profit_query_param_keeps_api_alias_range():
    source = PARTNER_ROUTE_PATH.read_text(encoding="utf-8")
    # External contract must stay `?range=week|month|year`.
    assert 'alias="range"' in source
    assert 'pattern="^(week|month|year)$"' in source
    # QR filters contract: both special referral source and UUID list must be supported.
    assert 'if s == "referral_link"' in source
    assert 'qr_code_id = ANY($4::uuid[])' in source


def test_profit_function_does_not_shadow_builtin_range():
    node = _get_profit_node()
    arg_names = [a.arg for a in node.args.args]
    # Regression guard: argument must not be named `range`.
    assert "range" not in arg_names

    # and the built-in range(...) call for day loop must remain callable.
    calls_builtin_range = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "range" for n in ast.walk(node)
    )
    assert calls_builtin_range


@pytest.mark.parametrize(
    ("period", "expected_days"),
    [("week", "7"), ("month", "30"), ("year", "365")],
)
def test_profit_days_mapping(period: str, expected_days: str):
    source = PARTNER_ROUTE_PATH.read_text(encoding="utf-8")
    # Lightweight contract check for mapping used by endpoint.
    assert f'range_param == "{period}"' in source or period == "year"
    assert f"for i in range(days)" in source
    assert f"days = 7 if range_param == \"week\" else 30 if range_param == \"month\" else 365" in source
    assert expected_days in source
