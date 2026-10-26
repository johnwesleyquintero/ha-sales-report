# HA Sales Report

Python-based data cleaning and validation for Hungry Artisan Amazon sales reports.

## Purpose

Prepare reliable sales data for Looker Studio visualizations, including sales bubble maps by region, state, and city.

The goal is to catch data quality issues before they affect reporting, while preserving the original source data for traceability.

## Workflow

1. **Export** — Download the sales report CSV from the designated source tab.
2. **Inspect** — Review columns, data types, missing values, duplicates, and inconsistencies.
3. **Clean** — Standardize geographic fields, dates, and data formats where appropriate.
4. **Validate** — Flag invalid locations, missing values, duplicate records, and sales figures that need investigation.
5. **Export** — Produce a cleaned CSV for Looker Studio.
6. **Review** — Resolve flagged issues before using the data for business decisions.

## Project Structure

```text
ha-sales-report/
├── README.md
├── .gitignore
├── requirements.txt
├── src/
│   └── clean_sales.py
├── data/
│   ├── raw/          # Original exports — local only
│   └── processed/    # Cleaned exports — local only
└── reports/          # Validation reports — local only
```

## Data Quality Rules

- Preserve the original source fields and values.
- Standardize US state codes and geographic field formats.
- Preserve ZIP codes as text to avoid losing leading zeros or ZIP+4 formatting.
- Validate dates and numeric fields before transformation.
- Compare duplicate-looking columns before deciding whether they are redundant.
- Investigate discrepancies in sales amounts rather than assuming a formula.
- Flag questionable records instead of silently guessing or deleting them.
- Keep raw exports unchanged so results can be traced back to the source.

## Data Privacy

Sales exports may contain customer names, order IDs, postal codes, and other sensitive business information.

- Do not commit raw or processed sales exports to GitHub.
- Keep local data and validation reports excluded through `.gitignore`.
- Do not publish customer-level information in documentation or examples.
- Share only the minimum data needed for troubleshooting.

## Tools

- Python
- pandas
- CSV
- Looker Studio

Dependencies will be added to `requirements.txt` as needed.

## Current Scope

**First milestone:** Create a validated, cleaned sales CSV suitable for Looker Studio's geographic bubble map.

Initial priorities:

- Standardize city, state, postal code, and region fields.
- Validate dates and numeric sales fields.
- Identify missing or invalid geographic data.
- Check duplicate records and inconsistent sales totals.
- Generate a validation summary alongside the cleaned output.

## Guiding Principle

Keep the workflow simple, transparent, and auditable. Fix actual data quality problems first; add complexity only when a real need emerges.