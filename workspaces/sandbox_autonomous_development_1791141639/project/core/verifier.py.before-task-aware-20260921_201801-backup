class IRISVerifier:

    def verify(self, request: str, execution: dict) -> dict:

        if not execution:
            return {
                "verified": False,
                "complete": False,
                "reason": "No execution result was produced."
            }

        if not execution.get("success"):
            return {
                "verified": False,
                "complete": False,
                "reason": execution.get(
                    "message",
                    "Execution failed."
                )
            }

        results = execution.get("results", [])

        successful = [
            item
            for item in results
            if item.get("success")
            and item.get("result") is not None
        ]

        if not successful:
            return {
                "verified": False,
                "complete": False,
                "reason": "No successful tool result was produced."
            }

        combined = "\n".join(
            str(item.get("result", ""))
            for item in successful
        )

        if not combined.strip():
            return {
                "verified": False,
                "complete": False,
                "reason": "Tool returned empty output."
            }

        request_lower = request.lower()

        # RAM-specific semantic verification
        if (
            "ram" in request_lower
            or "memory" in request_lower
            or "physical memory" in request_lower
        ):
            if "totalphysicalmemory" in combined.lower():
                return {
                    "verified": True,
                    "complete": True,
                    "reason": "Installed physical memory was returned.",
                    "results_checked": len(successful)
                }

            if "total physical memory" in combined.lower():
                return {
                    "verified": True,
                    "complete": True,
                    "reason": "Physical memory information was returned.",
                    "results_checked": len(successful)
                }

            return {
                "verified": False,
                "complete": False,
                "reason": "The result did not contain identifiable physical memory information."
            }

        return {
            "verified": True,
            "complete": True,
            "reason": "At least one successful tool result contains usable output.",
            "results_checked": len(successful)
        }
