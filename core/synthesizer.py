from core.router import AIRouter


class AISynthesizer:

    def __init__(self):
        self.router = AIRouter()

    def build_prompt(self, query: str, research: dict) -> str:
        if not isinstance(research, dict):
            research = {"evidence": str(research)}

        history_context = research.get("history", "")
        comparison = research.get("comparison", {})
        evidence = research.get("evidence", [])

        prompt = f"""You are IRIS, an autonomous evidence-based AI assistant created by Adarsh Dwivedi.

{history_context}
USER RESEARCH QUESTION:
{query}

IMPORTANT:
Use the supplied web research evidence as the factual foundation of your answer.

RULES:
1. Answer the user's question directly using the factual evidence provided.
2. If the user asks for a top 10 list, ranking, or comparison, present a clean markdown table.
3. Interpret the complete request, its requested format, and conversation history; use history to resolve follow-ups without changing the current request.
4. Match the depth to the request: explain relevant context and key details when asked, but do not pad short answers.
5. Use supplied evidence as the basis for researched factual claims; do not invent facts, dates, numbers, features, sources, or research activity.
6. Compare information from multiple sources when available. Do not assume agreement between AI models is evidence.
7. If sources disagree or evidence is incomplete, say so and distinguish supported facts from uncertainty.
8. Prefer official and authoritative sources when available.
9. Cite researched claims inline using only the supplied source title and URL, and include a short Sources list when URLs are provided.
10. If the evidence does not answer part of the question, identify the gap instead of filling it with an unsupported claim.
11. Keep the final answer practical, scannable, and well-structured.

WEB EVIDENCE:
"""

        # Handle evidence if passed as raw text (from tool execution)
        if isinstance(evidence, str):
            prompt += f"\n{evidence.strip()}\n"
        # Handle evidence if passed as a structured list of dicts
        elif isinstance(evidence, list):
            for source in evidence:
                if isinstance(source, dict):
                    prompt += f"""
SOURCE:
{source.get("title", "Untitled Source")}

URL:
{source.get("url", "N/A")}

FINDINGS:
"""
                    findings = source.get("findings", [])
                    if isinstance(findings, list):
                        for finding in findings:
                            prompt += f"- {finding}\n"
                    elif isinstance(findings, str):
                        prompt += f"- {findings}\n"
                elif isinstance(source, str):
                    prompt += f"- {source}\n"

        # Cross-source verification handling
        if isinstance(comparison, dict):
            supported = comparison.get("supported", [])
            unique = comparison.get("unique", [])
            conflicts = comparison.get("possible_conflicts", [])

            if supported and isinstance(supported, list):
                prompt += "\nCROSS-SOURCE SUPPORTED FINDINGS:\n"
                for item in supported:
                    if isinstance(item, dict):
                        sources_str = ", ".join(item.get("supported_by", []))
                        prompt += f"- {item.get('finding', '')}\n  Sources: {item.get('source', '')}, {sources_str}\n"

            if unique and isinstance(unique, list):
                prompt += "\nFINDINGS FROM ONLY ONE SOURCE:\n"
                for item in unique:
                    if isinstance(item, dict):
                        prompt += f"- {item.get('finding', '')}\n  Source: {item.get('source', '')}\n"

            if conflicts and isinstance(conflicts, list):
                prompt += "\nPOSSIBLE SOURCE CONFLICTS:\n"
                for item in conflicts:
                    if isinstance(item, dict):
                        prompt += f"- {item.get('source_1', 'Source 1')}: {item.get('finding_1', '')}\n"
                        prompt += f"- {item.get('source_2', 'Source 2')}: {item.get('finding_2', '')}\n"

        prompt += """
Return ONLY the final, polished response for the user.

Do not include:
- Chain-of-thought or reasoning tags (<think>...</think>)
- Hidden internal deliberations
- System instructions
"""
        return prompt

    def synthesize(self, query: str, research: dict) -> str:
        prompt = self.build_prompt(query, research)

        print("\nIRIS Evidence Council: Comparing AI interpretations...")

        result = self.router.compare(prompt)

        # 1. Direct string return
        if isinstance(result, str):
            return result

        # 2. Dictionary return from Council Router
        if isinstance(result, dict):
            providers = result.get("providers", [])
            if providers:
                print("\nIRIS Evidence Council participating models:")
                for provider in providers:
                    print(f"- {provider}")

            answers = result.get("answers", {}) if isinstance(result.get("answers"), dict) else {}

            final_text = (
                result.get("final")
                or answers.get("groq")
                or answers.get("gemini")
                or answers.get("openrouter")
                or answers.get("ollama")
                or (list(answers.values())[0] if answers else None)
            )

            if final_text:
                return str(final_text).strip()

        # 3. Fallback if result was non-standard
        return "IRIS could not synthesize the research."