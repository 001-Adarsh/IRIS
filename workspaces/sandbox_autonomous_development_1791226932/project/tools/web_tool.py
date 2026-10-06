import html
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

logger = logging.getLogger("WebTool")

# Resilient DDGS import
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
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_title(title: str) -> str:
    """Strips common site suffixes and trailing breadcrumbs."""
    title = clean_text(title)
    separators = [
        " - Wikipedia", " | PCMag", " - The Hindu", " - NDTV",
        " - Times of India", " - Reuters", " - Moneycontrol", " - Livemint"
    ]
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
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=max_results))
        for r in raw_results:
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
# ENGINE 3: BING HTML (High-Speed Fallback)
# -------------------------------------------------------------
def _search_bing_html(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    results = []
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://www.bing.com/search?q={encoded}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=6) as response:
            html_content = response.read().decode("utf-8", errors="ignore")

        # Regex fallback parser so beautifulsoup4 is not a hard dependency
        blocks = re.findall(r'<li class="b_algo".*?</h2>.*?</li>', html_content, flags=re.DOTALL)
        for b in blocks[:max_results]:
            match_link = re.search(r'<h2><a href="([^"]+)".*?>(.*?)</a></h2>', b, flags=re.DOTALL)
            if match_link:
                href = match_link.group(1)
                title = clean_title(match_link.group(2))
                snippet_match = re.search(r'<p.*?>(.*?)</p>', b, flags=re.DOTALL)
                snippet = clean_text(snippet_match.group(1)) if snippet_match else title
                if href and title:
                    results.append({
                        "title": title,
                        "url": href,
                        "snippet": snippet
                    })
    except Exception:
        pass
    return results


# -------------------------------------------------------------
# PRIMARY FUNCTION & CLASS EXPORTS
# -------------------------------------------------------------
def search_web(*args, **kwargs) -> List[Dict[str, str]]:
    """Dispatches search queries across Google News, DuckDuckGo, and Bing with automatic failover.

    Returns a list of result dictionaries so callers can inspect title/url/snippet fields.
    """
    query = ""
    if args and args[0]:
        query = str(args[0])

    if not query:
        query = (
            kwargs.get("query")
            or kwargs.get("search_query")
            or kwargs.get("q")
            or kwargs.get("keyword")
            or kwargs.get("text")
            or ""
        )

    if not query and kwargs:
        for val in kwargs.values():
            if isinstance(val, str) and val.strip():
                query = val.strip()
                break

    max_results = int(kwargs.get("max_results", 5))

    if not query or not query.strip():
        return []

    query = query.strip()
    q_low = query.lower()
    is_news = any(k in q_low for k in ["news", "headline", "breaking", "today", "latest", "update"])

    collected: List[Dict[str, str]] = []

    # Priority 1: Google News for live news
    if is_news:
        collected = _search_google_news(query, max_results=max_results)

    # Priority 2: DuckDuckGo
    if len(collected) < 2:
        ddg_results = _search_ddg(query, max_results=max_results)
        existing_urls = {item["url"] for item in collected}
        for item in ddg_results:
            if item["url"] not in existing_urls:
                collected.append(item)
                existing_urls.add(item["url"])

    # Priority 3: Bing HTML Fallback
    if len(collected) < 2:
        bing_results = _search_bing_html(query, max_results=max_results)
        existing_urls = {item["url"] for item in collected}
        for item in bing_results:
            if item["url"] not in existing_urls:
                collected.append(item)
                existing_urls.add(item["url"])

    return collected[:max_results]


class WebTool:
    """Wrapper class providing an object-oriented interface for ToolManager."""

    def __init__(self):
        pass

    def search(self, query: str = "", max_results: int = 5, **kwargs) -> str:
        return search_web(query=query, max_results=max_results, **kwargs)

    def run(self, query: str = "", max_results: int = 5, **kwargs) -> str:
        return search_web(query=query, max_results=max_results, **kwargs)