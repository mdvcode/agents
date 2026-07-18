# Report

Created `/Users/user/agents/output/pdf/Daryna_Barabanova_CV_ATS.pdf` as a new ATS-safe CV while leaving the supplied PDF untouched.

## Changes
- Rebuilt the CV as two uniform A4 pages in a simple single-column reading order.
- Embedded Arial regular and bold as TrueType subset fonts with ToUnicode maps.
- Added explicit separators between location, phone, and email.
- Printed complete LinkedIn, GitHub, employer, and institute URLs as visible text and valid links.
- Replaced Type3 per-character text with conventional text runs.
- Normalized punctuation, dates, and selected wording while preserving the supplied facts.
- Balanced the page break between the two experience entries.

## Validation
- PyPDF, PDFMiner, and pdfplumber all return `mdv.coding@gmail.com` exactly.
- Every parser finds `CMS`, `Make`, `LLM`, `Management`, `Mar`, `Maintained`, `MVP`, `MoonPay`, `EVM`, `GitHub`, `Python`, `Django`, `FastAPI`, `PostgreSQL`, and `LangChain`.
- Both pages are A4 and rendered cleanly at 180 DPI.
- All displayed fonts are embedded TrueType with ToUnicode mappings.
- No hidden, white, off-page, tiny, duplicated, image-only, scripted, encrypted, or embedded-file content was found.
- Final SHA-256: `5bb5099602450ccb06b4c3677c3f1acc54f9f685bc6cd9cd0c29c5d327bab22c`.

## Next Action
Use the generated PDF for applications. No commit, push, PR, merge, deployment, or external upload was performed.
