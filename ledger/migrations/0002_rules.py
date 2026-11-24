from django.db import migrations

SQL = """
CREATE FUNCTION ledger_refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'check_violation';
END $$;

CREATE TRIGGER ledger_entry_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger_entry
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_refuse_change();
CREATE TRIGGER ledger_line_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger_line
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_refuse_change();

-- Its name may be corrected, but not what its lines mean.
CREATE TRIGGER ledger_account_fixed
    BEFORE UPDATE ON ledger_account
    FOR EACH ROW
    WHEN (OLD.id <> NEW.id OR OLD.code <> NEW.code OR OLD.kind <> NEW.kind)
    EXECUTE FUNCTION ledger_refuse_change();

CREATE FUNCTION ledger_stamp_entry() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.transaction_id := pg_current_xact_id()::text::bigint;
    RETURN NEW;
END $$;
CREATE TRIGGER ledger_entry_stamp BEFORE INSERT ON ledger_entry
    FOR EACH ROW EXECUTE FUNCTION ledger_stamp_entry();

-- A balanced pair of lines would otherwise slip into an entry already in the books.
CREATE FUNCTION ledger_refuse_late_line() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ledger_entry
        WHERE id = NEW.entry_id
          AND transaction_id = pg_current_xact_id()::text::bigint
    ) THEN
        RAISE EXCEPTION 'entry % is already in the books', NEW.entry_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ledger_line_on_time BEFORE INSERT ON ledger_line
    FOR EACH ROW EXECUTE FUNCTION ledger_refuse_late_line();

CREATE FUNCTION ledger_check_entry() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    checked uuid;
    reversed uuid;
BEGIN
    IF TG_TABLE_NAME = 'ledger_entry' THEN
        checked := NEW.id;
    ELSE
        checked := NEW.entry_id;
    END IF;
    IF (
        SELECT count(*) < 2 OR sum(amount) <> 0
        FROM ledger_line WHERE entry_id = checked
    ) THEN
        RAISE EXCEPTION 'entry % does not balance', checked
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT reverses_id INTO reversed FROM ledger_entry WHERE id = checked;
    IF reversed IS NOT NULL AND EXISTS (
        SELECT 1 FROM ledger_line WHERE entry_id IN (checked, reversed)
        GROUP BY account_id HAVING sum(amount) <> 0
    ) THEN
        RAISE EXCEPTION 'entry % is not the opposite of the one it reverses', checked
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$;

-- Checked at the end of the transaction, once every line is there.
CREATE CONSTRAINT TRIGGER ledger_entry_balances AFTER INSERT ON ledger_entry
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_check_entry();
CREATE CONSTRAINT TRIGGER ledger_line_balances AFTER INSERT ON ledger_line
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_check_entry();
"""

UNDO = """
DROP TRIGGER ledger_line_balances ON ledger_line;
DROP TRIGGER ledger_entry_balances ON ledger_entry;
DROP TRIGGER ledger_line_on_time ON ledger_line;
DROP TRIGGER ledger_entry_stamp ON ledger_entry;
DROP TRIGGER ledger_account_fixed ON ledger_account;
DROP TRIGGER ledger_line_append_only ON ledger_line;
DROP TRIGGER ledger_entry_append_only ON ledger_entry;
DROP FUNCTION ledger_check_entry, ledger_refuse_late_line, ledger_stamp_entry,
    ledger_refuse_change;
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]
    operations = [migrations.RunSQL(SQL, UNDO)]
