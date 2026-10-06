
from pathlib import Path
import hashlib


class IRISSandboxDiff:

    def __init__(self, production_root, sandbox_root):
        self.production_root = Path(
            production_root
        ).resolve()

        self.sandbox_root = Path(
            sandbox_root
        ).resolve()

    def _safe_relative(self, root, path):
        try:
            return path.resolve().relative_to(root)
        except ValueError:
            return None

    def _sha256(self, path):
        digest = hashlib.sha256()

        with open(path, "rb") as file:
            for chunk in iter(
                lambda: file.read(1024 * 1024),
                b""
            ):
                digest.update(chunk)

        return digest.hexdigest()

    def _files(self, root):
        result = {}

        if not root.is_dir():
            return result

        for path in root.rglob("*"):

            if not path.is_file():
                continue

            relative = self._safe_relative(
                root,
                path
            )

            if relative is None:
                continue

            parts = relative.parts

            if any(
                part.startswith(".env")
                for part in parts
            ):
                continue

            if any(
                part in {
                    ".git",
                    ".venv",
                    "__pycache__",
                    "sandbox",
                    "data",
                    "upgrade_proposals",
                    "deployment_backups"
                }
                for part in parts
            ):
                continue

            result[str(relative)] = self._sha256(
                path
            )

        return result

    def compare(self):
        production = self._files(
            self.production_root
        )

        sandbox = self._files(
            self.sandbox_root
        )

        production_files = set(
            production
        )

        sandbox_files = set(
            sandbox
        )

        added = sorted(
            sandbox_files - production_files
        )

        removed = sorted(
            production_files - sandbox_files
        )

        modified = sorted(
            path
            for path in (
                production_files & sandbox_files
            )
            if production[path] != sandbox[path]
        )

        unchanged = sorted(
            path
            for path in (
                production_files & sandbox_files
            )
            if production[path] == sandbox[path]
        )

        return {
            "added": added,
            "removed": removed,
            "modified": modified,
            "unchanged": unchanged,
            "changed": sorted(
                set(added)
                | set(removed)
                | set(modified)
            ),
            "production_file_count": len(
                production
            ),
            "sandbox_file_count": len(
                sandbox
            )
        }
