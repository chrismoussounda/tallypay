"""The words shown for what the rest of the app stores."""

from django.utils.translation import gettext_lazy as _

WORDS = {
    # Statuses
    "pending": _("Waiting"),
    "succeeded": _("Succeeded"),
    "failed": _("Failed"),
    "delivered": _("Delivered"),
    "open": _("Open"),
    "proposed": _("To approve"),
    "returning": _("Being returned"),
    "resolved": _("Resolved"),
    "created": _("Created"),
    # Reasons for a refund
    "requested_by_customer": _("The customer asked"),
    "duplicate": _("Paid twice"),
    "fraudulent": _("Fraud"),
    # Scenarios
    "normal": _("Accepts, then calls back"),
    "decline": _("Declines, then calls back"),
    "slow": _("Takes a minute to decide"),
    "request_lost": _("Never gets the first request"),
    "answer_lost": _("Gets the request, but its answer is lost"),
    "duplicate_callback": _("Calls back three times"),
    "no_callback": _("Never calls back"),
    "silent": _("Moves the money, but never says so"),
    "late_success": _("Declines, but moves the money anyway"),
    "wrong_amount": _("Moves 500 francs too much"),
    "moved_twice": _("Moves the money twice"),
    "not_in_statement": _("Leaves it out of its statement"),
    # Accounts
    "asset": _("What we hold"),
    "liability": _("What we owe"),
    "income": _("What we earned"),
    "expense": _("What we lost"),
    "provider": _("Held by the provider"),
    "fees": _("Fees"),
    "refunds": _("Refunds on their way"),
    "suspense": _("Money not explained yet"),
    "differences": _("Differences taken on"),
    # Discrepancies
    "charge": _("Payment"),
    "refund": _("Refund"),
    "return": _("Give the money back to the customer"),
    "credit": _("Give the money to the merchant"),
    "write_off": _("Take the difference on ourselves"),
    "fail": _("Call it failed"),
    "matched": _("Settled by itself"),
    # Roles
    "owner": _("Owner"),
    "developer": _("Developer"),
    "support": _("Support"),
    "viewer": _("Viewer"),
    "operator": _("Operator"),
    # Actions
    "signed_in": _("Signed in"),
    "payment_created": _("Created a payment"),
    "refund_created": _("Refunded a payment"),
    "key_created": _("Created a key"),
    "key_revoked": _("Revoked a key"),
    "endpoint_added": _("Added a webhook address"),
    "endpoint_removed": _("Removed a webhook address"),
    "secret_shown": _("Looked at a webhook secret"),
    "delivery_replayed": _("Sent a webhook again"),
    "proposal_made": _("Proposed a solution"),
    "proposal_approved": _("Approved a solution"),
    "proposal_rejected": _("Rejected a solution"),
}

# Apart, as some share their value with a scenario.
DISCREPANCIES = {
    "unknown": _("Money moved that nobody asked for"),
    "after_failure": _("Money moved after it was said to have failed"),
    "wrong_amount": _("Another amount moved"),
    "missing": _("In our books, not in the statement"),
    "late": _("In the statement, after it was written off"),
    "stuck": _("Still waiting, and not in the statement"),
}

# Said by the rest of the app, in English.
ERRORS = [
    _("Only a payment that succeeded can be refunded."),
    _("That is more than is left of the payment."),
    _("That is more than your balance."),
    _("Someone else must approve it."),
    _("Another proposal was made since. Read it first."),
    _("It is open."),
    _("It is proposed."),
    _("It is returning."),
    _("It is resolved."),
]
