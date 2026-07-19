import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from fpdf import FPDF, XPos, YPos
from database import get_conn, DATABASE_URL

PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfs")
os.makedirs(PDF_DIR, exist_ok=True)

_REPLACE = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u2022": "-",
    "\u00a0": " ", "\u20ac": "EUR",
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


def _fit_cell_text(pdf, text, width):
    value = _sanitize(text)
    max_width = max(width - 2, 1)
    if pdf.get_string_width(value) <= max_width:
        return value
    suffix = "..."
    while value and pdf.get_string_width(value + suffix) > max_width:
        value = value[:-1]
    return value + suffix


class OsirisPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(76, 73, 150)
        self.cell(0, 6, "OSIRIS  /  ASISTENTE PERSONAL", align="L")
        self.set_draw_color(218, 220, 235)
        self.line(self.l_margin, 18, self.w - self.r_margin, 18)
        self.ln(10)

    def footer(self):
        self.set_y(-16)
        self.set_draw_color(225, 226, 235)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 8, "Generado por Osiris", align="L")
        self.set_y(-14)
        self.cell(0, 8, f"Página {self.page_no()}/{{nb}}", align="R")

def _filename(prefix):
    return os.path.join(PDF_DIR, f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")

def generate_expense_report(user_id, date_filter=None):
    conn = get_conn()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    if date_filter == "today":
        c.execute(f"SELECT amount, description, category, currency, created_at FROM expenses WHERE user_id = {_placeholder(1)} AND created_at LIKE {_placeholder(2)} ORDER BY created_at", (user_id, f"{today}%"))
    elif date_filter == "month":
        month = today[:7]
        c.execute(f"SELECT amount, description, category, currency, created_at FROM expenses WHERE user_id = {_placeholder(1)} AND created_at LIKE {_placeholder(2)} ORDER BY created_at", (user_id, f"{month}%"))
    else:
        c.execute(f"SELECT amount, description, category, currency, created_at FROM expenses WHERE user_id = {_placeholder(1)} ORDER BY created_at DESC LIMIT 100", (user_id,))
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

    totals = {}
    cats = {}
    for r in rows:
        cat = r[2] or "otros"
        currency = r[3] or "CRC"
        totals[currency] = totals.get(currency, 0) + r[0]
        cats[(cat, currency)] = cats.get((cat, currency), 0) + r[0]

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(40, 40, 80)
    totals_text = " / ".join(f"{amount:,.2f} {currency}" for currency, amount in sorted(totals.items()))
    pdf.cell(0, 8, _sanitize(f"Totales: {totals_text}"), align="R")
    pdf.ln(10)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(230, 230, 250)
    pdf.cell(80, 7, _sanitize("Categoria"), 1, align="C", fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(0, 7, _sanitize("Monto"), 1, align="C", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 10)
    for (cat, currency), monto in sorted(cats.items(), key=lambda x: -x[1]):
        pdf.cell(80, 6, _sanitize(cat.capitalize()), 1)
        pdf.cell(
            0,
            6,
            _sanitize(f"{monto:,.2f} {currency}"),
            1,
            align="R",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(230, 230, 250)
    pdf.cell(10, 7, _sanitize("#"), 1, align="C", fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(45, 7, _sanitize("Descripcion"), 1, align="C", fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(27, 7, _sanitize("Categoria"), 1, align="C", fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(38, 7, _sanitize("Monto"), 1, align="C", fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(0, 7, _sanitize("Fecha"), 1, align="C", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 9)
    for i, r in enumerate(rows, 1):
        pdf.cell(10, 6, _sanitize(str(i)), 1, align="C", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.cell(45, 6, _fit_cell_text(pdf, r[1] or "", 45), 1)
        pdf.cell(27, 6, _fit_cell_text(pdf, (r[2] or "otros").capitalize(), 27), 1, align="C", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.cell(38, 6, _fit_cell_text(pdf, f"{r[0]:,.2f} {r[3] or 'CRC'}", 38), 1, align="R", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.cell(
            0,
            6,
            _sanitize(r[4][:10] if r[4] else ""),
            1,
            align="C",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    path = _filename("gastos")
    pdf.output(path)
    return path, f"Reporte generado con {len(rows)} gastos."

def _render_text_content(pdf, content):
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line:
            pdf.ln(2)
            continue
        heading = line.lstrip("# ").rstrip(":")
        if line.startswith("#") or (line == line.upper() and len(line) <= 60):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(47, 48, 92)
            pdf.multi_cell(0, 7, _sanitize(heading), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_draw_color(221, 222, 238)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(3)
            continue
        if line.startswith(("- ", "* ")):
            pdf.set_font("Helvetica", "", 10.5)
            pdf.set_text_color(38, 38, 45)
            pdf.set_x(pdf.l_margin + 3)
            pdf.multi_cell(
                pdf.w - pdf.l_margin - pdf.r_margin - 3,
                6,
                _sanitize(f"- {line[2:].strip()}"),
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.ln(1)
            continue
        pdf.set_font("Helvetica", "", 10.5)
        pdf.set_text_color(38, 38, 45)
        pdf.multi_cell(0, 6, _sanitize(line), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)


def generate_text_pdf(title, content, prefix="documento", subtitle=None, sources=None):
    pdf = OsirisPDF()
    pdf.alias_nb_pages()
    pdf.set_margins(18, 12, 18)
    pdf.set_auto_page_break(auto=True, margin=22)
    pdf.add_page()
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 21)
    pdf.set_text_color(37, 38, 83)
    pdf.multi_cell(0, 10, _sanitize(title), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if subtitle:
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(92, 94, 112)
        pdf.multi_cell(
            0,
            6,
            _sanitize(f"Consulta: {subtitle}"),
            align="C",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(125, 126, 140)
    pdf.cell(
        0,
        6,
        _sanitize(f"Actualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"),
        align="C",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(8)

    _render_text_content(pdf, content)

    if sources:
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(47, 48, 92)
        pdf.cell(0, 8, "Fuentes consultadas", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_draw_color(221, 222, 238)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(4)
        for index, source in enumerate(sources, 1):
            title_text = _sanitize(source.get("title") or "Fuente sin título")
            url = str(source.get("href") or "").strip()
            domain = urlparse(url).netloc.removeprefix("www.") if url else "Fuente sin enlace"
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(45, 45, 55)
            pdf.multi_cell(
                0,
                5.5,
                _sanitize(f"{index}. {title_text}"),
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(76, 73, 150)
            pdf.cell(
                0,
                5,
                _sanitize(domain),
                link=url if url.startswith("http") else "",
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.ln(2)

    path = _filename(prefix)
    pdf.output(path)
    return path


def generate_weekly_report(user_id):
    cutoff = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    conn = get_conn()
    c = conn.cursor()
    p = _placeholder(1)
    c.execute(
        f"""SELECT action, COUNT(*) FROM activity_log
            WHERE user_id={p} AND timestamp >= {p}
            GROUP BY action ORDER BY COUNT(*) DESC""",
        (user_id, cutoff),
    )
    activities = c.fetchall()
    c.execute(
        f"""SELECT currency, SUM(amount), COUNT(*) FROM expenses
            WHERE user_id={p} AND created_at >= {p}
            GROUP BY currency ORDER BY currency""",
        (user_id, cutoff),
    )
    expenses = c.fetchall()
    c.execute(
        f"""SELECT h.name, COUNT(l.id), COALESCE(SUM(l.value), 0)
            FROM habits h LEFT JOIN habit_logs l
              ON l.habit_id=h.id AND l.log_date >= {p}
            WHERE h.user_id={p} AND h.active=1
            GROUP BY h.id, h.name ORDER BY h.name""",
        (cutoff, user_id),
    )
    habits = c.fetchall()
    c.execute(
        f"""SELECT title, progress, target_date FROM goals
            WHERE user_id={p} AND status='active' ORDER BY id DESC LIMIT 10""",
        (user_id,),
    )
    goals = c.fetchall()
    conn.close()

    pdf = OsirisPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(40, 40, 80)
    pdf.cell(0, 12, "Resumen semanal de Osiris", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(
        0,
        7,
        _sanitize(f"Periodo: {cutoff} a {datetime.now().strftime('%Y-%m-%d')}"),
        align="C",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(8)

    def section(title, rows):
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(40, 40, 80)
        pdf.cell(0, 8, _sanitize(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(30, 30, 30)
        if not rows:
            pdf.cell(0, 6, "Sin datos esta semana.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            for row in rows:
                pdf.multi_cell(0, 6, _sanitize(f"- {row}"))
        pdf.ln(3)

    section("Actividad", [f"{action}: {count}" for action, count in activities])
    section("Gastos", [f"{amount:,.2f} {currency} en {count} compra(s)" for currency, amount, count in expenses])
    section("Habitos", [f"{name}: {value:g} unidades en {days} dia(s)" for name, days, value in habits])
    section("Metas", [f"{title}: {progress}% - fecha {target or 'sin fecha'}" for title, progress, target in goals])

    path = _filename("resumen_semanal")
    pdf.output(path)
    return path, "Resumen semanal generado."

def _placeholder(n):
    return "%s" if DATABASE_URL else "?"
