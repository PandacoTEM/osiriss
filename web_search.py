from ddgs import DDGS


def search_results(query, max_results=5):
    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []
    results = []
    for result in raw_results:
        title = str(result.get("title") or "").strip()
        body = str(result.get("body") or "").strip()
        href = str(result.get("href") or "").strip()
        if title and body:
            results.append({"title": title, "body": body, "href": href})
    return results


def search(query, max_results=5):
    results = search_results(query, max_results)
    if not results:
        return "No encontré resultados para esa búsqueda."
    lines = [f"\U0001f50d *Resultados para:* \"{query}\"\n"]
    for i, result in enumerate(results, 1):
        title = result["title"]
        body = result["body"]
        href = result["href"]
        lines.append(f"{i}. *{title}*")
        lines.append(f"   {body[:250]}{'...' if len(body) > 250 else ''}")
        if href.startswith("http"):
            lines.append(f"   [{href}]({href})")
        lines.append("")
    return "\n".join(lines)


def search_raw(query, max_results=5):
    results = search_results(query, max_results)
    if not results:
        return ""
    lines = []
    for result in results:
        lines.append(
            f"Título: {result['title']}\n"
            f"Contenido: {result['body'][:500]}\n"
            f"Fuente: {result['href']}"
        )
    return "\n\n".join(lines)
