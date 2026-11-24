# TallyPay

A payment ledger and webhook gateway: money is never lost, duplicated or created, even when the
provider times out, answers twice or disagrees. All money and providers are simulated.

In progress. So far: the project, its CI, a delayed call to prove the hosting, the ledger,
the payments API and the simulated provider.

## The ledger

Every movement of money is an entry whose lines add up to zero, in whole XAF francs. Postgres itself
enforces the rules (`ledger/migrations/0002_rules.py`), so a bug in the application cannot break them:

- an entry that does not balance cannot be committed
- entries and lines cannot be changed, deleted or truncated, and nothing joins an entry once committed
- a mistake is undone by one exact opposite entry

The role that owns the tables could still turn these rules off; the application never does.

## The payments API

```sh
uv run python manage.py create_merchant "My shop"   # shows the API key, once

curl localhost:8000/v1/payments -H "Authorization: Bearer $KEY" -H "Idempotency-Key: order-1"   -H "Content-Type: application/json" -d '{"amount": 10000, "reference": "order-1"}'
```

Every route is described at `/v1/docs`.

- **Retries never pay twice.** The `Idempotency-Key`, the payment and the answer are committed
  together. A simultaneous request with the same key waits for the first, then gets its answer; if
  the first crashed, it takes over. The same key for a different request is refused.
- **A payment reaches the books once,** when it succeeds, less a 2% fee. It is then final.
- **API keys are kept only as hashes,** and a merchant sees only its own payments.

## The simulated provider

The provider is another company's system, so it can fail in any way. Each payment picks a `scenario`:

| Scenario | What the provider does |
|---|---|
| `normal` | Accepts, then calls back |
| `decline` | Declines, then calls back |
| `slow` | Takes a minute to decide |
| `request_lost` | Never gets the first request |
| `answer_lost` | Charges, but its answer is lost |
| `duplicate_callback` | Calls back three times |
| `no_callback` | Never calls back |

- **Charged once.** The provider is asked with the payment's own id, so asking again charges nothing.
- **Never forgotten.** The next look is written down before the provider is asked, so a crash cannot
  lose a payment. Looks are further and further apart; after about 7 hours and a half, the payment is
  left to reconciliation.
- **Postgres keeps the work to do;** QStash only wakes the app when some is due.

Tests run random mixes of scenarios, crashes and repeated calls. Each time, every payment must have
been charged once and the books must balance.

Without QStash, as on your own computer, `uv run python manage.py run_tasks --watch` does the work.

## Run it

Needs [uv](https://docs.astral.sh/uv/) and Docker.

```sh
docker compose up -d
uv sync
uv run python manage.py migrate
DEBUG=1 uv run python manage.py runserver
```

Checks: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy .`, `uv run pytest`.

## Deploy

On Vercel, with Neon for Postgres and QStash for delayed calls. Set the variables of
[.env.example](.env.example); `PUBLIC_URL` is the live address. A production build runs the
migrations; previews never do, and need a database of their own.

With the live `PUBLIC_URL`, `QSTASH_URL` and `QSTASH_TOKEN` set, this asks for a call in 60
seconds. The home page then shows when it arrived:

```sh
uv run python manage.py prove_wakeup --delay 60
```

## Licence

[MIT](LICENSE). htmx, in `static/vendor`, has [its own](static/vendor/htmx.LICENSE.txt).
