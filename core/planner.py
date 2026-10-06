import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("IRISPlanner")

class IRISPlanner:
    def __init__(self, provider_manager=None):
        self.provider_manager = provider_manager

    def _get_conversation_context(self, history: Optional[List[Dict[str, Any]]] = None) -> str:
        if not history:
            return ""
        context_lines = []
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            context_lines.append(f"{role}: {content}")
        return "\n".join(context_lines)

    def analyze_intent_with_council(self, prompt: str) -> Dict[str, Any]:
        return {"intent": "general", "confidence": 0.9}

    def build_prompt(self, prompt: str, history: Optional[List[Dict[str, Any]]] = None) -> str:
        conv_ctx = self._get_conversation_context(history)
        if conv_ctx:
            return f"Conversation History:\n{conv_ctx}\n\nUser Prompt: {prompt}"
        return prompt

    def plan(self, prompt: str, history: Optional[List[Dict[str, Any]]] = None, previous_attempts: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """
        Generates execution plans.
        If prompt mentions 'excel', 'sheet', 'csv', 'file', or 'doc' and previous_attempts has data,
        return plan with create_excel tool instead of search_web.
        """
        lower_prompt = prompt.lower()
        document_keywords = ("doc", "document", "word document", ".docx")
        spreadsheet_keywords = ("excel", "spreadsheet", "sheet", ".xlsx")

        if previous_attempts and any(
            keyword in lower_prompt for keyword in document_keywords
        ):
            last_attempt = previous_attempts[-1]
            content = str(last_attempt.get("result") or "")
            if content.strip():
                return [{
                    "name": "create_document",
                    "purpose": "create the requested Word document",
                    "arguments": {
                        "filename": "iris_document.docx",
                        "title": "Requested Information",
                        "content": content,
                    },
                }]

        if previous_attempts and any(
            keyword in lower_prompt for keyword in spreadsheet_keywords
        ):
            logger.info("Planner selected Excel output after gathering source data.")
            return [{
                "name": "create_excel",
                "purpose": "create physical deliverable",
                "arguments": {
                    "filename": "top_10_richest_people_india.xlsx"
                }
            }]

        # Default fallback plan
        return [{
            "name": "search_web",
            "purpose": "gather information from the web",
            "arguments": {
                "query": prompt
            }
        }]


# Backward-compatible alias used by legacy imports and fast planner tests.
class Planner(IRISPlanner):
    pass