#!/usr/bin/env python3
"""Normalize Hungry Artisan Amazon sales exports into Looker Studio-ready data.

Reads the raw Amazon customer-shipment exports in data/raw, merges them (dropping
rows duplicated across overlapping exports), standardizes geographic, date and
numeric fields, flags data-quality issues, and writes a cleaned dataset, a
location-level aggregate for bubble maps, and a validation report.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "HA Sales per month - FC and Region Mapping.csv"
RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "processed" / "clean_sales.csv"
DEFAULT_LOCATION_OUTPUT = PROJECT_ROOT / "data" / "processed" / "sales_by_location.csv"
DEFAULT_FLAGGED = PROJECT_ROOT / "reports" / "flagged_records.csv"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "validation_report.md"

DATE_COL = "Customer Shipment Date"
CITY_COL = "Shipment To City"
STATE_COL = "Shipment To State"
POSTAL_COL = "Shipment To Postal Code"
ORDER_COL = "Amazon Order Id"
SKU_COL = "Merchant SKU"
PRODUCT_COL = "Raw material"

NUMERIC_COLS = [
    "Quantity",
    "Product Amount",
    "Shipping Amount",
    "Gift Amount",
    "TOTAL PRICE",
    "Total Sales",
]

TERRITORY_REGIONS = {
    "PR": "Territory",
    "GU": "Territory",
    "VI": "Territory",
    "AS": "Territory",
    "MP": "Territory",
    "AE": "Military (APO/FPO)",
    "AA": "Military (APO/FPO)",
    "AP": "Military (APO/FPO)",
}

# APO/FPO destination codes. Not plottable as states, so the report groups them into
# one Military row; the codes still count as US geography and stay in the US totals.
MILITARY_CODES = {"AE", "AA", "AP"}

UPPERCASE_CITIES = {"APO", "DPO", "FPO"}

# Canadian provinces and territories (full names and codes) commonly present in
# Amazon North-America exports. None of these collide with USPS state codes.
CANADA_CODES = {
    "ON": "Ontario", "AB": "Alberta", "QC": "Quebec", "BC": "British Columbia",
    "SK": "Saskatchewan", "MB": "Manitoba", "NS": "Nova Scotia",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "PE": "Prince Edward Island", "YT": "Yukon", "NT": "Northwest Territories",
    "NU": "Nunavut",
}
CANADA_NAMES = {
    "ONTARIO": "ON", "ALBERTA": "AB", "QUEBEC": "QC", "QUÉBEC": "QC",
    "BRITISH COLUMBIA": "BC", "SASKATCHEWAN": "SK", "MANITOBA": "MB",
    "NOVA SCOTIA": "NS", "NEW BRUNSWICK": "NB",
    "NEWFOUNDLAND AND LABRADOR": "NL", "PRINCE EDWARD ISLAND": "PE",
    "YUKON": "YT", "NORTHWEST TERRITORIES": "NT", "NUNAVUT": "NU",
}

# Regions for US states that do not host an Amazon fulfillment center and are
# therefore absent from the FC mapping config. Kept consistent with its scheme.
US_REGION_FALLBACK = {
    "AK": "West", "HI": "West", "MT": "West", "WY": "West",
    "ND": "Central", "ME": "East", "VT": "East", "WV": "East",
}

# Amazon masks the buyer's location with these values when privacy is requested.
CONFIDENTIAL_MARKERS = {"CONFIDENTIAL", "--", "", "UNKNOWN"}

# Non-US marketplaces whose rows the state/province columns cannot identify, keyed
# by marketplace currency. The UK store only ships in GBP, so that is the signal.
MARKETPLACE_BY_CURRENCY = {"GBP": "United Kingdom"}


def load_reference(config_path: Path) -> tuple[dict, dict, set, dict, dict]:
    """Build state name/code lookups and state -> region map from the config file."""
    ref = pd.read_csv(config_path, dtype=str, keep_default_na=False)

    fc_table = ref.iloc[:, 0:5].copy()
    fc_table.columns = ["fc_code", "state_code", "state_name", "region", "facility_type"]

    name_table = ref.iloc[:, 6:8].copy()
    name_table.columns = ["state_full_name", "state_code"]

    name_to_code: dict[str, str] = {}
    for row in name_table.itertuples():
        full = row.state_full_name.strip().upper()
        code = row.state_code.strip().upper()
        if full and code and code not in CANADA_CODES:
            name_to_code[full] = code

    code_to_region: dict[str, str] = {}
    for row in fc_table.itertuples():
        code = row.state_code.strip().upper()
        region = row.region.strip()
        if code and region and code not in CANADA_CODES and code not in code_to_region:
            code_to_region[code] = region

    valid_codes = (
        set(US_REGION_FALLBACK)
        | set(code_to_region)
        | set(name_to_code.values())
        | set(TERRITORY_REGIONS)
    )

    # FC codes are matched case-insensitively: the config stores `Jan1` while an
    # export can write `JAN1`, and a case-sensitive lookup would leave Region blank.
    fc_map: dict[str, str] = {}
    for row in fc_table.itertuples():
        fcode = row.fc_code.strip().upper()
        region = row.region.strip()
        if fcode and region and fcode not in fc_map:
            fc_map[fcode] = region

    # Country-aware subdivision -> region table (Canada by province, UK by nation).
    # Drives Region (Standardized) outside the US, where the US region scheme does
    # not apply. Kept as a separate side table so it can be maintained per country.
    country_regions: dict[tuple[str, str], str] = {}
    if ref.shape[1] >= 13:
        subdiv_table = ref.iloc[:, 9:13].copy()
        subdiv_table.columns = ["country", "subdiv_code", "subdiv_name", "region"]
        for row in subdiv_table.itertuples():
            country = row.country.strip()
            code = row.subdiv_code.strip().upper()
            name = row.subdiv_name.strip().upper()
            region = row.region.strip()
            if country and code and region:
                country_regions[(country, code)] = region
            if country and name and region:
                country_regions[(country, name)] = region

    return name_to_code, code_to_region, valid_codes, fc_map, country_regions


def clean_city(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text:
        return ""
    if text.upper() in UPPERCASE_CITIES:
        return text.upper()
    return text.title()


def clean_postal(value: str) -> tuple[str, str]:
    raw = str(value).strip()
    if not raw:
        return "", ""
    base = raw.split("-")[0]
    digits = "".join(ch for ch in base if ch.isdigit())
    zip5 = digits.zfill(5)[:5] if digits else ""
    return raw, zip5


def to_number(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.replace(r"[$,]", "", regex=True).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


# Product fields a base Amazon export omits; recoverable from the SKU.
SKU_DERIVED_COLS = ["Raw material", "Name", "Catergory"]
MERGE_KEY = [ORDER_COL, SKU_COL, DATE_COL]


def _read_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    empty = [c for c in df.columns if str(c).startswith("Unnamed") or df[c].astype(str).str.strip().eq("").all()]
    return df.drop(columns=empty)


def _merge_keys(df: pd.DataFrame) -> list:
    """Order + SKU + shipment instant. Timestamps are normalized to UTC so a
    re-export written with a different offset still matches."""
    dates = pd.to_datetime(df[DATE_COL], errors="coerce", format="ISO8601", utc=True)
    return list(zip(df[ORDER_COL], df[SKU_COL], dates))


def _backfill(df: pd.DataFrame, columns: list, sku_maps: dict, fc_map: dict) -> pd.DataFrame:
    """Add the columns a partial export omits, deriving everything we can."""
    for col in columns:
        if col not in df.columns:
            df[col] = ""

    for col, mapping in sku_maps.items():
        blank = df[col].eq("")
        if blank.any():
            df.loc[blank, col] = df.loc[blank, SKU_COL].map(mapping).fillna("")

    # A foreign-marketplace export often leaves Region blank, and a column that is
    # blank in every row is dropped on read, so guard before filling it.
    if fc_map and "Region" in df.columns and "FC" in df.columns:
        blank = df["Region"].eq("")
        if blank.any():
            df.loc[blank, "Region"] = df.loc[blank, "FC"].str.upper().map(fc_map).fillna("")

    if "Product Amount" in df.columns:
        product = to_number(df["Product Amount"])
        shipping = to_number(df["Shipping Amount"]) if "Shipping Amount" in df.columns else 0.0
        gift = to_number(df["Gift Amount"]) if "Gift Amount" in df.columns else 0.0
        # Amazon's own sales columns are not exported in a base export; they equal
        # the amount columns the export does carry. `Product Amount` is a per-unit
        # price, so the quantity has to be applied before the line total is taken.
        quantity = to_number(df["Quantity"]).fillna(1) if "Quantity" in df.columns else 1.0
        if df["Total Sales"].eq("").all():
            df["Total Sales"] = (quantity * product - shipping - gift).round(2)
        if df["TOTAL PRICE"].eq("").all():
            df["TOTAL PRICE"] = (quantity * product).round(2)
    return df[columns]


def load_inputs(paths: list, fc_map: dict) -> tuple[pd.DataFrame, list]:
    """Read raw exports, backfill partial ones, and merge them into one frame.

    Files are merged richest-column-first. A row from a later file is dropped when
    the same order + SKU + shipment instant is already present, so overlapping
    re-exports collapse without losing genuinely new shipments. `fc_map` is the
    config-backed FC -> region lookup used to fill a blank Region on partial exports.
    """
    frames = [(p, _read_raw(p)) for p in paths]
    frames.sort(key=lambda item: len(item[1].columns), reverse=True)
    ref = frames[0][1]
    columns = list(ref.columns)

    sku_maps = {
        col: ref.drop_duplicates(SKU_COL).set_index(SKU_COL)[col].to_dict()
        for col in SKU_DERIVED_COLS
        if col in ref.columns and SKU_COL in ref.columns
    }

    # The reference export carries the authoritative FC -> region values (it also
    # covers Canadian and newer FCs the config omits); the config map only fills
    # the FCs the reference never shipped. Both are keyed in upper case because an
    # export can change the case of an FC code (`JAN1` vs the config's `Jan1`).
    if {"FC", "Region"} <= set(ref.columns):
        ref_fc_map = {
            str(k).strip().upper(): str(v).strip()
            for k, v in ref.drop_duplicates("FC").set_index("FC")["Region"].items()
            if str(v).strip()
        }
    else:
        ref_fc_map = {}
    fc_map = {**fc_map, **ref_fc_map}

    merged = None
    seen: set = set()
    for path, df in frames:
        df = _backfill(df, columns, sku_maps, fc_map)
        keys = _merge_keys(df)
        if merged is None:
            merged = df
            seen.update(keys)
        else:
            fresh = [k not in seen for k in keys]
            seen.update(k for k, ok in zip(keys, fresh) if ok)
            merged = pd.concat([merged, df[fresh]], ignore_index=True)
    return merged, [p for p, _ in frames]


def normalize(df: pd.DataFrame, name_to_code: dict, code_to_region: dict, valid_codes: set, country_regions: dict) -> pd.DataFrame:
    original_cols = list(df.columns)

    parsed = pd.to_datetime(df[DATE_COL], errors="coerce", format="ISO8601", utc=True)
    local = parsed.dt.tz_convert("America/Los_Angeles")

    df["Shipment Date"] = local.dt.strftime("%Y-%m-%d")
    df["Shipment Year-Month"] = local.dt.strftime("%Y-%m")
    df["Shipment Month"] = local.dt.strftime("%B")
    df["Shipment Week"] = local.dt.isocalendar()["week"]
    df["Shipment Day of Week"] = local.dt.day_name()
    df["Shipment Hour"] = local.dt.hour

    states = df[STATE_COL].astype(str).str.strip().str.upper()
    cities = df[CITY_COL].astype(str).str.strip().str.upper()
    region_map = {**US_REGION_FALLBACK, **code_to_region, **TERRITORY_REGIONS}

    # US state/territory: accept either a valid code or a full state name.
    us_state = states.where(states.isin(valid_codes), states.map(name_to_code)).fillna("")
    # Canadian province/territory: accept either a code or a full province name.
    ca_state = states.where(states.isin(CANADA_CODES), states.map(CANADA_NAMES)).fillna("")

    # APO/FPO is not a plottable state, so those rows collapse into one Military row
    # instead of separate AE/AA/AP rows. PR/GU/VI keep their own codes and rows.
    df["State (Standardized)"] = us_state.where(~us_state.isin(MILITARY_CODES), "Military (APO/FPO)")
    df["Province (Standardized)"] = ca_state

    # Amazon hides a buyer location by writing a placeholder into BOTH ship-to fields.
    # A placeholder state with a real city is a different case: the destination is
    # known to be foreign, only the state field is blank, so it must not be filed as
    # confidential. Both cases keep their sales in the overall totals.
    is_state_missing = states.isin(CONFIDENTIAL_MARKERS)
    is_city_missing = cities.isin(CONFIDENTIAL_MARKERS)
    is_confidential = is_state_missing & is_city_missing
    is_foreign_city = is_state_missing & ~is_city_missing

    is_canada = ca_state.ne("")
    is_us = us_state.ne("")
    # A marketplace export outside the US/CA store cannot be spotted from geography:
    # its `Shipment To State` holds a county, not a state or province. The
    # marketplace currency is the reliable signal instead.
    if "Currency" in df.columns:
        currency = df["Currency"].astype(str).str.strip().str.upper()
    else:
        currency = pd.Series("", index=df.index)
    marketplace = currency.map(MARKETPLACE_BY_CURRENCY).fillna("")
    country_override = marketplace.ne("")
    is_international = (
        (~is_us & ~is_canada & ~is_state_missing & ~country_override) | is_foreign_city
    )

    country = pd.Series("United States", index=df.index)
    region = us_state.map(region_map)

    # Outside the US the West/East/Central/South East scheme does not apply, so the
    # report groups Canada by province and the UK by nation (config-driven) instead
    # of collapsing every non-US row into a single bucket.
    ca_lookup = {
        code: country_regions.get(("Canada", code)) or name
        for code, name in CANADA_CODES.items()
    }
    ca_region = ca_state.map(ca_lookup).fillna("Canada")
    # Most marketplace exports do not name a nation/subdivision, so fall back to the
    # marketplace name itself until the config gains a county-level mapping.
    flat_lookup = {f"{c}|{k}": v for (c, k), v in country_regions.items()}
    marketplace_region = marketplace.str.cat(states, sep="|").map(flat_lookup)
    marketplace_region = marketplace_region.where(marketplace_region.notna(), marketplace)

    country = country.mask(is_canada, "Canada").mask(country_override, marketplace)
    country = country.mask(is_confidential, "Confidential (Amazon)").mask(is_international, "International")

    region = region.mask(is_canada, ca_region).mask(country_override, marketplace_region)
    region = region.mask(is_confidential, "Unknown").mask(is_international, "International")

    df["Country"] = country
    df["Region (Standardized)"] = region.fillna("Unknown")

    df["City (Standardized)"] = df[CITY_COL].map(clean_city)

    postal = df[POSTAL_COL].map(clean_postal)
    df["Postal Code (Raw)"] = [p[0] for p in postal]
    df["Postal Code (5)"] = [p[1] for p in postal]

    df["Location (City, State)"] = (
        df["City (Standardized)"] + ", " + df["State (Standardized)"]
    ).str.strip(", ")
    df["Location (City, State ZIP)"] = (
        df["Location (City, State)"] + " " + df["Postal Code (5)"]
    ).str.strip()
    # Blank the map location when we have no real US state to plot against.
    df.loc[df["State (Standardized)"].eq(""), ["Location (City, State)", "Location (City, State ZIP)"]] = ""

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = to_number(df[col])

    # Amount columns may be absent from unfamiliar exports; default them to 0 so
    # the derived totals still compute instead of raising KeyError.
    product = df.get("Product Amount", 0.0)
    shipping = df.get("Shipping Amount", 0.0)
    gift = df.get("Gift Amount", 0.0)
    # `Product Amount` is the per-unit price, so the quantity has to be applied
    # before the line total. Amazon's own `Total Sales` subtracts shipping and gift.
    quantity = df["Quantity"].fillna(1) if "Quantity" in df.columns else 1.0

    df["Calculated Net Sales"] = quantity * product - shipping - gift
    gross_cols = [c for c in ("Product Amount", "Shipping Amount", "Gift Amount") if c in df.columns]
    df["Calculated Gross Amount"] = df[gross_cols].sum(axis=1, min_count=1)

    df["flag_non_us_location"] = is_canada | country_override | is_international
    df["flag_confidential_location"] = is_confidential
    df["flag_missing_city"] = df["City (Standardized)"].eq("")
    df["flag_missing_postal"] = df["Postal Code (5)"].eq("")
    df["flag_net_sales_mismatch"] = (
        (df["Total Sales"] - df["Calculated Net Sales"]).abs().gt(0.01).fillna(False)
    )
    # Both flags compare like with like: `TOTAL PRICE` and `Total Sales` are line
    # totals, while `Product Amount` is a per-unit price, so the quantity applies.
    df["flag_total_price_mismatch"] = (
        (df["TOTAL PRICE"] - quantity * product).abs().gt(0.01).fillna(False)
    )
    df["flag_exact_duplicate"] = df.duplicated(subset=original_cols, keep="first")
    df["flag_repeat_order_sku"] = df.duplicated(subset=[ORDER_COL, SKU_COL], keep=False)

    review_flags = [
        "flag_confidential_location",
        "flag_missing_city",
        "flag_missing_postal",
        "flag_net_sales_mismatch",
        "flag_total_price_mismatch",
    ]
    df["record_status"] = "OK"
    df.loc[df[review_flags].any(axis=1), "record_status"] = "Needs review"
    df.loc[df["flag_exact_duplicate"], "record_status"] = "Duplicate (excluded)"

    # U.S. geographic share columns (Victoria's request). Shares are additive:
    # summing a column over the U.S. rows of a single SKU (or of all SKUs) gives
    # ~100%. Blank rows (non-U.S., exact duplicates) contribute nothing to a sum.
    df["US Share of Total Sales (%)"] = float("nan")
    df["US Share of SKU Sales (%)"] = float("nan")
    if "Total Sales" in df.columns:
        us_scope = df["Country"].eq("United States") & ~df["flag_exact_duplicate"]
        us_total = df.loc[us_scope, "Total Sales"].sum()
        if us_total:
            # Shares are kept at 6 dp on purpose: a single order is a tiny slice
            # of the U.S. total (~0.001%), so coarser rounding would erase it and
            # the column would no longer sum to ~100.
            df.loc[us_scope, "US Share of Total Sales (%)"] = (
                df.loc[us_scope, "Total Sales"] / us_total * 100
            ).round(6)
            if PRODUCT_COL in df.columns:
                sku_totals = df.loc[us_scope].groupby(PRODUCT_COL)["Total Sales"].sum()
                denom = df.loc[us_scope, PRODUCT_COL].map(sku_totals)
                df.loc[us_scope, "US Share of SKU Sales (%)"] = (
                    df.loc[us_scope, "Total Sales"] / denom * 100
                ).round(6)

        # Region/State breakdowns. Each value is constant within its group (every
        # U.S. row of one SKU+Region carries the same number), so these are read
        # with MAX or AVG in Looker - summing them would multiply by the row count.
        # The numerator is the group's sales, the denominator its parent: the same
        # SKU (or the whole U.S. market) when the column is scoped to total sales.
        # Two decimals is enough here because the values are group-sized (>=0.01),
        # unlike the two additive columns above, where a single order is ~0.001%.
        breakdown_cols = [
            "US Share of SKU Sales by Region (%)",
            "US Share of SKU Sales by State (%)",
            "US Share of Total Sales by Region (%)",
            "US Share of Total Sales by State (%)",
        ]
        for col in breakdown_cols:
            df[col] = float("nan")
        us_rows = df.loc[us_scope]
        for col, sku_key, geo_key in (
            (breakdown_cols[0], PRODUCT_COL, "Region (Standardized)"),
            (breakdown_cols[1], PRODUCT_COL, "State (Standardized)"),
            (breakdown_cols[2], None, "Region (Standardized)"),
            (breakdown_cols[3], None, "State (Standardized)"),
        ):
            if geo_key not in df.columns:
                continue
            keys = [geo_key] if sku_key is None or sku_key not in df.columns else [sku_key, geo_key]
            group_total = us_rows.groupby(keys)["Total Sales"].transform("sum")
            if len(keys) > 1:
                parent_total = us_rows.groupby(keys[0])["Total Sales"].transform("sum")
            else:
                parent_total = us_total
            df.loc[us_scope, col] = (
                group_total / pd.Series(parent_total, index=us_rows.index).replace(0, float("nan")) * 100
            ).round(2)

    ordered = original_cols + [
        "Shipment Date",
        "Shipment Year-Month",
        "Shipment Month",
        "Shipment Week",
        "Shipment Day of Week",
        "Shipment Hour",
        "City (Standardized)",
        "State (Standardized)",
        "Province (Standardized)",
        "Region (Standardized)",
        "Country",
        "Postal Code (Raw)",
        "Postal Code (5)",
        "Location (City, State)",
        "Location (City, State ZIP)",
        "Calculated Net Sales",
        "Calculated Gross Amount",
        "flag_non_us_location",
        "flag_confidential_location",
        "flag_missing_city",
        "flag_missing_postal",
        "flag_net_sales_mismatch",
        "flag_total_price_mismatch",
        "flag_exact_duplicate",
        "flag_repeat_order_sku",
        "record_status",
        "US Share of Total Sales (%)",
        "US Share of SKU Sales (%)",
        "US Share of SKU Sales by Region (%)",
        "US Share of SKU Sales by State (%)",
        "US Share of Total Sales by Region (%)",
        "US Share of Total Sales by State (%)",
    ]
    return df[[c for c in ordered if c in df.columns]]


def build_location_aggregate(df: pd.DataFrame) -> pd.DataFrame:
    # Exact duplicate export rows are byte-identical glitches: drop the extra
    # copies so the map does not double-count sales, while leaving the
    # row-level file untouched for traceability.
    deduped = df[~df["flag_exact_duplicate"]]
    group_cols = [
        "Country",
        "Region (Standardized)",
        "State (Standardized)",
        "Province (Standardized)",
        "City (Standardized)",
        "Postal Code (5)",
    ]
    agg = (
        deduped.groupby(group_cols, dropna=False)
        .agg(
            Orders=(ORDER_COL, "nunique"),
            Units=("Quantity", "sum"),
            Product_Amount=("Product Amount", "sum"),
            Shipping_Amount=("Shipping Amount", "sum"),
            Net_Sales=("Total Sales", "sum"),
        )
        .reset_index()
        .sort_values("Net_Sales", ascending=False)
    )
    # Prefer the US state; fall back to the Canadian province for the map label.
    state = agg["State (Standardized)"].where(agg["State (Standardized)"].ne(""), agg["Province (Standardized)"])
    agg["Location (City, State)"] = (agg["City (Standardized)"] + ", " + state).str.strip(", ")
    agg.loc[state.eq(""), "Location (City, State)"] = ""
    return agg


def build_report(df: pd.DataFrame, sources: list) -> str:
    deduped = df[~df["flag_exact_duplicate"]]
    source_list = ", ".join(f"`{p.name}`" for p in sources)
    lines = [
        "# HA Sales Report - Validation Summary",
        "",
        f"- Sources: {source_list}",
        f"- Rows processed: {len(df):,}",
        "",
        "## Record status",
        "",
        "| Status | Rows |",
        "| --- | --- |",
    ]
    for status, count in df["record_status"].value_counts().items():
        lines.append(f"| {status} | {count:,} |")

    lines += [
        "",
        "## Flagged issues",
        "",
        "| Issue | Rows |",
        "| --- | --- |",
    ]
    labels = {
        "flag_non_us_location": "Non-US location (Canada / International)",
        "flag_confidential_location": "Confidential location (Amazon hidden)",
        "flag_missing_city": "Missing city",
        "flag_missing_postal": "Missing postal code",
        "flag_net_sales_mismatch": "Net sales does not match Quantity x Product - Shipping - Gift",
        "flag_total_price_mismatch": "TOTAL PRICE does not match Quantity x Product Amount",
        "flag_exact_duplicate": "Exact duplicate row (excluded from totals)",
        "flag_repeat_order_sku": "Order Id + SKU appears more than once (often split shipments)",
    }
    for flag, label in labels.items():
        lines.append(f"| {label} | {int(df[flag].sum()):,} |")

    dates = df["Shipment Date"].replace("", pd.NA).dropna()
    if not dates.empty:
        lines += [
            "",
            "## Date coverage",
            "",
            f"- Earliest shipment: {dates.min()}",
            f"- Latest shipment: {dates.max()}",
            "",
            "Rows and net sales by year:",
            "",
            "| Year | Rows | Net Sales |",
            "| --- | --- | --- |",
        ]
        by_year = df.groupby(df["Shipment Date"].str[:4]).agg(
            Rows=(ORDER_COL, "size"), Net_Sales=("Total Sales", "sum")
        )
        for year, row in by_year.iterrows():
            if year:
                lines.append(f"| {year} | {int(row['Rows']):,} | {row['Net_Sales']:,.2f} |")
        lines += [
            "",
            "> Periods are uneven: 2025 starts in April and 2026 ends mid-October, so compare "
            "months/weeks rather than raw yearly totals.",
        ]

    lines += ["", "## Net sales by country", "", "| Country | Net Sales |", "| --- | --- |"]
    country_sales = deduped.groupby("Country")["Total Sales"].sum().sort_values(ascending=False)
    lines += [f"| {country} | {value:,.2f} |" for country, value in country_sales.items()]

    lines += ["", "## Net sales by region", "", "| Region | Net Sales |", "| --- | --- |"]
    region_sales = deduped.groupby("Region (Standardized)")["Total Sales"].sum().sort_values(ascending=False)
    lines += [f"| {region} | {value:,.2f} |" for region, value in region_sales.items()]

    dropped = df.loc[df["flag_exact_duplicate"], "Total Sales"].sum()
    lines += [
        "",
        "## Totals",
        "",
        f"- Net sales (all rows): {df['Total Sales'].sum():,.2f}",
        f"- Net sales (exact duplicates removed): {deduped['Total Sales'].sum():,.2f}",
        f"- Inflation removed by de-duplicating: {dropped:,.2f}",
        "",
        "## Next steps",
        "",
        "- The location aggregate and totals above exclude exact duplicate rows; report from `record_status != 'Duplicate (excluded)'`.",
        "- Non-US rows (Canada / International) are separated by `Country` so they do not distort the US bubble map.",
        "- Rows flagged `Confidential` have both ship-to fields masked by Amazon and cannot be mapped; a placeholder state with a real foreign city is classified as `International` instead.",
        "- Resolve rows marked `Needs review` before using for reporting.",
        "- Compare year/month/week using `Shipment Year-Month` and `Shipment Week` on the row-level file.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize Amazon sales exports for Looker Studio.")
    parser.add_argument(
        "--input", type=Path, nargs="*", default=None,
        help="Raw CSV export(s). Defaults to every CSV in data/raw, merged and de-duplicated.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--location-output", type=Path, default=DEFAULT_LOCATION_OUTPUT)
    parser.add_argument("--flagged", type=Path, default=DEFAULT_FLAGGED)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    inputs = args.input or sorted(RAW_DIR.glob("*.csv"))
    if not inputs:
        parser.error(f"No raw CSV exports found in {RAW_DIR}")

    name_to_code, code_to_region, valid_codes, fc_map, country_regions = load_reference(CONFIG_PATH)
    df, sources = load_inputs(inputs, fc_map)
    cleaned = normalize(df, name_to_code, code_to_region, valid_codes, country_regions)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    cleaned.to_csv(args.output, index=False)
    build_location_aggregate(cleaned).to_csv(args.location_output, index=False)
    cleaned.loc[cleaned["record_status"] != "OK"].to_csv(args.flagged, index=False)
    args.report.write_text(build_report(cleaned, sources), encoding="utf-8")

    print(f"Source files:    {len(sources)} (merged)")
    for path in sources:
        print(f"                 - {path.name}")
    print(f"Rows processed:  {len(cleaned):,}")
    print(f"Clean data:      {args.output}")
    print(f"Bubble map data: {args.location_output}")
    print(f"Flagged rows:    {args.flagged}")
    print(f"Report:          {args.report}")


if __name__ == "__main__":
    main()
