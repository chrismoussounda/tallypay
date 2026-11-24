from django.db import migrations

SQL = """
-- Once settled, a refund is final. What it is about never changes.
CREATE TRIGGER refund_final
    BEFORE UPDATE ON refunds_refund
    FOR EACH ROW
    WHEN (OLD.status <> 'pending'
        OR (OLD.id, OLD.payment_id, OLD.amount, OLD.hold_id, OLD.created_at)
        IS DISTINCT FROM
        (NEW.id, NEW.payment_id, NEW.amount, NEW.hold_id, NEW.created_at))
    EXECUTE FUNCTION ledger_refuse_change();
"""

UNDO = "DROP TRIGGER refund_final ON refunds_refund;"


class Migration(migrations.Migration):
    dependencies = [("refunds", "0001_initial"), ("ledger", "0002_rules")]
    operations = [migrations.RunSQL(SQL, UNDO)]
