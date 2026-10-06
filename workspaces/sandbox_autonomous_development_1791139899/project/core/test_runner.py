from pathlib import Path
import subprocess
import sys


class IRISTestRunner:

    TEST_TIMEOUT = 120

    def __init__(self, project_path):
        self.project_path = Path(
            project_path
        ).resolve()

    def compile_python(self):

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "compileall",
                "-q",
                str(self.project_path)
            ],
            cwd=str(self.project_path),
            capture_output=True,
            text=True,
            timeout=self.TEST_TIMEOUT
        )

        return {
            "passed": result.returncode == 0,
            "type": "compile",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode
        }

    def discover_unittest(self):

        tests_dir = self.project_path / "tests"

        if not tests_dir.is_dir():
            return {
                "passed": True,
                "type": "unittest",
                "skipped": True,
                "reason": "No tests directory found.",
                "stdout": "",
                "stderr": "",
                "returncode": 0
            }

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(tests_dir),
                "-v"
            ],
            cwd=str(self.project_path),
            capture_output=True,
            text=True,
            timeout=self.TEST_TIMEOUT
        )

        return {
            "passed": result.returncode == 0,
            "type": "unittest",
            "skipped": False,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode
        }

    def run(self):

        tests = []

        # ----------------------------------------------------
        # TEST 1: PYTHON COMPILATION
        # ----------------------------------------------------

        try:
            compile_result = self.compile_python()
            tests.append(compile_result)

        except subprocess.TimeoutExpired:
            tests.append({
                "passed": False,
                "type": "compile",
                "error": "Compilation timed out."
            })

        # Stop immediately if compilation failed.
        if not tests[-1]["passed"]:

            return {
                "passed": False,
                "tests": tests
            }

        # ----------------------------------------------------
        # TEST 2: UNITTEST DISCOVERY
        # ----------------------------------------------------

        try:
            unittest_result = (
                self.discover_unittest()
            )

            tests.append(
                unittest_result
            )

        except subprocess.TimeoutExpired:
            tests.append({
                "passed": False,
                "type": "unittest",
                "error": (
                    "Unit tests timed out "
                    f"after {self.TEST_TIMEOUT} seconds."
                )
            })

        overall_passed = all(
            test.get("passed", False)
            for test in tests
        )

        return {
            "passed": overall_passed,
            "tests": tests
        }
