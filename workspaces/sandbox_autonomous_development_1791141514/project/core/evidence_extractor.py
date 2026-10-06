import re


class EvidenceExtractor:

    def clean_text(self, text: str) -> str:

        if not text:
            return ""

        # Remove markdown images
        text = re.sub(
            r'!\[[^\]]*\]\([^)]+\)',
            '',
            text
        )

        # Convert markdown links to visible text
        text = re.sub(
            r'\[([^\]]+)\]\([^)]+\)',
            r'\1',
            text
        )

        # Remove reference-style links
        text = re.sub(
            r'\[\d+\]',
            '',
            text
        )

        # Remove standalone navigation lines
        noise_patterns = [
            r'(?im)^\s*skip to main content\s*$',
            r'(?im)^\s*skip to content\s*$',
            r'(?im)^\s*main menu\s*$',
            r'(?im)^\s*personal tools\s*$',
            r'(?im)^\s*move to sidebar\s*$',
            r'(?im)^\s*hide navigation\s*$',
            r'(?im)^\s*jump to content\s*$',
            r'(?im)^\s*edit links\s*$',
            r'(?im)^\s*search this site\s*$'
        ]

        for pattern in noise_patterns:
            text = re.sub(pattern, '', text)

        return text


    def is_good_evidence(self, sentence: str) -> bool:

        sentence = sentence.strip()

        if len(sentence) < 50:
            return False

        lower = sentence.lower()

        # Too many markdown/link brackets = navigation
        if sentence.count("[") >= 2:
            return False

        # Too many pipe separators = table/navigation
        if sentence.count("|") >= 2:
            return False

        # Navigation indicators
        navigation_words = [
            "skip to",
            "sidebar",
            "navigation",
            "personal tools",
            "move to",
            "search appearance",
            "account & billing"
        ]

        if any(
            word in lower
            for word in navigation_words
        ):
            return False

        # Very short heading-like text
        if len(sentence.split()) < 8:
            return False

        # A useful evidence sentence normally contains
        # some grammatical structure.
        if not re.search(
            r'\b(is|are|was|were|has|have|had|can|allows|provides|'
            r'includes|introduced|released|added|supports|'
            r'enables|helps|designed|offers|features|uses|'
            r'contains|available|improves|improved)\b',
            lower
        ):
            return False

        return True


    def extract_relevant(
        self,
        text: str,
        query: str,
        max_items: int = 8
    ):

        cleaned = self.clean_text(text)

        if not cleaned:
            return []

        sentences = []

        for line in cleaned.splitlines():

            parts = re.split(
                r'(?<=[.!?])\s+',
                line
            )

            sentences.extend(parts)

        query_words = [
            word.lower()
            for word in re.findall(
                r'\b\w+\b',
                query
            )
            if len(word) > 2
        ]

        information_words = [
            "released",
            "introduced",
            "added",
            "new",
            "feature",
            "features",
            "version",
            "support",
            "supported",
            "improved",
            "allows",
            "provides",
            "available",
            "includes",
            "change",
            "changes",
            "updated",
            "update",
            "upgrade",
            "security",
            "performance",
            "bugfix",
            "documentation",
            "designed",
            "helps",
            "enables",
            "built"
        ]

        scored = []

        for sentence in sentences:

            sentence = sentence.strip()

            if not self.is_good_evidence(sentence):
                continue

            lower = sentence.lower()

            query_score = sum(
                1
                for word in query_words
                if word in lower
            )

            if query_score == 0:
                continue

            score = query_score * 3

            score += sum(
                1
                for word in information_words
                if word in lower
            )

            scored.append(
                (score, sentence)
            )

        scored.sort(
            key=lambda item: item[0],
            reverse=True
        )

        findings = []
        seen = set()

        for score, sentence in scored:

            sentence = re.sub(
                r'[*#`]+',
                '',
                sentence
            ).strip()

            key = sentence.lower()

            if key in seen:
                continue

            seen.add(key)
            findings.append(sentence)

            if len(findings) >= max_items:
                break

        return findings


    def analyze_sources(
        self,
        sources: list[dict],
        query: str
    ):

        evidence = []

        for source in sources:

            snippet = source.get(
                "snippet",
                ""
            )

            content = source.get(
                "content",
                ""
            )

            combined = ""

            if snippet:
                combined += snippet + "\n"

            if content:
                combined += content

            findings = self.extract_relevant(
                combined,
                query
            )

            if findings:

                evidence.append({
                    "title": source.get(
                        "title",
                        ""
                    ),
                    "url": source.get(
                        "url",
                        ""
                    ),
                    "findings": findings
                })

        return evidence
