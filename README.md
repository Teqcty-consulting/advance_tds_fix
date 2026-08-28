# Advance TDS Fix

Fixes a real gap in ERPNext v16's TDS engine: when you raise a Payment Entry
as an advance against a Purchase Order (e.g. via Payment Request → Create
Payment Entry), ERPNext calculates TDS on the whole GST-inclusive amount
instead of the GST-exclusive base — even when the linked Tax Withholding
Category is set to "Net Total" (which is supposed to mean exactly that).

This is not a misconfiguration. Verified against ERPNext v16.32.3 source:
Purchase Invoice TDS works correctly because it computes tax withholding
item-by-item off `item.base_net_amount` (GST already excluded). Payment
Entry TDS uses a completely different code path
(`PaymentTaxWithholding._update_taxable_amounts` in
`erpnext/accounts/doctype/tax_withholding_entry/tax_withholding_entry.py`)
that just sums the raw allocated payment amount — which for a PO advance is
sourced from the PO's GST-inclusive `grand_total`, never `net_total`. The
"Net Total" setting on Tax Withholding Category is silently ignored for
Payment Entries.

Confirmed separately: Payment Entry's threshold logic
(`_is_threshold_crossed_for_category`) is hardcoded to always return `True`
for this doctype — Single/Cumulative Threshold settings never actually gate
Payment Entry TDS, so this fix doesn't need to touch threshold evaluation,
only the taxable amount and the resulting withheld amount.

## What it does

A single `before_submit` hook on Payment Entry
(`advance_tds_fix/advance_tds_fix/payment_entry_tds.py`,
`exclude_gst_from_advance_tds`). It runs *after* ERPNext's own (wrong)
calculation has already written its numbers, and — only when its narrow
assumptions hold — overwrites them with the correct GST-exclusive amount
before the document submits and posts to GL.

**Scope — deliberately narrow.** It only acts when *all* of these hold, and
does nothing (leaves ERPNext's own numbers untouched) otherwise:

- The Payment Entry has `apply_tds` checked and a `tax_withholding_category`.
- Exactly one `tax_withholding_entries` row exists (the common case; skips
  Lower Deduction Certificate splits or multiple categories on one payment,
  since correctly dividing the correction across those needs different
  handling this doesn't attempt).
- The linked Tax Withholding Category's "Deduct Tax On Basis" is **Net
  Total** (not "Gross Total" — that variant needs item-level tax-breakup
  handling this doesn't attempt).
- At least one reference on the payment is a Purchase Order.
- The recalculated amount is *smaller* than what ERPNext computed (a sanity
  check — removing GST from the base should only ever reduce the withheld
  amount; if it doesn't, the assumptions above don't hold for that document
  and the fix backs off rather than risk writing a wrong number).

## Install

Requires Frappe and ERPNext v16 (the fix reads fields/behavior specific to
v16's `Tax Withholding Entry` doctype — it has not been checked against
v14/v15's older TDS engine, which worked differently).

```bash
cd ~/frappe-bench

# From a local checkout:
bench get-app advance_tds_fix /path/to/advance_tds_fix
# ...or copy the folder into apps/ manually and:
# ./env/bin/pip install -e apps/advance_tds_fix

bench --site <your-site> install-app advance_tds_fix
bench --site <your-site> clear-cache
```

No DocTypes, no fixtures, no assets to build — just the one hook.

### Uninstall

```bash
bench --site <your-site> uninstall-app advance_tds_fix
```

## Testing it

1. On a Supplier, set a Tax Withholding Category whose "Deduct Tax On Basis"
   is "Net Total".
2. Create a Purchase Order for that supplier with GST/tax lines, so
   `grand_total > net_total`.
3. Raise a Payment Request against it, then "Create Payment Entry" for a
   partial or full advance.
4. Before this fix: the Payment Entry's TDS deduction is computed on the
   full (GST-inclusive) allocated amount.
5. With this fix installed: submit the Payment Entry and check the TDS
   deduction row — it should be computed on the GST-exclusive portion only
   (`net_total` scaled by however much of the PO this payment covers).

Worth checking `bench --site <your-site> console` for `frappe.log_error`
entries after a submit that didn't change anything, if you expect the fix to
have applied but it didn't — that tells you which of the scope conditions
above wasn't met for that document.
