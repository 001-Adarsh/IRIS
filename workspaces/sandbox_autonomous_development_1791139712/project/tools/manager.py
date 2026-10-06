import logging
from typing import Dict, Any, Callable

logger = logging.getLogger("ToolManager")

class ToolManager:
    """
    Manages available tools and provides alias mapping so that any tool call
    for 'web_tool' or 'search' redirects to 'search_web'.
    """
    def __init__(self):
        self.registry: Dict[str, Callable[..., Any]] = {}
        self._register_default_tools()

    def _register_default_tools(self):
        self.registry["search_web"] = lambda query: f"Searching web for: {query}"

    def register_tool(self, name: str, func: Callable[..., Any]):
        self.registry[name] = func

    def get_tool(self, name: str) -> Callable[..., Any]:
        """
        Retrieves the tool function with alias mapping:
        'web_tool' or 'search' redirects to 'search_web'.
        """
        normalized_name = name.lower()
        if normalized_name in ["web_tool", "search"]:
            normalized_name = "search_web"
            
        if normalized_name not in self.registry:
            logger.warning(f"Tool '{name}' (normalized to '{normalized_name}') not found in registry. Falling back to 'search_web'.")
            normalized_name = "search_web"
            
        return self.registry.get(normalized_name, lambda *args, **kwargs: f"Tool {name} executed.")

    def execute_tool(self, name: str, args: Dict[str, Any]) -> Any:
        tool_func = self.get_tool(name)
        if isinstance(args, dict):
            return tool_func(**args)
        elif isinstance(args, list):
            return tool_func(*args)
        else:
            return tool_func(args)