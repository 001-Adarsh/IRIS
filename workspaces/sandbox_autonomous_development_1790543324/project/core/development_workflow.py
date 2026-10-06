import ast
import json
import re
import shutil
import time
from pathlib import Path
from typing import Dict, Any, List

from core.router import AIRouter
from core.sandbox import IRISSandbox
from core.test_runner import IRISTestRunner
from core.upgrade_manager import IRISUpgradeManager


class IRISDevelopmentWorkflow:
    """Autonomous self-patching, cognitive development, and zero-touch deployment engine."""

    def __init__(self, project_root=None):
        self.project_root = Path(
            project_root or Path(__file__).resolve().parent.parent
        )
        self.router = AIRouter()
        self.sandbox = IRISSandbox()
        self.upgrades = IRISUpgradeManager(self.project_root)

    def _create_sandbox_snapshot(self, label: str = "autonomous_development") -> Dict[str, str]:
        """Creates an isolated, clean copy of production files without copying old sandbox debris."""
        ts = int(time.time())
        workspace = self.project_root / "workspaces" / f"sandbox_{label}_{ts}"
        project_path = workspace / "project"
        project_path.mkdir(parents=True, exist_ok=True)

        # STRICT EXCLUSIONS: Prevent copying legacy broken sandboxes or virtualenvs
        ignored_names = {
            ".venv", "venv", "env", ".git", "workspaces", "sandbox",
            "__pycache__", "upgrade_proposals", ".idea", ".vscode",
            "node_modules", ".pytest_cache", "data"
        }

        for item in self.project_root.iterdir():
            if item.name.lower() in ignored_names or item.name.startswith("."):
                continue
            dest = project_path / item.name
            try:
                if item.is_dir():
                    shutil.copytree(
                        item,
                        dest,
                        ignore=shutil.ignore_patterns("*.pyc", "__pycache__", "*.tmp", "*.log")
                    )
                else:
                    shutil.copy2(item, dest)
            except Exception as e:
                print(f"[Sandbox Snapshot Warning]: {item.name}: {e}")

        return {
            "workspace": str(workspace),
            "project": str(project_path)
        }

    def _get_project_context(self, project_path: Path) -> str:
        """Inspects module signatures and tool registrations."""
        core_dir = project_path / "core"
        tools_dir = project_path / "tools"
        context_snippets = []

        if core_dir.exists():
            for py_file in core_dir.glob("*.py"):
                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8"))
                    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
                    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
                    context_snippets.append(
                        f"core/{py_file.name}: Classes={classes}, Functions={funcs[:6]}"
                    )
                except Exception:
                    pass

        if tools_dir.exists():
            tool_files = [f.name for f in tools_dir.glob("*.py") if not f.name.startswith("__")]
            context_snippets.append(f"tools/: Existing tools={tool_files}")

        return "\n".join(context_snippets)

    def _planning_prompt(self, objective: str, project_files: List[str], project_context: str) -> str:
        files_text = "\n".join(project_files[:100])

        return f"""You are the IRIS Autonomous Core Developer.
Your objective is to implement, test, and self-patch this request into the codebase.

OBJECTIVE:
{objective}

ACTIVE REPOSITORY SIGNATURES:
{project_context}

AVAILABLE PROJECT FILES:
{files_text}

CRITICAL RULES FOR AUTONOMOUS DEPLOYMENT:
1. Production files must never be broken; all changes are sandbox-tested first.
2. If adding a new tool under tools/<tool_name>.py, you MUST ALSO update tools/manager.py to import and register it!
3. NEVER touch .env files, credentials, or production databases.
4. "action" MUST be either "create" (new file) OR "replace" (existing file).
5. Code in "content" must be complete, production-ready, and syntactically valid Python.
6. Escape double quotes and backslashes properly so the JSON parser accepts it.
7. Return ONLY a single raw JSON object. Do not wrap in markdown fences or add conversational commentary.

JSON SCHEMA:
{{
  "objective": "short summary",
  "reasoning_summary": "architectural changes made",
  "changes": [
    {{
      "path": "tools/diagnostics.py",
      "action": "create",
      "content": "<valid escaped python code>"
    }},
    {{
      "path": "tools/manager.py",
      "action": "replace",
      "content": "<valid escaped python code>"
    }}
  ],
  "tests": [
    "verify test scenario"
  ]
}}"""

    def _parse_plan(self, response: str) -> Dict[str, Any]:
        """Sanitizes raw reasoning blocks and safely parses JSON code objects."""
        cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'\s*```$', '', cleaned, flags=re.MULTILINE).strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end == -1:
            raise ValueError("Development planner did not return a valid JSON block.")

        json_str = cleaned[start:end + 1]

        try:
            data = json.loads(json_str, strict=False)
        except json.JSONDecodeError:
            sanitized = json_str.replace('\\', '\\\\')
            sanitized = sanitized.replace('\\\\n', '\\n').replace('\\\\"', '\\"')
            try:
                data = json.loads(sanitized, strict=False)
            except Exception as e:
                raise ValueError(f"Failed to parse development JSON: {e}")

        if not isinstance(data, dict):
            raise ValueError("Parsed output is not a JSON dictionary.")

        data.setdefault("changes", [])
        data.setdefault("tests", [])

        for change in data.get("changes", []):
            act = str(change.get("action", "")).lower().strip()
            if "create" in act and "replace" not in act:
                change["action"] = "create"
            else:
                change["action"] = "replace"

        return data

    def _safe_change_path(self, project_path: Path, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise PermissionError(f"Security: Path '{relative_path}' escapes sandbox boundaries.")

        target = (Path(project_path) / relative).resolve()
        root = Path(project_path).resolve()

        try:
            target.relative_to(root)
        except ValueError:
            raise PermissionError(f"Security: Target path '{target}' escapes sandbox root.")

        if target.name.startswith(".env"):
            raise PermissionError("Security: Environment files are protected from modification.")

        return target

    def develop(self, objective: str, auto_deploy: bool = True) -> Dict[str, Any]:
        """Main autonomous pipeline: Planning -> Sandbox -> AST Verify -> Test -> Auto-Deploy."""
        print("\n=== IRIS AUTONOMOUS SELF-DEVELOPMENT ===")
        print(f"Objective: {objective}")

        # Step 1: Create isolated sandbox snapshot
        snapshot = self._create_sandbox_snapshot("autonomous_development")
        workspace = Path(snapshot["workspace"])
        project_path = Path(snapshot["project"])

        print(f"Sandbox Isolated: {workspace}")
        print("Production State: PROTECTED")

        project_files = [
            str(path.relative_to(project_path))
            for path in project_path.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        ]

        print("\n[IRIS Dev Engine]: Analyzing project architecture and symbols...")
        project_context = self._get_project_context(project_path)
        prompt_text = self._planning_prompt(objective, project_files, project_context)

        # Step 2: Query Groq First -> Cascade to Council
        response = ""
        providers_order = ["groq", "openrouter", "gemini", "ollama"]
        for p_name in providers_order:
            prov = self.router.providers.get(p_name)
            if prov and prov.available:
                try:
                    res = prov.generate(prompt_text, max_tokens=4096)
                    if res and not str(res).lower().startswith("error"):
                        response = str(res).strip()
                        print(f"[IRIS Dev Engine]: Architecture planned using {p_name.upper()}.")
                        break
                except Exception:
                    continue

        if not response:
            planning_result = self.router.compare(prompt_text)
            response = planning_result.get("final", "").strip()

        if not response:
            return {
                "success": False,
                "reason": "Failed to generate development patch from AI Council.",
                "production_modified": False
            }

        # Step 3: Parse and validate plan
        try:
            plan = self._parse_plan(response)
        except Exception as e:
            return {
                "success": False,
                "reason": f"Plan parsing error: {e}",
                "production_modified": False
            }

        changes = plan.get("changes", [])
        if not changes:
            return {
                "success": False,
                "reason": "AI Council proposed zero changes.",
                "production_modified": False
            }

        print(f"\n[IRIS Dev Engine]: Inspecting {len(changes)} proposed code modification(s)...")
        applied_changes = []

        # Step 4: Apply changes with AST Pre-Flight Syntax Check
        for change in changes:
            if not isinstance(change, dict):
                continue

            rel_path = change.get("path")
            action = change.get("action")
            content = change.get("content")

            if not rel_path or not isinstance(content, str):
                continue

            # AST Syntax Verification for Python files
            if rel_path.endswith(".py"):
                try:
                    ast.parse(content)
                except SyntaxError as se:
                    print(f"[AST Syntax Error in {rel_path}]: {se} -> Rejecting unsafe change.")
                    continue

            try:
                target = self._safe_change_path(project_path, rel_path)
            except Exception as e:
                print(f"[Security Block]: {rel_path} - {e}")
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            applied_changes.append(rel_path)
            print(f"  ✓ Applied to Sandbox: {rel_path}")

        if not applied_changes:
            return {
                "success": False,
                "reason": "All proposed changes failed syntax or security checks.",
                "workspace": str(workspace),
                "production_modified": False
            }

        # Step 5: Execute Sandbox Test Suite with Granular Error Capture
        print("\n=== IRIS SANDBOX AUTOMATED TEST SUITE ===")
        tester = IRISTestRunner(project_path)
        test_results = tester.run()

        failure_reasons = []
        if not test_results.get("passed", False):
            for t in test_results.get("tests", []):
                if not t.get("passed", False):
                    out = t.get("stdout") or t.get("stderr") or ""
                    clean_err = out.strip().splitlines()[-1] if out.strip() else "Test assertion failed"
                    failure_reasons.append(f"{t.get('type')}: {clean_err}")

        error_summary = "; ".join(failure_reasons) if failure_reasons else "Unknown error"
        print(f"Test Suite Results: {'PASSED' if test_results['passed'] else 'FAILED'}")
        if not test_results["passed"]:
            print(f"[Test Error Detail]: {error_summary}")

        # Step 6: Create Proposal Record
        proposal = self.upgrades.create_proposal(
            objective=objective,
            workspace=workspace,
            changes=applied_changes,
            test_results=test_results
        )

        # Step 7: Zero-Touch Auto-Deployment on Success
        production_modified = False
        status_msg = "APPROVAL_REQUIRED"

        if test_results.get("passed", False) and auto_deploy:
            print("\n[IRIS Auto-Deployer]: Tests passed. Deploying patch directly into production...")
            try:
                from core.development_engine import IRISDevelopmentEngine
                engine = IRISDevelopmentEngine()
                deploy_res = engine.approve_and_deploy(proposal.get("proposal_file"))
                production_modified = deploy_res.get("production_modified", True)
                status_msg = "AUTONOMOUSLY_DEPLOYED"
                print("✓ Production hot-swap completed successfully.")
            except Exception as de:
                print(f"[Deployment Warning]: Could not auto-deploy: {de}")

        return {
            "success": test_results["passed"],
            "status": status_msg,
            "objective": objective,
            "workspace": str(workspace),
            "project": str(project_path),
            "changes": applied_changes,
            "tests": test_results,
            "reason": error_summary if not test_results["passed"] else "",
            "proposal": proposal,
            "production_modified": production_modified,
            "auto_deployed": production_modified
        }