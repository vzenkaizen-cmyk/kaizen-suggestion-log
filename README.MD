# Kaizen Suggestion Log — Vidullanka PLC

A Streamlit rebuild of the Kaizen Suggestion Log, with role-based access:

- **Staff / GEMBA workers** only ever see **Log Suggestion** and **My Suggestions** — no dashboard, no approval controls.
- **Approvers** sign in with a password and get **Dashboard**, **Approvals**, **All Suggestions**, and **Manage Approvers**.
- A QR code on the login screen links straight to the staff submission form for phone use.
- Optional Groq AI assist suggests a PQCDSM category + short quality note when a suggestion is submitted.
- Optional SharePoint sync mirrors every new suggestion to a SharePoint list.

## 1. Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app opens at `http://localhost:8501`. Data is stored in `kaizen.db` (SQLite) in the same folder — it persists across restarts as long as the file stays put.



## 2. Optional integrations

Copy `.streamlit/secrets_example.toml` to `.streamlit/secrets.toml` and fill in what you want to enable. Every key is optional — leave it out and that feature just doesn't run.

- **`GROQ_API_KEY`** — turns on the AI category/quality-note assist on the submission form. Get a key from [console.groq.com](https://console.groq.com). Double-check the model name in `app.py` (`ai_review()`) against the [current Groq model list](https://console.groq.com/docs/models) before deploying, since model IDs change over time.
- **`SP_TENANT_ID` / `SP_CLIENT_ID` / `SP_CLIENT_SECRET` / `SP_SITE_ID` / `SP_LIST_ID`** — mirrors every new suggestion into a SharePoint list via Microsoft Graph (app-only auth). You'll need:
  1. An Azure AD app registration with the **`Sites.ReadWrite.All`** *application* permission, admin-consented.
  2. A SharePoint list with columns: `Title, SubmittedBy, EmployeeType, Department, DateSubmitted, Description, Category, TechniqueUsed, Status`.
  3. The site ID and list ID (Graph Explorer or `GET /sites/{hostname}:/{site-path}` will give you these).
- **`APP_URL`** — pre-fills the QR-code target once you know your deployed URL, so staff don't have to paste it in each time.

## 3. Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repo.
2. On [share.streamlit.io](https://share.streamlit.io), create a new app pointing at `app.py`.
3. Under **Settings → Secrets**, paste the contents of your filled-in `secrets.toml`.
4. Once deployed, put the app's URL into `APP_URL` in secrets (or paste it into the QR-code box on the login page) so the QR code links correctly.

**Important limitation:** Streamlit Community Cloud's filesystem is ephemeral — `kaizen.db` will reset whenever the app redeploys or sleeps for a long time. For anything beyond a pilot, either (a) turn on the SharePoint sync above so nothing is lost, or (b) swap `DB_PATH` in `app.py` for a hosted database (e.g. a small Postgres instance, Turso, or Azure SQL — Vidullanka already uses Azure SQL for the WaterNet pipeline, so that's a natural fit if you want durability without adding a new vendor).

## 4. Where to extend

- `page_log_suggestion()` — the submission form (fields, categories, techniques).
- `page_dashboard()` — the KPI cards and charts.
- `page_approvals()` — the approve/reject workflow (Approved → optionally Implemented → reward).
- `push_to_sharepoint()` / `ai_review()` — the two optional integrations, both fail silently so they never block someone from submitting or approving a suggestion.
