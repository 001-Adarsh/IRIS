import os
import json
from typing import List, Dict, Any, Optional

class IRISPlanner:
    def __init__(self, provider_manager=None):
        self.provider_manager = provider_manager

    def _get_conversation_context(self, history: Optional[List[Dict[str, Any]]] = None) -> str:
        if not history:
            return ""
        context_lines = []
        for msg in history[-5:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            context_lines.append(f"{role}: {content}")
        return "\n".join(context_lines)

    def analyze_intent_with_council(self, prompt: str) -> Dict[str, Any]:
        return {"intent": "general", "confidence": 1.0}

    def build_prompt(self, prompt: str, context: str) -> str:
        return f"Context:\n{context}\n\nPrompt:\n{prompt}"

    def plan(self, prompt: str, history: Optional[List[Dict[str, Any]]] = None, previous_attempts: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        prompt_lower = prompt.lower()
        file_keywords = ('excel', 'csv', 'sheet', 'doc', 'file')
        
        # Check if any keyword is in the prompt and if previous_attempts has data
        if any(kw in prompt_lower for kw in file_keywords) and previous_attempts:
            return [{
                "name": "create_excel",
                "purpose": "create deliverable",
                "arguments": {"filename": "output.xlsx"}
            }]

        # Default fallback plan (e.g., search_web)
        return [{
            "name": "search_web",
            "purpose": "gather information",
            "arguments": {"query": prompt}
        }]