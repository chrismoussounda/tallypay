# TallyPay

A payment ledger and webhook gateway: money is never lost, duplicated or created, even when the
provider times out, answers twice or disagrees. All money and providers are simulated.

In progress. So far: the project, its CI, and a delayed call to prove the hosting.

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
