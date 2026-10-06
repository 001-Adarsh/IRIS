from tools.web_tool import search_web as ddgs_search
import requests


class SearchManager:

    def __init__(self, searxng_url=None):
        self.searxng_url = searxng_url

    def search_ddgs(self, query, max_results=5):
        return ddgs_search(
            query=query,
            max_results=max_results
        )

    def search_searxng(self, query, max_results=5):

        if not self.searxng_url:
            return []

        try:
            response = requests.get(
                f"{self.searxng_url.rstrip('/')}/search",
                params={
                    "q": query,
                    "format": "json",
                    "language": "en"
                },
                timeout=10
            )

            response.raise_for_status()

            data = response.json()

            results = []

            for item in data.get("results", [])[:max_results]:

                title = item.get("title", "").strip()
                url = item.get("url", "").strip()
                snippet = item.get("content", "").strip()

                if title and url:
                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "source": "searxng"
                    })

            return results

        except Exception:
            return []

    def search(self, query, max_results=5):

        ddgs_results = self.search_ddgs(
            query,
            max_results
        )

        searxng_results = self.search_searxng(
            query,
            max_results
        )

        combined = []

        for result in ddgs_results:
            result["source"] = "ddgs"
            combined.append(result)

        combined.extend(searxng_results)

        # Remove duplicate URLs
        unique = {}
        for result in combined:
            url = result.get("url", "").strip()

            if url and url not in unique:
                unique[url] = result

        return list(unique.values())
