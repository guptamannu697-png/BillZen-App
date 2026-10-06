"""
Tally Export Engine V2 for BillZen AI
Generates standard, verified TallyPrime Item Invoice XML.
Supports rate-wise ledgers, separate voucher vs invoice numbering,
inventory allocations, and GST-wise batch filtering & reconciliation.
"""

import xml.sax.saxutils as saxutils
from datetime import datetime

# Default Ledger Naming Convention matching CA / Accountant workflows
# Default Ledger Naming Convention matching CA / Accountant workflows
DEFAULT_LEDGER_MAP = {
    # Sales Ledgers (Support both Sales Local@X% and Sale Local@X%)
    "sale_local_18": "Sales Local@18%",
    "sale_local_5": "Sales Local@5%",
    "sale_local_0": "Sales Local@0%",
    "sale_interstate_18": "Sales Inter-State@18%",
    "sale_interstate_5": "Sales Inter-State@5%",
    "sale_interstate_0": "Sales Inter-State@0%",
    # Output GST Ledgers
    "cgst_output_9": "CGST Output@9%",
    "sgst_output_9": "SGST Output@9%",
    "cgst_output_2_5": "CGST Output@2.5%",
    "sgst_output_2_5": "SGST Output@2.5%",
    "igst_output_18": "IGST Output@18%",
    "igst_output_5": "IGST Output@5%",
    # Purchase Ledgers
    "purchase_local_18": "Purchase Local@18%",
    "purchase_local_5": "Purchase Local@5%",
    "purchase_local_0": "Purchase Local@0%",
    "purchase_interstate_18": "Purchase Inter-State@18%",
    "purchase_interstate_5": "Purchase Inter-State@5%",
    "purchase_interstate_0": "Purchase Inter-State@0%",
    # Input GST Ledgers
    "cgst_input_9": "CGST Input@9%",
    "sgst_input_9": "SGST Input@9%",
    "cgst_input_2_5": "CGST Input@2.5%",
    "sgst_input_2_5": "SGST Input@2.5%",
    "igst_input_18": "IGST Input@18%",
    "igst_input_5": "IGST Input@5%",
    # Round-off
    "round_off": "Short & Exces",
}

def escape_xml(val):
    if val is None:
        return ""
    return saxutils.escape(str(val))

def format_date_tally(d_str):
    """Converts YYYY-MM-DD or standard date to Tally format YYYYMMDD"""
    if not d_str:
        return datetime.now().strftime("%Y%m%d")
    clean = str(d_str).replace("-", "").replace("/", "")[:8]
    if len(clean) == 8:
        return clean
    try:
        return datetime.strptime(str(d_str)[:10], "%Y-%m-%d").strftime("%Y%m%d")
    except Exception:
        return datetime.now().strftime("%Y%m%d")

def generate_tally_v2_xml(company_name, sales_list=None, purchases_list=None, ledger_map=None, category='all', credit_notes_list=None, debit_notes_list=None):
    """
    Generates TallyPrime XML in 'Item Invoice' mode for Sales, Purchases, Credit Notes, and Debit Notes.
    category options: 'all', 'local_18', 'local_5', 'interstate_18', 'interstate_5'
    """
    lm = dict(DEFAULT_LEDGER_MAP)
    if ledger_map:
        # Support aliases like sales_local_18 -> sale_local_18
        for k, v in ledger_map.items():
            lm[k] = v
            if k.startswith("sales_"):
                lm["sale_" + k[6:]] = v
            elif k.startswith("sale_"):
                lm["sales_" + k[5:]] = v

    sales_list = sales_list or []
    purchases_list = purchases_list or []

    # Category Filtering
    def filter_sale(s):
        is_inter = bool(s.get('is_interstate', 0) or s.get('igst_amount', 0) > 0)
        items = s.get('items', [])
        rates = {round(float(it.get('gst_pct', 0)), 1) for it in items}
        if not rates and s.get('cgst_amount', 0) > 0:
            rates.add(18.0)
            
        if category == 'all':
            return True
        elif category == 'local_18':
            return not is_inter and (18.0 in rates or any(r > 10 for r in rates))
        elif category == 'local_5':
            return not is_inter and (5.0 in rates or any(0 < r <= 5 for r in rates))
        elif category == 'interstate_18':
            return is_inter and (18.0 in rates or any(r > 10 for r in rates))
        elif category == 'interstate_5':
            return is_inter and (5.0 in rates or any(0 < r <= 5 for r in rates))
        return True

    def filter_purchase(p):
        is_inter = bool(p.get('is_interstate', 0) or p.get('igst_amount', 0) > 0)
        items = p.get('items', [])
        rates = {round(float(it.get('gst_pct', 0)), 1) for it in items}
        if not rates and p.get('cgst_amount', 0) > 0:
            rates.add(18.0)

        if category == 'all':
            return True
        elif category == 'local_18':
            return not is_inter and (18.0 in rates or any(r > 10 for r in rates))
        elif category == 'local_5':
            return not is_inter and (5.0 in rates or any(0 < r <= 5 for r in rates))
        elif category == 'interstate_18':
            return is_inter and (18.0 in rates or any(r > 10 for r in rates))
        elif category == 'interstate_5':
            return is_inter and (5.0 in rates or any(0 < r <= 5 for r in rates))
        return True

    filtered_sales = [s for s in sales_list if filter_sale(s)]
    filtered_purchases = [p for p in purchases_list if filter_purchase(p)]

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<ENVELOPE>',
        '  <HEADER>',
        '    <TALLYREQUEST>Import Data</TALLYREQUEST>',
        '  </HEADER>',
        '  <BODY>',
        '    <IMPORTDATA>',
        '      <REQUESTDESC>',
        '        <REPORTNAME>Vouchers</REPORTNAME>',
        '        <STATICVARIABLES>',
        f'          <SVCURRENTCOMPANY>{escape_xml(company_name or "My Shop")}</SVCURRENTCOMPANY>',
        '        </STATICVARIABLES>',
        '      </REQUESTDESC>',
        '      <REQUESTDATA>'
    ]

    # --- PROCESS SALES VOUCHERS ---
    vch_no = 1
    for s in filtered_sales:
        bill_id = s.get('bill_id', '')
        date_tally = format_date_tally(s.get('sale_date'))
        party_name = (s.get('customer_name') or 'Cash Customer').strip()
        is_inter = bool(s.get('is_interstate', 0) or s.get('igst_amount', 0) > 0)
        total_amt = round(float(s.get('total_amount', 0)), 2)
        items = s.get('items', [])
        state_name = s.get('state_name') or s.get('customer_state') or 'Delhi'
        
        # Calculate rate-wise GST and taxable totals
        cgst_9 = sgst_9 = cgst_25 = sgst_25 = igst_18 = igst_5 = 0.0
        taxable_sum = 0.0
        
        for it in items:
            q = float(it.get('qty', 1))
            pr = float(it.get('price', 0))
            amt = round(q * pr, 2)
            taxable_sum += amt
            gst_pct = float(it.get('gst_pct', 0))
            if is_inter:
                if gst_pct >= 12:
                    igst_18 += round(amt * gst_pct / 100, 2)
                elif gst_pct > 0:
                    igst_5 += round(amt * gst_pct / 100, 2)
            else:
                if gst_pct >= 12:
                    half = round(amt * (gst_pct / 2) / 100, 2)
                    cgst_9 += half
                    sgst_9 += half
                elif gst_pct > 0:
                    half = round(amt * (gst_pct / 2) / 100, 2)
                    cgst_25 += half
                    sgst_25 += half
                    
        computed_total = taxable_sum + cgst_9 + sgst_9 + cgst_25 + sgst_25 + igst_18 + igst_5 - float(s.get('discount', 0))
        round_diff = round(total_amt - computed_total, 2)

        xml_lines.append('        <TALLYMESSAGE xmlns:UDF="TallyUDF">')
        xml_lines.append('          <VOUCHER VCHTYPE="Sales" ACTION="Create">')
        xml_lines.append(f'            <DATE>{date_tally}</DATE>')
        xml_lines.append(f'            <EFFECTIVEDATE>{date_tally}</EFFECTIVEDATE>')
        xml_lines.append('            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>')
        xml_lines.append('            <VCHENTRYMODE>Item Invoice</VCHENTRYMODE>')
        xml_lines.append(f'            <VOUCHERNUMBER>{vch_no}</VOUCHERNUMBER>')
        xml_lines.append(f'            <REFERENCE>{escape_xml(bill_id)}</REFERENCE>')
        xml_lines.append(f'            <INVOICENUMBER>{escape_xml(bill_id)}</INVOICENUMBER>')
        xml_lines.append(f'            <BILLNUMBER>{escape_xml(bill_id)}</BILLNUMBER>')
        xml_lines.append(f'            <PARTYLEDGERNAME>{escape_xml(party_name)}</PARTYLEDGERNAME>')
        xml_lines.append(f'            <PARTYNAME>{escape_xml(party_name)}</PARTYNAME>')
        xml_lines.append(f'            <STATENAME>{escape_xml(state_name)}</STATENAME>')
        xml_lines.append('            <COUNTRYOFRESIDENCE>India</COUNTRYOFRESIDENCE>')
        xml_lines.append(f'            <PLACEOFSUPPLY>{escape_xml(state_name)}</PLACEOFSUPPLY>')
        xml_lines.append(f'            <NARRATION>Tax Invoice {escape_xml(bill_id)} - {escape_xml(party_name)}</NARRATION>')

        # Party Ledger Entry (Debited in Sales -> negative in Tally Amount)
        xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
        xml_lines.append(f'              <LEDGERNAME>{escape_xml(party_name)}</LEDGERNAME>')
        xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
        xml_lines.append(f'              <AMOUNT>-{total_amt:.2f}</AMOUNT>')
        xml_lines.append('              <BILLALLOCATIONS.LIST>')
        xml_lines.append(f'                <NAME>{escape_xml(bill_id)}</NAME>')
        xml_lines.append('                <BILLTYPE>New Ref</BILLTYPE>')
        xml_lines.append(f'                <AMOUNT>-{total_amt:.2f}</AMOUNT>')
        xml_lines.append('              </BILLALLOCATIONS.LIST>')
        xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        # Inventory Items Entries
        for it in items:
            pname = it.get('product', 'Item')
            qty = float(it.get('qty', 1))
            unit = it.get('unit') or 'Pc'
            rate = float(it.get('price', 0))
            amt = round(qty * rate, 2)
            gst_pct = float(it.get('gst_pct', 0))

            # Determine sales ledger based on rate & place of supply
            if is_inter:
                if gst_pct >= 12:
                    sales_ledger = lm["sale_interstate_18"]
                elif gst_pct > 0:
                    sales_ledger = lm["sale_interstate_5"]
                else:
                    sales_ledger = lm["sale_interstate_0"]
            else:
                if gst_pct >= 12:
                    sales_ledger = lm["sale_local_18"]
                elif gst_pct > 0:
                    sales_ledger = lm["sale_local_5"]
                else:
                    sales_ledger = lm["sale_local_0"]

            # Stock Item naming convention (e.g. Shoes@5% or Shoes)
            stock_name = f"{pname}@{int(gst_pct)}%" if gst_pct > 0 else pname

            xml_lines.append('            <ALLINVENTORYENTRIES.LIST>')
            xml_lines.append(f'              <STOCKITEMNAME>{escape_xml(stock_name)}</STOCKITEMNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <RATE>{rate:.2f}/{escape_xml(unit)}</RATE>')
            xml_lines.append(f'              <AMOUNT>{amt:.2f}</AMOUNT>')
            xml_lines.append(f'              <ACTUALQTY>{qty} {escape_xml(unit)}</ACTUALQTY>')
            xml_lines.append(f'              <BILLEDQTY>{qty} {escape_xml(unit)}</BILLEDQTY>')
            xml_lines.append('              <BATCHALLOCATIONS.LIST>')
            xml_lines.append('                <GODOWNNAME>Main Location</GODOWNNAME>')
            xml_lines.append('                <BATCHNAME>Primary Batch</BATCHNAME>')
            xml_lines.append(f'                <AMOUNT>{amt:.2f}</AMOUNT>')
            xml_lines.append(f'                <ACTUALQTY>{qty} {escape_xml(unit)}</ACTUALQTY>')
            xml_lines.append(f'                <BILLEDQTY>{qty} {escape_xml(unit)}</BILLEDQTY>')
            xml_lines.append('              </BATCHALLOCATIONS.LIST>')
            xml_lines.append('              <ACCOUNTINGALLOCATIONS.LIST>')
            xml_lines.append(f'                <LEDGERNAME>{escape_xml(sales_ledger)}</LEDGERNAME>')
            xml_lines.append('                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'                <AMOUNT>{amt:.2f}</AMOUNT>')
            xml_lines.append('              </ACCOUNTINGALLOCATIONS.LIST>')
            xml_lines.append('            </ALLINVENTORYENTRIES.LIST>')

        # Rate-wise Output GST Ledgers
        if cgst_9 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["cgst_output_9"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{cgst_9:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["sgst_output_9"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{sgst_9:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if cgst_25 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["cgst_output_2_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{cgst_25:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["sgst_output_2_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{sgst_25:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if igst_18 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["igst_output_18"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{igst_18:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if igst_5 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["igst_output_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{igst_5:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        # Round Off
        if abs(round_diff) >= 0.01:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["round_off"])}</LEDGERNAME>')
            xml_lines.append(f'              <ISDEEMEDPOSITIVE>{"No" if round_diff > 0 else "Yes"}</ISDEEMEDPOSITIVE>')
            xml_lines.append('              <ROUNDTYPE>Normal Rounding</ROUNDTYPE>')
            xml_lines.append('              <ROUNDLIMIT>1</ROUNDLIMIT>')
            xml_lines.append(f'              <AMOUNT>{round_diff:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        xml_lines.append('          </VOUCHER>')
        xml_lines.append('        </TALLYMESSAGE>')
        vch_no += 1

    # --- PROCESS PURCHASE VOUCHERS ---
    pur_vch_no = 1
    for p in filtered_purchases:
        pur_id = p.get('purchase_id', f'P{pur_vch_no}')
        inv_no = p.get('invoice_no') or pur_id
        date_tally = format_date_tally(p.get('bill_date'))
        supplier_name = (p.get('supplier_name') or 'Supplier').strip()
        is_inter = bool(p.get('is_interstate', 0) or p.get('igst_amount', 0) > 0)
        total_amt = round(float(p.get('total_amount', 0)), 2)
        items = p.get('items', [])
        
        # Calculate rate-wise input GST
        cgst_9 = sgst_9 = cgst_25 = sgst_25 = igst_18 = igst_5 = 0.0
        taxable_sum = 0.0
        
        for it in items:
            q = float(it.get('qty', 1))
            pr = float(it.get('buy_price', it.get('price', 0)))
            amt = round(q * pr, 2)
            taxable_sum += amt
            gst_pct = float(it.get('gst_pct', 0))
            if is_inter:
                if gst_pct >= 12:
                    igst_18 += round(amt * gst_pct / 100, 2)
                elif gst_pct > 0:
                    igst_5 += round(amt * gst_pct / 100, 2)
            else:
                if gst_pct >= 12:
                    half = round(amt * (gst_pct / 2) / 100, 2)
                    cgst_9 += half
                    sgst_9 += half
                elif gst_pct > 0:
                    half = round(amt * (gst_pct / 2) / 100, 2)
                    cgst_25 += half
                    sgst_25 += half

        computed_total = taxable_sum + cgst_9 + sgst_9 + cgst_25 + sgst_25 + igst_18 + igst_5
        round_diff = round(total_amt - computed_total, 2)

        xml_lines.append('        <TALLYMESSAGE xmlns:UDF="TallyUDF">')
        xml_lines.append('          <VOUCHER VCHTYPE="Purchase" ACTION="Create">')
        xml_lines.append(f'            <DATE>{date_tally}</DATE>')
        xml_lines.append(f'            <EFFECTIVEDATE>{date_tally}</EFFECTIVEDATE>')
        xml_lines.append('            <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>')
        xml_lines.append('            <VCHENTRYMODE>Item Invoice</VCHENTRYMODE>')
        xml_lines.append(f'            <VOUCHERNUMBER>{pur_vch_no}</VOUCHERNUMBER>')
        xml_lines.append(f'            <REFERENCE>{escape_xml(inv_no)}</REFERENCE>')
        xml_lines.append(f'            <INVOICENUMBER>{escape_xml(inv_no)}</INVOICENUMBER>')
        xml_lines.append(f'            <BILLNUMBER>{escape_xml(inv_no)}</BILLNUMBER>')
        xml_lines.append(f'            <PARTYLEDGERNAME>{escape_xml(supplier_name)}</PARTYLEDGERNAME>')
        xml_lines.append(f'            <PARTYNAME>{escape_xml(supplier_name)}</PARTYNAME>')
        xml_lines.append(f'            <NARRATION>Purchase Bill {escape_xml(inv_no)} from {escape_xml(supplier_name)}</NARRATION>')

        # Supplier Ledger Entry (Credited in Purchase -> positive in Tally Amount)
        xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
        xml_lines.append(f'              <LEDGERNAME>{escape_xml(supplier_name)}</LEDGERNAME>')
        xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
        xml_lines.append(f'              <AMOUNT>{total_amt:.2f}</AMOUNT>')
        xml_lines.append('              <BILLALLOCATIONS.LIST>')
        xml_lines.append(f'                <NAME>{escape_xml(inv_no)}</NAME>')
        xml_lines.append('                <BILLTYPE>New Ref</BILLTYPE>')
        xml_lines.append(f'                <AMOUNT>{total_amt:.2f}</AMOUNT>')
        xml_lines.append('              </BILLALLOCATIONS.LIST>')
        xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        # Inventory Items Entries (Debited in Purchase -> negative in Tally Amount)
        for it in items:
            pname = it.get('product', 'Item')
            qty = float(it.get('qty', 1))
            unit = it.get('unit') or 'Pc'
            rate = float(it.get('buy_price', it.get('price', 0)))
            amt = round(qty * rate, 2)
            gst_pct = float(it.get('gst_pct', 0))

            if is_inter:
                if gst_pct >= 12:
                    pur_ledger = lm["purchase_interstate_18"]
                elif gst_pct > 0:
                    pur_ledger = lm["purchase_interstate_5"]
                else:
                    pur_ledger = lm["purchase_interstate_0"]
            else:
                if gst_pct >= 12:
                    pur_ledger = lm["purchase_local_18"]
                elif gst_pct > 0:
                    pur_ledger = lm["purchase_local_5"]
                else:
                    pur_ledger = lm["purchase_local_0"]

            stock_name = f"{pname}@{int(gst_pct)}%" if gst_pct > 0 else pname

            xml_lines.append('            <ALLINVENTORYENTRIES.LIST>')
            xml_lines.append(f'              <STOCKITEMNAME>{escape_xml(stock_name)}</STOCKITEMNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <RATE>{rate:.2f}/{escape_xml(unit)}</RATE>')
            xml_lines.append(f'              <AMOUNT>-{amt:.2f}</AMOUNT>')
            xml_lines.append(f'              <ACTUALQTY>{qty} {escape_xml(unit)}</ACTUALQTY>')
            xml_lines.append(f'              <BILLEDQTY>{qty} {escape_xml(unit)}</BILLEDQTY>')
            xml_lines.append('              <BATCHALLOCATIONS.LIST>')
            xml_lines.append('                <GODOWNNAME>Main Location</GODOWNNAME>')
            xml_lines.append('                <BATCHNAME>Primary Batch</BATCHNAME>')
            xml_lines.append(f'                <AMOUNT>-{amt:.2f}</AMOUNT>')
            xml_lines.append(f'                <ACTUALQTY>{qty} {escape_xml(unit)}</ACTUALQTY>')
            xml_lines.append(f'                <BILLEDQTY>{qty} {escape_xml(unit)}</BILLEDQTY>')
            xml_lines.append('              </BATCHALLOCATIONS.LIST>')
            xml_lines.append('              <ACCOUNTINGALLOCATIONS.LIST>')
            xml_lines.append(f'                <LEDGERNAME>{escape_xml(pur_ledger)}</LEDGERNAME>')
            xml_lines.append('                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'                <AMOUNT>-{amt:.2f}</AMOUNT>')
            xml_lines.append('              </ACCOUNTINGALLOCATIONS.LIST>')
            xml_lines.append('            </ALLINVENTORYENTRIES.LIST>')

        # Rate-wise Input GST Ledgers (Debited -> negative amount)
        if cgst_9 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["cgst_input_9"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{cgst_9:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["sgst_input_9"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{sgst_9:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if cgst_25 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["cgst_input_2_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{cgst_25:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["sgst_input_2_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{sgst_25:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if igst_18 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["igst_input_18"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{igst_18:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if igst_5 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["igst_input_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{igst_5:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if abs(round_diff) >= 0.01:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["round_off"])}</LEDGERNAME>')
            xml_lines.append(f'              <ISDEEMEDPOSITIVE>{"Yes" if round_diff < 0 else "No"}</ISDEEMEDPOSITIVE>')
            xml_lines.append('              <ROUNDTYPE>Normal Rounding</ROUNDTYPE>')
            xml_lines.append('              <ROUNDLIMIT>1</ROUNDLIMIT>')
            xml_lines.append(f'              <AMOUNT>-{round_diff:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        xml_lines.append('          </VOUCHER>')
        xml_lines.append('        </TALLYMESSAGE>')
        pur_vch_no += 1

    # --- PROCESS CREDIT NOTES (SALES RETURNS) ---
    filtered_cn = credit_notes_list or []
    cn_vch_no = 1
    for cn in filtered_cn:
        cn_no = cn.get('credit_note_no') or f"CN{cn.get('id')}"
        orig_bill = cn.get('original_bill_id') or ''
        date_tally = format_date_tally(cn.get('return_date'))
        party_name = (cn.get('customer_name') or 'Customer').strip()
        is_inter = bool(cn.get('is_interstate', 0) or cn.get('igst_amount', 0) > 0)
        total_amt = round(float(cn.get('total_amount', 0)), 2)
        items = cn.get('items', [])
        reason = cn.get('reason') or 'Goods Return'

        cgst_9 = sgst_9 = cgst_25 = sgst_25 = igst_18 = igst_5 = 0.0
        taxable_sum = 0.0

        for it in items:
            q = float(it.get('qty', 1))
            pr = float(it.get('price', 0))
            amt = round(q * pr, 2)
            taxable_sum += amt
            gst_pct = float(it.get('gst_pct', 18))
            if is_inter:
                if gst_pct >= 12:
                    igst_18 += round(amt * gst_pct / 100, 2)
                elif gst_pct > 0:
                    igst_5 += round(amt * gst_pct / 100, 2)
            else:
                if gst_pct >= 12:
                    half = round(amt * (gst_pct / 2) / 100, 2)
                    cgst_9 += half
                    sgst_9 += half
                elif gst_pct > 0:
                    half = round(amt * (gst_pct / 2) / 100, 2)
                    cgst_25 += half
                    sgst_25 += half

        computed_total = taxable_sum + cgst_9 + sgst_9 + cgst_25 + sgst_25 + igst_18 + igst_5
        round_diff = round(total_amt - computed_total, 2)

        xml_lines.append('        <TALLYMESSAGE xmlns:UDF="TallyUDF">')
        xml_lines.append('          <VOUCHER VCHTYPE="Credit Note" ACTION="Create">')
        xml_lines.append(f'            <DATE>{date_tally}</DATE>')
        xml_lines.append(f'            <EFFECTIVEDATE>{date_tally}</EFFECTIVEDATE>')
        xml_lines.append('            <VOUCHERTYPENAME>Credit Note</VOUCHERTYPENAME>')
        xml_lines.append(f'            <VOUCHERNUMBER>{escape_xml(cn_no)}</VOUCHERNUMBER>')
        xml_lines.append(f'            <REFERENCE>{escape_xml(orig_bill)}</REFERENCE>')
        xml_lines.append(f'            <PARTYLEDGERNAME>{escape_xml(party_name)}</PARTYLEDGERNAME>')
        xml_lines.append('            <ISINVOICE>Yes</ISINVOICE>')
        xml_lines.append('            <VCHENTRYMODE>Item Invoice</VCHENTRYMODE>')
        xml_lines.append(f'            <NARRATION>Credit Note {escape_xml(cn_no)} against Invoice {escape_xml(orig_bill)} - {escape_xml(reason)}</NARRATION>')

        # Party Ledger: Credited
        xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
        xml_lines.append(f'              <LEDGERNAME>{escape_xml(party_name)}</LEDGERNAME>')
        xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
        xml_lines.append(f'              <AMOUNT>{total_amt:.2f}</AMOUNT>')
        xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        # Inventory entries
        for it in items:
            q = float(it.get('qty', 1))
            pr = float(it.get('price', 0))
            amt = round(q * pr, 2)
            gst_pct = float(it.get('gst_pct', 18))
            unit = it.get('unit') or 'Pcs'
            if is_inter:
                sales_ledger = lm["sale_interstate_18"] if gst_pct >= 12 else (lm["sale_interstate_5"] if gst_pct > 0 else lm["sale_interstate_0"])
            else:
                sales_ledger = lm["sale_local_18"] if gst_pct >= 12 else (lm["sale_local_5"] if gst_pct > 0 else lm["sale_local_0"])

            xml_lines.append('            <ALLINVENTORYENTRIES.LIST>')
            xml_lines.append(f'              <STOCKITEMNAME>{escape_xml(it.get("product", "Item"))}</STOCKITEMNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <RATE>{pr:.2f}/{escape_xml(unit)}</RATE>')
            xml_lines.append(f'              <AMOUNT>-{amt:.2f}</AMOUNT>')
            xml_lines.append(f'              <ACTUALQTY>{q} {escape_xml(unit)}</ACTUALQTY>')
            xml_lines.append(f'              <BILLEDQTY>{q} {escape_xml(unit)}</BILLEDQTY>')
            xml_lines.append('              <ACCOUNTINGALLOCATIONS.LIST>')
            xml_lines.append(f'                <LEDGERNAME>{escape_xml(sales_ledger)}</LEDGERNAME>')
            xml_lines.append('                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'                <AMOUNT>-{amt:.2f}</AMOUNT>')
            xml_lines.append('              </ACCOUNTINGALLOCATIONS.LIST>')
            xml_lines.append('            </ALLINVENTORYENTRIES.LIST>')

        # Output GST Debited
        if cgst_9 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["cgst_output_9"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{cgst_9:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["sgst_output_9"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{sgst_9:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if cgst_25 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["cgst_output_2_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{cgst_25:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["sgst_output_2_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{sgst_25:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if igst_18 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["igst_output_18"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{igst_18:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if igst_5 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["igst_output_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{igst_5:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if abs(round_diff) >= 0.01:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["round_off"])}</LEDGERNAME>')
            xml_lines.append(f'              <ISDEEMEDPOSITIVE>{"No" if round_diff < 0 else "Yes"}</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{round_diff:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        xml_lines.append('          </VOUCHER>')
        xml_lines.append('        </TALLYMESSAGE>')
        cn_vch_no += 1

    # --- PROCESS DEBIT NOTES (PURCHASE RETURNS) ---
    filtered_dn = debit_notes_list or []
    dn_vch_no = 1
    for dn in filtered_dn:
        dn_no = dn.get('debit_note_no') or f"DN{dn.get('id')}"
        orig_inv = dn.get('original_invoice_no') or ''
        date_tally = format_date_tally(dn.get('return_date'))
        supplier_name = (dn.get('supplier_name') or 'Supplier').strip()
        is_inter = bool(dn.get('is_interstate', 0) or dn.get('igst_amount', 0) > 0)
        total_amt = round(float(dn.get('total_amount', 0)), 2)
        items = dn.get('items', [])
        reason = dn.get('reason') or 'Goods Returned to Supplier'

        cgst_9 = sgst_9 = cgst_25 = sgst_25 = igst_18 = igst_5 = 0.0
        taxable_sum = 0.0

        for it in items:
            q = float(it.get('qty', 1))
            pr = float(it.get('price', 0))
            amt = round(q * pr, 2)
            taxable_sum += amt
            gst_pct = float(it.get('gst_pct', 18))
            if is_inter:
                if gst_pct >= 12:
                    igst_18 += round(amt * gst_pct / 100, 2)
                elif gst_pct > 0:
                    igst_5 += round(amt * gst_pct / 100, 2)
            else:
                if gst_pct >= 12:
                    half = round(amt * (gst_pct / 2) / 100, 2)
                    cgst_9 += half
                    sgst_9 += half
                elif gst_pct > 0:
                    half = round(amt * (gst_pct / 2) / 100, 2)
                    cgst_25 += half
                    sgst_25 += half

        computed_total = taxable_sum + cgst_9 + sgst_9 + cgst_25 + sgst_25 + igst_18 + igst_5
        round_diff = round(total_amt - computed_total, 2)

        xml_lines.append('        <TALLYMESSAGE xmlns:UDF="TallyUDF">')
        xml_lines.append('          <VOUCHER VCHTYPE="Debit Note" ACTION="Create">')
        xml_lines.append(f'            <DATE>{date_tally}</DATE>')
        xml_lines.append(f'            <EFFECTIVEDATE>{date_tally}</EFFECTIVEDATE>')
        xml_lines.append('            <VOUCHERTYPENAME>Debit Note</VOUCHERTYPENAME>')
        xml_lines.append(f'            <VOUCHERNUMBER>{escape_xml(dn_no)}</VOUCHERNUMBER>')
        xml_lines.append(f'            <REFERENCE>{escape_xml(orig_inv)}</REFERENCE>')
        xml_lines.append(f'            <PARTYLEDGERNAME>{escape_xml(supplier_name)}</PARTYLEDGERNAME>')
        xml_lines.append('            <ISINVOICE>Yes</ISINVOICE>')
        xml_lines.append('            <VCHENTRYMODE>Item Invoice</VCHENTRYMODE>')
        xml_lines.append(f'            <NARRATION>Debit Note {escape_xml(dn_no)} against Invoice {escape_xml(orig_inv)} - {escape_xml(reason)}</NARRATION>')

        # Supplier Ledger: Debited
        xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
        xml_lines.append(f'              <LEDGERNAME>{escape_xml(supplier_name)}</LEDGERNAME>')
        xml_lines.append('              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>')
        xml_lines.append(f'              <AMOUNT>-{total_amt:.2f}</AMOUNT>')
        xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        # Inventory entries
        for it in items:
            q = float(it.get('qty', 1))
            pr = float(it.get('price', 0))
            amt = round(q * pr, 2)
            gst_pct = float(it.get('gst_pct', 18))
            unit = it.get('unit') or 'Pcs'
            if is_inter:
                pur_ledger = lm["purchase_interstate_18"] if gst_pct >= 12 else (lm["purchase_interstate_5"] if gst_pct > 0 else lm["purchase_interstate_0"])
            else:
                pur_ledger = lm["purchase_local_18"] if gst_pct >= 12 else (lm["purchase_local_5"] if gst_pct > 0 else lm["purchase_local_0"])

            xml_lines.append('            <ALLINVENTORYENTRIES.LIST>')
            xml_lines.append(f'              <STOCKITEMNAME>{escape_xml(it.get("product", "Item"))}</STOCKITEMNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <RATE>{pr:.2f}/{escape_xml(unit)}</RATE>')
            xml_lines.append(f'              <AMOUNT>{amt:.2f}</AMOUNT>')
            xml_lines.append(f'              <ACTUALQTY>{q} {escape_xml(unit)}</ACTUALQTY>')
            xml_lines.append(f'              <BILLEDQTY>{q} {escape_xml(unit)}</BILLEDQTY>')
            xml_lines.append('              <ACCOUNTINGALLOCATIONS.LIST>')
            xml_lines.append(f'                <LEDGERNAME>{escape_xml(pur_ledger)}</LEDGERNAME>')
            xml_lines.append('                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'                <AMOUNT>{amt:.2f}</AMOUNT>')
            xml_lines.append('              </ACCOUNTINGALLOCATIONS.LIST>')
            xml_lines.append('            </ALLINVENTORYENTRIES.LIST>')

        # Input GST Credited
        if cgst_9 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["cgst_input_9"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{cgst_9:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["sgst_input_9"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{sgst_9:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if cgst_25 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["cgst_input_2_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{cgst_25:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["sgst_input_2_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{sgst_25:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if igst_18 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["igst_input_18"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{igst_18:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if igst_5 > 0:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["igst_input_5"])}</LEDGERNAME>')
            xml_lines.append('              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>{igst_5:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        if abs(round_diff) >= 0.01:
            xml_lines.append('            <ALLLEDGERENTRIES.LIST>')
            xml_lines.append(f'              <LEDGERNAME>{escape_xml(lm["round_off"])}</LEDGERNAME>')
            xml_lines.append(f'              <ISDEEMEDPOSITIVE>{"Yes" if round_diff < 0 else "No"}</ISDEEMEDPOSITIVE>')
            xml_lines.append(f'              <AMOUNT>-{round_diff:.2f}</AMOUNT>')
            xml_lines.append('            </ALLLEDGERENTRIES.LIST>')

        xml_lines.append('          </VOUCHER>')
        xml_lines.append('        </TALLYMESSAGE>')
        dn_vch_no += 1

    xml_lines.append('      </REQUESTDATA>')
    xml_lines.append('    </IMPORTDATA>')
    xml_lines.append('  </BODY>')
    xml_lines.append('</ENVELOPE>')

    return "\n".join(xml_lines), len(filtered_sales), len(filtered_purchases)
