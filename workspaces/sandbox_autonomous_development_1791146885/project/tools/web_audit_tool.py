import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

def audit_website(url: str, max_pages: int = 8, **kwargs) -> str:
    """
    Crawls a website starting from URL, audits internal pages,
    and returns a formatted string report of broken links and SEO/markup errors.
    """
    if not url.startswith("http"):
        url = "https://" + url

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    parsed_start = urlparse(url)
    base_domain = parsed_start.netloc

    visited = set()
    to_visit = [url]
    
    pages_audited = []
    broken_links = []
    seo_issues = []

    session = requests.Session()
    session.headers.update(headers)

    while to_visit and len(visited) < max_pages:
        current_url = to_visit.pop(0)
        if current_url in visited:
            continue

        visited.add(current_url)

        try:
            resp = session.get(current_url, timeout=10, allow_redirects=True)
            status = resp.status_code

            if status >= 400:
                broken_links.append(f"- Broken Link ({status}): {current_url}")
                continue

            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            title = soup.find("title")
            title_text = title.get_text(strip=True) if title else None
            meta_desc = soup.find("meta", attrs={"name": "description"})
            desc_text = meta_desc.get("content", "").strip() if meta_desc else None
            h1_tags = soup.find_all("h1")
            images_without_alt = [img.get("src") for img in soup.find_all("img") if not img.get("alt")]

            page_issues = []
            if not title_text:
                page_issues.append("Missing <title> tag")
            if not desc_text:
                page_issues.append("Missing meta description")
            if len(h1_tags) == 0:
                page_issues.append("Missing <h1> tag")
            elif len(h1_tags) > 1:
                page_issues.append(f"Multiple <h1> tags found ({len(h1_tags)})")
            if images_without_alt:
                page_issues.append(f"{len(images_without_alt)} image(s) missing alt text")

            if page_issues:
                seo_issues.append(f"Page: {current_url}\n  - " + "\n  - ".join(page_issues))

            pages_audited.append(f"{current_url} (HTTP {status})")

            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"].strip()
                if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                full_link = urljoin(current_url, href)
                parsed_link = urlparse(full_link)

                if parsed_link.netloc == base_domain and full_link not in visited and full_link not in to_visit:
                    to_visit.append(full_link)

        except Exception as e:
            broken_links.append(f"- Error connecting to {current_url}: {str(e)}")

    lines = []
    lines.append(f"=== WEBSITE AUDIT REPORT FOR {url} ===")
    lines.append(f"Total Pages Crawled: {len(pages_audited)}")
    lines.append(f"Total Broken Links / HTTP Errors: {len(broken_links)}")
    lines.append(f"Pages with Markup/SEO Issues: {len(seo_issues)}\n")

    if broken_links:
        lines.append("--- BROKEN LINKS & HTTP ERRORS ---")
        lines.extend(broken_links)
        lines.append("")

    if seo_issues:
        lines.append("--- PAGE MARKUP & SEO ISSUES ---")
        lines.extend(seo_issues)
        lines.append("")

    lines.append("--- AUDITED URLS ---")
    lines.extend([f"- {p}" for p in pages_audited])

    return "\n".join(lines)
