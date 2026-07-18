import os
import re
import logging
from datetime import datetime
from fpdf import FPDF
from database import get_conn, DATABASE_URL, get_today_expenses

PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfs")
os.makedirs(PDF_DIR, exist_ok=True)

_REPLACE = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00e1": "a",
    "\u00e9": "e", "\u00ed": "i", "\u00f3": "o", "\u00fa": "u",
    "\u00f1": "n", "\u00c1": "A", "\u00c9": "E", "\u00cd": "I",
    "\u00d3": "O", "\u00da": "U", "\u00d1": "N", "\u00fc": "u",
    "\u00dc": "U", "\u00bf": "?", "\u00a1": "!", "\u20ac": "EUR",
    "\u00a3": "GBP", "\u00a5": "JPY", "\u2660": ".", "\u2663": ".",
    "\u2665": ".", "\u2666": ".", "\u2713": "V", "\u2714": "V",
    "\u2717": "X", "\u2190": "<-", "\u2192": "->", "\u2191": "^",
    "\u2193": "v", "\u25cf": "*", "\u25cb": "o", "\u25a0": "#",
    "\u25b2": "^", "\u25bc": "v", "\u2605": "*", "\u2606": "*",
    "\u263a": ":)", "\u2639": ":(", "\u266a": "~",
}
_RE_UNICODE = re.compile("|".join(re.escape(k) for k in sorted(_REPLACE, key=len, reverse=True)))

def _sanitize(text):
    """Replace unicode chars not supported by Helvetica with ASCII."""
    if not text:
        return ""
    text = _RE_UNICODE.sub(lambda m: _REPLACE[m.group(0)], str(text))
    # Strip any remaining non-ASCII
    return text.encode("latin-1", "replace").decode("latin-1")

class OsirisPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 100, 180)
        self.cell(0, 8, "Osiris - Asistente Personal", align="L")
        self.ln(12)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Pagina {self.page_no()}/{{nb}}", align="C")

def _filename(prefix):
    return os.path.join(PDF_DIR, f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")

def generate_expense_report(user_id, date_filter=None):
    conn = get_conn()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    if date_filter == "today":
        c.execute(f"SELECT amount, description, category, created_at FROM expenses WHERE user_id = {_placeholder(1)} AND created_at LIKE {_placeholder(2)} ORDER BY created_at", (user_id, f"{today}%"))
    elif date_filter == "month":
        month = today[:7]
        c.execute(f"SELECT amount, description, category, created_at FROM expenses WHERE user_id = {_placeholder(1)} AND created_at LIKE {_placeholder(2)} ORDER BY created_at", (user_id, f"{month}%"))
    else:
        c.execute(f"SELECT amount, description, category, created_at FROM expenses WHERE user_id = {_placeholder(1)} ORDER BY created_at DESC LIMIT 100", (user_id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return None, "No hay gastos registrados."

    pdf = OsirisPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(40, 40, 80)
    pdf.cell(0, 12, _sanitize("Reporte de Gastos"), align="C")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, _sanitize(f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"), align="C")
    pdf.ln(12)

    total = sum(r[0] for r in rows)
    cats = {}
    for r in rows:
        cat = r[2] or "otros"
        cats[cat] = cats.get(cat, 0) + r[0]

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(40, 40, 80)
    pdf.cell(0, 8, _sanitize(f"Total: {total:,.0f} CRC"), align="R")
    pdf.ln(10)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(230, 230, 250)
    pdf.cell(80, 7, _sanitize("Categoria"), 1, 0, "C", True)
    pdf.cell(0, 7, _sanitize("Monto"), 1, 1, "C", True)

    pdf.set_font("Helvetica", "", 10)
    for cat, monto in sorted(cats.items(), key=lambda x: -x[1]):
        pdf.cell(80, 6, _sanitize(cat.capitalize()), 1)
        pdf.cell(0, 6, _sanitize(f"{monto:,.0f} CRC"), 1, 1, "R")

    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(230, 230, 250)
    pdf.cell(10, 7, _sanitize("#"), 1, 0, "C", True)
    pdf.cell(50, 7, _sanitize("Descripcion"), 1, 0, "C", True)
    pdf.cell(30, 7, _sanitize("Categoria"), 1, 0, "C", True)
    pdf.cell(30, 7, _sanitize("Monto"), 1, 0, "C", True)
    pdf.cell(0, 7, _sanitize("Fecha"), 1, 1, "C", True)

    pdf.set_font("Helvetica", "", 9)
    for i, r in enumerate(rows, 1):
        pdf.cell(10, 6, _sanitize(str(i)), 1, 0, "C")
        pdf.cell(50, 6, _sanitize((r[1] or "")[:30]), 1)
        pdf.cell(30, 6, _sanitize((r[2] or "otros").capitalize()), 1, 0, "C")
        pdf.cell(30, 6, _sanitize(f"{r[0]:,.0f}"), 1, 0, "R")
        pdf.cell(0, 6, _sanitize(r[3][:10] if r[3] else ""), 1, 1, "C")

    path = _filename("gastos")
    pdf.output(path)
    return path, f"Reporte generado con {len(rows)} gastos."

def generate_text_pdf(title, content, prefix="documento"):
    pdf = OsirisPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(40, 40, 80)
    pdf.cell(0, 12, _sanitize(title), align="C")
    pdf.ln(10)

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(30, 30, 30)
    pdf.multi_cell(0, 6, _sanitize(content))

    path = _filename(prefix)
    pdf.output(path)
    return path

def _placeholder(n):
    return "%s" if DATABASE_URL else "?"
