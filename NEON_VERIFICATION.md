# Neon deployment verification — 2026-10-20

## Result

PostgreSQL is deployed and wired to the Python/Streamlit app in Neon organization
`org-jolly-frog-22174765`, project `nameless-wave-21691394`, branch `production`
(`br-wandering-queen-b5x2n6zu`). PostgreSQL is the only declared Neon service.

The app reads the generated `.env.local` variables through `python-dotenv`, uses
Psycopg 3 with the pooled connection for normal traffic, and the direct connection
for schema setup and migration. Credential files are ignored by Git. The app's
existing authentication and optional OneDrive integration were retained.

The Streamlit UI was started locally at http://127.0.0.1:8510. This setup deploys
the Neon database; it does not publish the frontend to Streamlit Community Cloud.
See [NEON_SETUP.md](NEON_SETUP.md) for the public Streamlit deployment settings.

## Deployment output

Command: `npx.cmd --yes neon@latest deploy`

```text
Applying to branch production (br-wandering-queen-b5x2n6zu)
No changes — branch production already matches the policy.

Utilized services: Postgres
Pulled 3 Neon variables into .env.local
  Postgres        DATABASE_URL, DATABASE_URL_UNPOOLED
  Branch          NEON_BRANCH
```

The migration was first executed and tested on an isolated copy of production,
then applied to production. It verified every copied field and preserved IDs:

```json
{
  "counts": {
    "antibodies": 338,
    "reviews": 1,
    "images": 0,
    "imported_rows": 334
  },
  "backup": "data/antibodies.before-cloud-20261020-105314-725721.sqlite3"
}
```

The original SQLite database remains unchanged. The temporary verification branch
was deleted; `neon branches list` afterward showed only production as ready,
default, and current.

## Build output

Command: `npm.cmd run build` (Windows launcher equivalent of `npm run build`)

```text
> build
> node scripts/build.mjs

53 passed in 14.44s
Build passed: Python compilation and regression tests.
```

## Running-server check

Command: `npm.cmd start`, with `STREAMLIT_SERVER_ADDRESS=127.0.0.1` and
`STREAMLIT_SERVER_PORT=8510`.

```text
> start
> node scripts/python.mjs -m streamlit run app.py --server.headless true

Uvicorn server started on 127.0.0.1:8510
You can now view your Streamlit app in your browser.
URL: http://127.0.0.1:8510
```

The request to `http://127.0.0.1:8510/_stcore/health` returned:

```text
HTTP 200 ok
```

## Live service/app check

Command: `npm.cmd run test:neon`. This uses Streamlit's app test harness with the
actual Neon production connection, not a mock database. A temporary in-memory test
account is injected into the harness; real lab accounts are unchanged.

```text
PASS: Streamlit sign-in and pooled Neon connection.
PASS: Inventory creation through the Streamlit storage form; persisted in Neon.
PASS: Planning search, selection and solution calculation using the saved inventory.
PASS: Review and picture stored in Neon, downloaded byte-for-byte and displayed in Streamlit.
PASS: Review update and deletion through Streamlit persisted in Neon.
PASS: Temporary verification records cleaned up.
Neon end-to-end verification passed.
```

The same workflow passed on the isolated verification branch before production
migration. OneDrive has not been connected and was not part of this PostgreSQL-only
Neon deployment.

## Setup failures and workarounds

- `npm i -g neon@latest` was blocked because PowerShell disallowed `npm.ps1`.
  Used the requested fallback through `npx.cmd --yes neon@latest`.
- The default Neon plugin installer failed (`plugins CLI failed`). The skill-only
  retry initially failed because Git could not verify the certificate issuer; the
  CLI also emitted a Windows Node assertion after those failed setup attempts.
- Retried Git downloads with Windows certificate trust (`http.sslBackend=schannel`)
  for that process. Installed both the `neon` and `neon-postgres` skills and read
  their guidance. Initialized project/config with `--no-agent-setup`, then completed
  browser sign-in. Certificate verification remained enabled.
- Automatic approval review rejected logging raw deployment output because it
  could save credentials. Deployment succeeded without that log, with any database
  URLs redacted from displayed command output.
- npm emitted an existing `min-release-age` environment-config warning. Streamlit's
  test harness emitted its documented bare-mode context warning. Neither prevented
  deployment, build, HTTP health, or end-to-end checks.

There are no unresolved Neon deployment or verification failures.
