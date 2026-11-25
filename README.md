# TallyPay

A payment ledger and webhook gateway: money is never lost, duplicated or created, even when the
provider times out, answers twice or disagrees. All money and providers are simulated.

**[Try it](https://tallypay-ledger.vercel.app)** without an account, or read
[how it was built](https://tallypay-ledger.vercel.app/about).

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
- **At most 10 requests a second** for each merchant, 30 at once. Above, the answer is 429 and
  `Retry-After` says how long to wait. Counted in one statement of Postgres.
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
| `silent` | Moves the money, but never says so |
| `late_success` | Declines, but moves the money anyway |
| `wrong_amount` | Moves 500 francs too much |
| `moved_twice` | Moves the money twice |
| `not_in_statement` | Leaves it out of its statement |

The last five only come out in reconciliation.

- **Charged once.** The provider is asked with the payment's own id, so asking again charges nothing.
- **Never forgotten.** The next look is written down before the provider is asked, so a crash cannot
  lose a payment. Looks are further and further apart; after about 7 hours, the payment is
  left to reconciliation.
- **Postgres keeps the work to do;** QStash only wakes the app when some is due.

Tests run random mixes of scenarios, crashes and repeated calls. Each time, every payment must have
been charged once and the books must balance.

Without QStash, as on your own computer, `uv run python manage.py run_tasks --watch` does the work.

## Webhooks

A merchant adds an address with `POST /v1/webhook-endpoints`, which shows its secret, and is told of
every payment that succeeds or fails.

- **Signed** as [Standard Webhooks](https://www.standardwebhooks.com) describe. Secrets are derived from
  `SECRET_KEY`, so they are not in the database; changing that key changes them all.
- **At least once.** The event is committed with the payment. A failure is tried again 6 times, over
  about 31 hours, with the same `Webhook-Id`, so that a merchant can spot a repeat.
- **Replay.** `GET /v1/webhook-deliveries?status=failed` lists what failed, and
  `POST /v1/webhook-deliveries/{id}/replay` sends it again.
- **Public addresses only.** Each send checks the address the name leads to, connects to that very
  address, and follows no redirect.

A daily call from Vercel, with `CRON_SECRET`, runs any task QStash missed, then reconciles.

## Refunds

`POST /v1/payments/{id}/refunds` gives money back, in full or in parts, never above what was paid.
The money leaves the merchant's balance at once, and comes back if the refund fails. A refund above
the balance is refused. The 2% fee is not returned.

## Reconciliation

Every day, the provider's statement is stored, then matched with the books.

- **The statement settles** what the provider never answered.
- **Money nobody expected** goes to a suspense account, so the books always say what the provider
  holds. Money taken for a payment that had failed is given back at once.
- **What is in the books but not in the statement** is called missing after an hour, as statements
  can be late.
- **Two people** resolve the rest: one proposes, another approves. Each resolution that moves money is
  an ordinary entry. Nothing is edited.

```sh
uv run python manage.py reconcile
uv run python manage.py discrepancies
```

## The dashboard

At `/app/`, in English and French, with HTMX. Every form also works without JavaScript.

| Role | Can |
|---|---|
| `owner` | Everything of its merchant, and read what its team did |
| `developer` | Payments, refunds, API keys, webhooks |
| `support` | Refunds |
| `viewer` | Read |
| `operator` | TallyPay's own books and discrepancies, and no merchant's pages |

- **Passwords** of 15 characters or more, as there is no second factor. Tries are limited for each
  email, each address and both together. A new key, a webhook secret and an approval ask for the password again.
- **Sessions** end after an hour without a request, and after 12 hours anyway.
- **A form sent twice** pays or refunds once.
- **An approval** is for the proposal that was read: if another took its place, it is refused.
- **Only the site's own scripts and styles** run, by its content security policy.

```sh
uv run python manage.py create_user ange@example.com "Ange" operator
uv run python manage.py create_user shop@example.com "Shop" owner --merchant ID
```

After changing the French in `locale`: `uv run pybabel compile -D django -d locale`.

## The demo

Each visitor gets a shop of their own with sample payments, its owner and two operators, without
passwords.

- **Nothing is deleted.** After 24 hours its users, keys and address are closed. Its entries stay, as
  the books have no way to lose one.
- **Limits:** 3 demos an hour for each address, 50 a day in all; 20 payments, refunds or webhooks
  sent again for each demo, 50 a day in all, and webhooks tried again 3 times only. The visitor's address is kept hashed, while it is open.
- **Webhooks go only to a test server** of the app itself, which checks their signatures. The visitor
  can switch it off to see the retries.
- **Operators of a demo** see its discrepancies only, and can compare the statement at once. Those of
  shops do not see the demos. The one scenario that leads to no shop, money moved twice, is left out.

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
seconds. `/last-wakeup` then shows when it arrived:

```sh
uv run python manage.py prove_wakeup --delay 60
```

## Licence

[MIT](LICENSE). htmx, in `static/vendor`, has [its own](static/vendor/htmx.LICENSE.txt).
