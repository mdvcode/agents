# Goal

## GOAL
- Create a new ATS-safe PDF CV from the supplied document, preserving its factual content while fixing parser, page-size, contact, URL, and wording defects.

## CONTEXT
- Source: `/Users/user/Desktop/Daryna_B_CV_AI_Developer (2).pdf`.
- Output: `/Users/user/agents/output/pdf/Daryna_Barabanova_CV_ATS.pdf`.
- Branch: `feat/v2`.
- Selected profile: `agent_workspace` for local generation and audit artifacts.

## CONSTRAINTS
- Use two uniform A4 pages and a single-column reading order.
- Use embedded standard TrueType fonts and selectable text.
- Use ASCII hyphens only; no tables, icons, sidebars, or hidden text.
- Keep personal data local and do not publish or upload the CV.

## PRIORITY
- Primary: robust extraction of contact data, section order, and exact technical keywords.
- Secondary: polished human-readable layout and balanced page breaks.
- Non-goals: invent achievements, alter employment claims, or publish the file.

## PLAN
1. Reconstruct and lightly normalize the supplied CV content.
2. Generate a two-page A4 PDF with ReportLab and embedded Arial fonts.
3. Render every page and inspect spacing, clipping, hierarchy, and page balance.
4. Compare extraction with PyPDF, PDFMiner, and pdfplumber; verify contacts and keywords.
5. Inspect font resources, links, page boxes, hidden text states, and structural safety.
6. Update current-task artifacts and run repository checks.

## DONE WHEN
- Both pages are A4 and visually defect-free.
- Arial is embedded as a conventional TrueType font with ToUnicode mapping.
- All parsers return the correct email and critical keywords without internal spaces.
- Contact data and full LinkedIn/GitHub URLs appear as visible text in reading order.
- Required local checks pass.

## VERIFY
- Commands: ReportLab generation; Poppler `pdfinfo`/`pdftoppm`; PyPDF; PDFMiner; pdfplumber; `make validate-artifacts`; `make security`; `make check`.
- Expected evidence: two A4 pages, correct extraction, embedded font program, valid links, no hidden or off-page text.

## OUTPUT
- Final PDF under `output/pdf/`.
- Temporary renders and generator removed after verification.
- Current-task artifacts and audit log.

## STOP RULES
- Stop before changing unverifiable facts or publishing the CV externally.
