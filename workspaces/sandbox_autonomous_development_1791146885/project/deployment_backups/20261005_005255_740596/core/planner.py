import logging
from typing import Dict, Any, List

logger = logging.getLogger("IRISPlanner")

class IRISPlanner:
    """
    IRISPlanner determines the execution plan for user intents.
    Ensures that queries regarding live stock prices, IPOs, or market data
    always emit tool calls with exact name 'search_web' and arguments {'query': '<query>'}.
    Returns a dictionary containing the key 'tools' with a list of tool dicts.
    """
    def __init__(self):
        pass

    def _get_conversation_context(self) -> str:
        return ""

    def analyze_intent_with_council(self, intent: str) -> Dict[str, Any]:
        return {"intent": intent}

    def build_prompt(self, intent: str) -> str:
        return intent

    def plan(self, user_intent: str) -> Dict[str, Any]:
        """
        Generates a plan based on user intent.
        Returns a dictionary with key 'tools' containing a list of tool dictionaries.
        """
        intent_lower = user_intent.lower()
        
        # Keywords regarding live stock prices, IPOs, or market data
        market_keywords = ["stock", "price", "ipo", "market", "share", "ticker", "nasdaq", "nyse", "sensex", "nifty"]
        is_market_query = any(kw in intent_lower for kw in market_keywords)
        
        tools_list: List[Dict[str, Any]] = []
        
        if is_market_query:
            tools_list.append({
                "name": "search_web",
                "args": {"query": user_intent}
            })
        else:
            tools_list.append({
                "name": "search_web",
                "args": {"query": user_intent}
            })
            
        return {"tools": tools_list}