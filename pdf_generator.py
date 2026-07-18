import os
import logging
from datetime import datetime
from fpdf import FPDF
from database import get_conn, DATABASE_URL, get_today_expenses

PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfs")
os.makedirs(PDF_DIR, exist_ok=True)

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
    pdf.cell(0, 12, "Reporte de Gastos", align="C")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}", align="C")
    pdf.ln(12)

    total = sum(r[0] for r in rows)
    cats = {}
    for r in rows:
        cat = r[2] or "otros"
        cats[cat] = cats.get(cat, 0) + r[0]

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(40, 40, 80)
    pdf.cell(0, 8, f"Total: {total:,.0f} CRC", align="R")
    pdf.ln(10)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(230, 230, 250)
    pdf.cell(80, 7, "Categoria", 1, 0, "C", True)
    pdf.cell(0, 7, "Monto", 1, 1, "C", True)

    pdf.set_font("Helvetica", "", 10)
    for cat, monto in sorted(cats.items(), key=lambda x: -x[1]):
        pdf.cell(80, 6, cat.capitalize(), 1)
        pdf.cell(0, 6, f"{monto:,.0f} CRC", 1, 1, "R")

    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(230, 230, 250)
    pdf.cell(10, 7, "#", 1, 0, "C", True)
    pdf.cell(50, 7, "Descripcion", 1, 0, "C", True)
    pdf.cell(30, 7, "Categoria", 1, 0, "C", True)
    pdf.cell(30, 7, "Monto", 1, 0, "C", True)
    pdf.cell(0, 7, "Fecha", 1, 1, "C", True)

    pdf.set_font("Helvetica", "", 9)
    for i, r in enumerate(rows, 1):
        pdf.cell(10, 6, str(i), 1, 0, "C")
        pdf.cell(50, 6, (r[1] or "")[:30], 1)
        pdf.cell(30, 6, (r[2] or "otros").capitalize(), 1, 0, "C")
        pdf.cell(30, 6, f"{r[0]:,.0f}", 1, 0, "R")
        pdf.cell(0, 6, r[3][:10] if r[3] else "", 1, 1, "C")

    path = _filename("gastos")
    pdf.output(path)
    return path, f"Reporte generado con {len(rows)} gastos."

def generate_text_pdf(title, content, prefix="documento"):
    pdf = OsirisPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(40, 40, 80)
    pdf.cell(0, 12, title, align="C")
    pdf.ln(10)

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(30, 30, 30)
    pdf.multi_cell(0, 6, content)

    path = _filename(prefix)
    pdf.output(path)
    return path

def _placeholder(n):
    return "%s" if DATABASE_URL else "?"
