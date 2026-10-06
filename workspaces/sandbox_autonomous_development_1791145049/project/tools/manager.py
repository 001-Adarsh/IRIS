import inspect
import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("ToolManager")


class ToolManager:

    def __init__(self):
        self.registry: Dict[str, Callable[..., Any]] = {}
        self.aliases: Dict[str, str] = {
            "web_tool": "search_web",
            "search": "search_web",
            "web": "search_web",
            "google": "search_web",
            "powershell": "run_powershell",
            "cmd": "run_powershell",
            "python": "run_python",
            "py": "run_python",
            "remember": "remember",
            "search_memory": "search_memory",
        }
        self._load_tools()

    def _load_tools(self):
        # 1. Web Tool Binding
        try:
            import tools.web_tool as wt

            if hasattr(wt, "WebTool"):
                inst = wt.WebTool()
                handler = getattr(inst, "search", getattr(inst, "run", None))
            elif hasattr(wt, "search_web"):
                handler = wt.search_web
            elif hasattr(wt, "search"):
                handler = wt.search
            elif hasattr(wt, "run"):
                handler = wt.run
            else:
                handler = None

            if handler:
                self.registry["search_web"] = handler
            else:
                self.registry["search_web"] = self._live_ddg_search
        except Exception as e:
            logger.warning(f"Falling back to built-in search: {e}")
            self.registry["search_web"] = self._live_ddg_search

        # 2. PowerShell Tool Binding
        try:
            import tools.powershell_tool as pt

            if hasattr(pt, "PowerShellTool"):
                inst = pt.PowerShellTool()
                self.registry["run_powershell"] = getattr(
                    inst, "run", getattr(inst, "execute", None)
                )
            elif hasattr(pt, "run_powershell"):
                self.registry["run_powershell"] = pt.run_powershell
            elif hasattr(pt, "run"):
                self.registry["run_powershell"] = pt.run
        except Exception:
            pass

        # 3. Python Tool Binding
        try:
            import tools.python_tool as pyt

            if hasattr(pyt, "PythonTool"):
                inst = pyt.PythonTool()
                self.registry["run_python"] = getattr(
                    inst, "run", getattr(inst, "execute", None)
                )
            elif hasattr(pyt, "run_python"):
                self.registry["run_python"] = pyt.run_python
            elif hasattr(pyt, "run"):
                self.registry["run_python"] = pyt.run
        except Exception:
            pass

        self.tools = self.registry

    def _live_ddg_search(
        self, query: str = "", max_results: int = 5, **kwargs
    ):
        """Native DuckDuckGo search returning real verifiable evidence snippets."""
        q = query or kwargs.get("q", "")
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results = list(ddgs.text(q, max_results=max_results))
            if results:
                lines = []
                for r in results:
                    lines.append(
                        f"SOURCE: {r.get('title')}\nURL: {r.get('href')}\nFINDINGS: {r.get('body')}\n"
                    )
                return "\n".join(lines)
        except Exception as e:
            return f"Search execution failed: {e}"
        return "No search results returned."

    def list_tools(self) -> List[str]:
        return sorted(
            list(set(self.registry.keys()).union(self.aliases.keys()))
        )

    def get_tools(self) -> List[str]:
        return self.list_tools()

    def available_tools(self) -> List[str]:
        return self.list_tools()

    def execute(self, tool_name: str, **kwargs) -> Any:
        name = self.aliases.get(
            tool_name.lower().strip(), tool_name.lower().strip()
        )
        handler = self.registry.get(name)

        if not handler:
            if "search" in name or "web" in name:
                handler = self.registry.get("search_web")
            else:
                return f"[ToolManager Error]: Tool '{tool_name}' is not registered."

        if "query" not in kwargs and "q" in kwargs:
            kwargs["query"] = kwargs["q"]

        try:
            sig = inspect.signature(handler)
            if any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values()
            ):
                return handler(**kwargs)
            valid_args = {
                k: v for k, v in kwargs.items() if k in sig.parameters
            }
            return handler(**valid_args)
        except Exception as e:
            return f"[Execution Error in {tool_name}]: {e}"