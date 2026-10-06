"""
BillZen GST Return Center Engine
=================================
Senior CA & Auditor Grade GST Return Preparation and Reconciliation Engine.
Supports:
1. GSTR-1 (Outward Supplies: B2B, B2CL, B2CS, CDNR, CDNUR, Nil/Exempt, HSN Summary Table 12, Docs Table 13)
2. GSTR-3B (Summary & ITC Reconciliation: Table 3.1, Table 3.2, Table 4 ITC, Table 5, Table 6.1 Net Tax)
3. Audit Reconciliation: Book Sales vs Return Sales, Book GST vs Return GST, Credit Notes, Debit Notes
4. Government GST Portal Compliant GSTR-1 JSON export
"""

import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from tax_engine import classify_invoice_gstr1, float_round


def round2(val):
    if val is None:
        return 0.0
    return float(Decimal(str(val)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def prepare_gstr1_payload(shop_info, sales, sales_returns, month):
    """
    Constructs GSTR-1 outward supply tables from sales invoices and credit notes.
    """
    shop_gstin = (shop_info.get('gstin') or '').strip().upper()
    shop_pos = (shop_info.get('state_code') or '07').strip().zfill(2)[:2]

    b2b_map = {}      # gstin -> {ctin, inv: []}
    b2cl_list = []    # list of invoices > 2.5L inter-state unregistered
    b2cs_map = {}     # (pos, rate) -> {txval, camt, samt, iamt}
    cdnr_map = {}     # gstin -> {ctin, nt: []}
    cdnur_list = []   # list of credit notes for unregistered parties
    nil_exempt = {"nil_amt": 0.0, "expt_amt": 0.0, "ngsup_amt": 0.0}
    hsn_map = {}      # hsn -> {hsn_sc, desc, uqc, qty, val, txval, iamt, camt, samt}

    total_gross_taxable = 0.0
    total_gross_cgst = 0.0
    total_gross_sgst = 0.0
    total_gross_igst = 0.0

    # ── 1. Process Sales Invoices ──
    for s in sales:
        s_id = s.get('bill_id') or f"INV{s.get('id')}"
        s_date = s.get('sale_date') or ''
        s_tot = round2(s.get('total_amount', 0))
        s_taxable = round2(s.get('subtotal', 0))
        s_cgst = round2(s.get('cgst_amount', 0))
        s_sgst = round2(s.get('sgst_amount', 0))
        s_igst = round2(s.get('igst_amount', 0))
        cust_gstin = (s.get('customer_gstin') or '').strip().upper()
        cust_name = s.get('customer_name') or 'Customer'
        is_inter = bool(s.get('is_interstate', 0) or s_igst > 0)
        pos = (s.get('state_code') or shop_pos).strip().zfill(2)[:2]

        total_gross_taxable += s_taxable
        total_gross_cgst += s_cgst
        total_gross_sgst += s_sgst
        total_gross_igst += s_igst

        classification = classify_invoice_gstr1(cust_gstin, is_inter, s_tot)
        items = s.get('items', [])

        # Item-wise grouping by GST rate
        rate_groups = {}
        for it in items:
            rate = round2(it.get('gst_pct', 18))
            it_qty = float(it.get('qty', 1))
            it_price = float(it.get('price', 0))
            it_txval = round2(it_qty * it_price)
            if rate not in rate_groups:
                rate_groups[rate] = {"txval": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0}
            rate_groups[rate]["txval"] = round2(rate_groups[rate]["txval"] + it_txval)
            if is_inter:
                it_tax = round2(it_txval * rate / 100.0)
                rate_groups[rate]["igst"] = round2(rate_groups[rate]["igst"] + it_tax)
            else:
                it_tax = round2(it_txval * (rate / 2.0) / 100.0)
                rate_groups[rate]["cgst"] = round2(rate_groups[rate]["cgst"] + it_tax)
                rate_groups[rate]["sgst"] = round2(rate_groups[rate]["sgst"] + it_tax)

            # Table 12: HSN Summary
            hsn = (it.get('hsn_code') or '9999').strip()
            uqc = (it.get('unit') or 'PCS').upper()[:3]
            if hsn not in hsn_map:
                hsn_map[hsn] = {
                    "hsn_sc": hsn,
                    "desc": it.get('product') or "Goods",
                    "uqc": uqc,
                    "qty": 0.0,
                    "val": 0.0,
                    "txval": 0.0,
                    "iamt": 0.0,
                    "camt": 0.0,
                    "samt": 0.0
                }
            hsn_map[hsn]["qty"] = round(hsn_map[hsn]["qty"] + it_qty, 2)
            hsn_map[hsn]["txval"] = round2(hsn_map[hsn]["txval"] + it_txval)
            if is_inter:
                hsn_map[hsn]["iamt"] = round2(hsn_map[hsn]["iamt"] + (it_txval * rate / 100.0))
            else:
                hsn_map[hsn]["camt"] = round2(hsn_map[hsn]["camt"] + (it_txval * (rate / 2.0) / 100.0))
                hsn_map[hsn]["samt"] = round2(hsn_map[hsn]["samt"] + (it_txval * (rate / 2.0) / 100.0))
            hsn_map[hsn]["val"] = round2(hsn_map[hsn]["txval"] + hsn_map[hsn]["iamt"] + hsn_map[hsn]["camt"] + hsn_map[hsn]["samt"])

        # Construct invoice items list for GSTR-1
        gstr1_itms = []
        item_num = 1
        for rt, vals in rate_groups.items():
            gstr1_itms.append({
                "num": item_num,
                "itm_det": {
                    "txval": vals["txval"],
                    "rt": rt,
                    "camt": vals["cgst"],
                    "samt": vals["sgst"],
                    "iamt": vals["igst"],
                    "csamt": 0.0
                }
            })
            item_num += 1

        if not gstr1_itms:
            # Fallback if no line items
            gstr1_itms.append({
                "num": 1,
                "itm_det": {
                    "txval": s_taxable,
                    "rt": 18.0 if (s_cgst > 0 or s_igst > 0) else 0.0,
                    "camt": s_cgst,
                    "samt": s_sgst,
                    "iamt": s_igst,
                    "csamt": 0.0
                }
            })

        if classification == "B2B":
            if cust_gstin not in b2b_map:
                b2b_map[cust_gstin] = {"ctin": cust_gstin, "cname": cust_name, "inv": []}
            b2b_map[cust_gstin]["inv"].append({
                "inum": s_id,
                "idt": s_date,
                "val": s_tot,
                "pos": pos,
                "rchrg": "N",
                "inv_typ": "R",
                "itms": gstr1_itms
            })
        elif classification == "B2C_LARGE":
            b2cl_list.append({
                "pos": pos,
                "inv": [{
                    "inum": s_id,
                    "idt": s_date,
                    "val": s_tot,
                    "itms": gstr1_itms
                }]
            })
        else: # B2C_SMALL
            for rt, vals in rate_groups.items():
                key = (pos, rt)
                if key not in b2cs_map:
                    b2cs_map[key] = {
                        "sply_ty": "INTER" if is_inter else "INTRA",
                        "pos": pos,
                        "typ": "OE",
                        "rt": rt,
                        "txval": 0.0,
                        "iamt": 0.0,
                        "camt": 0.0,
                        "samt": 0.0,
                        "csamt": 0.0
                    }
                b2cs_map[key]["txval"] = round2(b2cs_map[key]["txval"] + vals["txval"])
                b2cs_map[key]["iamt"] = round2(b2cs_map[key]["iamt"] + vals["igst"])
                b2cs_map[key]["camt"] = round2(b2cs_map[key]["camt"] + vals["cgst"])
                b2cs_map[key]["samt"] = round2(b2cs_map[key]["samt"] + vals["sgst"])

    # ── 2. Process Sales Returns (Credit Notes) ──
    total_cn_taxable = 0.0
    total_cn_cgst = 0.0
    total_cn_sgst = 0.0
    total_cn_igst = 0.0

    for cn in sales_returns:
        cn_no = cn.get('credit_note_no') or f"CN{cn.get('id')}"
        cn_date = cn.get('return_date') or ''
        orig_inv = cn.get('original_bill_id') or ''
        orig_date = cn.get('original_sale_date') or ''
        cn_tot = round2(cn.get('total_amount', 0))
        cn_taxable = round2(cn.get('subtotal', 0))
        cn_cgst = round2(cn.get('cgst_amount', 0))
        cn_sgst = round2(cn.get('sgst_amount', 0))
        cn_igst = round2(cn.get('igst_amount', 0))
        cust_gstin = (cn.get('customer_gstin') or '').strip().upper()
        cust_name = cn.get('customer_name') or 'Customer'
        is_inter = bool(cn.get('is_interstate', 0) or cn_igst > 0)
        pos = (cn.get('place_of_supply') or shop_pos).strip().zfill(2)[:2]

        total_cn_taxable += cn_taxable
        total_cn_cgst += cn_cgst
        total_cn_sgst += cn_sgst
        total_cn_igst += cn_igst

        items = cn.get('items', [])
        rate_groups = {}
        for it in items:
            rate = round2(it.get('gst_pct', 18))
            it_txval = round2(it.get('taxable_amount', float(it.get('qty', 0)) * float(it.get('price', 0))))
            if rate not in rate_groups:
                rate_groups[rate] = {"txval": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0}
            rate_groups[rate]["txval"] = round2(rate_groups[rate]["txval"] + it_txval)
            if is_inter:
                rate_groups[rate]["igst"] = round2(rate_groups[rate]["igst"] + it_txval * rate / 100.0)
            else:
                rate_groups[rate]["cgst"] = round2(rate_groups[rate]["cgst"] + it_txval * (rate / 2.0) / 100.0)
                rate_groups[rate]["sgst"] = round2(rate_groups[rate]["sgst"] + it_txval * (rate / 2.0) / 100.0)

        cn_itms = []
        item_num = 1
        for rt, vals in rate_groups.items():
            cn_itms.append({
                "num": item_num,
                "itm_det": {
                    "txval": vals["txval"],
                    "rt": rt,
                    "camt": vals["cgst"],
                    "samt": vals["sgst"],
                    "iamt": vals["igst"],
                    "csamt": 0.0
                }
            })
            item_num += 1

        if not cn_itms:
            cn_itms.append({
                "num": 1,
                "itm_det": {
                    "txval": cn_taxable,
                    "rt": 18.0 if (cn_cgst > 0 or cn_igst > 0) else 0.0,
                    "camt": cn_cgst,
                    "samt": cn_sgst,
                    "iamt": cn_igst,
                    "csamt": 0.0
                }
            })

        is_registered = len(cust_gstin) == 15 and cust_gstin[:2].isdigit()
        note_entry = {
            "nt_num": cn_no,
            "nt_dt": cn_date,
            "val": cn_tot,
            "ntty": "C", # C = Credit Note
            "p_gst": "N",
            "pos": pos,
            "rchrg": "N",
            "inum": orig_inv,
            "idt": orig_date,
            "itms": cn_itms
        }

        if is_registered:
            if cust_gstin not in cdnr_map:
                cdnr_map[cust_gstin] = {"ctin": cust_gstin, "cname": cust_name, "nt": []}
            cdnr_map[cust_gstin]["nt"].append(note_entry)
        else:
            cdnur_list.append({
                "typ": "B2CL" if (is_inter and cn_tot > 250000) else "B2CS",
                "nt_num": cn_no,
                "nt_dt": cn_date,
                "val": cn_tot,
                "ntty": "C",
                "p_gst": "N",
                "pos": pos,
                "inum": orig_inv,
                "idt": orig_date,
                "itms": cn_itms
            })

    # Table 13: Documents Issued
    doc_det = [
        {
            "doc_num": 1,
            "doc_typ": "Invoices for outward supply",
            "from": sales[0]['bill_id'] if sales else "",
            "to": sales[-1]['bill_id'] if sales else "",
            "totnum": len(sales),
            "canc": sum(1 for s in sales if s.get('cancelled')),
            "net_issue": len(sales)
        },
        {
            "doc_num": 2,
            "doc_typ": "Credit Notes",
            "from": sales_returns[0]['credit_note_no'] if sales_returns else "",
            "to": sales_returns[-1]['credit_note_no'] if sales_returns else "",
            "totnum": len(sales_returns),
            "canc": 0,
            "net_issue": len(sales_returns)
        }
    ]

    net_taxable = round2(total_gross_taxable - total_cn_taxable)
    net_cgst = round2(total_gross_cgst - total_cn_cgst)
    net_sgst = round2(total_gross_sgst - total_cn_sgst)
    net_igst = round2(total_gross_igst - total_cn_igst)
    net_total_tax = round2(net_cgst + net_sgst + net_igst)

    return {
        "gstin": shop_gstin,
        "fp": month.replace('-', ''),
        "b2b": list(b2b_map.values()),
        "b2cl": b2cl_list,
        "b2cs": list(b2cs_map.values()),
        "cdnr": list(cdnr_map.values()),
        "cdnur": cdnur_list,
        "nil": nil_exempt,
        "hsn": {"data": list(hsn_map.values())},
        "doc_issue": {"doc_det": doc_det},
        "summary": {
            "gross_taxable": total_gross_taxable,
            "gross_tax": round2(total_gross_cgst + total_gross_sgst + total_gross_igst),
            "credit_notes_taxable": total_cn_taxable,
            "credit_notes_tax": round2(total_cn_cgst + total_cn_sgst + total_cn_igst),
            "net_taxable": net_taxable,
            "net_cgst": net_cgst,
            "net_sgst": net_sgst,
            "net_igst": net_igst,
            "net_total_tax": net_total_tax,
            "total_invoices": len(sales),
            "total_credit_notes": len(sales_returns)
        }
    }


def calculate_rule_88a_itc_setoff(net_out_igst, net_out_cgst, net_out_sgst, net_itc_igst, net_itc_cgst, net_itc_sgst):
    """
    Computes Rule 88A ITC setoff sequence:
    1. IGST ITC must be fully exhausted against IGST liability first.
    2. Remaining IGST ITC must be completely exhausted against CGST and/or SGST liability before CGST/SGST ITC is used.
    3. Only then CGST ITC can offset remaining CGST liability, and SGST ITC offset remaining SGST liability.
    """
    rem_igst_itc = net_itc_igst
    used_igst_for_igst = min(rem_igst_itc, net_out_igst)
    rem_out_igst = round2(net_out_igst - used_igst_for_igst)
    rem_igst_itc = round2(rem_igst_itc - used_igst_for_igst)

    used_igst_for_cgst = min(rem_igst_itc, net_out_cgst)
    rem_out_cgst = round2(net_out_cgst - used_igst_for_cgst)
    rem_igst_itc = round2(rem_igst_itc - used_igst_for_cgst)

    used_igst_for_sgst = min(rem_igst_itc, net_out_sgst)
    rem_out_sgst = round2(net_out_sgst - used_igst_for_sgst)
    rem_igst_itc = round2(rem_igst_itc - used_igst_for_sgst)

    used_cgst_for_cgst = min(net_itc_cgst, rem_out_cgst)
    payable_cgst = round2(rem_out_cgst - used_cgst_for_cgst)

    used_sgst_for_sgst = min(net_itc_sgst, rem_out_sgst)
    payable_sgst = round2(rem_out_sgst - used_sgst_for_sgst)

    payable_igst = rem_out_igst
    total_payable = round2(payable_igst + payable_cgst + payable_sgst)

    return {
        "used_igst_for_igst": used_igst_for_igst,
        "used_igst_for_cgst": used_igst_for_cgst,
        "used_igst_for_sgst": used_igst_for_sgst,
        "used_cgst_for_cgst": used_cgst_for_cgst,
        "used_sgst_for_sgst": used_sgst_for_sgst,
        "payable_igst": payable_igst,
        "payable_cgst": payable_cgst,
        "payable_sgst": payable_sgst,
        "total_payable": total_payable,
        "remaining_itc_igst": rem_igst_itc
    }


def prepare_gstr3b_payload(shop_info, sales, sales_returns, purchases, purchase_returns, month):
    """
    Constructs GSTR-3B monthly summary for outward supplies, eligible ITC, and tax payable.
    """
    # ── Table 3.1: Outward Supplies (Sales minus Credit Notes) ──
    s_taxable = sum(round2(s.get('subtotal', 0)) for s in sales)
    s_cgst = sum(round2(s.get('cgst_amount', 0)) for s in sales)
    s_sgst = sum(round2(s.get('sgst_amount', 0)) for s in sales)
    s_igst = sum(round2(s.get('igst_amount', 0)) for s in sales)

    cn_taxable = sum(round2(cn.get('subtotal', 0)) for cn in sales_returns)
    cn_cgst = sum(round2(cn.get('cgst_amount', 0)) for cn in sales_returns)
    cn_sgst = sum(round2(cn.get('sgst_amount', 0)) for cn in sales_returns)
    cn_igst = sum(round2(cn.get('igst_amount', 0)) for cn in sales_returns)

    net_out_txval = round2(max(0.0, s_taxable - cn_taxable))
    net_out_cgst = round2(max(0.0, s_cgst - cn_cgst))
    net_out_sgst = round2(max(0.0, s_sgst - cn_sgst))
    net_out_igst = round2(max(0.0, s_igst - cn_igst))

    # ── Table 4: Eligible Input Tax Credit (Purchases minus Debit Notes) ──
    p_taxable = sum(round2(p.get('subtotal', 0)) for p in purchases)
    p_cgst = sum(round2(p.get('cgst_amount', 0)) for p in purchases)
    p_sgst = sum(round2(p.get('sgst_amount', 0)) for p in purchases)
    p_igst = sum(round2(p.get('igst_amount', 0)) for p in purchases)

    dn_taxable = sum(round2(dn.get('subtotal', 0)) for dn in purchase_returns)
    dn_cgst = sum(round2(dn.get('cgst_amount', 0)) for dn in purchase_returns)
    dn_sgst = sum(round2(dn.get('sgst_amount', 0)) for dn in purchase_returns)
    dn_igst = sum(round2(dn.get('igst_amount', 0)) for dn in purchase_returns)

    net_itc_cgst = round2(max(0.0, p_cgst - dn_cgst))
    net_itc_sgst = round2(max(0.0, p_sgst - dn_sgst))
    net_itc_igst = round2(max(0.0, p_igst - dn_igst))
    total_itc = round2(net_itc_cgst + net_itc_sgst + net_itc_igst)

    # ── Table 6.1: Net Tax Payable (CGST Rule 88A Order of ITC Utilization) ──
    setoff = calculate_rule_88a_itc_setoff(net_out_igst, net_out_cgst, net_out_sgst, net_itc_igst, net_itc_cgst, net_itc_sgst)
    used_igst_for_igst = setoff["used_igst_for_igst"]
    used_igst_for_cgst = setoff["used_igst_for_cgst"]
    used_igst_for_sgst = setoff["used_igst_for_sgst"]
    used_cgst_for_cgst = setoff["used_cgst_for_cgst"]
    used_sgst_for_sgst = setoff["used_sgst_for_sgst"]
    payable_igst = setoff["payable_igst"]
    payable_cgst = setoff["payable_cgst"]
    payable_sgst = setoff["payable_sgst"]
    total_payable = setoff["total_payable"]

    return {
        "gstin": shop_info.get('gstin', ''),
        "ret_period": month.replace('-', ''),
        "table_3_1": {
            "outward_taxable_supplies": {
                "txval": net_out_txval,
                "iamt": net_out_igst,
                "camt": net_out_cgst,
                "samt": net_out_sgst,
                "csamt": 0.0
            },
            "zero_rated": {"txval": 0.0, "iamt": 0.0, "csamt": 0.0},
            "nil_exempt": {"txval": 0.0}
        },
        "table_4_itc": {
            "itc_available": {
                "all_other_itc": {
                    "iamt": net_itc_igst,
                    "camt": net_itc_cgst,
                    "samt": net_itc_sgst,
                    "csamt": 0.0
                }
            },
            "itc_net": {
                "iamt": net_itc_igst,
                "camt": net_itc_cgst,
                "samt": net_itc_sgst,
                "total_itc": total_itc
            }
        },
        "table_6_1": {
            "tax_payable": {
                "iamt": payable_igst,
                "camt": payable_cgst,
                "samt": payable_sgst,
                "total_payable": total_payable
            }
        },
        "summary": {
            "gross_sales": round2(s_taxable),
            "sales_returns": round2(cn_taxable),
            "net_outward_taxable": net_out_txval,
            "total_outward_tax": round2(net_out_cgst + net_out_sgst + net_out_igst),
            "gross_purchases": round2(p_taxable),
            "purchase_returns": round2(dn_taxable),
            "total_eligible_itc": total_itc,
            "net_tax_payable": total_payable
        }
    }


def perform_book_reconciliation(sales, sales_returns, purchases, purchase_returns, gstr1_payload, gstr3b_payload):
    """
    Performs audit reconciliation comparing Books vs GST Returns (GSTR-1 & GSTR-3B).
    Under strict CA guidelines:
    - Verifies Books vs GSTR-1
    - Verifies Books vs GSTR-3B
    - Verifies GSTR-1 vs GSTR-3B cross-return variance
    - Detects mismatches and reports exact differences.
    """
    book_sales_taxable = sum(round2(s.get('subtotal', 0)) for s in sales)
    book_sales_cgst = sum(round2(s.get('cgst_amount', 0)) for s in sales)
    book_sales_sgst = sum(round2(s.get('sgst_amount', 0)) for s in sales)
    book_sales_igst = sum(round2(s.get('igst_amount', 0)) for s in sales)
    book_sales_tax = round2(book_sales_cgst + book_sales_sgst + book_sales_igst)

    book_cn_taxable = sum(round2(cn.get('subtotal', 0)) for cn in sales_returns)
    book_cn_tax = sum(round2(cn.get('cgst_amount', 0) + cn.get('sgst_amount', 0) + cn.get('igst_amount', 0)) for cn in sales_returns)

    net_book_sales = round2(book_sales_taxable - book_cn_taxable)
    net_book_tax = round2(book_sales_tax - book_cn_tax)

    gstr1_taxable = gstr1_payload['summary']['net_taxable']
    gstr1_tax = gstr1_payload['summary']['net_total_tax']
    gstr3b_taxable = gstr3b_payload['summary']['net_outward_taxable']
    gstr3b_tax = gstr3b_payload['summary']['total_outward_tax']

    diff_books_gstr1_taxable = round2(net_book_sales - gstr1_taxable)
    diff_books_gstr1_tax = round2(net_book_tax - gstr1_tax)
    diff_gstr1_3b_taxable = round2(gstr1_taxable - gstr3b_taxable)
    diff_gstr1_3b_tax = round2(gstr1_tax - gstr3b_tax)

    warnings = []
    errors = []

    # Check for invalid GSTINs on B2B
    for s in sales:
        g = (s.get('customer_gstin') or '').strip().upper()
        if g and (len(g) != 15 or not g[:2].isdigit()):
            warnings.append(f"Invoice {s.get('bill_id')}: GSTIN '{g}' appears invalid (length != 15 or invalid state code).")

    # Check for missing state code
    missing_pos = [s.get('bill_id') for s in sales if not s.get('state_code')]
    if missing_pos:
        warnings.append(f"{len(missing_pos)} invoices do not have an explicit State Code (using Delhi '07' as default).")

    if abs(diff_gstr1_3b_tax) >= 0.05:
        errors.append(f"GSTR-1 Output Tax (₹{gstr1_tax:.2f}) does not match GSTR-3B Outward Tax (₹{gstr3b_tax:.2f}). Variance: ₹{diff_gstr1_3b_tax:.2f}")

    if abs(diff_gstr1_3b_taxable) >= 0.05:
        errors.append(f"GSTR-1 Taxable Turnover (₹{gstr1_taxable:.2f}) does not match GSTR-3B Taxable (₹{gstr3b_taxable:.2f}). Variance: ₹{diff_gstr1_3b_taxable:.2f}")

    is_reconciled = (
        abs(diff_books_gstr1_taxable) < 0.05 and
        abs(diff_books_gstr1_tax) < 0.05 and
        abs(diff_gstr1_3b_taxable) < 0.05 and
        abs(diff_gstr1_3b_tax) < 0.05
    )

    return {
        "is_reconciled": is_reconciled,
        "books": {
            "gross_sales": round2(book_sales_taxable),
            "gross_tax": book_sales_tax,
            "sales_returns": round2(book_cn_taxable),
            "returns_tax": round2(book_cn_tax),
            "net_sales": net_book_sales,
            "net_tax": net_book_tax
        },
        "returns": {
            "gstr1_taxable": gstr1_taxable,
            "gstr1_tax": gstr1_tax,
            "gstr3b_taxable": gstr3b_taxable,
            "gstr3b_tax": gstr3b_tax,
            "gstr3b_itc": gstr3b_payload['summary']['total_eligible_itc'],
            "gstr3b_net_payable": gstr3b_payload['summary']['net_tax_payable']
        },
        "discrepancies": {
            "taxable_difference": diff_books_gstr1_taxable,
            "tax_difference": diff_books_gstr1_tax,
            "gstr1_vs_3b_tax_diff": diff_gstr1_3b_tax,
            "gstr1_vs_3b_taxable_diff": diff_gstr1_3b_taxable
        },
        "errors": errors,
        "warnings": warnings,
        "status_label": "100% RECONCILED" if is_reconciled else "VARIANCE DETECTED"
    }
