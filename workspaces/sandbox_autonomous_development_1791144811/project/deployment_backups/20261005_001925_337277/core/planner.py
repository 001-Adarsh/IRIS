import re
from typing import List, Dict, Any, Optional

class IRISPlanner:
    """
    IRISPlanner determines the execution plan for user queries,
    prioritizing specific tools based on intent and keywords (e.g., stock prices, IPO details).
    """

    def __init__(self, council=None):
        self.council = council

    def _get_conversation_context(self) -> str:
        return ""

    def analyze_intent_with_council(self, query: str) -> Dict[str, Any]:
        if self.council:
            try:
                return self.council.analyze(query)
            except Exception:
                pass
        return {"intent": "general", "priority": "normal"}

    def build_prompt(self, query: str, context: str = "") -> str:
        return f"Query: {query}\nContext: {context}"

    def plan(self, query: str, context: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Generate an execution plan based on the query.
        Prioritizes web search for stock prices and IPO details.
        """
        query_lower = query.lower()
        
        # Keywords check for stock prices or IPO details
        stock_ipo_patterns = [
            r"stock price",
            r"share price",
            r"shares of",
            r"ipo",
            r"initial public offering",
            r"ticker",
            r"market cap",
            r"stock value"
        ]
        
        requires_web_search = any(re.search(pattern, query_lower) for pattern in stock_ipo_patterns)
        
        plan_steps = []
        
        if requires_web_search:
            plan_steps.append({
                "tool": "web_tool",
                "action": "search",
                "query": query,
                "reason": "Prioritizing web search for real-time stock price or IPO details."
            })
            
        # Add subsequent analysis or default steps
        intent_info = self.analyze_intent_with_council(query)
        plan_steps.append({
            "tool": "reasoning",
            "action": "synthesize",
            "intent": intent_info.get("intent", "general"),
            "query": query
        })
        
        return plan_steps