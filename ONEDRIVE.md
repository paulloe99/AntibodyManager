# Store review pictures in OneDrive

New picture uploads are currently disabled in the app while the lab chooses its
storage setup. Existing pictures remain readable. The instructions below are for
the later IT connection; no OneDrive setup is required to deploy the text-only
review workflow with Neon.

New review pictures can be stored in a dedicated Microsoft 365 OneDrive folder.
Antibody inventory, ratings, comments, and picture references stay in the app's
database. New pictures are limited to **2048 pixels on the longest side and 1 MB
per stored picture** (at most 5 MB for five pictures in one review). Small pictures
are kept unchanged. Larger pictures become compressed JPEG review copies with a
`_review.jpg` filename; OneDrive uses unique generated filenames to avoid collisions.

Keep original microscopy files separately. Large 16-bit TIFFs are converted to
8-bit display copies with their intensity range scaled for viewing. These copies
are for reviewing results, not quantitative image analysis. Multi-page TIFFs must
be exported as the desired single frame before upload; frames are not silently
discarded. Downloads return the stored review copy. Existing pictures are unchanged.

This implementation uses application authentication for a **Microsoft 365 work
or university account**. Personal Outlook/Hotmail OneDrive accounts need a
different delegated sign-in flow and are not supported by this configuration.

## Microsoft setup

1. In OneDrive, create a folder named `Antibody Manager review pictures`.
2. Ask your Microsoft 365 administrator to register an application in Microsoft
   Entra ID for the university's tenant. Record the **Directory (tenant) ID** and
   **Application (client) ID**.
3. Under **Certificates & secrets**, create a client secret and record its
   **Value** (not its secret ID). Note its expiration date so it can be renewed.
4. Ask the administrator to configure Microsoft Graph **Application permissions**
   `Files.SelectedOperations.Selected`, grant administrator consent, and explicitly
   grant the application the `write` role on the dedicated pictures folder. The
   folder grant is a separate step: administrator consent alone does not give a
   Selected permission access to any files. The application needs to read the folder
   and create, read and delete its picture files. Microsoft documents this flow at
   https://learn.microsoft.com/en-us/graph/permissions-selected-overview . If your
   organization's setup requires another permission scheme, let IT choose it;
   `Files.ReadWrite.All` is a broader alternative that reaches across the tenant.
5. Obtain the **drive ID** and **folder item ID**. An administrator can use Graph
   Explorer or Graph API requests:

   ```text
   GET https://graph.microsoft.com/v1.0/users/USER_UPN/drive
   GET https://graph.microsoft.com/v1.0/drives/DRIVE_ID/root:/Antibody%20Manager%20review%20pictures
   ```

   Use the `id` from each response. An ordinary OneDrive sharing link is not a
   drive ID or folder item ID. Avoid `/me` endpoints for application credentials.

## App configuration

Append this section to your local `.streamlit/secrets.toml` and the hosted app's
**App settings -> Secrets**, alongside the existing `[accounts]` and `[database]`:

```toml
[onedrive]
tenant_id = "YOUR_DIRECTORY_TENANT_ID"
client_id = "YOUR_APPLICATION_CLIENT_ID"
client_secret = "YOUR_CLIENT_SECRET_VALUE"
drive_id = "YOUR_ONEDRIVE_DRIVE_ID"
folder_id = "YOUR_REVIEW_PICTURES_FOLDER_ID"
```

Restart the app. Optionally check authentication and folder access locally first:

```powershell
.\.venv\Scripts\python.exe -m antibody_manager.onedrive
```

This check does not upload or delete any files. Publish a review with a small
picture to test write access; verify that it appears both in the OneDrive folder
and in the review. Test the Download image button and deleting your test review.
The Microsoft connection has to be verified against your actual tenant after the
credentials are configured; the automated tests use simulated Graph responses.

## Behavior and recovery

- Without `[onedrive]`, pictures continue to be stored in the database.
- With `[onedrive]`, new pictures are optimized before upload and the database keeps
  the drive/item IDs and stored filename, with no duplicate picture blob.
- Existing database pictures continue to work and are not automatically moved.
  Cloud database migration preserves both database pictures and OneDrive references.
- If a new picture upload fails, the review is not published. Already uploaded
  pictures from that attempt are removed. Ambiguous uploads and failed deletions
  are tracked for cleanup when database access is available.
- Deleting a review queues its OneDrive pictures for deletion. Microsoft Graph
  moves deleted files to the recycle bin. If OneDrive is temporarily unavailable,
  cleanup remains queued and is retried on subsequent app interactions.
- OneDrive outages or manually deleted pictures show an unavailable-picture
  message without hiding the written review.
- Do not manually replace the uploaded files or remove the app's access. Original
  drive/item IDs, rather than expiring download links, are stored in the database.
- Removing the OneDrive configuration does not copy files back into the database;
  existing OneDrive pictures will require the connection to display again.
- Back up the database **and** retain the OneDrive folder. A database backup alone
  does not include externally stored picture bytes. Keep the same folder/drive
  connection when migrating the database or restoring a backup.

Official Microsoft documentation:

- Application authentication: https://learn.microsoft.com/en-us/graph/auth-v2-service
- Upload: https://learn.microsoft.com/en-us/graph/api/driveitem-put-content
- Download: https://learn.microsoft.com/en-us/graph/api/driveitem-get-content
- Delete/recycle bin: https://learn.microsoft.com/en-us/graph/api/driveitem-delete
