import frappe
from frappe.utils import flt

logger = frappe.logger("advance_tds_fix")


def exclude_gst_from_advance_tds(doc, method):
    """Correct TDS on a Payment Entry raised as an advance against a Purchase
    Order so it excludes GST from the taxable base.

    ERPNext's own TDS engine for Payment Entry (`PaymentTaxWithholding`,
    erpnext/accounts/doctype/tax_withholding_entry/tax_withholding_entry.py)
    taxes the raw allocated/paid amount, which for a PO advance is sourced
    from the PO's GST-inclusive grand_total -- it never looks at the PO's
    net_total. Purchase Invoice TDS gets this right (it works off item-level
    base_net_amount); Payment Entry TDS does not. This hook runs after
    ERPNext's own (wrong) calculation has already written its numbers, and
    overwrites them with the correct GST-exclusive amount before submit.

    Scope: only handles the "Net Total" tax_deduction_basis (excludes GST
    entirely from the base -- the standard case), only Purchase Order
    references (not Sales Order), and only the common case of a single
    Tax Withholding Category / single tax_withholding_entries row. It
    deliberately does nothing (rather than guess) outside that scope --
    Lower Deduction Certificate splits, multiple categories on one payment,
    or a "Gross Total" category all need different handling this does not
    attempt. Every early exit is logged (see the `advance_tds_fix` logger,
    logs/advance_tds_fix.log in the bench) so it's clear why a given
    document wasn't touched.
    """
    if not doc.apply_tds or not doc.tax_withholding_category:
        return

    entries = doc.get("tax_withholding_entries") or []
    if len(entries) != 1:
        logger.info(
            f"{doc.name}: skipped -- expected exactly 1 tax_withholding_entries row, found {len(entries)}"
        )
        return

    category = frappe.get_cached_doc("Tax Withholding Category", doc.tax_withholding_category)
    if category.tax_deduction_basis != "Net Total":
        logger.info(
            f"{doc.name}: skipped -- tax_deduction_basis is "
            f"{category.tax_deduction_basis!r}, this fix only handles 'Net Total'"
        )
        return

    po_references = [ref for ref in doc.get("references") if ref.reference_doctype == "Purchase Order"]
    if not po_references:
        logger.info(f"{doc.name}: skipped -- no Purchase Order references on this payment")
        return

    exchange_rate = flt(
        doc.source_exchange_rate if doc.payment_type == "Receive" else doc.target_exchange_rate
    ) or 1

    taxable_amount_in_party_currency = flt(doc.unallocated_amount)
    for ref in po_references:
        po_net_total, po_grand_total = frappe.db.get_value(
            "Purchase Order", ref.reference_name, ["net_total", "grand_total"]
        )
        if not po_grand_total:
            continue
        fraction_of_po = flt(ref.allocated_amount) / flt(po_grand_total)
        taxable_amount_in_party_currency += flt(po_net_total) * fraction_of_po

    precision = doc.precision("withholding_amount", "tax_withholding_entries")
    corrected_taxable_amount = flt(taxable_amount_in_party_currency * exchange_rate, precision)

    entry = entries[0]
    tax_rate = flt(entry.tax_rate)  # the rate ERPNext already correctly selected -- read it back
    corrected_withholding_amount = flt(corrected_taxable_amount * tax_rate / 100, precision)

    if corrected_withholding_amount >= entry.withholding_amount:
        # Correction should only ever reduce the amount (GST removed from the
        # base can't increase it). If it doesn't, the assumptions above don't
        # hold for this document -- leave ERPNext's numbers as-is.
        logger.info(
            f"{doc.name}: skipped -- corrected withholding amount "
            f"({corrected_withholding_amount}) was not smaller than ERPNext's own "
            f"({entry.withholding_amount}); leaving it untouched"
        )
        return

    logger.info(
        f"{doc.name}: correcting taxable_amount {entry.taxable_amount} -> {corrected_taxable_amount}, "
        f"withholding_amount {entry.withholding_amount} -> {corrected_withholding_amount}"
    )

    entry.taxable_amount = corrected_taxable_amount
    entry.withholding_amount = corrected_withholding_amount

    tds_accounts = {row.account for row in category.accounts if row.company == doc.company}
    tax_precision = doc.precision("tax_amount", "taxes")
    for tax_row in doc.get("taxes"):
        if tax_row.account_head not in tds_accounts:
            continue
        tax_row.base_tax_amount = corrected_withholding_amount
        tax_row.tax_amount = flt(corrected_withholding_amount / exchange_rate, tax_precision)

    doc.set_amounts_after_tax()
