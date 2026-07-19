from ddgs import DDGS

def search(query, max_results=5):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No encontr\u00e9 resultados para esa b\u00fasqueda."
        lines = [f"\U0001f50d *Resultados para:* \"{query}\"\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "Sin t\u00edtulo")
            body = r.get("body", "")
            href = r.get("href", "")
            lines.append(f"{i}. *{title}*")
            lines.append(f"   {body[:250]}{'...' if len(body) > 250 else ''}")
            if href.startswith("http"):
                lines.append(f"   [{href}]({href})")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"Error al buscar: {e}"

def search_raw(query, max_results=5):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return ""
        lines = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            if title and body:
                lines.append(f"T\u00edtulo: {title}\nContenido: {body[:500]}\nFuente: {href}")
        return "\n\n".join(lines)
    except Exception:
        return ""
