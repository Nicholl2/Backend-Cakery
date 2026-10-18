# Expense flow and sales trends

## Goals

- Record operating expenses with enough context to reconcile them later.
- Preserve an audit trail when an expense is changed or voided.
- Keep voided expenses visible in history but exclude them from financial totals.
- Keep inventory purchases separate from operating expenses; purchases affect inventory valuation and COGS, while operating expenses reduce period profit directly.
- Provide a compact time-series endpoint for the Finance page's Sales Trends chart.

## Expense flow

An expense records category, positive amount, expense date, and optional description, payee, payment method, and HTTPS receipt URL. Categories remain free text for compatibility with existing records and operational categories. Payment method uses stable values: `cash`, `bank_transfer`, `e_wallet`, `card`, or `other`.

Only Owner/Admin can create, edit, or void expenses. Creation records the user and timestamp. Edits record the actor and before/after changed fields in an audit table. Voiding is a soft cancellation requiring a reason; the expense and audit history remain queryable. Voided entries are excluded from P&L and category summaries.

List/detail endpoints may show voided records with their status. Existing date/category filters and pagination remain available. Summary totals, category breakdown, and count must all use the same date window and exclude voided rows.

Inventory purchases are not operating expenses. Supplier spending remains a separate procurement metric, and HPP is recognized through sold-order snapshots in the financial report.

## Sales trends

Add a protected `GET /reports/sales-trends` endpoint with inclusive `start_date`, `end_date`, and `granularity` (`day`, `week`, or `month`). Return a continuous series including empty periods. Each point contains `period_start`, `revenue`, `hpp`, `gross_profit`, and `order_count`.

Trend points use the same paid-order cohort as the financial report: successful payments, paid invoice, and order not cancelled/refunded. Revenue is the sum of successful payments for the order; the period is the order's latest successful settlement timestamp. HPP uses order-item snapshots (plus accessory subtotal, matching existing finance logic). This makes the series sum to the corresponding financial report totals for the same date window, subject to the existing finance definitions.

## Validation and security

- Reject blank/oversized category and description/payee fields, non-positive amounts, invalid dates, unsupported payment methods, and non-HTTP(S) receipt URLs.
- Updates must contain at least one supported field; callers cannot change IDs, recorder identity, audit fields, or void state.
- A void reason is required and bounded. A voided expense cannot be edited or voided again.
- Mutations require Owner/Admin. Reads remain internal authenticated routes.
- Use soft voiding and immutable audit events; do not hard-delete expense records.
- Validate date range order and supported trend granularity. Bound the report interval to prevent unbounded queries.

## Migration and compatibility

Add nullable expense metadata and void/audit columns with safe defaults for existing rows. Existing expense records remain active and retain their original category, amount, date, and recorder. Add an expense audit table. No existing purchase, payment, stock, or order rows are rewritten.

## Frontend contract

- `POST /expenses`: `{kategori, jumlah, tanggal?, deskripsi?, penerima?, metode_pembayaran?, bukti_url?}`.
- `PATCH /expenses/{id}`: partial update of editable expense fields.
- `POST /expenses/{id}/cancel`: `{reason}`; display canceled state and reason.
- `GET /expenses` and `GET /expenses/{id}` expose expense metadata and void status.
- `GET /expenses/{id}/audit` exposes immutable audit events to Owner/Admin.
- `GET /expenses/summary/dashboard` returns period-consistent total, category breakdown, and count.
- `GET /reports/sales-trends?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&granularity=day|week|month` returns the chart series.

Receipt storage is represented by an optional HTTPS URL. This backend change does not add binary receipt upload; the frontend should only submit a URL from an existing trusted upload flow or leave it empty.
