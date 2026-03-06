\# SampleDetect SAE — Demo Steps (2–4 minutes)



\## 1) Start the app

1\. Run: `SUBMISSION\\set\_storage\_root.ps1` (choose C:\\SampleDetectStorage or D:\\SampleDetectStorage)

2\. Run: `SUBMISSION\\start\_all.ps1`

3\. Open: http://localhost:3000



\## 2) Prepare library

1\. Place 3–5 beats into: `<STORAGE\_ROOT>\\library\_audio`

2\. Click: \*\*Rebuild audfprint index\*\*



\## 3) Run detection

1\. Upload a file (e.g., beat+vocals)

2\. Click: \*\*Audfprint Match (VR)\*\*

3\. Confirm the matched beat shows with “Detected ✅”



\## 4) Blockchain proof

1\. Click: \*\*Record Proof\*\*

2\. Confirm PolygonScan link appears



\## 5) Monitoring demo

1\. Drop a file into: `<STORAGE\_ROOT>\\monitor\_inbox`

2\. Click: \*\*Scan Monitor Inbox\*\*

3\. Confirm a new incident row appears + email status shows “sent ✅”

4\. (Optional) Copy the SendGrid Message ID from the UI to verify in SendGrid Activity.



\## 6) Clean UI

Click: \*\*Clear Recent Uploads\*\* (for presentation)

