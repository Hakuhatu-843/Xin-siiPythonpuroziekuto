# Trade Value API (Cloudflare Workers + D1)

The GitHub Pages form submits trade records to Cloudflare Workers. New submissions stay pending until a reviewer approves or rejects them. Priced submissions record whether the submitter acquired or gave away each character. Exchange-only submissions treat selected characters as acquired, while the description records what was given away. Exchange-only submissions have no price and are excluded from price calculations.

## API

- `GET /health` — health check
- `GET /characters` — returns the 154 tradeable characters; Ancient entries are management-only
- `POST /trades` — submit a priced single/bundle/set trade
- `POST /exchanges` — submit an exchange-only record with no price
- `GET /trades?status=pending&limit=50&after=0` — admin-only paginated review list
- `GET /exchanges?status=pending&limit=50&after=0` — admin-only paginated review list
- `POST /trades/{id}/status` and `POST /exchanges/{id}/status` — admin review
- `GET /admin/statistics` — admin-only aggregate statistics
- `GET /market-value` — median price estimate when enough approved single-trade samples exist
- `GET /market-snapshot?characterNumber=005` or `?characterName=...` — latest persisted snapshot, price sample counts, demand score (1.0–5.0), acquired/given/exchange-acquired counts, change since the previous snapshot, mutation multipliers, and comparison directions
- `GET/POST /admin/blocked-ips` — admin-only abuse controls

Admin endpoints require the Worker secret `ADMIN_KEY` via the `X-Admin-Key` header. Set it as a Worker secret; never commit the key to GitHub or the HTML pages.

## Apply migrations and deploy

From the repository Codespace terminal:

```sh
cd worker
npm install
npx wrangler d1 migrations apply trade-value --remote
npx wrangler deploy
```

Migrations `0004` and `0005` add the canonical character catalog and exchange workflow, then clear the existing test-only submissions and market snapshots as explicitly requested. They retain the catalog and the blocked-IP configuration table. Review the migration files before applying them to any database that contains real records.

Configure `ADMIN_KEY` once in Cloudflare Workers secrets, if it has not already been configured:

```sh
npx wrangler secret put ADMIN_KEY
```

## Local checks

```sh
npm run dev
```

The GitHub Actions workflow checks Worker/frontend JavaScript and applies the complete migration sequence to an in-memory SQLite database, including assertions for catalog counts and cleanup.
