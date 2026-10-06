from pathlib import Path
import json
import hashlib
from datetime import datetime


class IRISUpgradeManager:

    MAX_FILES = 50
    MAX_FILE_SIZE = 2 * 1024 * 1024

    def __init__(self, project_root):
        self.project_root = Path(
            project_root
        ).resolve()

        self.upgrade_dir = (
            self.project_root /
            "upgrade_proposals"
        )

        self.upgrade_dir.mkdir(
            parents=True,
            exist_ok=True
        )

    def _safe_path(
        self,
        project_path,
        relative_path
    ):
        relative = Path(
            relative_path
        )

        if relative.is_absolute():
            raise PermissionError(
                "Proposal path must be relative."
            )

        if any(
            part == ".."
            for part in relative.parts
        ):
            raise PermissionError(
                "Proposal path cannot contain '..'."
            )

        target = (
            Path(project_path) /
            relative
        ).resolve()

        try:
            target.relative_to(
                Path(project_path).resolve()
            )
        except ValueError:
            raise PermissionError(
                "Proposal path escaped sandbox."
            )

        if any(
            part.startswith(".env")
            for part in relative.parts
        ):
            raise PermissionError(
                "Proposal cannot include environment files."
            )

        if target.suffix.lower() in {
            ".db",
            ".sqlite",
            ".sqlite3"
        }:
            raise PermissionError(
                "Proposal cannot include database files."
            )

        return target

    def _sha256(self, path):
        digest = hashlib.sha256()

        with open(path, "rb") as file:
            for chunk in iter(
                lambda: file.read(1024 * 1024),
                b""
            ):
                digest.update(chunk)

        return digest.hexdigest()

    def _create_manifest(
        self,
        workspace,
        changes
    ):
        project_path = (
            Path(workspace) /
            "project"
        ).resolve()

        if not project_path.is_dir():
            raise FileNotFoundError(
                "Sandbox project does not exist."
            )

        if not isinstance(
            changes,
            list
        ):
            raise ValueError(
                "Proposal changes must be a list."
            )

        if not changes:
            raise ValueError(
                "Proposal cannot contain zero changes."
            )

        if len(changes) > self.MAX_FILES:
            raise ValueError(
                f"Proposal exceeds {self.MAX_FILES} files."
            )

        manifest = {}

        for relative_path in changes:

            target = self._safe_path(
                project_path,
                relative_path
            )

            if not target.is_file():
                raise FileNotFoundError(
                    f"Sandbox file missing: {relative_path}"
                )

            if (
                target.stat().st_size
                >
                self.MAX_FILE_SIZE
            ):
                raise ValueError(
                    f"File exceeds 2 MB: {relative_path}"
                )

            manifest[
                relative_path
            ] = {
                "sha256": self._sha256(
                    target
                ),
                "size": target.stat().st_size
            }

        return manifest

    def _proposal_digest(
        self,
        proposal
    ):
        canonical = {
            "objective": proposal.get(
                "objective"
            ),
            "workspace": proposal.get(
                "workspace"
            ),
            "changes": proposal.get(
                "changes"
            ),
            "test_results": proposal.get(
                "test_results"
            ),
            "file_manifest": proposal.get(
                "file_manifest"
            ),
            "production_modified": proposal.get(
                "production_modified"
            ),
            "approval_required": proposal.get(
                "approval_required"
            ),
            "status": proposal.get(
                "status"
            )
        }

        payload = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":")
        ).encode(
            "utf-8"
        )

        return hashlib.sha256(
            payload
        ).hexdigest()

    def create_proposal(
        self,
        objective,
        workspace,
        changes,
        test_results
    ):
        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S_%f"
        )

        file_manifest = self._create_manifest(
            workspace,
            changes
        )

        proposal = {
            "created": datetime.now().isoformat(),
            "objective": objective,
            "workspace": str(workspace),
            "changes": changes,
            "test_results": test_results,
            "file_manifest": file_manifest,
            "production_modified": False,
            "approval_required": True,
            "status": "awaiting_approval"
        }

        proposal["proposal_sha256"] = (
            self._proposal_digest(
                proposal
            )
        )

        path = (
            self.upgrade_dir /
            f"proposal_{timestamp}.json"
        )

        path.write_text(
            json.dumps(
                proposal,
                indent=2
            ),
            encoding="utf-8"
        )

        return {
            "proposal_file": str(path),
            "status": "awaiting_approval",
            "production_modified": False,
            "proposal_sha256": proposal[
                "proposal_sha256"
            ]
        }

    def verify_proposal_digest(
        self,
        proposal
    ):
        stored = proposal.get(
            "proposal_sha256"
        )

        if not stored:
            return {
                "passed": False,
                "reason": (
                    "Proposal does not contain "
                    "an integrity digest."
                )
            }

        actual = self._proposal_digest(
            proposal
        )

        if actual != stored:
            return {
                "passed": False,
                "reason": (
                    "Proposal SHA-256 digest "
                    "does not match."
                ),
                "expected": stored,
                "actual": actual
            }

        return {
            "passed": True,
            "reason": (
                "Proposal SHA-256 digest "
                "matches."
            )
        }

    def verify_file_manifest(
        self,
        proposal
    ):
        workspace = Path(
            proposal.get(
                "workspace",
                ""
            )
        ).resolve()

        project_path = (
            workspace /
            "project"
        )

        manifest = proposal.get(
            "file_manifest"
        )

        if not isinstance(
            manifest,
            dict
        ):
            return {
                "passed": False,
                "reason": "Proposal file manifest is missing."
            }

        mismatches = []

        for relative_path, expected in manifest.items():

            try:
                target = self._safe_path(
                    project_path,
                    relative_path
                )

                if not target.is_file():
                    mismatches.append({
                        "path": relative_path,
                        "reason": "file_missing"
                    })
                    continue

                actual_hash = self._sha256(
                    target
                )

                actual_size = target.stat().st_size

                if actual_hash != expected.get(
                    "sha256"
                ):
                    mismatches.append({
                        "path": relative_path,
                        "reason": "sha256_changed"
                    })

                elif actual_size != expected.get(
                    "size"
                ):
                    mismatches.append({
                        "path": relative_path,
                        "reason": "size_changed"
                    })

            except Exception as e:
                mismatches.append({
                    "path": relative_path,
                    "reason": str(e)
                })

        if mismatches:
            return {
                "passed": False,
                "reason": (
                    "Sandbox files do not match "
                    "the proposal manifest."
                ),
                "mismatches": mismatches
            }

        return {
            "passed": True,
            "reason": (
                "All sandbox files match "
                "the proposal manifest."
            ),
            "mismatches": []
        }
