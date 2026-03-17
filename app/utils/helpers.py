from datetime import date, datetime
from typing import Any


def today_str() -> str:
    return str(date.today())


def validate_date_string(date_str: str) -> bool:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0:
        return default
    return numerator / denominator


def clean_dict(d: dict[str, Any]) -> dict[str, Any]:
    return {
        k: clean_dict(v) if isinstance(v, dict) else v
        for k, v in d.items()
        if v is not None
    }
