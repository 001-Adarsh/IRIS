import ast
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.router import AIRouter
from core.sandbox import IRISSandbox
from core.test_runner import IRISTestRunner
from core.upgrade_manager import IRISUpgradeManager

logger = logging.getLogger("IRISDevelopment")


class IRISDevelopmentWorkflow:
    """Advanced autonomous self-patching and auto-deployment engine with multi-pattern extraction."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = Path(
            project_root or Path(__file__).resolve().parent.parent
        )
        self.router = AIRouter()
        self.sandbox = IRISSandbox()
        self.upgrades = IRISUpgradeManager(self.project_root)

    def _create_sandbox_snapshot(
        self, label: str = "autonomous_development"
    ) -> Dict[str, str]:
        """Creates an isolated clean copy of production files without copying old sandboxes."""
        ts = int(time.time())
        workspace = self.project_root / "workspaces" / f"sandbox_{label}_{ts}"
        project_path = workspace / "project"
        project_path.mkdir(parents=True, exist_ok=True)

        ignored_names = {
            ".venv", "venv", "env", ".git", "workspaces", "sandbox",
            "__pycache__", "upgrade_proposals", ".idea", ".vscode",
            "node_modules", ".pytest_cache", "data", "logs"
        }

        for item in self.project_root.iterdir():
            name_lower = item.name.lower()
            if name_lower in ignored_names or item.name.startswith((".", "sandbox_")):
                continue

            dest = project_path / item.name
            try:
                if item.is_dir():
                    shutil.copytree(
                        item,
                        dest,
                        ignore=shutil.ignore_patterns(
                            "*.pyc", "__pycache__", "*.tmp", "*.log", "sandbox_*"
                        )
                    )
                else:
                    shutil.copy2(item, dest)
            except Exception as e:
                logger.warning(f"Sandbox Snapshot warning for {item.name}: {e}")

        return {
            "workspace": str(workspace),
            "project": str(project_path)
        }

    def _get_project_context(self, project_path: Path) -> str:
        """Inspects core signatures and tools to provide grounding context."""
        core_dir = project_path / "core"
        tools_dir = project_path / "tools"
        context_snippets = []

        if core_dir.exists():
            for py_file in sorted(core_dir.glob("*.py")):
                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
                    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
                    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
                    context_snippets.append(
                        f"core/{py_file.name}: Classes={classes}, Functions={funcs[:6]}"
                    )
                except Exception:
                    pass

        if tools_dir.exists():
            tool_files = [f.name for f in sorted(tools_dir.glob("*.py")) if not f.name.startswith("__")]
            context_snippets.append(f"tools/: Active tool modules={tool_files}")

        return "\n".join(context_snippets)

    def _planning_prompt(
        self, objective: str, project_files: List[str], project_context: str
    ) -> str:
        files_text = "\n".join(project_files[:80])

        return (
            "You are the IRIS Autonomous Core Architect.\n"
            "Implement, test, and self-patch this objective into the codebase.\n\n"
            f"OBJECTIVE:\n{objective}\n\n"
            f"ACTIVE REPOSITORY CONTEXT:\n{project_context}\n\n"
            f"AVAILABLE REPOSITORY FILES:\n{files_text}\n\n"
            "CRITICAL RULES:\n"
            "1. Output clean, complete, production-ready Python files.\n"
            "2. Never emit partial snippets, pseudocode, or 'TODO' blocks.\n"
            "3. Never modify .env files, secrets, or credential stores.\n"
            "4. Enclose each modified or created file inside structured tags.\n\n"
            "FORMAT REQUIREMENTS:\n"
            "<<<FILE: path/to/target.py>>>\n"
            "```python\n"
            "# Complete working code here\n"
            "```\n"
            "<<<END_FILE>>>\n"
        )

    def _parse_plan(self, response: str) -> Dict[str, Any]:
        """
        Multi-pattern extractor supporting:
        1. Custom delimiters (<<<FILE: ...>>> ... <<<END_FILE>>>)
        2. Markdown header paths (### core/planner.py ... ```python ...)
        3. Inline comment paths (```python \n # filepath: ...)
        """
        changes = []
        found_paths = set()

        # Strategy 1: Explicit tag delimiters
        tag_pat = re.compile(
            r'<<<FILE:\s*([^\r\n>]+)>>>.*?(?:```(?:python)?\r?\n)?(.*?)(?:```)?\s*<<<END_FILE>>>',
            re.DOTALL | re.IGNORECASE
        )
        for match in tag_pat.finditer(response):
            rel_path = match.group(1).strip().strip("`'\" ")
            code = match.group(2).strip()
            # Clean possible markdown wrap within tag
            code = re.sub(r"^```[a-zA-Z0-9_-]*\r?\n", "", code)
            code = re.sub(r"\r?\n```$", "", code)
            if rel_path and code and rel_path not in found_paths:
                changes.append({"path": rel_path, "action": "replace", "content": code})
                found_paths.add(rel_path)

        # Strategy 2: Relaxed tag delimiters (without <<<ACTION: ...>>> requirement)
        if not changes:
            relaxed_pat = re.compile(
                r'<<<FILE:\s*([^\r\n>]+)>>>(?:.*?<<<ACTION:[^\r\n>]+>>>)?.*?(?:```(?:python)?\r?\n)(.*?)(?:\r?\n```)',
                re.DOTALL | re.IGNORECASE
            )
            for match in relaxed_pat.finditer(response):
                rel_path = match.group(1).strip().strip("`'\" ")
                code = match.group(2).strip()
                if rel_path and code and rel_path not in found_paths:
                    changes.append({"path": rel_path, "action": "replace", "content": code})
                    found_paths.add(rel_path)

        # Strategy 3: Markdown headings preceding code blocks
        if not changes:
            header_pat = re.compile(
                r'(?:###|\*\*|FILE:)\s*([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]+)\*?\*?[\s\S]*?```(?:python)?\r?\n(.*?)\r?\n```',
                re.DOTALL | re.IGNORECASE
            )
            for match in header_pat.finditer(response):
                rel_path = match.group(1).strip().strip("`'\" ")
                code = match.group(2).strip()
                if ("/" in rel_path or "\\" in rel_path or rel_path.endswith(".py")) and rel_path not in found_paths:
                    changes.append({"path": rel_path, "action": "replace", "content": code})
                    found_paths.add(rel_path)

        # Strategy 4: File comment inside opening code fence
        if not changes:
            comment_pat = re.compile(
                r'```(?:python)?\r?\n(?:#|//)\s*(?:filepath:|file:)?\s*([a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9]+)\r?\n(.*?)\r?\n```',
                re.DOTALL | re.IGNORECASE
            )
            for match in comment_pat.finditer(response):
                rel_path = match.group(1).strip().strip("`'\" ")
                code = match.group(2).strip()
                if ("/" in rel_path or "\\" in rel_path or rel_path.endswith(".py")) and rel_path not in found_paths:
                    changes.append({"path": rel_path, "action": "replace", "content": code})
                    found_paths.add(rel_path)

        if not changes:
            raise ValueError(
                "Development planner did not output structured file delimiters or extractable code blocks."
            )

        return {
            "objective": "Autonomous Patch",
            "changes": changes,
            "tests": ["AST compile and syntax check"]
        }

    def _safe_change_path(self, project_path: Path, relative_path: str) -> Path:
        """Enforces boundary checking to prevent directory traversal outside the sandbox."""
        clean_rel = relative_path.replace("\\", "/").strip().lstrip("/")
        relative = Path(clean_rel)

        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise PermissionError(f"Security: Path '{relative_path}' escapes sandbox boundaries.")

        target = (Path(project_path) / relative).resolve()
        root = Path(project_path).resolve()

        try:
            target.relative_to(root)
        except ValueError:
            raise PermissionError(f"Security: Target path '{target}' escapes sandbox root.")

        if target.name.startswith(".env"):
            raise PermissionError("Security: Environment files are protected from automated alteration.")

        return target

    def develop(self, objective: str, auto_deploy: bool = True) -> Dict[str, Any]:
        """Main autonomous pipeline: Isolation -> Council -> Verification -> Test -> Auto-Deploy."""
        print("\n=== IRIS AUTONOMOUS SELF-DEVELOPMENT ===")
        print(f"Objective: {objective}")

        # Step 1: Create isolated sandbox snapshot
        snapshot = self._create_sandbox_snapshot("autonomous_development")
        workspace = Path(snapshot["workspace"])
        project_path = Path(snapshot["project"])

        print(f"Sandbox Isolated: {workspace}")
        print("Production State: PROTECTED")

        project_files = [
            str(path.relative_to(project_path)).replace("\\", "/")
            for path in project_path.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        ]

        print("\n[IRIS Dev Engine]: Analyzing project architecture and symbols...")
        project_context = self._get_project_context(project_path)
        prompt_text = self._planning_prompt(objective, project_files, project_context)

        # Step 2: Query Providers in priority order
        response = ""
        providers_order = ["groq", "openrouter", "gemini", "ollama"]
        for p_name in providers_order:
            prov = self.router.providers.get(p_name)
            if prov and getattr(prov, "available", False):
                try:
                    res = prov.generate(prompt_text, max_tokens=4096)
                    if res and not str(res).lower().startswith("error"):
                        response = str(res).strip()
                        print(f"[IRIS Dev Engine]: Architecture planned using {p_name.upper()}.")
                        break
                except Exception as e:
                    logger.debug(f"Provider {p_name} generation skipped: {e}")
                    continue

        if not response and hasattr(self.router, "compare"):
            planning_result = self.router.compare(prompt_text)
            response = planning_result.get("final", "").strip()

        if not response:
            return {
                "success": False,
                "reason": "Failed to generate development patch from AI Council.",
                "production_modified": False
            }

        # Step 3: Resilient Plan Extraction
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

        # Step 4: Validate and Write to Sandbox
        for change in changes:
            if not isinstance(change, dict):
                continue

            rel_path = change.get("path")
            content = change.get("content")

            if not rel_path or not isinstance(content, str):
                continue

            # AST Pre-Flight Syntax Check
            if rel_path.endswith(".py"):
                try:
                    ast.parse(content)
                except SyntaxError as se:
                    print(f"[AST Syntax Error in {rel_path}]: {se} -> Skipping unsafe modification.")
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

        # Step 5: Sandbox Test Runner
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

        # Step 7: Auto-Deploy to Production on Verified Pass
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