import re
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from typing import List, Dict, Any

# Resilient DDGS import (handles both 'ddgs' and 'duckduckgo_search')
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


def clean_text(text: str) -> str:
    """Removes HTML entities, excess whitespaces, and system characters."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_title(title: str) -> str:
    """Strips common site suffixes and trailing breadcrumbs."""
    title = clean_text(title)
    separators = [" - Wikipedia", " | PCMag", " - The Hindu", " - NDTV", " - Times of India", " - Reuters"]
    for separator in separators:
        if separator in title:
            title = title.split(separator)[0]
    return title.strip()


# -------------------------------------------------------------
# ENGINE 1: GOOGLE NEWS RSS (Real-Time News & Headlines)
# -------------------------------------------------------------
def _search_google_news(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    results = []
    try:
        # Sanitize query for news lookup
        news_q = query
        for filler in ["top 10 news of the day", "top news", "news of the day", "give top 10", "latest news"]:
            news_q = re.sub(re.escape(filler), "", news_q, flags=re.IGNORECASE).strip()
        if not news_q or len(news_q) < 3:
            news_q = "breaking news India world"

        encoded = urllib.parse.quote(news_q)
        rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"

        req = urllib.request.Request(
            rss_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=6) as response:
            root = ET.fromstring(response.read())
            items = root.findall(".//item")[:max_results]
            for item in items:
                title = item.find("title").text if item.find("title") is not None else ""
                link = item.find("link").text if item.find("link") is not None else ""
                pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                source_elem = item.find("source")
                source_name = source_elem.text if source_elem is not None else "Google News"

                if title and link:
                    results.append({
                        "title": clean_title(title),
                        "url": link,
                        "snippet": f"Source: {source_name}. Published: {pub_date}. Headline: {title.strip()}"
                    })
    except Exception:
        pass
    return results


# -------------------------------------------------------------
# ENGINE 2: DUCKDUCKGO (Fast Multi-Topic Metasearch)
# -------------------------------------------------------------
def _search_ddg(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    results = []
    if not DDGS:
        return results

    try:
        ddg_results = list(DDGS().text(
            query,
            region="in-en",
            max_results=max_results
        ))
        for r in ddg_results:
            title = clean_title(r.get("title", ""))
            url = r.get("href", "")
            snippet = clean_text(r.get("body", ""))
            if title and url and snippet:
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet
                })
    except Exception:
        pass
    return results


# -------------------------------------------------------------
# ENGINE 3: BING WEB & BING LITE (High-Speed Fallback)
# -------------------------------------------------------------
def _search_bing_html(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    results = []
    try:
        from bs4 import BeautifulSoup
        encoded = urllib.parse.quote(query)
        url = f"https://www.bing.com/search?q={encoded}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=6) as response:
            soup = BeautifulSoup(response.read(), "html.parser")
            items = soup.find_all("li", class_="b_algo")
            for item in items[:max_results]:
                h2 = item.find("h2")
                if not h2:
                    continue
                a = h2.find("a")
                p = item.find("p")
                if a and a.get("href"):
                    title = clean_title(a.get_text(strip=True))
                    snippet = clean_text(p.get_text(strip=True)) if p else ""
                    results.append({
                        "title": title,
                        "url": a.get("href"),
                        "snippet": snippet or title
                    })
    except Exception:
        pass
    return results


# -------------------------------------------------------------
# PRIMARY DISPATCHER
# -------------------------------------------------------------
def search_web(*args, **kwargs) -> str:
    """Dispatches search queries across Google News, DuckDuckGo, and Bing with automatic failover."""
    query = ""
    if args and args[0]:
        query = str(args[0])

    if not query:
        query = (
            kwargs.get("query")
            or kwargs.get("search_query")
            or kwargs.get("q")
            or kwargs.get("keyword")
            or kwargs.get("keywords")
            or kwargs.get("text")
            or kwargs.get("prompt")
            or kwargs.get("query_string")
            or ""
        )

    if not query and kwargs:
        for val in kwargs.values():
            if isinstance(val, str) and val.strip():
                query = val.strip()
                break

    max_results = int(kwargs.get("max_results", 5))

    if not query or not query.strip():
        return "Search Error: No search query provided."

    query = query.strip()
    q_low = query.lower()
    is_news = any(k in q_low for k in ["news", "headline", "breaking", "today", "latest", "update"])

    collected: List[Dict[str, str]] = []

    # Priority 1: Google News for real-time news questions
    if is_news:
        collected = _search_google_news(query, max_results=max_results)

    # Priority 2: DuckDuckGo if needed
    if len(collected) < 2:
        ddg_results = _search_ddg(query, max_results=max_results)
        # Avoid duplicate URLs
        existing_urls = {item["url"] for item in collected}
        for item in ddg_results:
            if item["url"] not in existing_urls:
                collected.append(item)
                existing_urls.add(item["url"])

    # Priority 3: Bing HTML Fallback if still under quota
    if len(collected) < 2:
        bing_results = _search_bing_html(query, max_results=max_results)
        existing_urls = {item["url"] for item in collected}
        for item in bing_results:
            if item["url"] not in existing_urls:
                collected.append(item)
                existing_urls.add(item["url"])

    if not collected:
        return f"No search results could be retrieved for '{query}' across Google, DuckDuckGo, or Bing."

    # Format into structured evidence blocks for AI Synthesizer & Council
    formatted = []
    for r in collected[:max_results]:
        formatted.append(
            f"Title: {r['title']}\n"
            f"Source: {r['url']}\n"
            f"Evidence: {r['snippet']}"
        )

    return "\n---\n".join(formatted)