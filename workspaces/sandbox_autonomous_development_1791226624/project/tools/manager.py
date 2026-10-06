import os
import importlib
import inspect

class ToolManager:
    """
    Manages loading, registering, and executing tools for the IRIS Agent.
    """
    def __init__(self):
        self.registry = {}
        self.aliases = {}
        self.load_default_tools()

    def register(self, name, func, aliases=None):
        self.registry[name] = func
        if aliases:
            for alias in aliases:
                self.aliases[alias] = name

    def get(self, name):
        if name in self.registry:
            return self.registry[name]
        if name in self.aliases:
            return self.registry[self.aliases[name]]
        return None

    def list_tools(self):
        return list(self.registry.keys()) + list(self.aliases.keys())

    def load_default_tools(self):
        # Register core built-in tools
        self.register("python", self._run_python, aliases=["run_python", "execute_python"])
        self.register("file_read", self._read_file, aliases=["read_file"])
        self.register("file_write", self._write_file, aliases=["write_file"])
        self.register("web_search", self._web_search, aliases=["search"])
        self.register("create_excel", self._create_excel, aliases=["excel_create", "generate_excel"])

    def _run_python(self, code="", **kwargs):
        """Execute Python code safely or within sandbox."""
        from tools.python_tool import PythonTool
        return PythonTool.execute(code, **kwargs)

    def _read_file(self, filepath="", **kwargs):
        """Read content from a file."""
        from tools.file_tool import FileTool
        return FileTool.read(filepath, **kwargs)

    def _write_file(self, filepath="", content="", **kwargs):
        """Write content to a file."""
        from tools.file_tool import FileTool
        return FileTool.write(filepath, content, **kwargs)

    def _web_search(self, query="", **kwargs):
        """Perform a web search."""
        from tools.web_tool import WebTool
        return WebTool.search(query, **kwargs)

    def _create_excel(self, filename="output.xlsx", content="", **kwargs):
        """Create physical .xlsx files."""
        try:
            from tools.excel_tool import ExcelTool
            if hasattr(ExcelTool, "create_excel"):
                return ExcelTool.create_excel(filename=filename, content=content, **kwargs)
            elif hasattr(ExcelTool, "create"):
                return ExcelTool.create(filename=filename, content=content, **kwargs)
        except Exception:
            pass

        # Fallback implementation using openpyxl or pandas if available, or basic file creation
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Sheet1"
            
            if isinstance(content, list):
                for row_idx, row_data in enumerate(content, 1):
                    if isinstance(row_data, list):
                        for col_idx, val in enumerate(row_data, 1):
                            ws.cell(row=row_idx, column=col_idx, value=val)
                    else:
                        ws.cell(row=row_idx, column=1, value=str(row_data))
            elif isinstance(content, str) and content.strip():
                # Simple text/CSV split fallback
                lines = content.strip().split("\n")
                for row_idx, line in enumerate(lines, 1):
                    cols = line.split(",")
                    for col_idx, val in enumerate(cols, 1):
                        ws.cell(row=row_idx, column=col_idx, value=val.strip())
            else:
                ws.cell(row=1, column=1, value="Output")

            wb.save(filename)
            return f"Successfully created Excel file: {filename}"
        except Exception as e:
            # Absolute fallback if openpyxl is not present
            with open(filename, "w", encoding="utf-8") as f:
                f.write(str(content))
            return f"Created file {filename} (Excel dependencies missing, fallback text used): {e}"

tool_manager = ToolManager()