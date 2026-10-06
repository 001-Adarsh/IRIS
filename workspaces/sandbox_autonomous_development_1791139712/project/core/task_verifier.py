from pathlib import Path
import re


class IRISTaskVerifier:

    def verify(self, objective, tool_name, arguments, result):
        objective = str(objective or "").strip().lower()
        tool_name = str(tool_name or "").strip().lower()
        result_text = str(result or "").strip()

        if not result_text:
            return {
                "passed": False,
                "reason": "Tool returned no usable result.",
                "objective": objective
            }

        # Development tasks
        development_terms = (
            "develop ",
            "build ",
            "implement ",
            "create a feature",
            "add a feature",
            "improve iris",
            "upgrade iris",
            "modify iris",
            "change iris"
        )

        if (
            objective in {
                "develop",
                "build",
                "implement",
                "improve iris",
                "upgrade iris",
                "modify iris",
                "change iris"
            }
            or any(objective.startswith(x) for x in development_terms)
        ):
            if "development complete" in result_text.lower():
                return {
                    "passed": True,
                    "reason": "Development workflow reported successful completion.",
                    "objective": objective
                }

            if "tests passed: true" in result_text.lower():
                return {
                    "passed": True,
                    "reason": "Development sandbox tests passed.",
                    "objective": objective
                }

            return {
                "passed": False,
                "reason": "Development result does not confirm successful completion.",
                "objective": objective
            }

        # RAM / physical memory
        if any(word in objective for word in (
            "ram",
            "memory",
            "physical memory"
        )):
            match = re.search(
                r"TotalPhysicalMemory\s*\r?\n[-\s]+\r?\n\s*(\d+)",
                result_text,
                re.IGNORECASE
            )

            if not match:
                match = re.search(
                    r"TotalPhysicalMemory.*?(\d{7,})",
                    result_text,
                    re.IGNORECASE | re.DOTALL
                )

            if not match:
                return {
                    "passed": False,
                    "reason": (
                        "RAM objective was not verified because "
                        "TotalPhysicalMemory did not contain a valid numeric value."
                    ),
                    "objective": objective
                }

            try:
                bytes_value = int(match.group(1))
            except (TypeError, ValueError):
                return {
                    "passed": False,
                    "reason": "Physical RAM value could not be converted to an integer.",
                    "objective": objective
                }

            if bytes_value <= 0:
                return {
                    "passed": False,
                    "reason": "Physical RAM value must be greater than zero.",
                    "objective": objective
                }

            gib = bytes_value / (1024 ** 3)
            gb = bytes_value / (1000 ** 3)

            return {
                "passed": True,
                "verified": True,
                "reason": "Physical RAM was independently verified from TotalPhysicalMemory.",
                "objective": objective,
                "bytes": bytes_value,
                "gb": round(gb, 2),
                "gib": round(gib, 2)
            }

        # File creation
        if any(word in objective for word in (
            "create file",
            "create a file",
            "make a file",
            "write file",
            "write a file"
        )):
            path = arguments.get("path") if isinstance(arguments, dict) else None

            if path:
                target = Path(path).expanduser()

                if target.is_file():
                    return {
                        "passed": True,
                        "reason": f"Requested file exists: {target}",
                        "objective": objective
                    }

            if "created" in result_text.lower():
                return {
                    "passed": True,
                    "reason": "Tool reported file creation.",
                    "objective": objective
                }

            return {
                "passed": False,
                "reason": "Requested file creation was not independently verified.",
                "objective": objective
            }

        # Web research
        if tool_name in {
            "search_web",
            "read_webpage"
        }:
            if any(marker in result_text.lower() for marker in (
                "http://",
                "https://",
                "source",
                "evidence"
            )):
                return {
                    "passed": True,
                    "reason": "Research produced source/evidence information.",
                    "objective": objective
                }

            return {
                "passed": False,
                "reason": "Research result does not contain identifiable source evidence.",
                "objective": objective
            }

        # PowerShell
        if tool_name == "run_powershell":
            if "powershell error:" in result_text.lower():
                return {
                    "passed": False,
                    "reason": "PowerShell reported an error.",
                    "objective": objective
                }

            if "command blocked" in result_text.lower():
                return {
                    "passed": False,
                    "reason": "PowerShell command was blocked by the safety policy.",
                    "objective": objective
                }

            return {
                "passed": True,
                "reason": "PowerShell completed without an error or safety block.",
                "objective": objective
            }

        # Python
        if tool_name == "run_python":
            if any(marker in result_text.lower() for marker in (
                "traceback",
                "syntaxerror",
                "exception:"
            )):
                return {
                    "passed": False,
                    "reason": "Python execution reported an error.",
                    "objective": objective
                }

            return {
                "passed": True,
                "reason": "Python execution completed without a detected error.",
                "objective": objective
            }

        # Memory operations
        if tool_name in {
            "remember",
            "search_memory",
            "list_memories"
        }:
            return {
                "passed": True,
                "reason": "Memory operation returned usable data.",
                "objective": objective
            }

        # Safe fallback
        return {
            "passed": True,
            "reason": "Tool returned usable output; no task-specific verification rule matched.",
            "objective": objective
        }
