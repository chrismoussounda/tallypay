from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from merchants.models import ApiKey
from provider.models import Scenario
from refunds.models import Refund

from . import words


class Amount(forms.IntegerField):
    """Whole francs, with or without spaces between the thousands."""

    def to_python(self, value: Any) -> int | None:
        if isinstance(value, str):
            value = "".join(value.split())
        result: int | None = super().to_python(value)
        return result


def _text(placeholder: Any, **more: Any) -> forms.TextInput:
    return forms.TextInput({"placeholder": placeholder, **more})


def _amount() -> Amount:
    return Amount(
        label=_("Amount in francs"),
        min_value=1,
        max_value=10_000_000,
        widget=_text("10000", inputmode="numeric", autocomplete="off"),
    )


YOUR_PASSWORD = _("Your password")


def _password(label: Any = YOUR_PASSWORD) -> forms.CharField:
    return forms.CharField(
        label=label,
        max_length=128,
        strip=False,
        widget=forms.PasswordInput(
            {"placeholder": YOUR_PASSWORD, "autocomplete": "current-password"}
        ),
    )


def _scenario() -> forms.ChoiceField:
    return forms.ChoiceField(
        label=_("What the simulated provider does"),
        choices=[(value, words.WORDS[value]) for value in Scenario.values],
        initial=Scenario.NORMAL,
        widget=forms.RadioSelect,
    )


class Once(forms.Form):
    """Sent twice, it is done once."""

    once = forms.UUIDField(widget=forms.HiddenInput)


class SignIn(forms.Form):
    email = forms.EmailField(
        label=_("Email"),
        max_length=254,
        widget=forms.EmailInput(
            {"placeholder": "ange@example.com", "autocomplete": "username"}
        ),
    )
    password = _password(_("Password"))


class Password(forms.Form):
    password = _password()


class NewPayment(Once):
    amount = _amount()
    reference = forms.RegexField(
        label=_("Reference"),
        regex=r"^[^\x00-\x1f\x7f]+$",
        max_length=64,
        widget=_text(_("Order 1042"), autocomplete="off"),
    )
    scenario = _scenario()


class NewRefund(Once):
    amount = _amount()
    reason = forms.ChoiceField(
        label=_("Reason"),
        choices=[(value, words.WORDS[value]) for value in Refund.Reason.values],
        initial=Refund.Reason.REQUESTED,
        widget=forms.RadioSelect,
    )
    scenario = _scenario()


class NewKey(Password):
    name = forms.CharField(
        label=_("Name"),
        max_length=ApiKey._meta.get_field("name").max_length,
        widget=_text(_("Website"), autocomplete="off"),
    )
    field_order = ("name", "password")


class NewEndpoint(forms.Form):
    url = forms.CharField(
        label=_("Address"),
        max_length=500,
        widget=_text("https://example.com/webhooks", inputmode="url"),
    )


class Proposal(forms.Form):
    action = forms.ChoiceField(label=_("What to do"), widget=forms.RadioSelect)
    note = forms.CharField(
        label=_("Why"),
        max_length=300,
        widget=forms.Textarea({"rows": 3, "placeholder": _("What you found")}),
    )

    def __init__(self, actions: set[str], *args: Any) -> None:
        super().__init__(*args)
        field = self.fields["action"]
        assert isinstance(field, forms.ChoiceField)  # noqa: S101
        field.choices = [(value, words.WORDS[value]) for value in sorted(actions)]


class Decision(Password):
    proposal = forms.IntegerField(widget=forms.HiddenInput)
