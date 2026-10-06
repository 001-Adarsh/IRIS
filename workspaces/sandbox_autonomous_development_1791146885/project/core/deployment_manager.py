from pathlib import Path
import shutil
import json
import hashlib
from datetime import datetime


class IRISDeploymentManager:

    BLOCKED_SUFFIXES = {
        ".db",
        ".sqlite",
        ".sqlite3"
    }

    def __init__(self, project_root):
        self.project_root = Path(project_root).resolve()
        self.backup_root = (
            self.project_root / "deployment_backups"
        )
        self.backup_root.mkdir(
            parents=True,
            exist_ok=True
        )

    def _safe_relative_path(self, relative_path):
        relative = Path(relative_path)

        if relative.is_absolute():
            raise PermissionError(
                "Deployment path must be relative."
            )

        if any(part == ".." for part in relative.parts):
            raise PermissionError(
                "Deployment path cannot contain '..'."
            )

        if not relative.parts:
            raise PermissionError(
                "Deployment path cannot be empty."
            )

        target = (
            self.project_root / relative
        ).resolve()

        try:
            target.relative_to(
                self.project_root
            )
        except ValueError:
            raise PermissionError(
                "Deployment path escaped the IRIS project."
            )

        if any(
            part.startswith(".env")
            for part in relative.parts
        ):
            raise PermissionError(
                "Deployment cannot modify environment files."
            )

        if target.suffix.lower() in self.BLOCKED_SUFFIXES:
            raise PermissionError(
                "Deployment cannot modify database files."
            )

        return target

    def _safe_source_path(
        self,
        project_path,
        relative_path
    ):
        root = Path(project_path).resolve()

        source = (
            root / relative_path
        ).resolve()

        try:
            source.relative_to(root)
        except ValueError:
            raise PermissionError(
                "Sandbox source escaped the sandbox project."
            )

        if not source.is_file():
            raise FileNotFoundError(
                f"Sandbox file does not exist: {relative_path}"
            )

        return source

    def _sha256(self, path):
        digest = hashlib.sha256()

        with open(path, "rb") as file:
            for chunk in iter(
                lambda: file.read(1024 * 1024),
                b""
            ):
                digest.update(chunk)

        return digest.hexdigest()

    def _validate_changes(
        self,
        project_path,
        changes
    ):
        if not isinstance(changes, list):
            raise ValueError(
                "Proposal changes must be a list."
            )

        if not changes:
            raise ValueError(
                "Proposal contains no changes."
            )

        if len(changes) > 50:
            raise ValueError(
                "Deployment is limited to 50 files per proposal."
            )

        validated = []
        seen = set()

        for relative_path in changes:

            if not isinstance(relative_path, str):
                raise ValueError(
                    "Every deployment path must be a string."
                )

            if relative_path in seen:
                raise ValueError(
                    f"Duplicate deployment path: {relative_path}"
                )

            seen.add(relative_path)

            target = self._safe_relative_path(
                relative_path
            )

            source = self._safe_source_path(
                project_path,
                relative_path
            )

            if source.stat().st_size > 2 * 1024 * 1024:
                raise ValueError(
                    f"Deployment file exceeds 2 MB: "
                    f"{relative_path}"
                )

            validated.append(
                {
                    "relative_path": relative_path,
                    "target": target,
                    "source": source
                }
            )

        return validated

    def _create_backup(
        self,
        validated,
        timestamp
    ):
        backup_dir = (
            self.backup_root / timestamp
        )

        backup_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        manifest = {
            "created": datetime.now().isoformat(),
            "files": []
        }

        for item in validated:

            target = item["target"]
            relative_path = item["relative_path"]

            record = {
                "path": relative_path,
                "existed": target.exists()
            }

            if target.exists():

                if not target.is_file():
                    raise PermissionError(
                        f"Production target is not a file: "
                        f"{relative_path}"
                    )

                backup_target = (
                    backup_dir / relative_path
                )

                backup_target.parent.mkdir(
                    parents=True,
                    exist_ok=True
                )

                shutil.copy2(
                    target,
                    backup_target
                )

                record["backup"] = str(
                    backup_target
                )

            manifest["files"].append(record)

        manifest_path = (
            backup_dir / "manifest.json"
        )

        manifest_path.write_text(
            json.dumps(
                manifest,
                indent=2
            ),
            encoding="utf-8"
        )

        return backup_dir, manifest

    def _rollback(
        self,
        validated,
        backup_dir,
        manifest
    ):
        errors = []

        records = {
            item["path"]: item
            for item in manifest["files"]
        }

        for item in reversed(validated):

            relative_path = item["relative_path"]
            target = item["target"]
            record = records[relative_path]

            try:
                if record["existed"]:

                    backup_path = (
                        backup_dir /
                        relative_path
                    )

                    if backup_path.is_file():
                        shutil.copy2(
                            backup_path,
                            target
                        )
                    else:
                        raise FileNotFoundError(
                            f"Backup missing: "
                            f"{relative_path}"
                        )

                else:

                    if target.exists():
                        target.unlink()

            except Exception as e:
                errors.append(
                    f"{relative_path}: {e}"
                )

        return errors

    def verify_deployment(
        self,
        proposal,
        deployment_result
    ):
        """
        Verify that every deployed production file is
        byte-for-byte identical to its approved sandbox source.

        This is intentionally performed after deployment.
        """

        if deployment_result.get("status") != "deployed":
            return {
                "passed": False,
                "reason": "Deployment did not report successful deployment.",
                "files": []
            }

        workspace = Path(
            proposal.get("workspace", "")
        ).resolve()

        project_path = workspace / "project"

        if not project_path.is_dir():
            return {
                "passed": False,
                "reason": "Sandbox project no longer exists.",
                "files": []
            }

        changes = proposal.get("changes", [])

        verification = []

        for relative_path in changes:

            try:
                source = self._safe_source_path(
                    project_path,
                    relative_path
                )

                target = self._safe_relative_path(
                    relative_path
                )

                if not target.is_file():
                    return {
                        "passed": False,
                        "reason": (
                            f"Production file missing after deployment: "
                            f"{relative_path}"
                        ),
                        "files": verification
                    }

                source_hash = self._sha256(source)
                target_hash = self._sha256(target)

                matched = source_hash == target_hash

                verification.append(
                    {
                        "path": relative_path,
                        "source_sha256": source_hash,
                        "production_sha256": target_hash,
                        "matched": matched
                    }
                )

                if not matched:
                    return {
                        "passed": False,
                        "reason": (
                            f"SHA-256 mismatch after deployment: "
                            f"{relative_path}"
                        ),
                        "files": verification
                    }

            except Exception as e:
                return {
                    "passed": False,
                    "reason": (
                        f"Verification error for "
                        f"{relative_path}: {e}"
                    ),
                    "files": verification
                }

        return {
            "passed": True,
            "reason": "All deployed files match their sandbox sources.",
            "files": verification
        }

    def deploy(self, proposal):

        if proposal.get("status") != "awaiting_approval":
            raise PermissionError(
                "Only proposals awaiting approval can be deployed."
            )

        if not proposal.get(
            "approval_required",
            True
        ):
            raise PermissionError(
                "Deployment requires explicit approval."
            )

        if proposal.get(
            "production_modified",
            False
        ):
            raise PermissionError(
                "Proposal already indicates production was modified."
            )

        project_path = (
            Path(
                proposal.get(
                    "workspace",
                    ""
                )
            ).resolve() /
            "project"
        )

        if not project_path.is_dir():
            raise FileNotFoundError(
                "Sandbox project does not exist."
            )

        changes = proposal.get(
            "changes",
            []
        )

        # Validate EVERY file before modifying production.
        validated = self._validate_changes(
            project_path,
            changes
        )

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S_%f"
        )

        backup_dir, manifest = (
            self._create_backup(
                validated,
                timestamp
            )
        )

        deployed = []

        try:

            for item in validated:

                target = item["target"]
                source = item["source"]

                target.parent.mkdir(
                    parents=True,
                    exist_ok=True
                )

                shutil.copy2(
                    source,
                    target
                )

                deployed.append(
                    item["relative_path"]
                )

        except Exception as error:

            rollback_errors = self._rollback(
                validated,
                backup_dir,
                manifest
            )

            return {
                "status": "rolled_back",
                "production_modified": False,
                "error": str(error),
                "rollback_errors": rollback_errors,
                "backup_directory": str(
                    backup_dir
                ),
                "deployed_before_failure": deployed
            }

        return {
            "status": "deployed",
            "production_modified": True,
            "backup_directory": str(
                backup_dir
            ),
            "deployed": deployed,

            # Only files that existed BEFORE deployment
            # are included in the backed_up list.
            "backed_up": [
                item["relative_path"]
                for item in validated
                if item["target"].exists()
                and any(
                    record["path"] == item["relative_path"]
                    and record["existed"]
                    for record in manifest["files"]
                )
            ],

            "rollback_available": True
        }

    def rollback(self, backup_directory):

        backup_dir = Path(
            backup_directory
        ).resolve()

        try:
            backup_dir.relative_to(
                self.backup_root.resolve()
            )
        except ValueError:
            raise PermissionError(
                "Backup directory must be inside "
                "deployment_backups."
            )

        manifest_path = (
            backup_dir / "manifest.json"
        )

        if not manifest_path.is_file():
            raise FileNotFoundError(
                "Backup manifest not found."
            )

        manifest = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )

        errors = []

        for record in reversed(
            manifest.get("files", [])
        ):

            relative_path = record["path"]

            target = self._safe_relative_path(
                relative_path
            )

            try:

                if record["existed"]:

                    backup_path = (
                        backup_dir /
                        relative_path
                    )

                    if not backup_path.is_file():
                        raise FileNotFoundError(
                            f"Backup file missing: "
                            f"{relative_path}"
                        )

                    target.parent.mkdir(
                        parents=True,
                        exist_ok=True
                    )

                    shutil.copy2(
                        backup_path,
                        target
                    )

                else:

                    if target.exists():
                        target.unlink()

            except Exception as e:
                errors.append(
                    f"{relative_path}: {e}"
                )

        if errors:
            return {
                "status": "rollback_failed",
                "errors": errors
            }

        return {
            "status": "rolled_back",
            "production_modified": False,
            "backup_directory": str(
                backup_dir
            )
        }
