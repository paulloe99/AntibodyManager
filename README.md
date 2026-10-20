# Antibody Manager

A Streamlit workspace for experiment planning, antibody inventory, and ratings with result pictures.

## Run locally

Requires Python 3.10+. From this repository in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Configure login as described below, then open the local URL printed by Streamlit. Add real antibodies in **Storage overview**, or explicitly load fictional demo data from the sidebar. Demo dilutions are interface examples, not experimental recommendations.

## Login setup

Create `.streamlit/secrets.toml` using `.streamlit/secrets.toml` as a template. Generate an account line with:

```powershell
.\.venv\Scripts\python.exe -m antibody_manager.auth --username laura
```

The command prompts privately for a password and confirmation, then prints a salted PBKDF2-SHA256 hash. There is no password length restriction. Paste that line under `[accounts]` in the secrets file. Repeat for each lab member. Never commit the secrets file or use a real password in command-line arguments. On hosted Streamlit, configure the same `[accounts]` section in deployment secrets. Restart the app after changing secrets.

Every page, inventory action, review and image download requires login. There are no default accounts or public registration. New reviews use the signed-in username; existing review authors remain unchanged. All accounts currently share the same inventory permissions. Sessions expire after eight hours and are cleared on logout; a browser reload may require login again. Changing a hash or removing an account revokes its existing session on the next app interaction. To reset a password, generate a replacement hash and update the account line.

Login attempts are limited to five failures per account per minute within one server process. For internet deployment, use HTTPS and an upstream rate limiter; multiple server processes do not share this limiter. This local account implementation does not include MFA or password recovery emails. Streamlit's [secrets management documentation](https://docs.streamlit.io/develop/concepts/connections/secrets-management) explains deployment configuration.

## Workflows

- **Experimental planning:** search names/catalogs/lots and filter primaries by reactivity, host, company, and application. Select secondaries matched to primary hosts, with reactivity, color, host and company filters. View ratings and locations, adjust application-specific dilutions, calculate volumes, and download a CSV plan.
- **Storage overview:** add and edit antibodies; filter inventory by type, host, reactivity, company and location. Deletion archives an entry to preserve reviews and pictures; archived entries can be restored. Each entry represents a lot/aliquot with its own location.
- **AB Rating:** add 1–5 star reviews with reviewer, application, protocol notes and up to five PNG/JPEG images (10 MB each). Browse reviews by application and download stored pictures, including for archived antibodies.

Enter species as comma-separated values. Dilution suggestions use one `application=denominator` per line: `IF=500` means 1:500.

## Calculation assumptions

All volumes are in µL. Final solution volume is `samples × volume per sample × (1 + excess percent / 100)`. Stock volume is `final volume / dilution denominator`; diluent is `final volume − stock volume`. Each row is a separate solution, not a mixed cocktail. Missing suggestions require an explicitly supplied dilution. Stock volumes below 1 µL are flagged.

Secondary matching uses primary host and secondary reactivity only. Confirm application, isotype, cross-adsorption and protocol suitability. Shared primary hosts and identical conjugates trigger warnings. Ratings summarize all applications; reviews can be filtered by application.

## Persistence and development

### Import the lab Excel inventory

The Histology Antibodies workbook can be imported from a local file or accessible university share:

```powershell
.\.venv\Scripts\python.exe -m antibody_manager.import_histology "Y:\AG Basic\+Allgemein\#Hohensinner Lab General\#1 Histology Antibodies.xlsx"
# Add --apply after inspecting the preview to write the database.
```

The importer is specific to this workbook's headers. It backs up SQLite before applying an additive, transactional import. Repeating an import skips previously imported source sheet/row IDs and does not overwrite app edits. This is an initial migration, not automatic Excel synchronization; inserted/reordered source rows need a new reconciliation before reimporting. Existing demo entries are archived when applying the import.

Storage positions remain separate records. Missing fields are explicitly marked unknown. Original column values, application notes, dilution ranges, concentrations and source row references are retained in notes. Only exact, application-labelled ratio dilutions become calculator suggestions; unlabelled ratios, ranges and concentration-based instructions require manual confirmation. Non-antibody cell mask and a malformed secondary row are excluded and listed in `data/histology_import_report.json`. An entry marked finished is imported as archived.

SQLite stores inventory, reviews and validated image blobs in `data/antibodies.sqlite3`, ignored by Git. Override the location with `ANTIBODY_DB`. Back up the database while the app is stopped; it includes the pictures. Hosted deployments need persistent storage.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

The entry point is `app.py`. `antibody_manager/ui.py` contains the workflows, `domain.py` contains matching and calculations, and `store.py` handles transactional persistence and image validation. Tests cover these services and page smoke checks.

The initial app provides named-account login but no differentiated roles, audit trail or stock consumption tracking. Future refinements can include institutional single sign-on, inventory imports, saved experiment plans, mixed cocktails, isotype matching and application-specific rating summaries.
