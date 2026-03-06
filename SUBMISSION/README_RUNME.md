\# SampleDetect SAE — Run Me (Local Prototype)



\## What this is

A working prototype that:

\- uploads audio

\- detects matches using audfprint (VR preprocessing)

\- generates spectrograms

\- records proof hashes to Polygon Amoy (testnet)

\- scans a local “monitor inbox” folder and emails an alert on detection (SendGrid)



---



\## Requirements (on Windows)

1\) Node.js + npm installed  

2\) Python installed  

3\) PostgreSQL installed \& running  

4\) FFmpeg installed (ffmpeg works in PowerShell)  

5\) fpcalc installed (fpcalc works in PowerShell)



---



\## First-time install (if needed)

Backend:

\- `cd C:\\Projects\\sampledetect-mvp\\backend`

\- `python -m venv .venv`

\- `.\\.venv\\Scripts\\activate`

\- `pip install -r requirements.txt`



Frontend:

\- `cd C:\\Projects\\sampledetect-mvp\\frontend`

\- `npm install`



---



\## Storage Folder

This prototype uses an external/local folder:

`D:\\SampleDetectStorage`



It contains:

\- `uploads\\`

\- `fingerprints\\`

\- `spectrograms\\`

\- `library\_audio\\`

\- `library\_fingerprints\\`

\- `audfprint\\`

\- `monitor\_inbox\\`

\- `monitor\_processed\\`



---



\## How to run (ONE CLICK)

1\) Open PowerShell

2\) Run:



`C:\\Projects\\sampledetect-mvp\\SUBMISSION\\start\_all.ps1`



3\) Open:

http://localhost:3000



Backend Swagger:

http://127.0.0.1:8001/docs



---



\## If you don't have a D: drive

Run this once to choose a storage folder (example C:\\SampleDetectStorage):



`C:\\Projects\\sampledetect-mvp\\SUBMISSION\\set\_storage\_root.ps1`



---



\## Demo steps (quick)

1\) Click \*\*Rebuild audfprint index\*\*

2\) Upload an audio file

3\) Click \*\*Audfprint Match (VR)\*\*

4\) Click \*\*Record Proof\*\* (writes proof hash to Polygon Amoy + sends email)

5\) Put a file in:

`D:\\SampleDetectStorage\\monitor\_inbox`

6\) Click \*\*Scan Monitor Inbox\*\*

7\) Check email for alert



---



\## Notes

\- If the page is cluttered, click \*\*Clear Recent Uploads\*\* (this deletes only uploaded test assets, not the library).

