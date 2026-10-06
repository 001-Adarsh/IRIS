
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.router import AIRouter
from core.sandbox import IRISSandbox
from core.test_runner import IRISTestRunner
from core.upgrade_manager import IRISUpgradeManager

logger = logging.getLogger("IRISDevelopment")


class IRISDevelopmentWorkflow:
    """
    Advanced IRIS autonomous development pipeline.

    Pipeline:
        Snapshot -> Context -> AI Planning -> Protocol Repair ->
        Safe Parsing -> AST Validation -> Sandbox Write ->
        Automated Tests -> Proposal -> Approval/Deployment

    Safety principles:
    - Production is never edited during planning or testing.
    - Unknown file targets are rejected; paths are never guessed.
    - AI output may be retried/reformatted, but repository files are not.
    - Python syntax is validated before sandbox writes.
    - Failed validation/test runs fail closed.
    - Automatic deployment is OFF by default.
    """

    MAX_PLAN_ATTEMPTS = 3
    MAX_FORMAT_REPAIR_ATTEMPTS = 2
    MAX_FILES_PER_PLAN = 20
    MAX_FILE_SIZE = 2 * 1024 * 1024

    PROTECTED_NAMES = {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "credentials.json",
        "secrets.json",
    }

    PROTECTED_PATH_PARTS = {
        ".git",
        ".venv",
        "venv",
        "env",
        "workspaces",
        "node_modules",
    }

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = Path(
            project_root or Path(__file__).resolve().parent.parent
        ).resolve()

        self.router = AIRouter()
        self.sandbox = IRISSandbox()
        self.upgrades = IRISUpgradeManager(self.project_root)

    # ------------------------------------------------------------------
    # SNAPSHOT / CONTEXT
    # ------------------------------------------------------------------

    def _create_sandbox_snapshot(
        self, label: str = "autonomous_development"
    ) -> Dict[str, str]:
        """Create an isolated copy of production files."""
        ts = int(time.time())
        workspace = self.project_root / "workspaces" / f"sandbox_{label}_{ts}"
        project_path = workspace / "project"
        project_path.mkdir(parents=True, exist_ok=True)

        ignored_names = {
            ".venv",
            "venv",
            "env",
            ".git",
            "workspaces",
            "sandbox",
            "__pycache__",
            "upgrade_proposals",
            ".idea",
            ".vscode",
            "node_modules",
            ".pytest_cache",
            "data",
            "logs",
        }

        for item in self.project_root.iterdir():
            name_lower = item.name.lower()

            if (
                name_lower in ignored_names
                or item.name.startswith(".")
                or item.name.startswith("sandbox_")
            ):
                continue

            dest = project_path / item.name

            try:
                if item.is_dir():
                    shutil.copytree(
                        item,
                        dest,
                        ignore=shutil.ignore_patterns(
                            "*.pyc",
                            "__pycache__",
                            "*.tmp",
                            "*.log",
                            "sandbox_*",
                        ),
                    )
                elif item.is_file():
                    shutil.copy2(item, dest)
            except Exception as exc:
                logger.warning(
                    "Sandbox snapshot warning for %s: %s",
                    item.name,
                    exc,
                )

        return {
            "workspace": str(workspace),
            "project": str(project_path),
        }

    def _get_project_context(self, project_path: Path) -> str:
        """Inspect repository symbols without sending full source to the model."""
        context_snippets: List[str] = []

        core_dir = project_path / "core"
        tools_dir = project_path / "tools"

        if core_dir.exists():
            for py_file in sorted(core_dir.glob("*.py")):
                try:
                    source = py_file.read_text(
                        encoding="utf-8",
                        errors="ignore",
                    )

                    tree = ast.parse(source)

                    classes = [
                        node.name
                        for node in ast.walk(tree)
                        if isinstance(node, ast.ClassDef)
                    ]

                    funcs = [
                        node.name
                        for node in ast.walk(tree)
                        if isinstance(
                            node,
                            (ast.FunctionDef, ast.AsyncFunctionDef),
                        )
                    ]

                    context_snippets.append(
                        f"core/{py_file.name}: "
                        f"Classes={classes}, Functions={funcs[:12]}"
                    )
                except Exception as exc:
                    logger.debug(
                        "Could not inspect %s: %s",
                        py_file,
                        exc,
                    )

        if tools_dir.exists():
            tool_files = [
                f.name
                for f in sorted(tools_dir.glob("*.py"))
                if not f.name.startswith("__")
            ]
            context_snippets.append(
                f"tools/: Active tool modules={tool_files}"
            )

        return "\n".join(context_snippets)

    # ------------------------------------------------------------------
    # AI PROTOCOL
    # ------------------------------------------------------------------

    def _planning_prompt(
        self,
        objective: str,
        project_files: List[str],
        project_context: str,
    ) -> str:
        files_text = "\n".join(project_files[:120])

        return f"""
You are the IRIS Autonomous Development Engineer.

Your task is to implement this objective inside an isolated sandbox:

OBJECTIVE:
{objective}

ACTIVE REPOSITORY CONTEXT:
{project_context}

AVAILABLE REPOSITORY FILES:
{files_text}

============================================================
MANDATORY IRIS DEVELOPMENT PROTOCOL
============================================================

You MUST return machine-readable file blocks.

For every changed file use exactly:

<<<FILE: relative/path/to/file.py>>>
```python
FULL COMPLETE FILE CONTENT
```
<<<END_FILE>>>

For non-Python files, use:

<<<FILE: relative/path/to/file.json>>>
```text
FULL COMPLETE FILE CONTENT
```
<<<END_FILE>>>

If multiple files are required, output multiple blocks.

If no safe change can be produced, output exactly:

<<<NO_CHANGE>>>

============================================================
STRICT RULES
============================================================

1. Output ONLY file blocks or <<<NO_CHANGE>>>.
2. Do not provide explanations.
3. Do not provide conversational text.
4. Do not output JSON as the primary protocol.
5. Do not output Markdown explanations.
6. Never use absolute paths.
7. Never use '..' path components.
8. Never modify credentials.
9. Never modify .env files.
10. Never modify .git files.
11. Never modify the virtual environment.
12. Never invent a file that is unrelated to the objective.
13. Every modified Python file must be complete.
14. Never use '...', TODO, 'rest of code', or placeholders.
15. Never truncate a file.
16. Preserve existing functionality unless the objective requires a change.
17. Prefer the smallest safe change.
18. Do not guess an unknown target file.
19. If you cannot determine the correct target file, return <<<NO_CHANGE>>>.
20. The response will be parsed automatically. Protocol compliance is mandatory.

============================================================
IMPORTANT
============================================================

The generated files will first be written to a sandbox.
Production will NOT be modified during this request.

Do not attempt to execute commands.
Do not attempt to deploy.
Do not describe what you would change.

Return the actual complete file contents only.
""".strip()

    def _query_model(self, prompt: str) -> str:
        """
        Query available providers in priority order.

        Provider failure is isolated so one unavailable provider
        cannot terminate the entire development workflow.
        """
        provider_names = [
            "groq",
            "gemini",
            "openrouter",
            "ollama",
        ]

        for provider_name in provider_names:
            provider = self.router.providers.get(provider_name)

            if not provider:
                continue

            if not getattr(provider, "available", False):
                continue

            try:
                result = provider.generate(
                    prompt,
                    max_tokens=8192,
                )

                if result:
                    text = str(result).strip()

                    if text and not text.lower().startswith("error"):
                        print(
                            f"[IRIS Dev Engine]: "
                            f"Response generated via {provider_name.upper()}."
                        )
                        return text

            except Exception as exc:
                logger.warning(
                    "%s generation failed: %s",
                    provider_name,
                    exc,
                )

        # Optional router-level fallback.
        try:
            if hasattr(self.router, "compare"):
                result = self.router.compare(prompt)

                if isinstance(result, dict):
                    final = result.get("final", "")

                    if final:
                        return str(final).strip()

        except Exception as exc:
            logger.warning(
                "Router comparison fallback failed: %s",
                exc,
            )

        return ""

    # ------------------------------------------------------------------
    # PATH / CONTENT SECURITY
    # ------------------------------------------------------------------

    def _clean_path(
        self,
        raw_path: str,
        valid_files: Set[str],
    ) -> Optional[str]:
        """Normalize and strictly validate an AI-supplied repository path."""
        if not raw_path:
            return None

        clean = (
            raw_path
            .strip()
            .strip("`'\" <>*?")
            .replace("\\", "/")
        )

        clean = re.sub(r"/+", "/", clean)

        if not clean:
            return None

        if clean.startswith("/") or re.match(r"^[A-Za-z]:/", clean):
            return None

        parts = Path(clean).parts

        if ".." in parts:
            return None

        if any(
            token in clean
            for token in (
                "*",
                "?",
                "(",
                ")",
                "[",
                "]",
                "<",
                ">",
            )
        ):
            return None

        lower = clean.lower()

        if any(
            protected == lower
            or lower.endswith("/" + protected)
            for protected in self.PROTECTED_NAMES
        ):
            return None

        if any(
            part.lower() in self.PROTECTED_PATH_PARTS
            for part in parts
        ):
            return None

        # Prefer exact repository paths.
        if clean in valid_files:
            return clean

        # Allow case-insensitive exact matching on Windows.
        for valid in valid_files:
            if valid.lower() == lower:
                return valid

        # Do NOT guess unknown files.
        return None

    def _safe_change_path(
        self,
        project_path: Path,
        relative_path: str,
    ) -> Path:
        """Enforce sandbox boundary and protected-file rules."""
        clean_rel = (
            relative_path
            .replace("\\", "/")
            .strip()
            .lstrip("/")
        )

        relative = Path(clean_rel)

        if relative.is_absolute():
            raise PermissionError(
                f"Security: absolute path rejected: {relative_path}"
            )

        if any(part == ".." for part in relative.parts):
            raise PermissionError(
                f"Security: traversal path rejected: {relative_path}"
            )

        if any(
            part.lower() in self.PROTECTED_PATH_PARTS
            for part in relative.parts
        ):
            raise PermissionError(
                f"Security: protected path rejected: {relative_path}"
            )

        target = (Path(project_path) / relative).resolve()
        root = Path(project_path).resolve()

        try:
            target.relative_to(root)
        except ValueError as exc:
            raise PermissionError(
                f"Security: target escapes sandbox: {target}"
            ) from exc

        if target.name.lower() in self.PROTECTED_NAMES:
            raise PermissionError(
                "Security: protected environment/credential file."
            )

        return target

    def _validate_content_size(self, content: str) -> None:
        size = len(content.encode("utf-8"))

        if size > self.MAX_FILE_SIZE:
            raise ValueError(
                f"Generated file exceeds {self.MAX_FILE_SIZE} bytes."
            )

    # ------------------------------------------------------------------
    # CODE VALIDATION / HEALING
    # ------------------------------------------------------------------

    def _repair_code_truncation(self, code: str) -> str:
        """
        Remove markdown fences only.

        Important:
        This method intentionally does NOT invent missing source code
        or close arbitrary strings. Suspected truncation is rejected
        by AST validation and sent back to the model for regeneration.
        """
        text = str(code or "").strip()

        text = re.sub(
            r"^```[a-zA-Z0-9_-]*\r?\n",
            "",
            text,
            count=1,
        )

        text = re.sub(
            r"\r?\n```$",
            "",
            text,
            count=1,
        )

        return text.strip()

    def _validate_python(self, path: str, content: str) -> None:
        """Validate Python syntax before sandbox write."""
        try:
            ast.parse(content, filename=path)
        except SyntaxError as exc:
            raise ValueError(
                f"Python syntax error in {path}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # PLAN PARSING
    # ------------------------------------------------------------------

    def _extract_file_blocks(
        self,
        response: str,
        valid_files: Set[str],
    ) -> List[Dict[str, str]]:
        """
        Extract ONLY explicitly identified file blocks.

        Unknown targets are rejected instead of guessed.
        """
        if not response:
            return []

        normalized = response.replace("\r\n", "\n").replace("\r", "\n")
        changes: List[Dict[str, str]] = []
        found_paths: Set[str] = set()

        # Primary protocol.
        pattern = re.compile(
            r"""
            <<<FILE:\s*(?P<path>[^\n>]+?)\s*>>>
            \s*
            ```[^\n]*\n
            (?P<code>.*?)
            \n```
            \s*
            <<<END_FILE>>>
            """,
            re.IGNORECASE | re.DOTALL | re.VERBOSE,
        )

        for match in pattern.finditer(normalized):
            rel_path = self._clean_path(
                match.group("path"),
                valid_files,
            )

            if not rel_path:
                logger.warning(
                    "Rejected AI file target: %r",
                    match.group("path"),
                )
                continue

            if rel_path in found_paths:
                continue

            code = self._repair_code_truncation(
                match.group("code")
            )

            if not code:
                continue

            self._validate_content_size(code)

            changes.append(
                {
                    "path": rel_path,
                    "action": "replace",
                    "content": code,
                }
            )

            found_paths.add(rel_path)

        # Secondary tolerant protocol.
        if not changes:
            tolerant = re.compile(
                r"""
                <<<FILE:\s*(?P<path>[^\n>]+?)\s*>>>
                \s*
                ```(?:python|py|text|json)?\s*\n
                (?P<code>.*?)
                \n```
                """,
                re.IGNORECASE | re.DOTALL | re.VERBOSE,
            )

            for match in tolerant.finditer(normalized):
                rel_path = self._clean_path(
                    match.group("path"),
                    valid_files,
                )

                if not rel_path or rel_path in found_paths:
                    continue

                code = self._repair_code_truncation(
                    match.group("code")
                )

                if not code:
                    continue

                self._validate_content_size(code)

                changes.append(
                    {
                        "path": rel_path,
                        "action": "replace",
                        "content": code,
                    }
                )

                found_paths.add(rel_path)

        return changes

    def _parse_plan(
        self,
        response: str,
        valid_files: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """Parse a strict IRIS development response."""
        files_set = valid_files or set()
        text = str(response or "").strip()

        if not text:
            raise ValueError("Empty development planner response.")

        if text.upper() == "<<<NO_CHANGE>>>":
            return {
                "objective": "No Change",
                "changes": [],
                "tests": [],
            }

        changes = self._extract_file_blocks(
            text,
            files_set,
        )

        if not changes:
            raise ValueError(
                "Development planner did not return any valid "
                "IRIS file blocks."
            )

        if len(changes) > self.MAX_FILES_PER_PLAN:
            raise ValueError(
                f"Development plan contains {len(changes)} files; "
                f"maximum allowed is {self.MAX_FILES_PER_PLAN}."
            )

        # Every generated Python file gets a final parser check.
        for change in changes:
            if change["path"].lower().endswith(".py"):
                self._validate_python(
                    change["path"],
                    change["content"],
                )

        return {
            "objective": "Autonomous Patch",
            "changes": changes,
            "tests": ["AST compile and sandbox test suite"],
        }

    # ------------------------------------------------------------------
    # FORMAT REPAIR
    # ------------------------------------------------------------------

    def _repair_plan_format(
        self,
        response: str,
        objective: str,
        valid_files: Set[str],
    ) -> str:
        """
        Ask another provider to convert an invalid response into
        the strict IRIS file protocol.

        This operation does not touch repository files.
        """
        available = "\n".join(sorted(valid_files)[:150])

        prompt = f"""
You are the IRIS Development Protocol Formatter.

The development model returned an invalid response.

OBJECTIVE:
{objective}

AVAILABLE FILES:
{available}

INVALID RESPONSE:
{response}

Convert the response into the required IRIS protocol.

Return ONLY one or more blocks:

<<<FILE: relative/path/to/file.py>>>
```python
COMPLETE FILE CONTENT
```
<<<END_FILE>>>

OR:

<<<NO_CHANGE>>>

Rules:
- Do not explain anything.
- Do not invent a target file.
- Do not invent functionality.
- Do not use placeholders.
- Do not use ...
- Do not use absolute paths.
- Do not use '..'.
- Do not modify .env or credentials.
- Preserve the intended implementation.
- If there is no reliable complete implementation, return <<<NO_CHANGE>>>.
""".strip()

        return self._query_model(prompt)

    def _generate_plan_with_recovery(
        self,
        prompt: str,
        objective: str,
        valid_files: Set[str],
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        Generate and parse a development plan with controlled retries.
        """
        last_error = ""

        for attempt in range(1, self.MAX_PLAN_ATTEMPTS + 1):
            print(
                f"[IRIS Dev Engine]: Planning attempt "
                f"{attempt}/{self.MAX_PLAN_ATTEMPTS}"
            )

            response = self._query_model(prompt)

            if not response:
                last_error = "AI providers returned no response."
                continue

            try:
                plan = self._parse_plan(
                    response,
                    valid_files,
                )
                return plan, ""

            except Exception as exc:
                last_error = str(exc)

                print(
                    "[IRIS Dev Parser]: Invalid response format."
                )

                # First-level protocol repair.
                for repair_attempt in range(
                    1,
                    self.MAX_FORMAT_REPAIR_ATTEMPTS + 1,
                ):
                    print(
                        "[IRIS Dev Parser]: "
                        f"Format repair "
                        f"{repair_attempt}/"
                        f"{self.MAX_FORMAT_REPAIR_ATTEMPTS}"
                    )

                    repaired = self._repair_plan_format(
                        response=response,
                        objective=objective,
                        valid_files=valid_files,
                    )

                    if not repaired:
                        continue

                    try:
                        plan = self._parse_plan(
                            repaired,
                            valid_files,
                        )
                        return plan, ""

                    except Exception as repair_exc:
                        last_error = str(repair_exc)

        return None, last_error

    # ------------------------------------------------------------------
    # AUTO HEAL
    # ------------------------------------------------------------------

    def _auto_heal_code(
        self,
        file_path: str,
        code: str,
        error_msg: str,
    ) -> str:
        """
        Request a complete replacement after a Python syntax failure.

        No automatic quote-closing or source invention is performed locally.
        """
        print(
            f"[IRIS Auto-Heal]: Requesting complete syntax repair "
            f"for {file_path}..."
        )

        prompt = f"""
You are repairing a Python source file.

FILE:
{file_path}

SYNTAX ERROR:
{error_msg}

BROKEN CODE:
{code}

Return ONLY:

<<<FILE: {file_path}>>>
```python
COMPLETE CORRECTED FILE
```
<<<END_FILE>>>

Rules:
- Return the complete file.
- Do not use ...
- Do not use TODO placeholders.
- Do not explain anything.
- Preserve all existing functionality.
""".strip()

        response = self._query_model(prompt)

        if not response:
            return ""

        blocks = self._extract_file_blocks(
            response,
            {file_path},
        )

        if not blocks:
            return ""

        repaired = blocks[0]["content"]

        try:
            self._validate_python(
                file_path,
                repaired,
            )
        except Exception:
            return ""

        return repaired

    # ------------------------------------------------------------------
    # MAIN DEVELOPMENT PIPELINE
    # ------------------------------------------------------------------

    def develop(
        self,
        objective: str,
        auto_deploy: bool = False,
    ) -> Dict[str, Any]:
        """
        Autonomous development pipeline.

        auto_deploy defaults to False so passing tests cannot silently
        modify production.
        """
        print("\n=== IRIS AUTONOMOUS SELF-DEVELOPMENT ===")
        print(f"Objective: {objective}")

        snapshot = self._create_sandbox_snapshot(
            "autonomous_development"
        )

        workspace = Path(snapshot["workspace"])
        project_path = Path(snapshot["project"])

        print(f"Sandbox Isolated: {workspace}")
        print("Production State: PROTECTED")

        # --------------------------------------------------------------
        # Repository inventory
        # --------------------------------------------------------------

        project_files = [
            str(path.relative_to(project_path)).replace("\\", "/")
            for path in project_path.rglob("*")
            if (
                path.is_file()
                and not path.name.startswith(".")
                and "__pycache__" not in path.parts
            )
        ]

        valid_files_set = set(project_files)

        print(
            "\n[IRIS Dev Engine]: "
            "Analyzing project architecture and symbols..."
        )

        project_context = self._get_project_context(
            project_path
        )

        prompt_text = self._planning_prompt(
            objective,
            project_files,
            project_context,
        )

        # --------------------------------------------------------------
        # AI planning + recovery
        # --------------------------------------------------------------

        plan, plan_error = self._generate_plan_with_recovery(
            prompt=prompt_text,
            objective=objective,
            valid_files=valid_files_set,
        )

        if plan is None:
            return {
                "success": False,
                "status": "PLAN_FAILED",
                "reason": (
                    "Development planning failed after "
                    f"controlled retries: {plan_error}"
                ),
                "workspace": str(workspace),
                "production_modified": False,
            }

        changes = plan.get("changes", [])

        if not changes:
            return {
                "success": True,
                "status": "NO_CHANGE",
                "objective": objective,
                "workspace": str(workspace),
                "changes": [],
                "tests": {},
                "production_modified": False,
            }

        print(
            f"\n[IRIS Dev Engine]: Inspecting "
            f"{len(changes)} proposed code modification(s)..."
        )

        applied_changes: List[str] = []

        # --------------------------------------------------------------
        # Validate and write sandbox changes
        # --------------------------------------------------------------

        for change in changes:
            rel_path = change.get("path")
            content = change.get("content")

            if not rel_path:
                print(
                    "[IRIS Security]: Rejected change with no path."
                )
                continue

            if not isinstance(content, str):
                print(
                    f"[IRIS Security]: Rejected non-text content: "
                    f"{rel_path}"
                )
                continue

            try:
                validated_path = self._clean_path(
                    rel_path,
                    valid_files_set,
                )

                if not validated_path:
                    raise PermissionError(
                        f"Unknown or protected target: {rel_path}"
                    )

                self._validate_content_size(content)

                # Existing files only.
                target = self._safe_change_path(
                    project_path,
                    validated_path,
                )

                if not target.exists():
                    raise FileNotFoundError(
                        f"AI attempted to modify a file that does not "
                        f"exist in the sandbox: {validated_path}"
                    )

                if validated_path.lower().endswith(".py"):
                    try:
                        self._validate_python(
                            validated_path,
                            content,
                        )
                    except ValueError as syntax_error:
                        print(
                            f"[AST Syntax Warning in "
                            f"{validated_path}]: "
                            f"{syntax_error}"
                        )

                        healed = self._auto_heal_code(
                            validated_path,
                            content,
                            str(syntax_error),
                        )

                        if not healed:
                            print(
                                f"[IRIS Auto-Heal]: "
                                f"Could not safely repair "
                                f"{validated_path}. Skipping."
                            )
                            continue

                        content = healed

                        self._validate_python(
                            validated_path,
                            content,
                        )

                        print(
                            f"  ✓ Code repaired successfully for "
                            f"{validated_path}"
                        )

                target.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                target.write_text(
                    content,
                    encoding="utf-8",
                )

                applied_changes.append(
                    validated_path
                )

                print(
                    f"  ✓ Applied to Sandbox: "
                    f"{validated_path}"
                )

            except Exception as exc:
                print(
                    f"[IRIS Change Rejected]: "
                    f"{rel_path} - {exc}"
                )

        if not applied_changes:
            return {
                "success": False,
                "status": "NO_SAFE_CHANGES",
                "reason": (
                    "All proposed changes were rejected by "
                    "security, path, size, or syntax validation."
                ),
                "workspace": str(workspace),
                "production_modified": False,
            }

        # --------------------------------------------------------------
        # Sandbox tests
        # --------------------------------------------------------------

        print("\n=== IRIS SANDBOX AUTOMATED TEST SUITE ===")

        try:
            tester = IRISTestRunner(project_path)
            test_results = tester.run()
        except Exception as exc:
            logger.exception(
                "Sandbox test runner crashed."
            )

            test_results = {
                "passed": False,
                "tests": [
                    {
                        "type": "test_runner",
                        "passed": False,
                        "stderr": str(exc),
                        "stdout": "",
                    }
                ],
            }

        failure_reasons: List[str] = []

        if not test_results.get("passed", False):
            for test in test_results.get("tests", []):
                if not test.get("passed", False):
                    output = (
                        test.get("stdout")
                        or test.get("stderr")
                        or ""
                    )

                    lines = [
                        line.strip()
                        for line in str(output).splitlines()
                        if line.strip()
                    ]

                    last_line = (
                        lines[-1]
                        if lines
                        else "Test assertion failed"
                    )

                    failure_reasons.append(
                        f"{test.get('type', 'unknown')}: "
                        f"{last_line}"
                    )

        error_summary = (
            "; ".join(failure_reasons)
            if failure_reasons
            else "Unknown error"
        )

        print(
            "Test Suite Results: "
            f"{'PASSED' if test_results.get('passed') else 'FAILED'}"
        )

        if not test_results.get("passed", False):
            print(
                f"[Test Error Detail]: {error_summary}"
            )

        # --------------------------------------------------------------
        # Proposal
        # --------------------------------------------------------------

        try:
            proposal = self.upgrades.create_proposal(
                objective=objective,
                workspace=workspace,
                changes=applied_changes,
                test_results=test_results,
            )
        except Exception as exc:
            logger.exception(
                "Could not create upgrade proposal."
            )

            return {
                "success": False,
                "status": "PROPOSAL_FAILED",
                "objective": objective,
                "workspace": str(workspace),
                "changes": applied_changes,
                "tests": test_results,
                "reason": f"Proposal creation failed: {exc}",
                "production_modified": False,
            }

        # --------------------------------------------------------------
        # Deployment
        # --------------------------------------------------------------

        production_modified = False
        status_msg = "APPROVAL_REQUIRED"

        if test_results.get("passed", False):
            if auto_deploy:
                print(
                    "\n[IRIS Auto-Deployer]: "
                    "Tests passed. Deployment requested..."
                )

                try:
                    from core.development_engine import (
                        IRISDevelopmentEngine,
                    )

                    engine = IRISDevelopmentEngine()

                    deploy_res = engine.approve_and_deploy(
                        proposal.get("proposal_file")
                    )

                    production_modified = bool(
                        deploy_res.get(
                            "production_modified",
                            False,
                        )
                    )

                    status_msg = (
                        "AUTONOMOUSLY_DEPLOYED"
                        if production_modified
                        else "DEPLOYMENT_NOT_CONFIRMED"
                    )

                    if production_modified:
                        print(
                            "✓ Production deployment completed."
                        )

                except Exception as exc:
                    logger.exception(
                        "Deployment failed."
                    )

                    status_msg = "DEPLOYMENT_FAILED"

                    return {
                        "success": False,
                        "status": status_msg,
                        "objective": objective,
                        "workspace": str(workspace),
                        "changes": applied_changes,
                        "tests": test_results,
                        "reason": str(exc),
                        "proposal": proposal,
                        "production_modified": False,
                        "auto_deployed": False,
                    }

            else:
                print(
                    "\n[IRIS Deployment]: "
                    "Tests passed. Human approval is required."
                )
                status_msg = "APPROVAL_REQUIRED"

        else:
            status_msg = "TESTS_FAILED"

        return {
            "success": bool(test_results.get("passed", False)),
            "status": status_msg,
            "objective": objective,
            "workspace": str(workspace),
            "project": str(project_path),
            "changes": applied_changes,
            "tests": test_results,
            "reason": (
                error_summary
                if not test_results.get("passed", False)
                else ""
            ),
            "proposal": proposal,
            "production_modified": production_modified,
            "auto_deployed": production_modified,
        }
