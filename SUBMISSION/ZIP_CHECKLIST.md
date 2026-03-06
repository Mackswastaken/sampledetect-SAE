\# SampleDetect SAE — ZIP Checklist (What to Submit)



\## Include these folders/files

C:\\Projects\\sampledetect-mvp\\

\- SUBMISSION\\

&nbsp; - README\_RUNME.md

&nbsp; - ZIP\_CHECKLIST.md

&nbsp; - DEMO\_STEPS.md (optional)

&nbsp; - start\_all.ps1

&nbsp; - start\_backend.ps1

&nbsp; - start\_frontend.ps1

&nbsp; - set\_storage\_root.ps1

\- backend\\

&nbsp; - main.py

&nbsp; - settings.py

&nbsp; - db.py

&nbsp; - models.py

&nbsp; - audfprint\_runner.py

&nbsp; - requirements.txt (if you have it)

&nbsp; - .env.example

\- frontend\\

&nbsp; - app\\

&nbsp; - public\\

&nbsp;   - mirxflow-logo.png

&nbsp; - package.json

&nbsp; - package-lock.json

&nbsp; - next.config.\* (if present)

&nbsp; - .env.local.example



\## Optional (recommended)

\- A small demo library of 3–5 beats:

&nbsp; Put them under:

&nbsp; D:\\SampleDetectStorage\\library\_audio\\

&nbsp; (Or instruct the grader to add their own.)



\## Do NOT include (too big / machine-specific / secrets)

\- backend\\.venv\\

\- frontend\\node\_modules\\

\- backend\\.env  (contains secrets)

\- frontend\\.env.local (contains local settings)

\- D:\\SampleDetectStorage\\ (your audio files + generated fingerprints)

\- Any private keys / API keys



\## Tip

If the grader needs to install dependencies:

\- Backend: `pip install -r requirements.txt`

\- Frontend: `npm install`

