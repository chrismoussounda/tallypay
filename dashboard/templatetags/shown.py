from typing import Any

from django import template
from django.utils.formats import number_format

from dashboard import guide, words
from merchants.models import Demo

register = template.Library()


@register.filter
def francs(amount: int) -> str:
    """1 234 567 XAF, as the language writes it, and never cut at a line's end."""
    shown = number_format(abs(amount), use_l10n=True, force_grouping=True)
    sign = "\u2212" if amount < 0 else ""
    return f"{sign}{shown}\u00a0XAF".replace(" ", "\u00a0")


@register.filter
def signed(amount: int) -> str:
    return f"+{francs(amount)}" if amount > 0 else francs(amount)


@register.filter
def word(value: str) -> str:
    return str(words.WORDS.get(value, value))


@register.filter
def discrepancy(kind: str) -> str:
    return str(words.DISCREPANCIES.get(kind, kind))


@register.simple_tag
def guide_of(demo: Demo) -> dict[str, Any]:
    return guide.steps(demo)
