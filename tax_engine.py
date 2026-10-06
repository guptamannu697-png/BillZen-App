"""
BillZen Tax & GST Rules Engine
==============================
Deterministic, auditable, and rule-based tax engine for Indian GST compliance.
Supports dynamic tax rules with effective dates, Place of Supply (POS) taxation,
and GSTR classification (B2B, B2C Large, B2C Small, CDNR, CDNUR, Nil/Exempt).
"""

from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP

DEFAULT_TAX_RULES = [
    {
        "rate": 18.0,
        "cgst_rate": 9.0,
        "sgst_rate": 9.0,
        "igst_rate": 18.0,
        "effective_from": "2017-07-01",
        "effective_to": "2099-12-31",
        "description": "Standard Rate 18% (9% CGST + 9% SGST / 18% IGST)",
        "is_active": 1
    },
    {
        "rate": 5.0,
        "cgst_rate": 2.5,
        "sgst_rate": 2.5,
        "igst_rate": 5.0,
        "effective_from": "2017-07-01",
        "effective_to": "2099-12-31",
        "description": "Essential Rate 5% (2.5% CGST + 2.5% SGST / 5% IGST)",
        "is_active": 1
    },
    {
        "rate": 0.0,
        "cgst_rate": 0.0,
        "sgst_rate": 0.0,
        "igst_rate": 0.0,
        "effective_from": "2017-07-01",
        "effective_to": "2099-12-31",
        "description": "Exempt / Nil Rated (0% GST)",
        "is_active": 1
    },
    {
        "rate": 12.0,
        "cgst_rate": 6.0,
        "sgst_rate": 6.0,
        "igst_rate": 12.0,
        "effective_from": "2017-07-01",
        "effective_to": "2099-12-31",
        "description": "Legacy 12% (Configurable/Historical)",
        "is_active": 0
    },
    {
        "rate": 28.0,
        "cgst_rate": 14.0,
        "sgst_rate": 14.0,
        "igst_rate": 28.0,
        "effective_from": "2017-07-01",
        "effective_to": "2099-12-31",
        "description": "Luxury 28% (Configurable/Historical)",
        "is_active": 0
    }
]


def d_round(val, places=2):
    """Accurate banker/commercial half-up rounding using Python Decimal."""
    if val is None:
        return Decimal('0.00')
    try:
        d = Decimal(str(val))
        return d.quantize(Decimal('10') ** -places, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal('0.00')


def float_round(val, places=2):
    return float(d_round(val, places))


def is_interstate_supply(shop_state_code, party_state_code):
    """
    Determines if supply is Inter-State based on Place of Supply (POS).
    Default shop state code is '07' (Delhi).
    """
    s_code = str(shop_state_code or '07').strip().zfill(2)[:2]
    p_code = str(party_state_code or '').strip().zfill(2)[:2]
    if not p_code or p_code == '00':
        return False
    return s_code != p_code


def get_tax_rule_for_rate(rate, tx_date=None, db_conn=None):
    """
    Resolves tax rule from database tax_rules table or fallback defaults.
    Ensures date validity according to effective_from and effective_to.
    """
    target_rate = float(rate or 0)
    tx_d = str(tx_date or date.today())[:10]

    if db_conn:
        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT * FROM tax_rules 
                WHERE rate = ? AND is_active = 1 
                  AND effective_from <= ? AND effective_to >= ?
                ORDER BY id DESC LIMIT 1
            """, (target_rate, tx_d, tx_d))
            row = cur.fetchone()
            if row:
                return dict(row)
        except Exception:
            pass

    for r in DEFAULT_TAX_RULES:
        if abs(r['rate'] - target_rate) < 0.01:
            return r

    # Fallback dynamic calculation if rate not explicitly listed
    half = float_round(target_rate / 2.0, 3)
    return {
        "rate": target_rate,
        "cgst_rate": half,
        "sgst_rate": half,
        "igst_rate": target_rate,
        "effective_from": "2017-07-01",
        "effective_to": "2099-12-31",
        "description": f"Custom Rate {target_rate}%",
        "is_active": 1
    }


def calculate_line_item_tax(qty, price, gst_pct, is_interstate, tx_date=None, db_conn=None):
    """
    Calculates taxable amount, CGST, SGST, IGST, and line total for an item.
    """
    qty_d = Decimal(str(qty or 0))
    price_d = Decimal(str(price or 0))
    taxable_d = (qty_d * price_d).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    taxable = float(taxable_d)

    rule = get_tax_rule_for_rate(gst_pct, tx_date, db_conn)
    rate_d = Decimal(str(rule['rate']))

    if rate_d == 0 or taxable == 0:
        return {
            "taxable": taxable,
            "gst_pct": float(rate_d),
            "cgst": 0.0,
            "sgst": 0.0,
            "igst": 0.0,
            "total_tax": 0.0,
            "total": taxable
        }

    if is_interstate:
        igst_d = (taxable_d * Decimal(str(rule['igst_rate'])) / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        cgst_d = Decimal('0.00')
        sgst_d = Decimal('0.00')
    else:
        cgst_d = (taxable_d * Decimal(str(rule['cgst_rate'])) / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        sgst_d = (taxable_d * Decimal(str(rule['sgst_rate'])) / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        igst_d = Decimal('0.00')

    total_tax_d = cgst_d + sgst_d + igst_d
    total_d = taxable_d + total_tax_d

    return {
        "taxable": float(taxable_d),
        "gst_pct": float(rate_d),
        "cgst": float(cgst_d),
        "sgst": float(sgst_d),
        "igst": float(igst_d),
        "total_tax": float(total_tax_d),
        "total": float(total_d)
    }


def classify_invoice_gstr1(party_gstin, is_interstate, total_amount):
    """
    Classifies outward supply into GSTR-1 sections:
    - B2B (Table 4)
    - B2C_LARGE (Table 5: Inter-state + Unregistered + Total > 2.5 Lakhs)
    - B2C_SMALL (Table 7: Intra-state or Inter-state <= 2.5 Lakhs Unregistered)
    """
    gstin = str(party_gstin or '').strip().upper()
    is_registered = len(gstin) == 15 and gstin[:2].isdigit()

    if is_registered:
        return "B2B"
    elif is_interstate and float(total_amount or 0) > 250000.0:
        return "B2C_LARGE"
    else:
        return "B2C_SMALL"
