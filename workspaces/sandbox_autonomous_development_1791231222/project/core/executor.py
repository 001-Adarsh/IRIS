class IRISExecutor:

    SAFE_TOOLS = {
        "get_owner_profile",
        "search_web",
        "read_webpage",
        "run_powershell",
        "run_python",
        "create_file",
        "read_file",
        "list_files",
        "read_document",
        "create_excel",
        "remember",
        "search_memory",
        "list_memories",
        "audit_website",
    }

    def __init__(self, tool_manager):
        self.tools = tool_manager

    def execute_plan(self, plan: dict):

        selected_tools = plan.get("tools", [])

        if not selected_tools:
            return {
                "success": False,
                "results": [],
                "message": "No tools were selected by the planner."
            }

        results = []

        for tool in selected_tools:

            if not isinstance(tool, dict):
                continue

            tool_name = tool.get("name")
            arguments = tool.get("arguments", {})

            if not tool_name:
                continue

            if tool_name not in self.SAFE_TOOLS:
                results.append({
                    "tool": tool_name,
                    "success": False,
                    "result": "Tool is not permitted for autonomous execution."
                })
                continue

            available = self.tools.list_tools()
            if tool_name not in available:
                print(f"[Executor Debug] Tool '{tool_name}' rejected! Not in available list: {available}")
                results.append({
                    "tool": tool_name,
                    "success": False,
                    "result": f"Tool '{tool_name}' is not available."
                })
                continue

            if not isinstance(arguments, dict):
                results.append({
                    "tool": tool_name,
                    "success": False,
                    "result": "Tool arguments must be an object."
                })
                continue

            try:

                result = self.tools.execute(tool_name, **arguments)
                if isinstance(result, str) and ("error" in result.lower() or "exception" in result.lower()):
                    print("[Executor Debug]", tool_name, "returned error:", result)
                results.append({
                    "tool": tool_name,
                    "success": True,
                    "result": result
                })

            except Exception as e:
                import traceback
                print(f"[Executor Error] {tool_name} failed: {type(e).__name__}: {e}")
                traceback.print_exc()
                results.append({
                    "tool": tool_name,
                    "success": False,
                    "result": f"{type(e).__name__}: {e}"
                })

        success = any(
            item["success"]
            for item in results
        )

        return {
            "success": success,
            "task_type": plan.get("task_type", "unknown"),
            "results": results
        }
