# Trade Value Worker

Cloudflare Worker API and D1 schema for Web-submitted trades, review, and market snapshots.

## Local checks

```bash
cd worker
npm test
node --check src/index.js
```

Python tests also apply all SQL migrations to an isolated in-memory SQLite database and verify that existing trade and character IDs remain intact:

```bash
cd python-app
PYTHONPATH=src python -m unittest discover -s tests -v
```

## API

- `GET /health`: Worker health.
- `GET /characters`: registered number-to-character catalog for the Web form.
- `GET /characters/resolve?number=001`: resolve a three-digit number.
- `POST /trades`: create a `pending` trade. Web submissions use `characterNumber` and a UUID `submissionId`; retries are idempotent.
- `POST /exchanges`: create a price-free exchange submission with an exchange description and numbered characters. It is stored separately from priced trades and never enters market calculations.
- `GET /trades?status=pending&limit=50&after=0`: authenticated review queue (`X-Admin-Key`), oldest first. The first response fixes `throughCursor`; send that value with `nextCursor` as `after` on every later page while `hasMore` is true. This bounds the review run so submissions arriving during pagination are picked up on the next poll. The page limit counts parent submissions; all of each submission's details are returned.
- `POST /trades/:id/status`: authenticated, one-time `approved` or `rejected` transition with reviewer history.
- `GET /exchanges?status=pending&limit=50&after=0`: authenticated exchange review queue, with the same stable upper-bound cursor and complete-detail behavior.
- `POST /exchanges/:id/status`: authenticated, one-time exchange approval or rejection with reviewer history.
- `GET /market-value`: market value from eligible approved single trades.
- `GET /market-snapshot?characterNumber=001` (or the compatible `characterName` parameter): latest D1 snapshot and change directions.
- `GET|POST /admin/blocked-ips`: authenticated blocked-IP management.

The Worker owns market calculations. The Discord Bot only fetches and formats snapshot values; the Web form posts to the Worker and does not calculate prices.

## D1 and production safety

`wrangler.toml` binds the existing `trade-value` database. Migrations `0001` through `0008` are additive; do not run them against production until the target database, applied-migration history, backup/recovery method, and current row counts have been verified. Do not use `schema.sql` to initialize or replace a production database.

The current snapshot implementation uses level windows of ±20 and treats level 301 as `Lv.MAX`, matching existing schema constraints. The game's official maximum level has not been independently confirmed. Price sample counts are persisted separately for Lv.1 and Lv.MAX; historical snapshots return `null` because their price sample counts cannot be reconstructed safely. Mutation multipliers compare the same character and level and are reported only when at least three matched samples exist. Exchange-only submissions use the separate `exchange_submissions` tables and are never converted to a price or included in market calculations.

Daily snapshots are scheduled at `0 15 * * *` UTC. Each character's snapshot and mutation rows are written in one D1 batch so a failed batch does not leave a partial snapshot.

Set `ADMIN_KEY` as a Worker secret, and provide the same value to the Bot host as `WORKER_ADMIN_KEY`. Never store either value in source code or commit it.
