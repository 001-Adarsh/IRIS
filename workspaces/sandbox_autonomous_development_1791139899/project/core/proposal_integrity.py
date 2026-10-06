from pathlib import Path
import hashlib


class IRISProposalIntegrity:

    MAX_FILES = 50
    MAX_FILE_SIZE = 2 * 1024 * 1024

    def __init__(self, project_path):
        self.project_path = Path(
            project_path
        ).resolve()

    def _safe_path(self, relative_path):
        relative = Path(relative_path)

        if relative.is_absolute():
            raise PermissionError(
                "Integrity path must be relative."
            )

        if any(
            part == ".."
            for part in relative.parts
        ):
            raise PermissionError(
                "Integrity path cannot contain '..'."
            )

        target = (
            self.project_path / relative
        ).resolve()

        try:
            target.relative_to(
                self.project_path
            )
        except ValueError:
            raise PermissionError(
                "Integrity path escaped sandbox."
            )

        if any(
            part.startswith(".env")
            for part in relative.parts
        ):
            raise PermissionError(
                "Integrity cannot include environment files."
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

    def create_manifest(self, changes):
        if not isinstance(changes, list):
            raise ValueError(
                "Changes must be a list."
            )

        if not changes:
            raise ValueError(
                "Integrity manifest cannot be empty."
            )

        if len(changes) > self.MAX_FILES:
            raise ValueError(
                "Integrity manifest exceeds file limit."
            )

        manifest = {}

        for relative_path in changes:

            if not isinstance(
                relative_path,
                str
            ):
                raise ValueError(
                    "Integrity paths must be strings."
                )

            target = self._safe_path(
                relative_path
            )

            if not target.is_file():
                raise FileNotFoundError(
                    f"Sandbox file missing: {relative_path}"
                )

            if target.stat().st_size > self.MAX_FILE_SIZE:
                raise ValueError(
                    f"Sandbox file exceeds 2 MB: "
                    f"{relative_path}"
                )

            manifest[
                relative_path
            ] = self._sha256(target)

        return manifest

    def verify_manifest(self, manifest):
        if not isinstance(
            manifest,
            dict
        ):
            return {
                "passed": False,
                "reason": "Invalid integrity manifest.",
                "mismatches": []
            }

        mismatches = []

        for relative_path, expected_hash in manifest.items():

            try:
                target = self._safe_path(
                    relative_path
                )

                if not target.is_file():

                    mismatches.append(
                        {
                            "path": relative_path,
                            "reason": "file_missing"
                        }
                    )

                    continue

                actual_hash = self._sha256(
                    target
                )

                if actual_hash != expected_hash:

                    mismatches.append(
                        {
                            "path": relative_path,
                            "reason": "sha256_changed",
                            "expected": expected_hash,
                            "actual": actual_hash
                        }
                    )

            except Exception as e:

                mismatches.append(
                    {
                        "path": relative_path,
                        "reason": str(e)
                    }
                )

        if mismatches:

            return {
                "passed": False,
                "reason": (
                    "Sandbox files changed after approval."
                ),
                "mismatches": mismatches
            }

        return {
            "passed": True,
            "reason": (
                "All approved sandbox files "
                "remain unchanged."
            ),
            "mismatches": []
        }
