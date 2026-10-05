# Labeling 120 reports

The labels are the gold set that decides whether any extracted fundamental field reaches 99% precision (`src/archive/fundamentals_quality.py`).

## Run the tool

```bash
cd ~/Desktop/arthasignal
venv/bin/python -m src.archive.label_tool          # prints: labeling tool on http://127.0.0.1:8765
```

Open http://127.0.0.1:8765 in a browser.
- **The index** lists the 120 reports (`docs/labels/label_set.json`) with their status.
- **Navigation:** click an id to open the report image beside the form. Use *zoom +* / *zoom -*, or *open full size*, to read small print.
- **To stop:** press Ctrl+C in the terminal. Labels are saved after every *save*.
- The tool only listens on 127.0.0.1, so it is not reachable from other machines.

## How to fill a report

1. **fiscal_year:** as printed, for example `2079/80`. **period:** quarterly or annual. **quarter:** 1-4 (4 for an annual report).
2. **basis:** if the report shows both *Group* and *Bank* (standalone) columns, pick one and use it for every field; otherwise choose "only one entity shown".
3. **unit:** the statement's unit line ("Rs in '000" means thousands; "Amount in NPR" means rupees).
4. **Numbers:** type them exactly as printed, in the current-period column.
   - Commas, Devanagari digits and brackets for losses are fine.
   - **net_profit:** the year-to-date figure ("Up to this quarter"), not the this-quarter-only column, when both exist.
   - **eps:** as printed, and set *eps_annualized* to yes or no when the report says so.
   - **book_value_per_share:** "Net worth per share" or "Book value per share" (often in the Nepali disclosure: प्रति शेयर नेटवर्थ).
   - **net_worth:** total equity. **reserves:** reserves and surplus total. **paid_up_capital:** share capital (it is needed for the EPS and book-value checks).
   - **npl_ratio and capital_adequacy:** banks and financial institutions only ("NPL to total loan", "Capital fund to RWA").
5. **Absent figures:** tick *not in report* when a figure is not printed. Do not compute a figure that is not printed.
6. **Quality:** set *language* and *legibility*. Use *unreadable* when a number cannot be read with certainty; those reports are left out of the measurement.
7. **save and next:** if a number cannot be parsed, the page shows the error and stays open.

Each report is saved as `docs/labels/labels/L###.json`. When you are done (or after any batch), commit them:

```bash
git add docs/labels/labels && git commit -m "Add report labels" && git push
```

## After labeling

```bash
venv/bin/python -m src.archive.fundamentals_quality
```

This measures, for every field and method, precision and coverage with 95% Wilson intervals, and writes `docs/fundamentals_precision.json`. The methods are Tesseract, PaddleOCR mobile, Surya, text-layer PDFs, two-engine consensus, consensus after the accounting checks, and the Sharesansar headline. A field is used only if its precision is at least 99%.

**Caution:** with 120 reports, even 120 right out of 120 gives a lower 95% bound of about 97%. The 99% rule is applied to the point estimate, and the interval is reported next to it.
