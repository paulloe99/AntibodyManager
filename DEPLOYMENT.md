# Streamlit Community Cloud deployment

If you used the Neon CLI setup for this repository, follow
[NEON_SETUP.md](NEON_SETUP.md): its generated `.env.local` connection takes
priority over the legacy `[database].url` setting in the running app.

The app runs on Streamlit Community Cloud and stores inventory, reviews, and
uploaded PNG/JPEG/TIFF images in hosted PostgreSQL. Local SQLite remains available
for development. There is one shared online inventory, independent of your PC.

Pictures can optionally live in Microsoft 365 OneDrive instead. See
[ONEDRIVE.md](ONEDRIVE.md) for setup; then include `[onedrive]` in both your local
and hosted secrets. The database will hold picture references and review text.

Streamlit does not guarantee persistent local files:
https://docs.streamlit.io/develop/concepts/connections/connecting-to-data

## 1. Create an online database

Create a PostgreSQL project at https://neon.com (Supabase or another PostgreSQL
provider also works). Choose a region near the lab. In the project's Connect
dialog, copy the PostgreSQL connection URL, including its `sslmode=require`
setting. Use the direct connection URL for the initial migration.

Check your provider's current storage allowance before choosing a plan, especially
if you upload many TIFF files. Images are stored in PostgreSQL along with reviews.

## 2. Configure the connection locally

Keep your existing `[accounts]` section in `.streamlit/secrets.toml` and append:

```toml
[database]
url = "postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require"
```

Use the actual URL from the provider. This file is ignored by Git; the example
in `.streamlit/secrets.example.toml` contains placeholders only. Credentials stay
private even if the inventory and app are public. Alternatively, commands can read
the connection URL from the `ANTIBODY_DATABASE_URL` environment variable.

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m antibody_manager.migrate_cloud
```

The second command previews the numbers of inventory entries, reviews and images
without connecting to the online database or changing records.

## 3. Transfer the current data once

Stop the local app while moving the data, so nobody changes it during migration.
Run:

```powershell
.\.venv\Scripts\python.exe -m antibody_manager.migrate_cloud --apply
```

The migration creates a local SQLite backup, copies inventory (including archived
entries), reviews, original image bytes and import tracking, and preserves their
IDs. It verifies every copied value before committing. It refuses a nonempty
destination instead of overwriting or duplicating data. Your original local
database is unchanged. If it fails, correct the connection and rerun against an
empty destination. Do not manually delete live cloud records to repeat a migration.

Start the local app once to check the hosted data. With `[database].url` configured,
the app now reads and writes PostgreSQL. Removing that section returns to the
separate local SQLite inventory; the two databases are not synchronized.

## 4. Deploy on Streamlit

1. Push the application code, `requirements.txt`, and `.streamlit/config.toml` to
   your GitHub repository. Do not commit the SQLite file or real secrets.
2. At https://share.streamlit.io choose **Create app**, select the repository and
   branch, and use `app.py` as the main file.
3. In **Advanced settings**, select Python **3.12** and paste your complete local
   secrets configuration: both `[accounts]` and `[database]`.
4. Deploy and log in. Check the inventory count, a stored review/image if available,
   and a small edit. Reboot the app and confirm the edit remains.
5. Share the resulting `https://...streamlit.app` address with your colleagues.

Keep the database section in the cloud secrets: without it the app defaults to
local SQLite, which is unsuitable for persistent Community Cloud storage. A
configured but unreachable database stops the app rather than silently switching
to a blank local inventory.

The current app still requires named lab accounts for viewing and editing. Data
being non-secret does not require anonymous write access. Each review remains tied
to its author. Configure database backups with your database provider.

Official deployment and secrets instructions:
https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
