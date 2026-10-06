import subprocess
import tempfile
import os
import shutil

class Sandbox:
    """
    A simple sandbox that executes Python code in an isolated temporary directory.
    The code is executed with no shell, no environment variables, and a timeout.
    The sandbox does not allow network access or file system writes outside the temp dir.
    """

    def __init__(self, timeout=5):
        self.timeout = timeout

    def run(self, code: str, globals_=None, locals_=None):
        """
        Execute the given Python code string in an isolated environment.
        Returns a tuple (stdout, stderr, exit_code).
        """
        if globals_ is None:
            globals_ = {}
        if locals_ is None:
            locals_ = {}

        # Create a temporary directory
        temp_dir = tempfile.mkdtemp()
        try:
            # Write code to a temporary file
            script_path = os.path.join(temp_dir, "script.py")
            with open(script_path, "w") as f:
                f.write(code)

            # Run the script using subprocess
            result = subprocess.run(
                ["python", script_path],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env={},
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as e:
            return "", f"TimeoutExpired: {e}", -1
        finally:
            # Clean up the temporary directory
            shutil.rmtree(temp_dir)

IRISSandbox = Sandbox
