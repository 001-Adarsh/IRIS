import re


class SourceComparison:

    def normalize(self, text: str) -> str:

        text = text.lower()

        # Remove punctuation
        text = re.sub(
            r'[^a-z0-9\s]',
            ' ',
            text
        )

        # Remove common filler words
        stop_words = {
            "the", "a", "an", "and", "or",
            "of", "to", "in", "on", "for",
            "with", "is", "are", "was", "were",
            "this", "that", "these", "those",
            "it", "its", "from", "by", "as"
        }

        words = [
            word
            for word in text.split()
            if word not in stop_words
        ]

        return " ".join(words)


    def similarity(
        self,
        text1: str,
        text2: str
    ) -> float:

        words1 = set(
            self.normalize(text1).split()
        )

        words2 = set(
            self.normalize(text2).split()
        )

        if not words1 or not words2:
            return 0.0

        intersection = words1.intersection(
            words2
        )

        # Compare against the smaller set.
        # This helps when one source gives
        # a longer version of the same fact.

        smaller = min(
            len(words1),
            len(words2)
        )

        return len(intersection) / smaller


    def compare(
        self,
        evidence: list[dict]
    ) -> dict:

        all_findings = []

        for source in evidence:

            for finding in source.get(
                "findings",
                []
            ):

                all_findings.append({
                    "source": source.get(
                        "title",
                        ""
                    ),
                    "url": source.get(
                        "url",
                        ""
                    ),
                    "finding": finding
                })


        supported = []
        unique = []
        possible_conflicts = []

        used = set()


        # Compare findings between sources

        for i, item in enumerate(
            all_findings
        ):

            if i in used:
                continue

            matches = []

            for j, other in enumerate(
                all_findings
            ):

                if i == j:
                    continue

                # Never compare a source with itself
                if (
                    item["source"]
                    == other["source"]
                ):
                    continue

                similarity = self.similarity(
                    item["finding"],
                    other["finding"]
                )

                if similarity >= 0.40:
                    matches.append(
                        other
                    )

            if matches:

                supported.append({
                    "finding": item[
                        "finding"
                    ],
                    "source": item[
                        "source"
                    ],
                    "url": item[
                        "url"
                    ],
                    "supported_by": [
                        match["source"]
                        for match in matches
                    ]
                })

                used.add(i)

                for match in matches:

                    for index, candidate in enumerate(
                        all_findings
                    ):

                        if (
                            candidate["finding"]
                            == match["finding"]
                            and
                            candidate["source"]
                            == match["source"]
                        ):
                            used.add(index)

            else:

                unique.append(item)


        # Basic contradiction detection
        contradiction_pairs = [
            ("increased", "decreased"),
            ("increase", "decrease"),
            ("before", "after"),
            ("available", "unavailable"),
            ("supported", "unsupported"),
            ("supports", "does not support")
        ]

        for i, item in enumerate(
            all_findings
        ):

            for j, other in enumerate(
                all_findings
            ):

                if i >= j:
                    continue

                if (
                    item["source"]
                    == other["source"]
                ):
                    continue

                text1 = self.normalize(
                    item["finding"]
                )

                text2 = self.normalize(
                    other["finding"]
                )

                for word1, word2 in contradiction_pairs:

                    if (
                        word1 in text1
                        and word2 in text2
                    ):

                        possible_conflicts.append({
                            "source_1": item[
                                "source"
                            ],
                            "finding_1": item[
                                "finding"
                            ],
                            "source_2": other[
                                "source"
                            ],
                            "finding_2": other[
                                "finding"
                            ]
                        })


        return {
            "supported": supported,
            "unique": unique,
            "possible_conflicts":
                possible_conflicts
        }
