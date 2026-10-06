import logging
import os
import re
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("TaskVerifier")


class IRISTaskVerifier:
    """
    Advanced verification engine evaluating tool executions against user objectives.
    Prevents false-positive hardware traps, validates deliverables, and checks evidence blocks.
    """

    def verify(
        self,
        objective: str,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Any,
    ) -> Dict[str, Any]:
        obj_text = str(objective or "").strip().lower()
        tool = str(tool_name or "").strip().lower()
        res_str = str(result or "").strip()

        if not res_str:
            return {
                "passed": False,
                "reason": "Tool returned no usable result or empty output.",
                "objective": objective,
            }

        # -------------------------------------------------------------
        # 1. AUTONOMOUS DEVELOPMENT TASKS
        # -------------------------------------------------------------
        dev_indicators = (
            "develop",
            "self-develop",
            "patch",
            "autonomously",
            "refactor",
            "modify core",
        )
        if any(term in obj_text for term in dev_indicators) or tool in {
            "develop",
            "auto_develop",
        }:
            res_lower = res_str.lower()
            if (
                "development complete" in res_lower
                or "hot-swap completed" in res_lower
                or "tests passed: true" in res_lower
                or "autonomously_deployed" in res_lower
            ):
                return {
                    "passed": True,
                    "reason": "Autonomous development workflow confirmed successful deployment.",
                    "objective": objective,
                }
            if "plan parsing error" in res_lower or "failed" in res_lower:
                return {
                    "passed": False,
                    "reason": "Autonomous development failed during execution or test verification.",
                    "objective": objective,
                }

        # -------------------------------------------------------------
        # 2. EXCEL & DELIVERABLE SPREADSHEET VERIFICATION
        # -------------------------------------------------------------
        if tool in {"create_excel", "excel_tool", "make_excel"} or any(
            w in obj_text for w in ["excel", ".xlsx", "spreadsheet"]
        ):
            # Inspect argument paths or result output
            target_path = None
            if isinstance(arguments, dict):
                target_path = (
                    arguments.get("filename")
                    or arguments.get("file")
                    or arguments.get("path")
                )

            # Check if physical file exists on disk
            if target_path and Path(target_path).is_file():
                size = os.path.getsize(target_path)
                if size > 0:
                    return {
                        "passed": True,
                        "verified": True,
                        "reason": f"Excel deliverable successfully verified on disk ({target_path}, {size} bytes).",
                        "objective": objective,
                    }

            # Check textual confirmation from tool
            if (
                "created" in res_str.lower()
                or ".xlsx" in res_str.lower()
                or "success" in res_str.lower()
            ):
                return {
                    "passed": True,
                    "reason": "Excel creation confirmed by execution handler.",
                    "objective": objective,
                }

            # If tool was explicitly create_excel and didn't crash
            if tool in {"create_excel", "excel_tool"}:
                return {
                    "passed": True,
                    "reason": "Excel generation completed successfully.",
                    "objective": objective,
                }

        # -------------------------------------------------------------
        # 3. HARDWARE TELEMETRY / RAM CHECKS (Strict Word Boundaries)
        # -------------------------------------------------------------
        # Uses regex \b boundaries so 'program', 'framing', 'parameter' do not trigger this
        is_ram_query = bool(
            re.search(
                r"\b(ram|physical memory|total memory|system memory)\b",
                obj_text,
            )
        )
        if (
            is_ram_query
            and "search_web" not in tool
            and "web" not in obj_text
            and "ipo" not in obj_text
        ):
            match = re.search(
                r"TotalPhysicalMemory\s*\r?\n[-\s]+\r?\n\s*(\d+)",
                res_str,
                re.IGNORECASE,
            )
            if not match:
                match = re.search(
                    r"TotalPhysicalMemory.*?(\d{7,})",
                    res_str,
                    re.IGNORECASE | re.DOTALL,
                )

            if match:
                try:
                    bytes_val = int(match.group(1))
                    if bytes_val > 0:
                        gb = round(bytes_val / (1000**3), 2)
                        gib = round(bytes_val / (1024**3), 2)
                        return {
                            "passed": True,
                            "verified": True,
                            "reason": f"Physical RAM verified: {gib} GiB ({bytes_val} bytes).",
                            "objective": objective,
                            "bytes": bytes_val,
                            "gb": gb,
                            "gib": gib,
                        }
                except (ValueError, TypeError):
                    pass

            return {
                "passed": False,
                "reason": "Hardware check could not extract a numeric TotalPhysicalMemory byte count.",
                "objective": objective,
            }

        # -------------------------------------------------------------
        # 4. WEB SEARCH & RESEARCH EVIDENCE
        # -------------------------------------------------------------
        if tool in {"search_web", "web_tool", "search", "read_webpage"}:
            res_lower = res_str.lower()
            has_source = any(
                k in res_lower
                for k in [
                    "source:",
                    "url:",
                    "http://",
                    "https://",
                    "findings:",
                    "title:",
                    "headline",
                ]
            )
            if has_source and len(res_str) > 40:
                return {
                    "passed": True,
                    "reason": "Research produced verifiable source and evidence blocks.",
                    "objective": objective,
                }
            if "no search results" in res_lower or "error" in res_lower:
                return {
                    "passed": False,
                    "reason": "Web query returned no usable data.",
                    "objective": objective,
                }
            return {
                "passed": True,
                "reason": "Web information retrieved.",
                "objective": objective,
            }

        # -------------------------------------------------------------
        # 5. GENERAL FILE OPERATIONS
        # -------------------------------------------------------------
        if any(
            op in obj_text
            for op in ["create file", "write file", "save file", "make a file"]
        ):
            path = (
                arguments.get("path") or arguments.get("filename")
                if isinstance(arguments, dict)
                else None
            )
            if path and Path(path).expanduser().is_file():
                return {
                    "passed": True,
                    "reason": f"File verified on disk: {path}",
                    "objective": objective,
                }
            if "created" in res_str.lower() or "saved" in res_str.lower():
                return {
                    "passed": True,
                    "reason": "File creation confirmed by tool output.",
                    "objective": objective,
                }

        # -------------------------------------------------------------
        # 6. SYSTEM RUNNERS (PowerShell / Python)
        # -------------------------------------------------------------
        if tool in {"run_powershell", "powershell"}:
            if "powershell error:" in res_str.lower():
                return {
                    "passed": False,
                    "reason": "PowerShell command returned an error.",
                    "objective": objective,
                }
            if "command blocked" in res_str.lower():
                return {
                    "passed": False,
                    "reason": "Command blocked by security policy.",
                    "objective": objective,
                }
            return {
                "passed": True,
                "reason": "PowerShell executed without error.",
                "objective": objective,
            }

        if tool in {"run_python", "python"}:
            if any(
                err in res_str.lower()
                for err in ["traceback", "syntaxerror", "exception:"]
            ):
                return {
                    "passed": False,
                    "reason": "Python execution threw an unhandled exception.",
                    "objective": objective,
                }
            return {
                "passed": True,
                "reason": "Python execution completed successfully.",
                "objective": objective,
            }

        # -------------------------------------------------------------
        # 7. DEFAULT PASSTHROUGH
        # -------------------------------------------------------------
        return {
            "passed": True,
            "reason": "Execution completed without failure flags.",
            "objective": objective,
        }