# HA Sales Report - Validation Summary

- Sources: `HA Sales per month - AMZ Customer shipments.csv`, `Customer Shipment Sales.csv`
- Rows processed: 101,040

## Record status

| Status | Rows |
| --- | --- |
| OK | 100,512 |
| Duplicate (excluded) | 299 |
| Needs review | 229 |

## Flagged issues

| Issue | Rows |
| --- | --- |
| Non-US location (Canada / International) | 4,555 |
| Confidential location (Amazon hidden) | 221 |
| Missing city | 0 |
| Missing postal code | 225 |
| Net sales does not match Quantity x Product - Shipping - Gift | 0 |
| TOTAL PRICE does not match Quantity x Product Amount | 0 |
| Exact duplicate row (excluded from totals) | 299 |
| Order Id + SKU appears more than once (often split shipments) | 2,464 |

## Date coverage

- Earliest shipment: 2025-04-01
- Latest shipment: 2026-10-26

Rows and net sales by year:

| Year | Rows | Net Sales |
| --- | --- | --- |
| 2025 | 56,027 | 2,066,203.82 |
| 2026 | 45,013 | 1,872,323.57 |

> Periods are uneven: 2025 starts in April and 2026 ends mid-October, so compare months/weeks rather than raw yearly totals.

## Net sales by country

| Country | Net Sales |
| --- | --- |
| United States | 3,598,594.67 |
| Canada | 303,898.94 |
| International | 16,108.35 |
| Confidential (Amazon) | 7,741.46 |

## Net sales by region

| Region | Net Sales |
| --- | --- |
| Central | 1,355,620.59 |
| East | 1,145,314.95 |
| West | 757,732.33 |
| South East | 336,730.29 |
| Ontario | 117,838.54 |
| Alberta | 50,981.31 |
| Quebec | 49,481.75 |
| British Columbia | 45,664.94 |
| International | 16,108.35 |
| Saskatchewan | 12,099.30 |
| Manitoba | 10,491.56 |
| Nova Scotia | 8,210.98 |
| New Brunswick | 7,748.25 |
| Unknown | 7,741.46 |
| Territory | 2,010.79 |
| Military (APO/FPO) | 1,185.72 |
| Prince Edward Island | 1,004.75 |
| Newfoundland and Labrador | 154.98 |
| Yukon | 137.59 |
| Nunavut | 84.99 |

## Totals

- Net sales (all rows): 3,938,527.39
- Net sales (exact duplicates removed): 3,926,343.42
- Inflation removed by de-duplicating: 12,183.97

## Next steps

- The location aggregate and totals above exclude exact duplicate rows; report from `record_status != 'Duplicate (excluded)'`.
- Non-US rows (Canada / International) are separated by `Country` so they do not distort the US bubble map.
- Rows flagged `Confidential` have both ship-to fields masked by Amazon and cannot be mapped; a placeholder state with a real foreign city is classified as `International` instead.
- Resolve rows marked `Needs review` before using for reporting.
- Compare year/month/week using `Shipment Year-Month` and `Shipment Week` on the row-level file.
