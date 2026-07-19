import os
import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from ddgs import DDGS


_AUTHORITATIVE_DOMAINS = (
    "fifa.com",
    "uefa.com",
    "olympics.com",
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "rtve.es",
    "espn.com",
    "espn.com.mx",
    "espn.com.co",
    "elpais.com",
    "cadenaser.com",
    "as.com",
    "tycsports.com",
    "milenio.com",
    "who.int",
    "un.org",
)
_LOW_VALUE_DOMAINS = ("youtube.com", "youtu.be", "tiktok.com", "facebook.com", "instagram.com")
_VIDEO_TERMS = ("video", "videos", "highlights", "mejores momentos")
_ACCESSORY_TERMS = ("camiseta", "camisetas", "equipación", "uniforme", "entradas", "merchandising")
_SPORTS_TERMS = ("mundial", "copa", "torneo", "campeonato", "liga", "partido")


def _local_date():
    timezone_name = os.getenv("TIMEZONE") or os.getenv("TZ") or "America/Costa_Rica"
    try:
        return datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _normalize_result(result):
    title = str(result.get("title") or "").strip()
    body = str(result.get("body") or result.get("description") or "").strip()
    href = str(result.get("href") or result.get("url") or "").strip()
    if not title or not body or not href.startswith(("http://", "https://")):
        return None
    return {
        "title": title,
        "body": body,
        "href": href,
        "date": str(result.get("date") or "").strip(),
    }


def _domain(url):
    return urlsplit(url).netloc.casefold().removeprefix("www.")


def _canonical_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), "", ""))


def _source_score(result, query):
    domain = _domain(result["href"])
    title = result["title"].casefold()
    body = result["body"].casefold()
    terms = {term for term in re.findall(r"[\wáéíóúüñ]{4,}", query.casefold())}
    score = sum(3 for term in terms if term in title)
    score += sum(1 for term in terms if term in body)
    if result.get("kind") == "news":
        score += 12
    if any(domain == trusted or domain.endswith(f".{trusted}") for trusted in _AUTHORITATIVE_DOMAINS):
        score += 24
    if domain.endswith(".gov") or ".gov." in domain or domain.endswith(".edu"):
        score += 18
    if any(domain == low or domain.endswith(f".{low}") for low in _LOW_VALUE_DOMAINS):
        score -= 30
    if any(term in title for term in _VIDEO_TERMS):
        score -= 22
    if any(term in title for term in _ACCESSORY_TERMS):
        score -= 18
    return score


def search_results(query, max_results=5):
    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []
    return [normalized for item in raw_results if (normalized := _normalize_result(item))]


def research_results(query, max_results=6):
    """Gather diverse, current sources for a synthesized report."""
    clean_query = " ".join(str(query or "").split())
    if not clean_query:
        return []
    today = _local_date()
    if any(term in clean_query.casefold() for term in _SPORTS_TERMS):
        queries = (
            f"{clean_query} resultados semifinales final {today}",
            f"{clean_query} datos clave estadísticas torneo",
            f"{clean_query} fuente oficial",
        )
    else:
        queries = (
            f"{clean_query} hechos clave resumen actualizado {today}",
            f"{clean_query} contexto datos cifras fuente oficial",
        )
    candidates = {}
    try:
        with DDGS() as ddgs:
            if any(term in clean_query.casefold() for term in _SPORTS_TERMS):
                try:
                    news_query = f"{clean_query} resultados semifinales final"
                    for raw in ddgs.news(news_query, max_results=max(6, max_results)):
                        result = _normalize_result(raw)
                        if not result:
                            continue
                        result["kind"] = "news"
                        candidates[_canonical_url(result["href"])] = result
                except Exception:
                    pass
            for search_query in queries:
                try:
                    raw_results = ddgs.text(search_query, max_results=max(5, max_results))
                    for raw in raw_results:
                        result = _normalize_result(raw)
                        if not result:
                            continue
                        key = _canonical_url(result["href"])
                        previous = candidates.get(key)
                        if previous is None or len(result["body"]) > len(previous["body"]):
                            if previous and previous.get("kind") == "news":
                                result["kind"] = "news"
                            candidates[key] = result
                except Exception:
                    continue
    except Exception:
        return search_results(clean_query, max_results)

    ranked = sorted(
        candidates.values(),
        key=lambda item: (_source_score(item, clean_query), len(item["body"])),
        reverse=True,
    )
    selected = []
    domain_counts = {}
    for result in ranked:
        domain = _domain(result["href"])
        if domain_counts.get(domain, 0) >= 2:
            continue
        selected.append(result)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        if len(selected) >= max_results:
            break
    return selected or search_results(clean_query, max_results)


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
