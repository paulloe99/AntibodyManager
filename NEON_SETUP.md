# Neon setup

This repository is linked to organization `org-jolly-frog-22174765`, project
`nameless-wave-21691394`, branch `production`. `neon.ts` declares PostgreSQL only;
the existing lab-account login and optional OneDrive picture integration remain
part of the Python app. Neon hosts the database; Streamlit hosts/runs the UI.

## Run locally against Neon

`neon deploy` writes the pooled `DATABASE_URL` and direct
`DATABASE_URL_UNPOOLED` to the ignored `.env.local` file (or `.env` if present).
The Python app reads those files automatically using `python-dotenv`. Process
environment variables take precedence. Schema setup and migrations use the direct
URL; normal inventory/review operations use the pooled URL through Psycopg 3.

```powershell
npm.cmd run build
npm.cmd start
```

The npm scripts call the configured project `.venv` Python interpreter. Set
`ANTIBODY_PYTHON` to an explicit executable path if your environment is elsewhere.
`build` compiles Python and runs the regression suite; Streamlit needs no bundled
JavaScript frontend. Linux/macOS users can use `npm` instead of `npm.cmd`.

To isolate local development from Neon, pass a SQLite path to `Store`, or set
`ANTIBODY_DB` to a local SQLite filename. This explicitly selects local storage.
`ANTIBODY_DATABASE_URL` is also supported as a custom cloud-connection override.

## Repeatable service verification

```powershell
npm.cmd run test:neon
```

This command runs Streamlit's application test harness against live Neon. It tests
sign-in, inventory creation, experimental planning, review/image round trips,
review updates and review deletion. It uses a temporary test account inside the
test harness, never changes real account credentials, and removes only its unique
test records afterward. It does not configure or test the optional OneDrive service.

For a separate test branch, first pull that branch's env into an ignored file and
pass `--env-file data/.env.neon-verification`. The production `.env.local` and `.neon`
link do not need to change.

## Migrating an existing SQLite inventory

The one-time migration command is:

```powershell
.\.venv\Scripts\python.exe -m antibody_manager.migrate_cloud --env-file .env.local --apply
```

It reads the direct Neon URL, creates a local backup, preserves IDs, verifies all
copied fields/image bytes and refuses to overwrite a nonempty destination. It is
not a synchronization command. See [DEPLOYMENT.md](DEPLOYMENT.md) for the full flow.

## Streamlit Community Cloud

Push the source files (including `requirements.txt` and `neon.ts`) to GitHub and
deploy `app.py` on Streamlit. Do not commit `.env.local` or `.streamlit/secrets.toml`.

In Streamlit's **App settings -> Secrets**, retain your existing `[accounts]` and
add the two values from the local Neon environment file as top-level TOML strings:

```toml
DATABASE_URL = "<pooled URL from .env.local>"
DATABASE_URL_UNPOOLED = "<direct URL from .env.local>"

[deployment]
require_cloud_database = true

[accounts]
# Paste your existing account password hashes here.
```

Top-level Streamlit secrets become environment variables, which the app reads.
Alternatively `[database].url` remains supported for deployments using one direct
connection URL. With both configured, the Neon environment variables take priority.
Do not use the npm start script as Streamlit's entrypoint; use `app.py` and select
Python 3.12 in Streamlit's deployment settings.

New review picture uploads are temporarily disabled; no OneDrive connection is
needed to launch. Existing stored pictures remain viewable. The production
inventory is already migrated, so do not repeat the migration for deployment.

The `[deployment]` setting makes a missing cloud URL a setup error instead of
silently starting a temporary local inventory. Use `.streamlit/secrets.example.toml`
as the complete template. After deployment, sign in, confirm the inventory,
publish a text-only review, reboot the app, and verify that the review remains.
