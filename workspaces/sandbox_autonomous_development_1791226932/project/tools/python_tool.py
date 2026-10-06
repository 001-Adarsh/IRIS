import subprocess
import sys
from pathlib import Path

PYTHON = sys.executable


def run_python(code: str) -> str:

    if not code.strip():
        return "Python code is empty."

    try:

        result = subprocess.run(
            [PYTHON, "-c", code],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=Path(__file__).resolve().parent.parent
        )

        if result.returncode != 0:
            return (
                "Python error:\n"
                f"{result.stderr.strip()}"
            )

        return result.stdout.strip()

    except subprocess.TimeoutExpired:
        return "Python execution timed out."

    except Exception as e:
        return f"Python tool error: {e}"
