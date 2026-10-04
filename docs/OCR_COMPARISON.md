# Quarterly-report OCR: engine comparison and extraction gate

## Sources of report documents

- **Text PDFs** (the first choice) are not available at scale:
  - NEPSE's notices answer 401.
  - SEBON hosts no quarterly reports.
  - The three bank websites checked (Nabil, Global IME, NIC Asia) list reports through JavaScript, and no PDF link was reachable from their pages. Per-company crawling of about 300 sites was not attempted.
  - Every Sharesansar attachment in the sample is an image (JPEG or PNG). None is a PDF.
- **Images:** the report images attached to Sharesansar announcements (`src/archive/report_documents.py`) are the only source at scale. They are stored immutably with their URL, the announcement date and, where the file name carries one, the upload timestamp.

## Sample and ground truth

- **Sample:** 72 Sharesansar quarterly-report announcements, 6 per publication year from 2014 to 2025 (seeded), gave 71 images (one page is a 404). Manifest: `docs/ocr/sample_manifest.json`.
- **Net profit:** the Sharesansar headline figure for all 71. It is rounded to 2-3 significant digits, so a value counts as right within 2%. Its basis (group or standalone, year-to-date) follows the headline.
- **Gold labels** (`docs/ocr/gold_labels.json`): 11 reports from 2014-2025, read by eye from the images, with crops where the print was small, for net profit, EPS and net worth (book value) per share.
  - **The set spans:** commercial and development banks, a microfinance company, insurance, manufacturing, English tables and Nepali disclosures with Devanagari digits.
  - **Tolerance:** EPS and book value count as right within 1%.
  - **Why not our parsed EPS and book values:** they exist only for the 2026 latest quarter, inside the holdout, and were not used.
- **One parser for every engine** (`src/archive/ocr_eval.py`). It does not choose units by looking at the truth. It handles:
  - row labels in English and Nepali;
  - Devanagari digits;
  - slash decimals (रु.१०१/५०);
  - the unit line (Rs in '000, lakh);
  - the year-to-date column when the header shows both "This Quarter" and "Up to This Quarter".

  **It was frozen before scoring and not tuned on the gold set,** so the numbers below are not fitted to these 11 reports.

## Results (laptop: 8 GB RAM, CPU only)

| Engine | Seconds per report | Net profit vs headline (71) | Net profit vs gold (11) | EPS vs gold (11) | Book value vs gold (11) | Gold EPS/BV present anywhere in the text (22) |
|---|---|---|---|---|---|---|
| Tesseract 5.5.3, nep+eng, psm 6 | 7.3 | 9 (13%) | 1 | 4 | 4 | 11 |
| PaddleOCR 3.7, PP-OCRv6 medium, English | 90.7 | 53 (75%) | 8 | 5 | 5 | 11 |
| PaddleOCR 3.7, PP-OCRv5 mobile, English | 50.4 | 51 (72%) | 8 | 6 | 5 | 11 |
| Surya 0.22 (llama.cpp) | 198 | 5 of the 11 gold reports | 5 | 8 | 6 | 14 |

**Notes:**
- **Surya:** the full 71-report run stalled under memory pressure (its model server ran at 14-20% CPU while swapping), so Surya was scored on the 11 gold reports only.
- **Gold values present:** this counts whether the right number appears anywhere in the OCR text. It separates OCR errors from parser errors.

**What fails:**
- **Tesseract** splits table cells and misreads digits (32,59 for 32.55).
- **PaddleOCR (English models)** reads English tables well. It cannot read the Devanagari disclosures, where most older reports state EPS and net worth per share, so EPS and book value are missing on 4 of 11 gold reports.
- **Surya** reads the Nepali disclosures correctly (BSBL, SEWA, RMDC all right). It is about 4 times slower than PaddleOCR mobile here, and it could not finish the full sample on this machine.
- **The parser,** for every engine:
  - **Wrong column:** it takes the first number after the label, which is the bank column where the headline uses the group figure (NMB), or the group figure where the headline uses the bank (NABIL EPS).
  - **Wrong unit:** a stray "लाख" can switch the unit to lakh (JBBL with Surya).
  - **Wrong row:** it can match a "net profit before share of associates" row (PRIN).

  These are known and should be fixed, then re-measured on a **new** gold set, not on these 11.

## Choice and gate

- **Engine:** PaddleOCR with the PP-OCRv5 mobile models. Its accuracy is the same as the medium models within one report, it is about 45% faster, and it runs on this laptop. Surya is the better reader of Nepali text, but it is too slow and too memory-hungry here.
- **Gate:** an OCR value is accepted only for a field whose measured accuracy is at least 90% on both the headline check and the gold set. **No field passes:**
  - net profit 0.72 against the headline and 0.73 against gold;
  - EPS 0.55;
  - book value 0.45.
- **Unmeasured fields:** reserves, NPL ratio, capital adequacy and dividend declared have no measured accuracy, so they are flagged as well.
- **What is stored:** every extracted value goes into `report_field_values` with `accepted = false` and the reason, so nothing unverified can enter a feature. The holdout policy applies on the report's announcement date.

## Pipeline

1. `python -m src.archive.report_documents`: collects report images (rate-limited, resumable). It runs newest first. On 2026-10-05, 2024 and 2025 are complete and 2023 is partial (310 of 930 reports); earlier years have only the sample.
2. `python -m src.archive.report_extraction manifest`: lists the stored images (2,101 at the time of writing).
3. `nice -n 19 .venv-ocr/bin/python scripts/ocr/ocr_run.py batch <manifest> paddleocr_mobile <dir>`: writes one text file per image, resumable. At about 50 s per image, the 2,101 images take about 30 hours and all 9,900 development-window reports about 6 days on this laptop.
4. `python -m src.archive.report_extraction store`: extracts net profit, EPS, book value, reserves, NPL ratio, capital adequacy and dividend, and stores them with the gate decision.

**Images by sector so far:** Hydro Power 742, Microfinance 453, Commercial Banks 158, Development Banks 135, Finance 132, Life Insurance 115, Non Life Insurance 113, Manufacturing 81, Investment 61, Hotels 53, Others 50, Trading 9.

## Next steps that would raise accuracy

1. Use a Devanagari recognition model (PaddleOCR `devanagari` or Surya on a machine with more memory) for the disclosure section.
2. Fix the parser's column, unit and row choices, then measure on a new gold set of at least 30 reports.
3. Prefer the Sharesansar net-profit headline (already dated and 94% parsed) over OCR net profit until OCR passes the gate.
