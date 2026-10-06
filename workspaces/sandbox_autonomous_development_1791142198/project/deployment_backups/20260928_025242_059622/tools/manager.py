import inspect
from pathlib import Path
from typing import Dict, Any, List

from core.memory import Memory
from tools.web_audit_tool import audit_website
from tools.excel_tool import create_excel
from tools.file_tool import create_file, read_file, list_files
from tools.web_tool import search_web
from tools.web_reader import read_webpage
from tools.powershell_tool import run_powershell
from tools.python_tool import run_python
from tools.document_tool import read_document

from core.sandbox_tools import (
    sandbox_list_files,
    sandbox_read_file,
    sandbox_write_file,
    sandbox_create_file,
    sandbox_diff
)

from core.development_tools import (
    create_sandbox_workspace,
    test_sandbox_workspace,
    create_upgrade_proposal
)


def get_owner_profile(url: str = "") -> str:
    """Returns verified owner identity and profile metadata."""
    f = Path.home() / "IRIS" / "data" / "owner_profile.json"
    if f.exists():
        try:
            return f.read_text(encoding="utf-8")
        except Exception:
            pass
    return '{"name": "Adarsh Dwivedi", "role": "Creator & Lead Engineer"}'


class ToolManager:
    """Central registry and resilient execution manager for all IRIS capabilities."""

    def __init__(self):
        self.memory = Memory()

        # Primary Registry
        self.tools: Dict[str, Any] = {
            # Web & Research
            "search_web": search_web,
            "read_webpage": self._safe_read_webpage,
            "fetch_webpage": self._safe_read_webpage,
            "read_url": self._safe_read_webpage,
            "audit_website": audit_website,

            # System & Code Execution
            "run_powershell": run_powershell,
            "run_python": run_python,

            # File & Document Operations
            "create_excel": create_excel,
            "create_file": create_file,
            "read_file": read_file,
            "list_files": list_files,
            "read_document": read_document,

            # Memory & Identity
            "remember": self._safe_remember,
            "search_memory": self.memory.search,
            "list_memories": self.memory.list_memories,
            "get_owner_profile": get_owner_profile,

            # Sandbox & Autonomous Development
            "sandbox_list_files": sandbox_list_files,
            "sandbox_read_file": sandbox_read_file,
            "sandbox_write_file": sandbox_write_file,
            "sandbox_create_file": sandbox_create_file,
            "sandbox_diff": sandbox_diff,
            "create_sandbox_workspace": create_sandbox_workspace,
            "test_sandbox_workspace": test_sandbox_workspace,
            "create_upgrade_proposal": create_upgrade_proposal,
        }

    def _safe_read_webpage(self, **kwargs) -> str:
        """Adapts flexible LLM parameter names ('url', 'link', 'target_url') into read_webpage."""
        url = kwargs.get("url") or kwargs.get("link") or kwargs.get("target_url") or kwargs.get("web_url")
        if not url:
            return "Error: No target URL provided to read_webpage."
        
        try:
            # Check signature of underlying read_webpage tool
            sig = inspect.signature(read_webpage)
            if len(sig.parameters) == 1:
                return read_webpage(str(url).strip())
            return read_webpage(url=str(url).strip())
        except Exception as e:
            return f"Error reading webpage content: {e}"

    def _safe_remember(self, **kwargs) -> str:
        """Extracts text content across varied planner argument keys."""
        content = (
            kwargs.get("content")
            or kwargs.get("text")
            or kwargs.get("memory")
            or kwargs.get("note")
            or str(kwargs)
        )
        try:
            return self.memory.save(content)
        except TypeError:
            return self.memory.save("user_note", content)

    def list_tools(self) -> List[str]:
        """Returns all registered tool names."""
        return sorted(list(self.tools.keys()))

    def get_tool_descriptions(self) -> str:
        """Exports human/LLM-readable reference schemas for core tools."""
        return """
- search_web: arguments: {"query": "<search terms>", "max_results": 5}
  Purpose: Discover candidate links and summary snippets across the web.
- read_webpage: arguments: {"url": "<http_url>"}
  Purpose: Download and extract full article body text from a webpage URL.
- run_powershell: arguments: {"command": "<command>"}
  Purpose: Execute diagnostic or administration commands on the local machine.
- run_python: arguments: {"code": "<code>"}
  Purpose: Execute Python scripts for math, calculations, and structured data handling.
- create_excel: arguments: {"filename": "<name.xlsx>", "data": [...]}
  Purpose: Generate structured Excel workbooks.
- remember: arguments: {"content": "<fact to store>"}
  Purpose: Save facts, preferences, or tasks to persistent long-term memory.
- search_memory: arguments: {"query": "<search terms>"}
  Purpose: Recall stored long-term memories.
"""

    def execute(self, tool_name: str, **kwargs) -> Any:
        """Executes a registered tool with parameter filtering and defensive error catching."""
        if tool_name not in self.tools:
            return f"Tool '{tool_name}' is not available."

        target_fn = self.tools[tool_name]

        try:
            # Introspect tool signature to discard extraneous arguments sent by LLMs
            sig = inspect.signature(target_fn)
            accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

            if accepts_kwargs:
                return target_fn(**kwargs)

            # Filter only arguments accepted by the function signature
            valid_args = {k: v for k, v in kwargs.items() if k in sig.parameters}
            return target_fn(**valid_args)

        except Exception as e:
            return f"Tool '{tool_name}' execution error: {str(e)}"