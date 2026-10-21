"""Number formatting for the dashboard. Plain English, no em dashes."""

from __future__ import annotations

NA = "n/a"
NOT_AVAILABLE = "Not available with this filter"


def money(value: float | None) -> str:
    if value is None:
        return NA
    sign = "-" if value < 0 else ""
    return f"{sign}£{abs(value):,.2f}"


def money_whole(value: float | None) -> str:
    if value is None:
        return NA
    sign = "-" if value < 0 else ""
    return f"{sign}£{abs(value):,.0f}"


def money_k(value: float | None) -> str:
    """Card revenue in thousands to one decimal place, for example £27.0k."""
    if value is None:
        return NA
    sign = "-" if value < 0 else ""
    return f"{sign}£{abs(value) / 1000:,.1f}k"


def rpe(value: float | None) -> str:
    return money(value)


def count(value: int | float | None) -> str:
    if value is None:
        return NA
    return f"{int(round(value)):,}"


def percent(value: float | None, places: int = 1) -> str:
    if value is None:
        return NA
    return f"{value * 100:.{places}f}%"


def pct_delta(change: float | None) -> str | None:
    """Whole % change, signed, for example "+12%". The card adds the arrow."""
    if change is None:
        return None
    rounded = round(change)
    return f"{rounded:+,.0f}%" if rounded else "0%"


def pp_delta(change: float | None) -> str | None:
    """Percentage point change, signed, for example "-1.4pp"."""
    if change is None:
        return None
    rounded = round(change, 1)
    return f"{rounded:+.1f}pp" if rounded else "0.0pp"
