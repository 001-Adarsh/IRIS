import importlib
import os
from typing import Dict, Any, List

class ToolManager:
    """
    Manages and registers available system tools including the high‑speed system diagnostic tool.
    """
    def __init__(self):
        self.tools: Dict[str, Any] = {}
        self.load_tools()

    def register_tool(self, name: str, tool_instance: Any):
        self.tools[name] = tool_instance

    def get_tool(self, name: str) -> Any:
        return self.tools.get(name)

    def list_tools(self) -> List[str]:
        return list(self.tools.keys())

    def load_tools(self):
        """
        Load all tool modules in the `tools` package. The diagnostic tool is imported
        explicitly to guarantee its registration even if the file name changes.
        """
        # Explicitly load the diagnostics tool
        try:
            from tools.diagnostics import SystemDiagnosticsTool
            diag_tool = SystemDiagnosticsTool()
            self.register_tool(diag_tool.name, diag_tool)
        except Exception as e:
            print(f"Warning: Could not load SystemDiagnosticsTool: {e}")

        # Dynamically load any other .py files in the tools directory
        tools_dir = os.path.dirname(__file__)
        for filename in os.listdir(tools_dir):
            if (
                filename.endswith(".py")
                and filename not in ["__init__.py", "manager.py", "diagnostics.py"]
            ):
                module_name = filename[:-3]
                try:
                    module = importlib.import_module(f"tools.{module_name}")
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if hasattr(attr, "name") and hasattr(attr, "run"):
                            self.register_tool(attr.name, attr())
                except Exception:
                    # Silently ignore modules that fail to load
                    pass

# Singleton instance of ToolManager
tool_manager = ToolManager()