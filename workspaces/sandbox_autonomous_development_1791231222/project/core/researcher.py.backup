from tools.manager import ToolManager
from core.evidence_extractor import EvidenceExtractor
from core.source_comparison import SourceComparison


class WebResearcher:

    def __init__(self):
        self.tools = ToolManager()
        self.extractor = EvidenceExtractor()
        self.comparator = SourceComparison()


    def rank_source(
        self,
        result: dict,
        query: str
    ) -> int:

        title = result.get(
            "title", ""
        ).lower()

        url = result.get(
            "url", ""
        ).lower()

        snippet = result.get(
            "snippet", ""
        ).lower()

        text = f"{title} {url} {snippet}"

        score = 0

        if "microsoft.com" in url:
            score += 60

        elif "python.org" in url:
            score += 60

        elif ".gov" in url:
            score += 50

        elif "github.com" in url:
            score += 30

        elif "docs." in url:
            score += 20

        elif "wikipedia.org" in url:
            score += 10

        query_words = [
            word.lower()
            for word in query.split()
            if len(word) > 2
        ]

        for word in query_words:

            if word in text:
                score += 5

        keywords = [
            "official",
            "documentation",
            "docs",
            "release",
            "security",
            "features",
            "support",
            "updated",
            "update",
            "performance"
        ]

        for keyword in keywords:

            if keyword in text:
                score += 3

        return score


    def research(
        self,
        query: str,
        max_sources: int = 3
    ) -> dict:

        print(
            "\nIRIS Researcher: "
            "Searching the web..."
        )

        results = self.tools.execute(
            "search_web",
            query=query,
            max_results=10
        )

        if not results:

            return {
                "query": query,
                "sources": [],
                "evidence": [],
                "comparison": {}
            }


        ranked = sorted(
            results,
            key=lambda r: self.rank_source(
                r,
                query
            ),
            reverse=True
        )


        print(
            "\nIRIS Source Ranking:"
        )

        for i, result in enumerate(
            ranked[:5],
            1
        ):

            score = self.rank_source(
                result,
                query
            )

            print(
                f"{i}. [{score}] "
                f"{result.get('title', '')}"
            )


        sources = []

        for result in ranked:

            if len(sources) >= max_sources:
                break

            title = result.get(
                "title",
                ""
            )

            url = result.get(
                "url",
                ""
            )

            snippet = result.get(
                "snippet",
                ""
            )

            if not url:
                continue

            print(
                f"\nReading source: {title}"
            )

            content = self.tools.execute(
                "read_webpage",
                url=url
            )

            if (
                not content
                or content.startswith(
                    "Web reader error"
                )
            ):

                print(
                    "Skipping unreadable source."
                )

                continue


            sources.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "content": content[:1500]
            })


        if not sources:

            return {
                "query": query,
                "sources": [],
                "evidence": [],
                "comparison": {}
            }


        print(
            "\nIRIS Evidence Extractor: "
            "Finding relevant evidence..."
        )

        evidence = self.extractor.analyze_sources(
            sources,
            query
        )


        print(
            "\nIRIS Source Comparison: "
            "Comparing evidence..."
        )

        comparison = self.comparator.compare(
            evidence
        )


        return {
            "query": query,
            "sources": sources,
            "evidence": evidence,
            "comparison": comparison
        }
