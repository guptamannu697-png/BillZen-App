"""
BillZen AI — Professional Accounting Excel Export Engine
Standardized to CA / Accountant Audit Quality:
- Multi-sheet workbooks with Dashboard Summary, Registers, Stock, Customer, and GST
- Proper typography, headers, freeze panes, AutoFilter, borders, landscape printing
- True numeric values with Indian Currency formatting (₹#,##,##0.00 / [$₹-en-IN]#,##0.00)
- Prominent accounting bottom TOTAL rows with SUM formulas
"""

import io
from datetime import datetime, date
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Palette & Fonts
FONT_NAME = 'Calibri'
NAVY_HEADER_FILL = PatternFill(start_color='1E293B', end_color='1E293B', fill_type='solid')      # Slate 800
SUBHEADER_FILL = PatternFill(start_color='334155', end_color='334155', fill_type='solid')        # Slate 700
ACCENT_FILL = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')           # Slate 100
ZEBRA_FILL = PatternFill(start_color='F8FAFC', end_color='F8FAFC', fill_type='solid')            # Slate 50
TOTAL_FILL = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')

FONT_TITLE = Font(name=FONT_NAME, size=15, bold=True, color='0F172A')
FONT_SUBTITLE = Font(name=FONT_NAME, size=10, italic=True, color='475569')
FONT_HEADER = Font(name=FONT_NAME, size=11, bold=True, color='FFFFFF')
FONT_DATA = Font(name=FONT_NAME, size=10, color='1E293B')
FONT_TOTAL = Font(name=FONT_NAME, size=11, bold=True, color='0F172A')

THIN_BORDER_SIDE = Side(style='thin', color='CBD5E1')
DOUBLE_BORDER_SIDE = Side(style='double', color='0F172A')

BORDER_CELL = Border(left=THIN_BORDER_SIDE, right=THIN_BORDER_SIDE, top=THIN_BORDER_SIDE, bottom=THIN_BORDER_SIDE)
BORDER_HEADER = Border(left=THIN_BORDER_SIDE, right=THIN_BORDER_SIDE, top=THIN_BORDER_SIDE, bottom=THIN_BORDER_SIDE)
BORDER_TOTAL = Border(left=THIN_BORDER_SIDE, right=THIN_BORDER_SIDE, top=THIN_BORDER_SIDE, bottom=DOUBLE_BORDER_SIDE)

ALIGN_LEFT = Alignment(horizontal='left', vertical='center')
ALIGN_CENTER = Alignment(horizontal='center', vertical='center')
ALIGN_RIGHT = Alignment(horizontal='right', vertical='center')

FMT_CURRENCY = r'_("₹"* #,##0.00_);_("₹"* \(#,##0.00\);_("₹"* "-"??_);_(@_)'
FMT_QTY = '#,##0.00'
FMT_QTY_INT = '#,##0'
FMT_DATE = 'DD-MM-YYYY'


def _format_date(val):
    if not val:
        return ''
    if isinstance(val, (datetime, date)):
        return val.strftime('%d-%m-%Y')
    val_str = str(val).strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(val_str[:10], fmt[:8] if len(fmt) > 8 and len(val_str) == 10 else fmt).strftime('%d-%m-%Y')
        except Exception:
            pass
    return val_str


def _setup_sheet_view(ws, header_row, min_col=1, max_col=1, landscape=True):
    """Applies freeze panes, auto-filter, and print properties."""
    ws.views.sheetView[0].showGridLines = True
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    
    col_start_letter = get_column_letter(min_col)
    col_end_letter = get_column_letter(max_col)
    ws.auto_filter.ref = f"{col_start_letter}{header_row}:{col_end_letter}{ws.max_row}"
    
    # Page setup for printing
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE if landscape else ws.ORIENTATION_PORTRAIT
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = f'{header_row}:{header_row}'


def _auto_fit_columns(ws, min_col=1, max_col=None, padding=3):
    """Computes legible column widths without cutting content."""
    if max_col is None:
        max_col = ws.max_column
    for col in range(min_col, max_col + 1):
        col_letter = get_column_letter(col)
        max_len = 0
        for cell in ws[col_letter]:
            if cell.row in (1, 2, 3):  # Ignore title banners for column width calculation
                continue
            val_str = str(cell.value or '')
            if cell.number_format == FMT_CURRENCY:
                val_str = f"₹{val_str}.00"
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + padding, 12)


def _write_title_block(ws, shop_name, report_title, period_str='', max_col=10):
    """Writes standard professional BillZen report header banner."""
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)
    ws.cell(row=1, column=1, value=f"BILLZEN AI  •  {shop_name.upper()}").font = FONT_TITLE
    ws.cell(row=1, column=1).alignment = ALIGN_LEFT
    ws.row_dimensions[1].height = 24

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_col)
    period_info = f"Period: {period_str}   |   " if period_str else ""
    gen_time = datetime.now().strftime('%d-%m-%Y %I:%M %p')
    ws.cell(row=2, column=1, value=f"{report_title}   |   {period_info}Generated On: {gen_time}").font = FONT_SUBTITLE
    ws.cell(row=2, column=1).alignment = ALIGN_LEFT
    ws.row_dimensions[2].height = 18
    ws.row_dimensions[3].height = 8


# ─────────────────────────────────────────────────────────────
# 1. REPORTS FULL WORKBOOK (6 SHEETS)
# ─────────────────────────────────────────────────────────────
def generate_reports_workbook(shop_info, month_str, sales_data, purchases_data, products_data, customers_data):
    """
    Builds the 6-sheet Reports Master Excel:
    1. Dashboard Summary
    2. Sales Register
    3. Purchase Register
    4. Stock Report
    5. Customer Summary
    6. GST Summary
    """
    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    shop_name = shop_info.get('name') or 'BillZen Business'
    period_label = f"Month: {month_str}" if month_str else "All Records"

    # ==================== SHEET 1: DASHBOARD SUMMARY ====================
    ws1 = wb.create_sheet(title="Dashboard Summary")
    _write_title_block(ws1, shop_name, "Executive Financial Dashboard", period_label, max_col=6)

    # Key Performance Indicators
    tot_sales = sum(float(s.get('total_amount') or 0) for s in sales_data if not s.get('cancelled'))
    tot_purch = sum(float(p.get('total_amount') or 0) for p in purchases_data)
    tot_paid_sales = sum(float(s.get('paid_amount') or 0) for s in sales_data if not s.get('cancelled'))
    tot_pending = max(0.0, tot_sales - tot_paid_sales)
    net_profit = tot_sales - tot_purch
    inv_cost = sum(float(p.get('stock') or 0) * float(p.get('buy_price') or 0) for p in products_data)
    inv_sell = sum(float(p.get('stock') or 0) * float(p.get('sell_price') or 0) for p in products_data)
    inv_profit = inv_sell - inv_cost

    kpis = [
        ("TOTAL REVENUE (SALES)", tot_sales),
        ("TOTAL PURCHASES", tot_purch),
        ("ESTIMATED NET OPERATING PROFIT", net_profit),
        ("PENDING CUSTOMER RECEIVABLES", tot_pending),
        ("CURRENT INVENTORY COST VALUE", inv_cost),
    ]

    r = 4
    ws1.cell(row=r, column=1, value="KEY METRICS").font = Font(name=FONT_NAME, size=11, bold=True, color='FFFFFF')
    ws1.cell(row=r, column=1).fill = NAVY_HEADER_FILL
    ws1.cell(row=r, column=2, value="AMOUNT (₹)").font = Font(name=FONT_NAME, size=11, bold=True, color='FFFFFF')
    ws1.cell(row=r, column=2).fill = NAVY_HEADER_FILL
    ws1.cell(row=r, column=2).alignment = ALIGN_RIGHT
    ws1.row_dimensions[r].height = 20

    for name, val in kpis:
        r += 1
        c1 = ws1.cell(row=r, column=1, value=name)
        c1.font = FONT_DATA
        c1.border = BORDER_CELL
        c2 = ws1.cell(row=r, column=2, value=float(round(val, 2)))
        c2.font = FONT_TOTAL
        c2.number_format = FMT_CURRENCY
        c2.alignment = ALIGN_RIGHT
        c2.border = BORDER_CELL
        ws1.row_dimensions[r].height = 20

    # Stock Value Analysis Table
    r += 2
    ws1.cell(row=r, column=1, value="STOCK VALUE ANALYSIS").font = Font(name=FONT_NAME, size=11, bold=True, color='FFFFFF')
    ws1.cell(row=r, column=1).fill = SUBHEADER_FILL
    ws1.cell(row=r, column=2, value="VALUE (₹)").font = Font(name=FONT_NAME, size=11, bold=True, color='FFFFFF')
    ws1.cell(row=r, column=2).fill = SUBHEADER_FILL
    ws1.cell(row=r, column=2).alignment = ALIGN_RIGHT

    stock_metrics = [
        ("Inventory Cost Value", inv_cost),
        ("Potential Sale Value", inv_sell),
        ("Potential Inventory Profit", inv_profit),
    ]
    for sm, sv in stock_metrics:
        r += 1
        c1 = ws1.cell(row=r, column=1, value=sm)
        c1.font = FONT_DATA
        c1.border = BORDER_CELL
        c2 = ws1.cell(row=r, column=2, value=float(round(sv, 2)))
        c2.font = FONT_TOTAL
        c2.number_format = FMT_CURRENCY
        c2.alignment = ALIGN_RIGHT
        c2.border = BORDER_CELL

    # Top Customers Table
    r += 2
    ws1.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    ws1.cell(row=r, column=1, value="TOP REVENUE CUSTOMERS").font = Font(name=FONT_NAME, size=11, bold=True, color='FFFFFF')
    ws1.cell(row=r, column=1).fill = NAVY_HEADER_FILL
    r += 1
    top_headers = ["Customer Name", "Invoices", "Total Sales (₹)", "Status"]
    for ci, th in enumerate(top_headers, 1):
        cell = ws1.cell(row=r, column=ci, value=th)
        cell.font = Font(name=FONT_NAME, size=10, bold=True, color='FFFFFF')
        cell.fill = SUBHEADER_FILL
        cell.alignment = ALIGN_RIGHT if "₹" in th or "Invoices" in th else ALIGN_LEFT

    cust_totals = {}
    for s in sales_data:
        if s.get('cancelled'):
            continue
        cname = (s.get('customer_name') or 'Walk-in Customer').strip()
        amt = float(s.get('total_amount') or 0)
        cust_totals.setdefault(cname, {'count': 0, 'total': 0.0})
        cust_totals[cname]['count'] += 1
        cust_totals[cname]['total'] += amt

    sorted_custs = sorted(cust_totals.items(), key=lambda x: x[1]['total'], reverse=True)[:10]
    for cname, info in sorted_custs:
        r += 1
        ws1.cell(row=r, column=1, value=cname).font = FONT_DATA
        ws1.cell(row=r, column=1).border = BORDER_CELL
        c_cnt = ws1.cell(row=r, column=2, value=info['count'])
        c_cnt.font = FONT_DATA
        c_cnt.alignment = ALIGN_RIGHT
        c_cnt.border = BORDER_CELL
        c_tot = ws1.cell(row=r, column=3, value=float(round(info['total'], 2)))
        c_tot.font = FONT_TOTAL
        c_tot.number_format = FMT_CURRENCY
        c_tot.alignment = ALIGN_RIGHT
        c_tot.border = BORDER_CELL
        ws1.cell(row=r, column=4, value="Active").alignment = ALIGN_CENTER
        ws1.cell(row=r, column=4).border = BORDER_CELL

    ws1.views.sheetView[0].showGridLines = True
    _auto_fit_columns(ws1, min_col=1, max_col=6)

    # ==================== SHEET 2: SALES REGISTER ====================
    ws2 = wb.create_sheet(title="Sales Register")
    _write_title_block(ws2, shop_name, "Comprehensive Sales Register", period_label, max_col=14)
    h_row = 4
    sales_headers = [
        "Bill ID", "Invoice No", "Date", "Customer", "GSTIN", "Supply Type",
        "Taxable Value", "CGST", "SGST", "IGST", "Invoice Total", "Paid", "Pending", "Status"
    ]
    for ci, th in enumerate(sales_headers, 1):
        cell = ws2.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Value", "CGST", "SGST", "IGST", "Total", "Paid", "Pending")) else ALIGN_LEFT
    ws2.row_dimensions[h_row].height = 22

    start_data_row = h_row + 1
    curr_row = h_row
    for s in sales_data:
        curr_row += 1
        is_canc = bool(s.get('cancelled'))
        is_inter = bool(s.get('is_interstate') or (s.get('igst_amount') or 0) > 0)
        inv_no = s.get('invoice_no') or s.get('bill_id') or ''
        taxable = float(s.get('subtotal') or 0)
        cgst = float(s.get('cgst_amount') or 0)
        sgst = float(s.get('sgst_amount') or 0)
        igst = float(s.get('igst_amount') or 0)
        total = float(s.get('total_amount') or 0)
        paid = float(s.get('paid_amount') or 0)
        pending = max(0.0, total - paid)
        status_val = "Cancelled" if is_canc else (s.get('payment_status') or 'Paid')

        row_vals = [
            s.get('bill_id', ''),
            inv_no,
            _format_date(s.get('sale_date')),
            s.get('customer_name') or 'Walk-in',
            s.get('customer_gstin') or '',
            'Inter-State' if is_inter else 'Intra-State',
            round(taxable, 2),
            round(cgst, 2),
            round(sgst, 2),
            round(igst, 2),
            round(total, 2),
            round(paid, 2),
            round(pending, 2),
            status_val
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws2.cell(row=curr_row, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (7, 8, 9, 10, 11, 12, 13):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci in (1, 2, 3, 5, 14):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_row % 2 == 0:
            for ci in range(1, 15):
                if ws2.cell(row=curr_row, column=ci).fill == PatternFill(fill_type=None):
                    ws2.cell(row=curr_row, column=ci).fill = ZEBRA_FILL
        ws2.row_dimensions[curr_row].height = 19

    # TOTAL ROW
    if curr_row >= start_data_row:
        tot_row = curr_row + 1
        ws2.cell(row=tot_row, column=1, value="TOTAL SALES").font = FONT_TOTAL
        ws2.cell(row=tot_row, column=1).alignment = ALIGN_LEFT
        for col_idx in range(1, 15):
            c = ws2.cell(row=tot_row, column=col_idx)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if col_idx in (7, 8, 9, 10, 11, 12, 13):
                col_let = get_column_letter(col_idx)
                c.value = f"=SUM({col_let}{start_data_row}:{col_let}{curr_row})"
                c.font = FONT_TOTAL
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws2.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws2, h_row, min_col=1, max_col=14, landscape=True)
    _auto_fit_columns(ws2, min_col=1, max_col=14)

    # ==================== SHEET 3: PURCHASE REGISTER ====================
    ws3 = wb.create_sheet(title="Purchase Register")
    _write_title_block(ws3, shop_name, "Inward Supplies & Purchase Register", period_label, max_col=12)
    h_row = 4
    pur_headers = [
        "Purchase ID", "Invoice No", "Date", "Supplier", "GSTIN", "Supply Type",
        "Taxable Value", "CGST", "SGST", "IGST", "Purchase Total", "Payment Status"
    ]
    for ci, th in enumerate(pur_headers, 1):
        cell = ws3.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Value", "CGST", "SGST", "IGST", "Total")) else ALIGN_LEFT
    ws3.row_dimensions[h_row].height = 22

    start_data_row = h_row + 1
    curr_row = h_row
    for p in purchases_data:
        curr_row += 1
        is_inter = bool(p.get('is_interstate') or (p.get('igst_amount') or 0) > 0)
        taxable = float(p.get('subtotal') or 0)
        cgst = float(p.get('cgst_amount') or 0)
        sgst = float(p.get('sgst_amount') or 0)
        igst = float(p.get('igst_amount') or 0)
        total = float(p.get('total_amount') or 0)

        row_vals = [
            p.get('purchase_id') or f"PUR-{p.get('id')}",
            p.get('invoice_no') or '',
            _format_date(p.get('bill_date')),
            p.get('supplier_name') or '',
            p.get('supplier_gstin') or '',
            'Inter-State' if is_inter else 'Intra-State',
            round(taxable, 2),
            round(cgst, 2),
            round(sgst, 2),
            round(igst, 2),
            round(total, 2),
            p.get('payment_status') or 'Paid'
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws3.cell(row=curr_row, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (7, 8, 9, 10, 11):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci in (1, 2, 3, 5, 12):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_row % 2 == 0:
            for ci in range(1, 13):
                ws3.cell(row=curr_row, column=ci).fill = ZEBRA_FILL
        ws3.row_dimensions[curr_row].height = 19

    # TOTAL ROW
    if curr_row >= start_data_row:
        tot_row = curr_row + 1
        ws3.cell(row=tot_row, column=1, value="TOTAL PURCHASE").font = FONT_TOTAL
        ws3.cell(row=tot_row, column=1).alignment = ALIGN_LEFT
        for col_idx in range(1, 13):
            c = ws3.cell(row=tot_row, column=col_idx)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if col_idx in (7, 8, 9, 10, 11):
                col_let = get_column_letter(col_idx)
                c.value = f"=SUM({col_let}{start_data_row}:{col_let}{curr_row})"
                c.font = FONT_TOTAL
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws3.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws3, h_row, min_col=1, max_col=12, landscape=True)
    _auto_fit_columns(ws3, min_col=1, max_col=12)

    # ==================== SHEET 4: STOCK REPORT ====================
    ws4 = wb.create_sheet(title="Stock Report")
    _write_title_block(ws4, shop_name, "Inventory Valuation & Stock Status Report", period_label, max_col=10)
    h_row = 4
    stock_headers = [
        "Product", "HSN", "Unit", "Current Stock", "Cost Price", "Inventory Value",
        "Selling Price", "Potential Sale Value", "Potential Profit", "GST %"
    ]
    for ci, th in enumerate(stock_headers, 1):
        cell = ws4.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Stock", "Price", "Value", "Profit", "%")) else ALIGN_LEFT
    ws4.row_dimensions[h_row].height = 22

    start_data_row = h_row + 1
    curr_row = h_row
    for pr in products_data:
        curr_row += 1
        stk = float(pr.get('stock') or 0)
        buy = float(pr.get('buy_price') or 0)
        sell = float(pr.get('sell_price') or 0)
        inv_val = stk * buy
        sale_val = stk * sell
        pot_prof = sale_val - inv_val

        row_vals = [
            pr.get('name', ''),
            pr.get('hsn_code') or '',
            pr.get('unit') or 'Pcs',
            round(stk, 2),
            round(buy, 2),
            round(inv_val, 2),
            round(sell, 2),
            round(sale_val, 2),
            round(pot_prof, 2),
            f"{pr.get('gst_pct', 18)}%"
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws4.cell(row=curr_row, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (5, 6, 7, 8, 9):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci == 4:
                c.number_format = FMT_QTY
                c.alignment = ALIGN_RIGHT
            elif ci in (2, 3, 10):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_row % 2 == 0:
            for ci in range(1, 11):
                ws4.cell(row=curr_row, column=ci).fill = ZEBRA_FILL
        ws4.row_dimensions[curr_row].height = 19

    # TOTAL ROW FOR STOCK
    if curr_row >= start_data_row:
        tot_row = curr_row + 1
        ws4.cell(row=tot_row, column=1, value="TOTALS").font = FONT_TOTAL
        ws4.cell(row=tot_row, column=1).alignment = ALIGN_LEFT
        for col_idx in range(1, 11):
            c = ws4.cell(row=tot_row, column=col_idx)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if col_idx in (4, 6, 8, 9):
                col_let = get_column_letter(col_idx)
                c.value = f"=SUM({col_let}{start_data_row}:{col_let}{curr_row})"
                c.font = FONT_TOTAL
                c.number_format = FMT_QTY if col_idx == 4 else FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws4.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws4, h_row, min_col=1, max_col=10, landscape=True)
    _auto_fit_columns(ws4, min_col=1, max_col=10)

    # ==================== SHEET 5: CUSTOMER SUMMARY ====================
    ws5 = wb.create_sheet(title="Customer Summary")
    _write_title_block(ws5, shop_name, "Customer Sales & Receivable Ledger", period_label, max_col=7)
    h_row = 4
    cust_headers = [
        "Customer", "GSTIN", "Total Sales", "Paid", "Pending", "Credit Notes/Returns", "Net Revenue"
    ]
    for ci, th in enumerate(cust_headers, 1):
        cell = ws5.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Sales", "Paid", "Pending", "Returns", "Revenue")) else ALIGN_LEFT
    ws5.row_dimensions[h_row].height = 22

    start_data_row = h_row + 1
    curr_row = h_row
    # Build per-customer aggregation
    cust_map = {}
    for c in customers_data:
        cname = (c.get('name') or '').strip()
        cust_map[cname] = {
            'gstin': c.get('gstin') or '',
            'sales': 0.0,
            'paid': 0.0,
            'returns': 0.0
        }

    for s in sales_data:
        if s.get('cancelled'):
            continue
        cname = (s.get('customer_name') or 'Walk-in Customer').strip()
        cust_map.setdefault(cname, {'gstin': s.get('customer_gstin') or '', 'sales': 0.0, 'paid': 0.0, 'returns': 0.0})
        cust_map[cname]['sales'] += float(s.get('total_amount') or 0)
        cust_map[cname]['paid'] += float(s.get('paid_amount') or 0)

    for cname, cinfo in sorted(cust_map.items()):
        curr_row += 1
        pend = max(0.0, cinfo['sales'] - cinfo['paid'])
        net_rev = cinfo['sales'] - cinfo['returns']
        row_vals = [
            cname,
            cinfo['gstin'],
            round(cinfo['sales'], 2),
            round(cinfo['paid'], 2),
            round(pend, 2),
            round(cinfo['returns'], 2),
            round(net_rev, 2)
        ]
        for ci, val in enumerate(row_vals, 1):
            c = ws5.cell(row=curr_row, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (3, 4, 5, 6, 7):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci == 2:
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT
        if curr_row % 2 == 0:
            for ci in range(1, 8):
                ws5.cell(row=curr_row, column=ci).fill = ZEBRA_FILL
        ws5.row_dimensions[curr_row].height = 19

    if curr_row >= start_data_row:
        tot_row = curr_row + 1
        ws5.cell(row=tot_row, column=1, value="TOTALS").font = FONT_TOTAL
        ws5.cell(row=tot_row, column=1).alignment = ALIGN_LEFT
        for col_idx in range(1, 8):
            c = ws5.cell(row=tot_row, column=col_idx)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if col_idx in (3, 4, 5, 6, 7):
                col_let = get_column_letter(col_idx)
                c.value = f"=SUM({col_let}{start_data_row}:{col_let}{curr_row})"
                c.font = FONT_TOTAL
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws5.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws5, h_row, min_col=1, max_col=7, landscape=False)
    _auto_fit_columns(ws5, min_col=1, max_col=7)

    # ==================== SHEET 6: GST SUMMARY ====================
    ws6 = wb.create_sheet(title="GST Summary")
    _write_title_block(ws6, shop_name, "GST Rate-Wise Tax Liability Summary", period_label, max_col=6)
    h_row = 4
    gst_headers = ["GST Rate", "Taxable Value", "CGST", "SGST", "IGST", "Total GST"]
    for ci, th in enumerate(gst_headers, 1):
        cell = ws6.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if "Value" in th or "GST" in th else ALIGN_LEFT
    ws6.row_dimensions[h_row].height = 22

    # Collect rate buckets from official (non-kacha) tax invoices
    rate_buckets = {
        "28%": {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0},
        "18%": {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0},
        "12%": {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0},
        "5%":  {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0},
        "0% (Exempt)": {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0},
    }

    for s in sales_data:
        if s.get('cancelled') or s.get('bill_category') == 'kacha':
            continue
        is_inter = bool(s.get('is_interstate') or (s.get('igst_amount') or 0) > 0)
        items = s.get('items') or []
        if items:
            for it in items:
                pct = round(float(it.get('gst_pct') or 0), 2)
                q = float(it.get('qty') or 1)
                pr = float(it.get('price') or 0)
                taxable = round(q * pr, 2)
                if abs(pct - 28.0) <= 0.1:
                    b_name = "28%"
                elif abs(pct - 18.0) <= 0.1:
                    b_name = "18%"
                elif abs(pct - 12.0) <= 0.1:
                    b_name = "12%"
                elif abs(pct - 5.0) <= 0.1:
                    b_name = "5%"
                elif abs(pct - 0.0) <= 0.1:
                    b_name = "0% (Exempt)"
                else:
                    continue
                rate_buckets[b_name]["taxable"] += taxable
                if is_inter:
                    rate_buckets[b_name]["igst"] += round(taxable * (pct / 100), 2)
                else:
                    half = round(taxable * (pct / 200), 2)
                    rate_buckets[b_name]["cgst"] += half
                    rate_buckets[b_name]["sgst"] += half
        else:
            # Fallback to invoice header amounts
            sub = float(s.get('subtotal') or 0)
            cgst = float(s.get('cgst_amount') or 0)
            sgst = float(s.get('sgst_amount') or 0)
            igst = float(s.get('igst_amount') or 0)
            tot_tax = cgst + sgst + igst
            eff_pct = round((tot_tax / sub * 100.0), 2) if sub > 0 else 0.0
            if abs(eff_pct - 28.0) <= 0.5:
                fb_name = "28%"
            elif abs(eff_pct - 18.0) <= 0.5:
                fb_name = "18%"
            elif abs(eff_pct - 12.0) <= 0.5:
                fb_name = "12%"
            elif abs(eff_pct - 5.0) <= 0.5:
                fb_name = "5%"
            elif abs(eff_pct - 0.0) <= 0.5:
                fb_name = "0% (Exempt)"
            else:
                continue
            rate_buckets[fb_name]["taxable"] += sub
            rate_buckets[fb_name]["cgst"] += cgst
            rate_buckets[fb_name]["sgst"] += sgst
            rate_buckets[fb_name]["igst"] += igst

    start_data_row = h_row + 1
    curr_row = h_row
    for r_label, r_vals in rate_buckets.items():
        curr_row += 1
        tot_tax = r_vals['cgst'] + r_vals['sgst'] + r_vals['igst']
        row_vals = [
            r_label,
            round(r_vals['taxable'], 2),
            round(r_vals['cgst'], 2),
            round(r_vals['sgst'], 2),
            round(r_vals['igst'], 2),
            round(tot_tax, 2)
        ]
        for ci, val in enumerate(row_vals, 1):
            c = ws6.cell(row=curr_row, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci > 1:
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            else:
                c.alignment = ALIGN_LEFT
        if curr_row % 2 == 0:
            for ci in range(1, 7):
                ws6.cell(row=curr_row, column=ci).fill = ZEBRA_FILL
        ws6.row_dimensions[curr_row].height = 19

    if curr_row >= start_data_row:
        tot_row = curr_row + 1
        ws6.cell(row=tot_row, column=1, value="TOTAL GST").font = FONT_TOTAL
        ws6.cell(row=tot_row, column=1).alignment = ALIGN_LEFT
        for col_idx in range(1, 7):
            c = ws6.cell(row=tot_row, column=col_idx)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if col_idx > 1:
                col_let = get_column_letter(col_idx)
                c.value = f"=SUM({col_let}{start_data_row}:{col_let}{curr_row})"
                c.font = FONT_TOTAL
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws6.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws6, h_row, min_col=1, max_col=6, landscape=False)
    _auto_fit_columns(ws6, min_col=1, max_col=6)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# ─────────────────────────────────────────────────────────────
# 2. CA / AUDIT GST WORKBOOK
# ─────────────────────────────────────────────────────────────
def generate_gst_ca_workbook(shop_info, month_str, drilldown_data, gstr1_summary=None, gstr3b_summary=None):
    """
    Builds the CA-ready GST Audit Workbook:
    - Summary Sheet: Audit comparison, Rate analysis, ITC & Net Tax Payable
    - 18% Outward Register
    - 5% Outward Register
    - Inward Supplies / ITC Register
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    shop_name = shop_info.get('name') or 'BillZen Business'
    period_label = f"Month: {month_str}" if month_str else "All Records"

    # ==================== SUMMARY SHEET ====================
    ws = wb.create_sheet(title="GST Summary & Reconciliation")
    _write_title_block(ws, shop_name, "GSTR-1 & 3B Accountant Audit Report", period_label, max_col=7)

    ws.cell(row=4, column=1, value="RATE-WISE OUTWARD LIABILITY").font = Font(name=FONT_NAME, size=11, bold=True, color='FFFFFF')
    ws.cell(row=4, column=1).fill = NAVY_HEADER_FILL
    ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=7)

    headers = ["Rate Category", "Taxable Value (₹)", "CGST (₹)", "SGST (₹)", "IGST (₹)", "Total Tax (₹)", "Invoice Count"]
    h_row = 5
    for ci, th in enumerate(headers, 1):
        cell = ws.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = SUBHEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if "₹" in th or "Count" in th else ALIGN_LEFT
    ws.row_dimensions[h_row].height = 20

    curr_row = h_row
    rates = drilldown_data.get('rates', {})
    for bucket_key in ['28', '18', '12', '5', '0']:
        curr_row += 1
        b_info = rates.get(bucket_key, {})
        label = b_info.get('rate_label', f"{bucket_key}% GST")
        taxable = float(b_info.get('sales_taxable', 0))
        cgst = float(b_info.get('cgst', 0))
        sgst = float(b_info.get('sgst', 0))
        igst = float(b_info.get('igst', 0))
        tot_tax = float(b_info.get('total_tax', 0))
        inv_cnt = len(b_info.get('invoices', []))

        vals = [label, taxable, cgst, sgst, igst, tot_tax, inv_cnt]
        for ci, val in enumerate(vals, 1):
            c = ws.cell(row=curr_row, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if 2 <= ci <= 6:
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci == 7:
                c.alignment = ALIGN_RIGHT
            else:
                c.alignment = ALIGN_LEFT
        ws.row_dimensions[curr_row].height = 19

    # Totals Row
    curr_row += 1
    ws.cell(row=curr_row, column=1, value="TOTAL OUTWARD GST").font = FONT_TOTAL
    for ci in range(1, 8):
        c = ws.cell(row=curr_row, column=ci)
        c.fill = TOTAL_FILL
        c.border = BORDER_TOTAL
        if 2 <= ci <= 6:
            col_let = get_column_letter(ci)
            c.value = f"=SUM({col_let}6:{col_let}{curr_row-1})"
            c.font = FONT_TOTAL
            c.number_format = FMT_CURRENCY
            c.alignment = ALIGN_RIGHT
    ws.row_dimensions[curr_row].height = 22

    _setup_sheet_view(ws, h_row, min_col=1, max_col=7, landscape=False)
    _auto_fit_columns(ws, min_col=1, max_col=7)

    # ==================== DRILLDOWN SHEETS (28%, 18%, 12%, 5%) ====================
    for bucket_key, sheet_title in [('28', '28% GST Register'), ('18', '18% GST Register'), ('12', '12% GST Register'), ('5', '5% GST Register')]:
        ws_reg = wb.create_sheet(title=sheet_title)
        _write_title_block(ws_reg, shop_name, f"GST {bucket_key}% Itemized Audit Register", period_label, max_col=12)

        reg_headers = [
            "Bill ID", "Date", "Party Name", "GSTIN", "Supply Type",
            "Item Name", "HSN/SAC", "Qty", "Rate", "Taxable Value", "Tax (CGST+SGST/IGST)", "Total Invoice"
        ]
        h_row = 4
        for ci, th in enumerate(reg_headers, 1):
            cell = ws_reg.cell(row=h_row, column=ci, value=th)
            cell.font = FONT_HEADER
            cell.fill = NAVY_HEADER_FILL
            cell.border = BORDER_HEADER
            cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Qty", "Rate", "Value", "Tax", "Total")) else ALIGN_LEFT
        ws_reg.row_dimensions[h_row].height = 22

        invoices = rates.get(bucket_key, {}).get('invoices', [])
        r_idx = h_row
        start_r = h_row + 1
        for inv in invoices:
            items = inv.get('items') or [{}]
            for it in items:
                r_idx += 1
                q = float(it.get('qty', 1))
                pr = float(it.get('price', 0))
                taxable = round(q * pr, 2)
                gst_pct = float(it.get('gst_pct', float(bucket_key)))
                is_inter = bool(inv.get('is_interstate'))
                tax = round(taxable * (gst_pct / 100), 2)
                line_tot = taxable + tax

                row_vals = [
                    inv.get('bill_id', ''),
                    _format_date(inv.get('date')),
                    inv.get('party', ''),
                    inv.get('gstin', ''),
                    'Inter-State' if is_inter else 'Intra-State',
                    it.get('product') or 'Item',
                    it.get('hsn_code') or '',
                    q,
                    pr,
                    taxable,
                    tax,
                    line_tot
                ]
                for ci, val in enumerate(row_vals, 1):
                    c = ws_reg.cell(row=r_idx, column=ci, value=val)
                    c.font = FONT_DATA
                    c.border = BORDER_CELL
                    if ci in (9, 10, 11, 12):
                        c.number_format = FMT_CURRENCY
                        c.alignment = ALIGN_RIGHT
                    elif ci == 8:
                        c.number_format = FMT_QTY
                        c.alignment = ALIGN_RIGHT
                    elif ci in (1, 2, 4, 7):
                        c.alignment = ALIGN_CENTER
                    else:
                        c.alignment = ALIGN_LEFT

                if r_idx % 2 == 0:
                    for ci in range(1, 13):
                        ws_reg.cell(row=r_idx, column=ci).fill = ZEBRA_FILL
                ws_reg.row_dimensions[r_idx].height = 19

        if r_idx >= start_r:
            tot_r = r_idx + 1
            ws_reg.cell(row=tot_r, column=1, value="TOTAL").font = FONT_TOTAL
            for ci in range(1, 13):
                c = ws_reg.cell(row=tot_r, column=ci)
                c.fill = TOTAL_FILL
                c.border = BORDER_TOTAL
                if ci in (8, 10, 11, 12):
                    col_let = get_column_letter(ci)
                    c.value = f"=SUM({col_let}{start_r}:{col_let}{r_idx})"
                    c.font = FONT_TOTAL
                    c.number_format = FMT_QTY if ci == 8 else FMT_CURRENCY
                    c.alignment = ALIGN_RIGHT
            ws_reg.row_dimensions[tot_r].height = 22

        _setup_sheet_view(ws_reg, h_row, min_col=1, max_col=12, landscape=True)
        _auto_fit_columns(ws_reg, min_col=1, max_col=12)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# ─────────────────────────────────────────────────────────────
# 3. SINGLE BILL EXCEL (SALES OR PURCHASE INVOICE)
# ─────────────────────────────────────────────────────────────
def generate_single_invoice_excel(shop_info, bill_type, bill_data, items_data):
    """
    Produces a professional single invoice Excel document:
    - Header: Shop name, GSTIN, Address, Phone
    - Party info: Name, GSTIN, Date, Invoice No
    - Items table: Product, HSN, Qty, Unit, Rate, GST %, Taxable, CGST, SGST, IGST, Total
    - Grand Totals
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tax Invoice" if bill_type == 'sales' else "Supplier Bill"

    shop_name = shop_info.get('name') or 'BillZen Enterprise'
    inv_title = "TAX INVOICE" if bill_data.get('bill_category') != 'kacha' else "ESTIMATE / CASH MEMO"
    if bill_type == 'purchase':
        inv_title = "PURCHASE BILL DETAIL"

    # Banner
    ws.merge_cells("A1:K1")
    ws["A1"] = f"{shop_name.upper()}"
    ws["A1"].font = FONT_TITLE
    ws["A1"].alignment = ALIGN_LEFT

    ws.merge_cells("A2:K2")
    addr_line = f"GSTIN: {shop_info.get('gstin', 'Unregistered')}  |  Address: {shop_info.get('address', '')}  |  Ph: {shop_info.get('phone', '')}"
    ws["A2"] = addr_line
    ws["A2"].font = FONT_SUBTITLE
    ws["A2"].alignment = ALIGN_LEFT

    ws.merge_cells("A3:K3")
    ws["A3"] = f"{inv_title}"
    ws["A3"].font = Font(name=FONT_NAME, size=13, bold=True, color='1E293B')
    ws["A3"].alignment = ALIGN_LEFT

    # Invoice / Party Block
    party_label = "Customer Details" if bill_type == 'sales' else "Supplier Details"
    party_name = bill_data.get('customer_name') or bill_data.get('supplier_name') or 'N/A'
    party_gstin = bill_data.get('customer_gstin') or bill_data.get('supplier_gstin') or 'Unregistered'
    inv_no = bill_data.get('bill_id') or bill_data.get('invoice_no') or bill_data.get('purchase_id') or ''
    inv_date = _format_date(bill_data.get('sale_date') or bill_data.get('bill_date'))
    is_inter = bool(bill_data.get('is_interstate') or (bill_data.get('igst_amount') or 0) > 0)

    ws["A5"] = party_label
    ws["A5"].font = Font(name=FONT_NAME, size=10, bold=True, color='475569')
    ws["A6"] = f"Name: {party_name}"
    ws["A7"] = f"GSTIN: {party_gstin}"
    ws["A8"] = f"Supply Type: {'Inter-State Supply' if is_inter else 'Intra-State Supply'}"

    ws["G5"] = "Invoice Metadata"
    ws["G5"].font = Font(name=FONT_NAME, size=10, bold=True, color='475569')
    ws["G6"] = f"Invoice / Bill No: {inv_no}"
    ws["G7"] = f"Date: {inv_date}"
    ws["G8"] = f"Payment Status: {bill_data.get('payment_status') or 'Paid'}"

    # Item Table
    h_row = 10
    headers = ["#", "Product Description", "HSN/SAC", "Qty", "Unit", "Rate", "GST %", "Taxable Value", "CGST", "SGST", "IGST", "Line Total"]
    for ci, th in enumerate(headers, 1):
        cell = ws.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Qty", "Rate", "GST", "Value", "CGST", "SGST", "IGST", "Total")) else ALIGN_LEFT
    ws.row_dimensions[h_row].height = 22

    start_r = h_row + 1
    curr_r = h_row
    is_kacha = (bill_data.get('bill_category') == 'kacha')
    for idx, it in enumerate(items_data, 1):
        curr_r += 1
        q = float(it.get('qty', 1))
        pr = float(it.get('price') or it.get('buy_price') or 0)
        taxable = float(it.get('taxable_amount') or it.get('taxable_val') or round(q * pr, 2))
        gst_pct = float(it.get('gst_pct') if it.get('gst_pct') is not None else 18)

        if is_kacha:
            cgst = 0.0
            sgst = 0.0
            igst = 0.0
        else:
            # 1. CGST
            if it.get('cgst_amount') is not None and float(it.get('cgst_amount') or 0) > 0:
                cgst = float(it['cgst_amount'])
            elif it.get('cgst_amt') is not None and float(it.get('cgst_amt') or 0) > 0:
                cgst = float(it['cgst_amt'])
            elif it.get('cgst_pct') is not None and float(it.get('cgst_pct') or 0) > 0:
                cgst = round(taxable * float(it['cgst_pct']) / 100, 2)
            elif not is_inter and gst_pct > 0:
                cgst = round(taxable * (gst_pct / 2) / 100, 2)
            else:
                cgst = 0.0

            # 2. SGST
            if it.get('sgst_amount') is not None and float(it.get('sgst_amount') or 0) > 0:
                sgst = float(it['sgst_amount'])
            elif it.get('sgst_amt') is not None and float(it.get('sgst_amt') or 0) > 0:
                sgst = float(it['sgst_amt'])
            elif it.get('sgst_pct') is not None and float(it.get('sgst_pct') or 0) > 0:
                sgst = round(taxable * float(it['sgst_pct']) / 100, 2)
            elif not is_inter and gst_pct > 0:
                sgst = round(taxable * (gst_pct / 2) / 100, 2)
            else:
                sgst = 0.0

            # 3. IGST
            if it.get('igst_amount') is not None and float(it.get('igst_amount') or 0) > 0:
                igst = float(it['igst_amount'])
            elif it.get('igst_amt') is not None and float(it.get('igst_amt') or 0) > 0:
                igst = float(it['igst_amt'])
            elif it.get('igst_pct') is not None and float(it.get('igst_pct') or 0) > 0:
                igst = round(taxable * float(it['igst_pct']) / 100, 2)
            elif is_inter and gst_pct > 0:
                igst = round(taxable * gst_pct / 100, 2)
            else:
                igst = 0.0

        line_tot = float(it.get('total_amount') or round(taxable + cgst + sgst + igst, 2))

        row_vals = [
            idx,
            it.get('product', 'Item'),
            it.get('hsn_code', ''),
            q,
            it.get('unit', 'Pcs'),
            pr,
            f"{gst_pct}%",
            taxable,
            cgst,
            sgst,
            igst,
            line_tot
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws.cell(row=curr_r, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (6, 8, 9, 10, 11, 12):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci == 4:
                c.number_format = FMT_QTY
                c.alignment = ALIGN_RIGHT
            elif ci in (1, 3, 5, 7):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_r % 2 == 0:
            for ci in range(1, 13):
                ws.cell(row=curr_r, column=ci).fill = ZEBRA_FILL
        ws.row_dimensions[curr_r].height = 19

    # Totals Row
    tot_row = curr_r + 1
    ws.cell(row=tot_row, column=2, value="SUBTOTAL / TOTALS").font = FONT_TOTAL
    for ci in range(1, 13):
        c = ws.cell(row=tot_row, column=ci)
        c.fill = TOTAL_FILL
        c.border = BORDER_TOTAL
        if ci in (4, 8, 9, 10, 11, 12):
            col_let = get_column_letter(ci)
            c.value = f"=SUM({col_let}{start_r}:{col_let}{curr_r})"
            c.font = FONT_TOTAL
            c.number_format = FMT_QTY if ci == 4 else FMT_CURRENCY
            c.alignment = ALIGN_RIGHT
    ws.row_dimensions[tot_row].height = 22

    # Grand Summary Card
    g_row = tot_row + 2
    ws.merge_cells(start_row=g_row, start_column=8, end_row=g_row, end_column=11)
    ws.cell(row=g_row, column=8, value="GRAND TOTAL").font = Font(name=FONT_NAME, size=12, bold=True, color='FFFFFF')
    ws.cell(row=g_row, column=8).fill = NAVY_HEADER_FILL
    gt_cell = ws.cell(row=g_row, column=12, value=float(bill_data.get('total_amount', 0)))
    gt_cell.font = Font(name=FONT_NAME, size=12, bold=True, color='FFFFFF')
    gt_cell.fill = NAVY_HEADER_FILL
    gt_cell.number_format = FMT_CURRENCY
    gt_cell.alignment = ALIGN_RIGHT
    ws.row_dimensions[g_row].height = 24

    ws.views.sheetView[0].showGridLines = True
    ws.freeze_panes = ws.cell(row=h_row + 1, column=1)
    ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = f'{h_row}:{h_row}'
    _auto_fit_columns(ws, min_col=1, max_col=12)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# ─────────────────────────────────────────────────────────────
# 4. CREDIT NOTES (SALES RETURNS) WORKBOOK
# ─────────────────────────────────────────────────────────────
def generate_credit_notes_workbook(shop_info, returns_records):
    """Generates professional Credit Notes / Sales Returns Register."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Credit Notes Register"

    shop_name = shop_info.get('name') or 'BillZen Enterprise'
    _write_title_block(ws, shop_name, "Credit Notes & Sales Returns Register", max_col=17)

    h_row = 4
    headers = [
        "Credit Note No", "Original Bill ID", "Original Sale Date", "Return Date",
        "Customer Name", "Customer GSTIN", "Product", "HSN/SAC", "Returned Qty", "Unit",
        "Rate", "Taxable Value", "CGST", "SGST", "IGST", "Credit Note Value",
        "Return Reason", "Settlement Mode"
    ]
    for ci, th in enumerate(headers, 1):
        cell = ws.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Qty", "Rate", "Value", "CGST", "SGST", "IGST")) else ALIGN_LEFT
    ws.row_dimensions[h_row].height = 22

    start_r = h_row + 1
    curr_r = h_row
    for r in returns_records:
        curr_r += 1
        q = float(r.get('qty', 0))
        pr = float(r.get('price', 0))
        taxable = float(r.get('taxable_amount', 0))
        cgst = float(r.get('cgst_amount', 0))
        sgst = float(r.get('sgst_amount', 0))
        igst = float(r.get('igst_amount', 0))
        tot = float(r.get('total_amount', 0))

        row_vals = [
            r.get('credit_note_no', ''),
            r.get('original_bill_id', ''),
            _format_date(r.get('original_sale_date')),
            _format_date(r.get('return_date')),
            r.get('customer_name') or 'Walk-in',
            r.get('customer_gstin') or '',
            r.get('product') or '',
            r.get('hsn_code') or '',
            q,
            r.get('unit') or 'Pcs',
            pr,
            taxable,
            cgst,
            sgst,
            igst,
            tot,
            r.get('reason') or 'Goods Return',
            r.get('refund_mode') or 'Credit Note'
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws.cell(row=curr_r, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (11, 12, 13, 14, 15, 16):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci == 9:
                c.number_format = FMT_QTY
                c.alignment = ALIGN_RIGHT
            elif ci in (1, 2, 3, 4, 6, 8, 10):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_r % 2 == 0:
            for ci in range(1, 19):
                ws.cell(row=curr_r, column=ci).fill = ZEBRA_FILL
        ws.row_dimensions[curr_r].height = 19

    if curr_r >= start_r:
        tot_row = curr_r + 1
        ws.cell(row=tot_row, column=1, value="TOTAL CREDIT NOTES").font = FONT_TOTAL
        for ci in range(1, 19):
            c = ws.cell(row=tot_row, column=ci)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if ci in (9, 12, 13, 14, 15, 16):
                col_let = get_column_letter(ci)
                c.value = f"=SUM({col_let}{start_r}:{col_let}{curr_r})"
                c.font = FONT_TOTAL
                c.number_format = FMT_QTY if ci == 9 else FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws, h_row, min_col=1, max_col=18, landscape=True)
    _auto_fit_columns(ws, min_col=1, max_col=18)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# ─────────────────────────────────────────────────────────────
# 5. DEBIT NOTES (PURCHASE RETURNS) WORKBOOK
# ─────────────────────────────────────────────────────────────
def generate_debit_notes_workbook(shop_info, returns_records):
    """Generates professional Debit Notes / Purchase Returns Register."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Debit Notes Register"

    shop_name = shop_info.get('name') or 'BillZen Enterprise'
    _write_title_block(ws, shop_name, "Debit Notes & Purchase Returns Register", max_col=17)

    h_row = 4
    headers = [
        "Debit Note No", "Original Invoice No", "Original Purchase Date", "Return Date",
        "Supplier Name", "Supplier GSTIN", "Product", "HSN/SAC", "Returned Qty", "Unit",
        "Rate", "Taxable Reversal", "CGST", "SGST", "IGST", "Debit Note Value",
        "Return Reason", "Settlement Mode"
    ]
    for ci, th in enumerate(headers, 1):
        cell = ws.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Qty", "Rate", "Reversal", "CGST", "SGST", "IGST", "Value")) else ALIGN_LEFT
    ws.row_dimensions[h_row].height = 22

    start_r = h_row + 1
    curr_r = h_row
    for r in returns_records:
        curr_r += 1
        q = float(r.get('qty', 0))
        pr = float(r.get('price', 0))
        taxable = float(r.get('taxable_amount', 0))
        cgst = float(r.get('cgst_amount', 0))
        sgst = float(r.get('sgst_amount', 0))
        igst = float(r.get('igst_amount', 0))
        tot = float(r.get('total_amount', 0))

        row_vals = [
            r.get('debit_note_no', ''),
            r.get('original_invoice_no', ''),
            _format_date(r.get('original_bill_date')),
            _format_date(r.get('return_date')),
            r.get('supplier_name') or '',
            r.get('supplier_gstin') or '',
            r.get('product') or '',
            r.get('hsn_code') or '',
            q,
            r.get('unit') or 'Pcs',
            pr,
            taxable,
            cgst,
            sgst,
            igst,
            tot,
            r.get('reason') or 'Defective Goods Return',
            r.get('refund_mode') or 'Debit Note'
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws.cell(row=curr_r, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (11, 12, 13, 14, 15, 16):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci == 9:
                c.number_format = FMT_QTY
                c.alignment = ALIGN_RIGHT
            elif ci in (1, 2, 3, 4, 6, 8, 10):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_r % 2 == 0:
            for ci in range(1, 19):
                ws.cell(row=curr_r, column=ci).fill = ZEBRA_FILL
        ws.row_dimensions[curr_r].height = 19

    if curr_r >= start_r:
        tot_row = curr_r + 1
        ws.cell(row=tot_row, column=1, value="TOTAL DEBIT NOTES").font = FONT_TOTAL
        for ci in range(1, 19):
            c = ws.cell(row=tot_row, column=ci)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if ci in (9, 12, 13, 14, 15, 16):
                col_let = get_column_letter(ci)
                c.value = f"=SUM({col_let}{start_r}:{col_let}{curr_r})"
                c.font = FONT_TOTAL
                c.number_format = FMT_QTY if ci == 9 else FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws, h_row, min_col=1, max_col=18, landscape=True)
    _auto_fit_columns(ws, min_col=1, max_col=18)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# ─────────────────────────────────────────────────────────────
# 6. SUPPLIER BILLS WORKBOOK (PART 6)
# ─────────────────────────────────────────────────────────────
def generate_supplier_bills_workbook(shop_info, supplier_info, purchases_data):
    """Generates professional register of all purchase bills under a specific supplier."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Supplier Invoices"

    shop_name = shop_info.get('name') or 'BillZen Enterprise'
    sup_name = supplier_info.get('name', 'Supplier')
    _write_title_block(ws, shop_name, f"Supplier Ledger — {sup_name}", max_col=9)

    ws["A4"] = f"GSTIN: {supplier_info.get('gstin', 'Unregistered')}   |   Phone: {supplier_info.get('phone', '')}   |   Address: {supplier_info.get('address', '')}"
    ws["A4"].font = FONT_SUBTITLE

    h_row = 6
    headers = ["Purchase ID", "Invoice No", "Date", "Taxable Value", "CGST", "SGST", "IGST", "Grand Total", "Status"]
    for ci, th in enumerate(headers, 1):
        cell = ws.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Value", "CGST", "SGST", "IGST", "Total")) else ALIGN_LEFT
    ws.row_dimensions[h_row].height = 22

    start_r = h_row + 1
    curr_r = h_row
    for p in purchases_data:
        curr_r += 1
        taxable = float(p.get('subtotal') or 0)
        cgst = float(p.get('cgst_amount') or 0)
        sgst = float(p.get('sgst_amount') or 0)
        igst = float(p.get('igst_amount') or 0)
        tot = float(p.get('total_amount') or 0)

        row_vals = [
            p.get('purchase_id') or f"PUR-{p.get('id')}",
            p.get('invoice_no') or '-',
            _format_date(p.get('bill_date')),
            taxable,
            cgst,
            sgst,
            igst,
            tot,
            p.get('payment_status') or 'Paid'
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws.cell(row=curr_r, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (4, 5, 6, 7, 8):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci in (1, 2, 3, 9):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_r % 2 == 0:
            for ci in range(1, 10):
                ws.cell(row=curr_r, column=ci).fill = ZEBRA_FILL
        ws.row_dimensions[curr_r].height = 19

    if curr_r >= start_r:
        tot_row = curr_r + 1
        ws.cell(row=tot_row, column=1, value="TOTAL PURCHASED").font = FONT_TOTAL
        for ci in range(1, 10):
            c = ws.cell(row=tot_row, column=ci)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if ci in (4, 5, 6, 7, 8):
                col_let = get_column_letter(ci)
                c.value = f"=SUM({col_let}{start_r}:{col_let}{curr_r})"
                c.font = FONT_TOTAL
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws, h_row, min_col=1, max_col=9, landscape=False)
    _auto_fit_columns(ws, min_col=1, max_col=9)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# ─────────────────────────────────────────────────────────────
# 7. STANDALONE SALES REGISTER WORKBOOK (WOW STANDARD)
# ─────────────────────────────────────────────────────────────
def generate_sales_register_workbook(shop_info, sales_data, sales_items_data=None):
    """
    Produces a dedicated, audit-ready Sales Register workbook:
    - Sheet 1: Sales Register (Invoices with tax breakdown, payment status, totals)
    - Sheet 2: Itemized Line Items (Optional / if item data provided)
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    shop_name = shop_info.get('name') or 'BillZen Enterprise'
    gen_time = datetime.now().strftime('%d-%m-%Y %I:%M %p')

    # ==================== SHEET 1: SALES REGISTER ====================
    ws1 = wb.create_sheet(title="Sales Register")
    _write_title_block(ws1, shop_name, "Comprehensive Sales Register & Outward Supplies", "All Records", max_col=16)

    # KPI Summary Cards at the top
    tot_sales_val = sum(float(s.get('total_amount') or 0) for s in sales_data if not s.get('cancelled'))
    tot_taxable_val = sum(float(s.get('subtotal') or 0) for s in sales_data if not s.get('cancelled'))
    tot_paid_val = sum(float(s.get('paid_amount') or 0) for s in sales_data if not s.get('cancelled'))
    tot_pend_val = max(0.0, tot_sales_val - tot_paid_val)

    summary_labels = ["Total Invoices", "Taxable Value (₹)", "Total Sales (₹)", "Total Collected (₹)", "Outstanding (₹)"]
    summary_vals = [len([s for s in sales_data if not s.get('cancelled')]), tot_taxable_val, tot_sales_val, tot_paid_val, tot_pend_val]

    for ci, (lbl, val) in enumerate(zip(summary_labels, summary_vals), 1):
        c_lbl = ws1.cell(row=4, column=ci * 2 - 1, value=lbl)
        c_lbl.font = Font(name=FONT_NAME, size=9, bold=True, color='475569')
        c_lbl.alignment = ALIGN_LEFT
        c_val = ws1.cell(row=5, column=ci * 2 - 1, value=val if ci > 1 else int(val))
        c_val.font = Font(name=FONT_NAME, size=12, bold=True, color='0F172A')
        if ci > 1:
            c_val.number_format = FMT_CURRENCY
        c_val.alignment = ALIGN_LEFT

    h_row = 7
    sales_headers = [
        "#", "Bill ID", "Invoice No", "Date", "Customer Name", "Customer GSTIN", "Supply Type",
        "Taxable Value", "CGST", "SGST", "IGST", "Invoice Total", "Paid", "Pending", "Payment Mode", "Status"
    ]
    for ci, th in enumerate(sales_headers, 1):
        cell = ws1.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Value", "CGST", "SGST", "IGST", "Total", "Paid", "Pending")) else ALIGN_LEFT
    ws1.row_dimensions[h_row].height = 22

    start_data_row = h_row + 1
    curr_row = h_row
    for idx, s in enumerate(sales_data, 1):
        curr_row += 1
        is_canc = bool(s.get('cancelled'))
        is_inter = bool(s.get('is_interstate') or (s.get('igst_amount') or 0) > 0)
        inv_no = s.get('invoice_no') or s.get('bill_id') or ''
        taxable = float(s.get('subtotal') or 0)
        cgst = float(s.get('cgst_amount') or 0)
        sgst = float(s.get('sgst_amount') or 0)
        igst = float(s.get('igst_amount') or 0)
        total = float(s.get('total_amount') or 0)
        paid = float(s.get('paid_amount') or 0)
        pending = max(0.0, total - paid)
        status_val = "Cancelled" if is_canc else (s.get('payment_status') or 'Paid')

        row_vals = [
            idx,
            s.get('bill_id', ''),
            inv_no,
            _format_date(s.get('sale_date')),
            s.get('customer_name') or 'Walk-in Customer',
            s.get('customer_gstin') or '',
            'Inter-State' if is_inter else 'Intra-State',
            round(taxable, 2),
            round(cgst, 2),
            round(sgst, 2),
            round(igst, 2),
            round(total, 2),
            round(paid, 2),
            round(pending, 2),
            s.get('payment_mode') or 'Cash',
            status_val
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws1.cell(row=curr_row, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (8, 9, 10, 11, 12, 13, 14):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci in (1, 2, 3, 4, 6, 15, 16):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_row % 2 == 0:
            for ci in range(1, 17):
                ws1.cell(row=curr_row, column=ci).fill = ZEBRA_FILL
        ws1.row_dimensions[curr_row].height = 19

    if curr_row >= start_data_row:
        tot_row = curr_row + 1
        ws1.cell(row=tot_row, column=1, value="TOTAL SALES").font = FONT_TOTAL
        ws1.cell(row=tot_row, column=1).alignment = ALIGN_LEFT
        for col_idx in range(1, 17):
            c = ws1.cell(row=tot_row, column=col_idx)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if col_idx in (8, 9, 10, 11, 12, 13, 14):
                col_let = get_column_letter(col_idx)
                c.value = f"=SUM({col_let}{start_data_row}:{col_let}{curr_row})"
                c.font = FONT_TOTAL
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws1.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws1, h_row, min_col=1, max_col=16, landscape=True)
    _auto_fit_columns(ws1, min_col=1, max_col=16)

    # ==================== SHEET 2: ITEMIZED LINE ITEMS (IF PRESENT) ====================
    if sales_items_data:
        ws2 = wb.create_sheet(title="Itemized Line Items")
        _write_title_block(ws2, shop_name, "Sales Invoice Line Items Drilldown", "All Records", max_col=16)
        h_row2 = 4
        it_headers = [
            "#", "Bill ID", "Invoice No", "Date", "Customer Name", "Product Description", "HSN/SAC",
            "Qty", "Unit", "Rate", "Taxable Value", "GST %", "CGST", "SGST", "IGST", "Line Total"
        ]
        for ci, th in enumerate(it_headers, 1):
            cell = ws2.cell(row=h_row2, column=ci, value=th)
            cell.font = FONT_HEADER
            cell.fill = NAVY_HEADER_FILL
            cell.border = BORDER_HEADER
            cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Qty", "Rate", "Value", "CGST", "SGST", "IGST", "Total")) else ALIGN_LEFT
        ws2.row_dimensions[h_row2].height = 22

        start_r2 = h_row2 + 1
        curr_r2 = h_row2
        for idx, it in enumerate(sales_items_data, 1):
            curr_r2 += 1
            q = float(it.get('qty', 1))
            pr = float(it.get('price', 0))
            if it.get('taxable_val') is not None and float(it.get('taxable_val') or 0) > 0:
                taxable = float(it['taxable_val'])
            elif it.get('taxable_amount') is not None and float(it.get('taxable_amount') or 0) > 0:
                taxable = float(it['taxable_amount'])
            else:
                taxable = round(q * pr, 2)

            gst_pct = float(it.get('gst_pct') if it.get('gst_pct') is not None else 0)
            is_kacha = (it.get('bill_category') == 'kacha')
            is_inter = bool(it.get('is_interstate')) or float(it.get('igst_amt') or it.get('igst_amount') or it.get('igst_pct') or 0) > 0

            if is_kacha:
                cgst = 0.0
                sgst = 0.0
                igst = 0.0
            else:
                # 1. CGST
                if it.get('cgst_amount') is not None and float(it.get('cgst_amount') or 0) > 0:
                    cgst = float(it['cgst_amount'])
                elif it.get('cgst_amt') is not None and float(it.get('cgst_amt') or 0) > 0:
                    cgst = float(it['cgst_amt'])
                elif it.get('cgst_pct') is not None and float(it.get('cgst_pct') or 0) > 0:
                    cgst = round(taxable * float(it['cgst_pct']) / 100, 2)
                elif not is_inter and gst_pct > 0:
                    cgst = round(taxable * (gst_pct / 2) / 100, 2)
                else:
                    cgst = 0.0

                # 2. SGST
                if it.get('sgst_amount') is not None and float(it.get('sgst_amount') or 0) > 0:
                    sgst = float(it['sgst_amount'])
                elif it.get('sgst_amt') is not None and float(it.get('sgst_amt') or 0) > 0:
                    sgst = float(it['sgst_amt'])
                elif it.get('sgst_pct') is not None and float(it.get('sgst_pct') or 0) > 0:
                    sgst = round(taxable * float(it['sgst_pct']) / 100, 2)
                elif not is_inter and gst_pct > 0:
                    sgst = round(taxable * (gst_pct / 2) / 100, 2)
                else:
                    sgst = 0.0

                # 3. IGST
                if it.get('igst_amount') is not None and float(it.get('igst_amount') or 0) > 0:
                    igst = float(it['igst_amount'])
                elif it.get('igst_amt') is not None and float(it.get('igst_amt') or 0) > 0:
                    igst = float(it['igst_amt'])
                elif it.get('igst_pct') is not None and float(it.get('igst_pct') or 0) > 0:
                    igst = round(taxable * float(it['igst_pct']) / 100, 2)
                elif is_inter and gst_pct > 0:
                    igst = round(taxable * gst_pct / 100, 2)
                else:
                    igst = 0.0

            line_tot = float(it.get('total_amount') or round(taxable + cgst + sgst + igst, 2))

            row_vals = [
                idx,
                it.get('bill_id', ''),
                it.get('invoice_no') or it.get('bill_id', ''),
                _format_date(it.get('sale_date')),
                it.get('customer_name') or 'Walk-in',
                it.get('product') or 'Item',
                it.get('hsn_code', ''),
                q,
                it.get('unit', 'Pcs'),
                pr,
                taxable,
                f"{gst_pct:g}%",
                cgst,
                sgst,
                igst,
                line_tot
            ]
            for ci, val in enumerate(row_vals, 1):
                c = ws2.cell(row=curr_r2, column=ci, value=val)
                c.font = FONT_DATA
                c.border = BORDER_CELL
                if ci in (10, 11, 13, 14, 15, 16):
                    c.number_format = FMT_CURRENCY
                    c.alignment = ALIGN_RIGHT
                elif ci == 8:
                    c.number_format = FMT_QTY
                    c.alignment = ALIGN_RIGHT
                elif ci in (1, 2, 3, 4, 7, 9, 12):
                    c.alignment = ALIGN_CENTER
                else:
                    c.alignment = ALIGN_LEFT

            if curr_r2 % 2 == 0:
                for ci in range(1, 17):
                    ws2.cell(row=curr_r2, column=ci).fill = ZEBRA_FILL
            ws2.row_dimensions[curr_r2].height = 19

        if curr_r2 >= start_r2:
            tot_row2 = curr_r2 + 1
            ws2.cell(row=tot_row2, column=1, value="TOTAL ITEM SALES").font = FONT_TOTAL
            for col_idx in range(1, 17):
                c = ws2.cell(row=tot_row2, column=col_idx)
                c.fill = TOTAL_FILL
                c.border = BORDER_TOTAL
                if col_idx in (8, 11, 13, 14, 15, 16):
                    col_let = get_column_letter(col_idx)
                    c.value = f"=SUM({col_let}{start_r2}:{col_let}{curr_r2})"
                    c.font = FONT_TOTAL
                    c.number_format = FMT_QTY if col_idx == 8 else FMT_CURRENCY
                    c.alignment = ALIGN_RIGHT
            ws2.row_dimensions[tot_row2].height = 22

        _setup_sheet_view(ws2, h_row2, min_col=1, max_col=16, landscape=True)
        _auto_fit_columns(ws2, min_col=1, max_col=16)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# ─────────────────────────────────────────────────────────────
# 8. STANDALONE PURCHASE REGISTER WORKBOOK (WOW STANDARD)
# ─────────────────────────────────────────────────────────────
def generate_purchase_register_workbook(shop_info, purchases_data, purchase_items_data=None):
    """
    Produces a dedicated, audit-ready Purchase Register workbook:
    - Sheet 1: Purchase Register (Inward supplies with tax breakdown and totals)
    - Sheet 2: Itemized Inward Supplies (Optional / if item data provided)
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    shop_name = shop_info.get('name') or 'BillZen Enterprise'

    # ==================== SHEET 1: PURCHASE REGISTER ====================
    ws1 = wb.create_sheet(title="Purchase Register")
    _write_title_block(ws1, shop_name, "Inward Supplies & Purchase Register", "All Records", max_col=13)

    tot_pur_val = sum(float(p.get('total_amount') or 0) for p in purchases_data)
    tot_taxable_val = sum(float(p.get('subtotal') or 0) for p in purchases_data)
    tot_cgst_val = sum(float(p.get('cgst_amount') or 0) for p in purchases_data)
    tot_sgst_val = sum(float(p.get('sgst_amount') or 0) for p in purchases_data)
    tot_igst_val = sum(float(p.get('igst_amount') or 0) for p in purchases_data)

    summary_labels = ["Total Bills", "Taxable Value (₹)", "Input CGST (₹)", "Input SGST (₹)", "Input IGST (₹)", "Total Purchases (₹)"]
    summary_vals = [len(purchases_data), tot_taxable_val, tot_cgst_val, tot_sgst_val, tot_igst_val, tot_pur_val]

    for ci, (lbl, val) in enumerate(zip(summary_labels, summary_vals), 1):
        c_lbl = ws1.cell(row=4, column=ci * 2 - 1, value=lbl)
        c_lbl.font = Font(name=FONT_NAME, size=9, bold=True, color='475569')
        c_val = ws1.cell(row=5, column=ci * 2 - 1, value=val if ci > 1 else int(val))
        c_val.font = Font(name=FONT_NAME, size=12, bold=True, color='0F172A')
        if ci > 1:
            c_val.number_format = FMT_CURRENCY

    h_row = 7
    pur_headers = [
        "#", "Purchase ID", "Supplier Invoice No", "Bill Date", "Supplier Name", "Supplier GSTIN",
        "Supply Type", "Taxable Value", "Input CGST", "Input SGST", "Input IGST", "Purchase Total", "Payment Status"
    ]
    for ci, th in enumerate(pur_headers, 1):
        cell = ws1.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Value", "CGST", "SGST", "IGST", "Total")) else ALIGN_LEFT
    ws1.row_dimensions[h_row].height = 22

    start_data_row = h_row + 1
    curr_row = h_row
    for idx, p in enumerate(purchases_data, 1):
        curr_row += 1
        is_inter = bool(p.get('is_interstate') or (p.get('igst_amount') or 0) > 0)
        taxable = float(p.get('subtotal') or 0)
        cgst = float(p.get('cgst_amount') or 0)
        sgst = float(p.get('sgst_amount') or 0)
        igst = float(p.get('igst_amount') or 0)
        total = float(p.get('total_amount') or 0)

        row_vals = [
            idx,
            p.get('purchase_id') or f"PUR-{p.get('id')}",
            p.get('invoice_no') or '',
            _format_date(p.get('bill_date')),
            p.get('supplier_name') or '',
            p.get('supplier_gstin') or '',
            'Inter-State' if is_inter else 'Intra-State',
            round(taxable, 2),
            round(cgst, 2),
            round(sgst, 2),
            round(igst, 2),
            round(total, 2),
            p.get('payment_status') or 'Paid'
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws1.cell(row=curr_row, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (8, 9, 10, 11, 12):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci in (1, 2, 3, 4, 6, 13):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_row % 2 == 0:
            for ci in range(1, 14):
                ws1.cell(row=curr_row, column=ci).fill = ZEBRA_FILL
        ws1.row_dimensions[curr_row].height = 19

    if curr_row >= start_data_row:
        tot_row = curr_row + 1
        ws1.cell(row=tot_row, column=1, value="TOTAL PURCHASE").font = FONT_TOTAL
        ws1.cell(row=tot_row, column=1).alignment = ALIGN_LEFT
        for col_idx in range(1, 14):
            c = ws1.cell(row=tot_row, column=col_idx)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if col_idx in (8, 9, 10, 11, 12):
                col_let = get_column_letter(col_idx)
                c.value = f"=SUM({col_let}{start_data_row}:{col_let}{curr_row})"
                c.font = FONT_TOTAL
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws1.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws1, h_row, min_col=1, max_col=13, landscape=True)
    _auto_fit_columns(ws1, min_col=1, max_col=13)

    # ==================== SHEET 2: ITEMIZED INWARD SUPPLIES ====================
    if purchase_items_data:
        ws2 = wb.create_sheet(title="Itemized Inward Supplies")
        _write_title_block(ws2, shop_name, "Purchase Bills Line Items Drilldown", "All Records", max_col=16)
        h_row2 = 4
        it_headers = [
            "#", "Purchase ID", "Supplier Invoice No", "Bill Date", "Supplier Name", "Product Description", "HSN/SAC",
            "Qty", "Unit", "Rate", "Taxable Value", "GST %", "CGST", "SGST", "IGST", "Line Total"
        ]
        for ci, th in enumerate(it_headers, 1):
            cell = ws2.cell(row=h_row2, column=ci, value=th)
            cell.font = FONT_HEADER
            cell.fill = NAVY_HEADER_FILL
            cell.border = BORDER_HEADER
            cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Qty", "Rate", "Value", "CGST", "SGST", "IGST", "Total")) else ALIGN_LEFT
        ws2.row_dimensions[h_row2].height = 22

        start_r2 = h_row2 + 1
        curr_r2 = h_row2
        for idx, it in enumerate(purchase_items_data, 1):
            curr_r2 += 1
            q = float(it.get('qty', 1))
            pr = float(it.get('buy_price') or it.get('price', 0))
            if it.get('taxable_val') is not None and float(it.get('taxable_val') or 0) > 0:
                taxable = float(it['taxable_val'])
            elif it.get('taxable_amount') is not None and float(it.get('taxable_amount') or 0) > 0:
                taxable = float(it['taxable_amount'])
            else:
                taxable = round(q * pr, 2)

            gst_pct = float(it.get('gst_pct') if it.get('gst_pct') is not None else 0)
            is_inter = bool(it.get('is_interstate')) or float(it.get('igst_amt') or it.get('igst_amount') or it.get('igst_pct') or 0) > 0

            # 1. CGST
            if it.get('cgst_amt') is not None and float(it.get('cgst_amt') or 0) > 0:
                cgst = float(it['cgst_amt'])
            elif it.get('cgst_amount') is not None and float(it.get('cgst_amount') or 0) > 0:
                cgst = float(it['cgst_amount'])
            elif it.get('cgst_pct') is not None and float(it.get('cgst_pct') or 0) > 0:
                cgst = round(taxable * float(it['cgst_pct']) / 100, 2)
            elif not is_inter and gst_pct > 0:
                cgst = round(taxable * (gst_pct / 2) / 100, 2)
            else:
                cgst = 0.0

            # 2. SGST
            if it.get('sgst_amt') is not None and float(it.get('sgst_amt') or 0) > 0:
                sgst = float(it['sgst_amt'])
            elif it.get('sgst_amount') is not None and float(it.get('sgst_amount') or 0) > 0:
                sgst = float(it['sgst_amount'])
            elif it.get('sgst_pct') is not None and float(it.get('sgst_pct') or 0) > 0:
                sgst = round(taxable * float(it['sgst_pct']) / 100, 2)
            elif not is_inter and gst_pct > 0:
                sgst = round(taxable * (gst_pct / 2) / 100, 2)
            else:
                sgst = 0.0

            # 3. IGST
            if it.get('igst_amt') is not None and float(it.get('igst_amt') or 0) > 0:
                igst = float(it['igst_amt'])
            elif it.get('igst_amount') is not None and float(it.get('igst_amount') or 0) > 0:
                igst = float(it['igst_amount'])
            elif it.get('igst_pct') is not None and float(it.get('igst_pct') or 0) > 0:
                igst = round(taxable * float(it['igst_pct']) / 100, 2)
            elif is_inter and gst_pct > 0:
                igst = round(taxable * gst_pct / 100, 2)
            else:
                igst = 0.0

            if it.get('amount') is not None and float(it.get('amount') or 0) > 0:
                line_tot = float(it['amount'])
            elif it.get('total_amount') is not None and float(it.get('total_amount') or 0) > 0:
                line_tot = float(it['total_amount'])
            else:
                line_tot = round(taxable + cgst + sgst + igst, 2)

            row_vals = [
                idx,
                it.get('purchase_id', ''),
                it.get('invoice_no') or '',
                _format_date(it.get('bill_date')),
                it.get('supplier_name') or '',
                it.get('product') or 'Item',
                it.get('hsn_code', ''),
                q,
                it.get('unit', 'Pcs'),
                pr,
                taxable,
                f"{gst_pct:g}%",
                cgst,
                sgst,
                igst,
                line_tot
            ]
            for ci, val in enumerate(row_vals, 1):
                c = ws2.cell(row=curr_r2, column=ci, value=val)
                c.font = FONT_DATA
                c.border = BORDER_CELL
                if ci in (10, 11, 13, 14, 15, 16):
                    c.number_format = FMT_CURRENCY
                    c.alignment = ALIGN_RIGHT
                elif ci == 8:
                    c.number_format = FMT_QTY
                    c.alignment = ALIGN_RIGHT
                elif ci in (1, 2, 3, 4, 7, 9, 12):
                    c.alignment = ALIGN_CENTER
                else:
                    c.alignment = ALIGN_LEFT

            if curr_r2 % 2 == 0:
                for ci in range(1, 17):
                    ws2.cell(row=curr_r2, column=ci).fill = ZEBRA_FILL
            ws2.row_dimensions[curr_r2].height = 19

        if curr_r2 >= start_r2:
            tot_row2 = curr_r2 + 1
            ws2.cell(row=tot_row2, column=1, value="TOTAL ITEM PURCHASES").font = FONT_TOTAL
            for col_idx in range(1, 17):
                c = ws2.cell(row=tot_row2, column=col_idx)
                c.fill = TOTAL_FILL
                c.border = BORDER_TOTAL
                if col_idx in (8, 11, 13, 14, 15, 16):
                    col_let = get_column_letter(col_idx)
                    c.value = f"=SUM({col_let}{start_r2}:{col_let}{curr_r2})"
                    c.font = FONT_TOTAL
                    c.number_format = FMT_QTY if col_idx == 8 else FMT_CURRENCY
                    c.alignment = ALIGN_RIGHT
            ws2.row_dimensions[tot_row2].height = 22

        _setup_sheet_view(ws2, h_row2, min_col=1, max_col=16, landscape=True)
        _auto_fit_columns(ws2, min_col=1, max_col=16)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# ─────────────────────────────────────────────────────────────
# 9. STANDALONE STOCK VALUATION & INVENTORY AUDIT (WOW STANDARD)
# ─────────────────────────────────────────────────────────────
def generate_stock_inventory_workbook(shop_info, products_data):
    """
    Produces a dedicated, audit-grade Stock Valuation & Inventory Audit workbook.
    Includes inventory cost basis, potential sales revenue, profit margins, and stock alert levels.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stock Valuation & Inventory"

    shop_name = shop_info.get('name') or 'BillZen Enterprise'
    _write_title_block(ws, shop_name, "Inventory Valuation & Stock Status Audit", "Current Position", max_col=14)

    # Valuation Metrics
    tot_skus = len(products_data)
    tot_units = sum(float(p.get('stock') or 0) for p in products_data)
    inv_cost = sum(float(p.get('stock') or 0) * float(p.get('buy_price') or 0) for p in products_data)
    inv_sell = sum(float(p.get('stock') or 0) * float(p.get('sell_price') or 0) for p in products_data)
    inv_profit = inv_sell - inv_cost

    summary_labels = ["Total SKUs", "Total Units in Stock", "Inventory Cost (₹)", "Potential Sales (₹)", "Potential Profit (₹)"]
    summary_vals = [tot_skus, tot_units, inv_cost, inv_sell, inv_profit]

    for ci, (lbl, val) in enumerate(zip(summary_labels, summary_vals), 1):
        c_lbl = ws.cell(row=4, column=ci * 2 - 1, value=lbl)
        c_lbl.font = Font(name=FONT_NAME, size=9, bold=True, color='475569')
        c_val = ws.cell(row=5, column=ci * 2 - 1, value=val if ci > 2 else (int(val) if ci == 1 else round(val, 2)))
        c_val.font = Font(name=FONT_NAME, size=12, bold=True, color='0F172A')
        if ci in (3, 4, 5):
            c_val.number_format = FMT_CURRENCY
        elif ci == 2:
            c_val.number_format = FMT_QTY

    h_row = 7
    headers = [
        "#", "Product Name", "HSN/SAC", "Category", "Unit", "Current Stock",
        "Cost Price (₹)", "Inventory Cost (₹)", "Selling Price (₹)", "Potential Sales (₹)",
        "Potential Profit (₹)", "GST %", "Stock Status"
    ]
    for ci, th in enumerate(headers, 1):
        cell = ws.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Stock", "Price", "Cost", "Sales", "Profit", "%")) else ALIGN_LEFT
    ws.row_dimensions[h_row].height = 22

    start_data_row = h_row + 1
    curr_row = h_row
    for idx, pr in enumerate(products_data, 1):
        curr_row += 1
        stk = float(pr.get('stock') or 0)
        buy = float(pr.get('buy_price') or 0)
        sell = float(pr.get('sell_price') or 0)
        inv_val = stk * buy
        sale_val = stk * sell
        pot_prof = sale_val - inv_val

        status_text = "Out of Stock" if stk <= 0 else ("Low Stock" if stk < 10 else "In Stock")

        row_vals = [
            idx,
            pr.get('name', ''),
            pr.get('hsn_code') or '',
            pr.get('category') or 'General',
            pr.get('unit') or 'Pcs',
            round(stk, 2),
            round(buy, 2),
            round(inv_val, 2),
            round(sell, 2),
            round(sale_val, 2),
            round(pot_prof, 2),
            f"{pr.get('gst_pct', 18)}%",
            status_text
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws.cell(row=curr_row, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (7, 8, 9, 10, 11):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci == 6:
                c.number_format = FMT_QTY
                c.alignment = ALIGN_RIGHT
            elif ci in (1, 3, 4, 5, 12, 13):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_row % 2 == 0:
            for ci in range(1, 14):
                ws.cell(row=curr_row, column=ci).fill = ZEBRA_FILL
        ws.row_dimensions[curr_row].height = 19

    if curr_row >= start_data_row:
        tot_row = curr_row + 1
        ws.cell(row=tot_row, column=2, value="TOTAL INVENTORY VALUATION").font = FONT_TOTAL
        for col_idx in range(1, 14):
            c = ws.cell(row=tot_row, column=col_idx)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if col_idx in (6, 8, 10, 11):
                col_let = get_column_letter(col_idx)
                c.value = f"=SUM({col_let}{start_data_row}:{col_let}{curr_row})"
                c.font = FONT_TOTAL
                c.number_format = FMT_QTY if col_idx == 6 else FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws, h_row, min_col=1, max_col=13, landscape=True)
    _auto_fit_columns(ws, min_col=1, max_col=13)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


# ─────────────────────────────────────────────────────────────
# 10. STANDALONE CUSTOMER LEDGER STATEMENT (WOW STANDARD)
# ─────────────────────────────────────────────────────────────
def generate_customer_ledger_workbook(shop_info, customer_info, sales_data, payments_data=None):
    """
    Produces a dedicated, customer-facing Statement of Account & Ledger.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Customer Statement"

    shop_name = shop_info.get('name') or 'BillZen Enterprise'
    cust_name = customer_info.get('name') or 'Customer'
    _write_title_block(ws, shop_name, f"Customer Statement of Account — {cust_name}", "All Records", max_col=14)

    # Customer Profile block
    ws["A4"] = f"GSTIN: {customer_info.get('gstin', 'Unregistered')}   |   Phone: {customer_info.get('phone', '')}   |   Address: {customer_info.get('address', '')}"
    ws["A4"].font = FONT_SUBTITLE

    tot_billed = sum(float(s.get('total_amount') or 0) for s in sales_data if not s.get('cancelled'))
    tot_paid = sum(float(s.get('paid_amount') or 0) for s in sales_data if not s.get('cancelled'))
    balance = max(0.0, tot_billed - tot_paid)

    summary_labels = ["Total Invoices", "Total Billed (₹)", "Total Paid (₹)", "Balance Due (₹)"]
    summary_vals = [len([s for s in sales_data if not s.get('cancelled')]), tot_billed, tot_paid, balance]

    for ci, (lbl, val) in enumerate(zip(summary_labels, summary_vals), 1):
        c_lbl = ws.cell(row=5, column=ci * 2 - 1, value=lbl)
        c_lbl.font = Font(name=FONT_NAME, size=9, bold=True, color='475569')
        c_val = ws.cell(row=6, column=ci * 2 - 1, value=val if ci > 1 else int(val))
        c_val.font = Font(name=FONT_NAME, size=12, bold=True, color='0F172A')
        if ci > 1:
            c_val.number_format = FMT_CURRENCY

    h_row = 8
    headers = [
        "#", "Bill ID", "Invoice No", "Date", "Supply Type", "Taxable Value",
        "CGST", "SGST", "IGST", "Invoice Total", "Paid Amount", "Pending Balance", "Payment Mode", "Status"
    ]
    for ci, th in enumerate(headers, 1):
        cell = ws.cell(row=h_row, column=ci, value=th)
        cell.font = FONT_HEADER
        cell.fill = NAVY_HEADER_FILL
        cell.border = BORDER_HEADER
        cell.alignment = ALIGN_RIGHT if any(k in th for k in ("Value", "CGST", "SGST", "IGST", "Total", "Paid", "Pending", "Balance")) else ALIGN_LEFT
    ws.row_dimensions[h_row].height = 22

    start_data_row = h_row + 1
    curr_row = h_row
    for idx, s in enumerate(sales_data, 1):
        curr_row += 1
        is_inter = bool(s.get('is_interstate') or (s.get('igst_amount') or 0) > 0)
        taxable = float(s.get('subtotal') or 0)
        cgst = float(s.get('cgst_amount') or 0)
        sgst = float(s.get('sgst_amount') or 0)
        igst = float(s.get('igst_amount') or 0)
        total = float(s.get('total_amount') or 0)
        paid = float(s.get('paid_amount') or 0)
        pending = max(0.0, total - paid)

        row_vals = [
            idx,
            s.get('bill_id', ''),
            s.get('invoice_no') or s.get('bill_id', ''),
            _format_date(s.get('sale_date')),
            'Inter-State' if is_inter else 'Intra-State',
            round(taxable, 2),
            round(cgst, 2),
            round(sgst, 2),
            round(igst, 2),
            round(total, 2),
            round(paid, 2),
            round(pending, 2),
            s.get('payment_mode') or 'Cash',
            s.get('payment_status') or 'Paid'
        ]

        for ci, val in enumerate(row_vals, 1):
            c = ws.cell(row=curr_row, column=ci, value=val)
            c.font = FONT_DATA
            c.border = BORDER_CELL
            if ci in (6, 7, 8, 9, 10, 11, 12):
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
            elif ci in (1, 2, 3, 4, 13, 14):
                c.alignment = ALIGN_CENTER
            else:
                c.alignment = ALIGN_LEFT

        if curr_row % 2 == 0:
            for ci in range(1, 15):
                ws.cell(row=curr_row, column=ci).fill = ZEBRA_FILL
        ws.row_dimensions[curr_row].height = 19

    if curr_row >= start_data_row:
        tot_row = curr_row + 1
        ws.cell(row=tot_row, column=2, value="TOTAL STATEMENT").font = FONT_TOTAL
        for col_idx in range(1, 15):
            c = ws.cell(row=tot_row, column=col_idx)
            c.fill = TOTAL_FILL
            c.border = BORDER_TOTAL
            if col_idx in (6, 7, 8, 9, 10, 11, 12):
                col_let = get_column_letter(col_idx)
                c.value = f"=SUM({col_let}{start_data_row}:{col_let}{curr_row})"
                c.font = FONT_TOTAL
                c.number_format = FMT_CURRENCY
                c.alignment = ALIGN_RIGHT
        ws.row_dimensions[tot_row].height = 22

    _setup_sheet_view(ws, h_row, min_col=1, max_col=14, landscape=True)
    _auto_fit_columns(ws, min_col=1, max_col=14)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio
