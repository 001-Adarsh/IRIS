import inspect
import logging
from typing import Any, Callable, Dict, List

from core.memory import Memory
from tools.excel_tool import create_excel
from tools.file_tool import create_file, list_files, read_file
from tools.document_writer import create_document
from tools.powershell_tool import run_powershell
from tools.python_tool import run_python
from tools.web_audit_tool import audit_website
from tools.web_reader import read_webpage
from tools.web_tool import search_web

logger = logging.getLogger("ToolManager")


class ToolManager:

    def __init__(self):
        self.memory = Memory()
        self.registry: Dict[str, Callable[..., Any]] = {
            "create_excel": create_excel,
            "create_document": create_document,
            "create_file": create_file,
            "read_file": read_file,
            "list_files": list_files,
            "search_web": search_web,
            "read_webpage": read_webpage,
            "run_powershell": run_powershell,
            "run_python": run_python,
            "audit_website": audit_website,
            "remember": self.memory.save,
            "search_memory": self.memory.search,
            "list_memories": self.memory.list_memories,
        }
        self.aliases: Dict[str, str] = {
            "web_tool": "search_web",
            "search": "search_web",
            "web": "search_web",
            "google": "search_web",
            "excel": "create_excel",
            "create_excel": "create_excel",
            "document": "create_document",
            "docx": "create_document",
            "create_docx": "create_document",
            "powershell": "run_powershell",
            "cmd": "run_powershell",
            "python": "run_python",
            "py": "run_python",
            "remember": "remember",
            "search_memory": "search_memory",
            "list_memories": "list_memories",
        }
        self._load_tools()

    def _load_tools(self):
        """Attach runtime tool implementations when available while keeping the core registry authoritative."""
        try:
            import tools.document_tool as doc_tool

            if hasattr(doc_tool, "read_document"):
                self.registry["read_document"] = doc_tool.read_document
        except Exception:
            pass

        self.registry.setdefault("search_web", self._live_ddg_search)
        self.registry.setdefault("read_webpage", self._live_webpage_reader)
        self.registry.setdefault("run_powershell", self._fallback_powershell)
        self.registry.setdefault("run_python", self._fallback_python)
        self.tools = self.registry

    def _live_ddg_search(
        self, query: str = "", max_results: int = 5, **kwargs
    ) -> List[Dict[str, str]]:
        """Native DuckDuckGo search returning structured evidence entries for agent workflows."""
        q = query or kwargs.get("q", "")
        results: List[Dict[str, str]] = []
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                raw_results = list(ddgs.text(q, max_results=max_results))
            for r in raw_results:
                url = str(r.get("href") or r.get("url") or "").strip()
                title = str(r.get("title") or "").strip()
                snippet = str(r.get("body") or "").strip()
                if url:
                    results.append({"title": title, "url": url, "snippet": snippet})
        except Exception:
            return []
        return results

    def _live_webpage_reader(self, url: str, **kwargs) -> str:
        try:
            import requests

            resp = requests.get(url, timeout=15, headers={"User-Agent": "IRIS/1.0"})
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            return f"Web reader error: {e}"

    def _fallback_powershell(self, command: str, **kwargs) -> str:
        return f"PowerShell execution unavailable: {command[:80]}"

    def _fallback_python(self, code: str, **kwargs) -> str:
        return f"Python execution unavailable: {code[:80]}"

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