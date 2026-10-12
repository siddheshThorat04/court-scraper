# Google Sheets storage - one-time setup (owner)
1. Create a new Google Sheet (name it e.g. "court-data").
2. Extensions > Apps Script. Delete the sample code and paste all of `apps_script/Code.gs`. Save.
3. Project Settings (gear) > Script properties > Add: `SECRET` = a long random string. Save.
4. Select function `setup` in the toolbar > Run. Approve the permission prompt (Advanced > Go to project).
5. Deploy > New deployment > type: Web app. Execute as: **Me**. Who has access: **Anyone**. Deploy. Copy the Web app URL (ends in /exec).
6. In `.env` (yours and each operator's): `SHEETS_URL=<that URL>` and `SHEETS_SECRET=<the same secret>`.
7. `python test_db.py` should print OK, then `python app_schema.py`.
If you edit Code.gs later: Deploy > Manage deployments > Edit > Version: New version > Deploy (the URL stays the same).
Operators can append rows and read the names/pages sheets only. They cannot read or delete the cases.
