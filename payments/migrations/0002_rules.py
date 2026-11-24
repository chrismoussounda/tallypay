from django.db import migrations

SQL = """
CREATE TRIGGER payment_event_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON payments_paymentevent
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_refuse_change();

-- Once settled, a payment is final. What it is about never changes.
CREATE TRIGGER payment_final
    BEFORE UPDATE ON payments_payment
    FOR EACH ROW
    WHEN (OLD.status <> 'pending'
        OR (OLD.id, OLD.merchant_id, OLD.amount, OLD.reference, OLD.created_at)
        IS DISTINCT FROM
        (NEW.id, NEW.merchant_id, NEW.amount, NEW.reference, NEW.created_at))
    EXECUTE FUNCTION ledger_refuse_change();
"""

UNDO = """
DROP TRIGGER payment_final ON payments_payment;
DROP TRIGGER payment_event_append_only ON payments_paymentevent;
"""


class Migration(migrations.Migration):
    dependencies = [("payments", "0001_initial"), ("ledger", "0002_rules")]
    operations = [migrations.RunSQL(SQL, UNDO)]
