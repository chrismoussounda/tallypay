from django.urls import path

from . import demo, developers, operators, payments, sessions

urlpatterns = [
    path("", sessions.start, name="start"),
    path("sign-in", sessions.sign_in, name="sign-in"),
    path("sign-out", sessions.sign_out, name="sign-out"),
    path("docs", sessions.docs, name="docs"),
    path("demo/become", demo.become, name="become"),
    path("demo/reconcile", demo.reconcile, name="reconcile"),
    path("demo/inbox", demo.switch_inbox, name="switch-inbox"),
    path("overview", payments.overview, name="overview"),
    path("payments", payments.payment_list, name="payments"),
    path("payments/new", payments.new_payment, name="new-payment"),
    path("payments/<uuid:payment_id>", payments.payment_detail, name="payment"),
    path("payments/<uuid:payment_id>/refund", payments.new_refund, name="new-refund"),
    path("balance", payments.balance, name="balance"),
    path("keys", developers.key_list, name="keys"),
    path("keys/new", developers.new_key, name="new-key"),
    path("keys/<int:key_id>/revoke", developers.revoke_key, name="revoke-key"),
    path("webhooks", developers.webhooks, name="webhooks"),
    path("webhooks/new", developers.new_endpoint, name="new-endpoint"),
    path(
        "webhooks/<uuid:endpoint_id>/secret",
        developers.endpoint_secret,
        name="endpoint-secret",
    ),
    path(
        "webhooks/<uuid:endpoint_id>/remove",
        developers.remove_endpoint,
        name="remove-endpoint",
    ),
    path("deliveries/<uuid:delivery_id>", developers.delivery_detail, name="delivery"),
    path("activity", developers.activity, name="activity"),
    path("books", operators.account_list, name="books"),
    path("discrepancies", operators.discrepancy_list, name="discrepancies"),
    path(
        "discrepancies/<uuid:found_id>",
        operators.discrepancy_detail,
        name="discrepancy",
    ),
    path("discrepancies/<uuid:found_id>/propose", operators.propose, name="propose"),
    path("discrepancies/<uuid:found_id>/approve", operators.approve, name="approve"),
    path("discrepancies/<uuid:found_id>/reject", operators.reject, name="reject"),
]
