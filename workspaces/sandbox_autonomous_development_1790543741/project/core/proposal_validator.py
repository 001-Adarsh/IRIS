from pathlib import Path


class IRISProposalValidator:

    MAX_FILES = 50
    MAX_FILE_SIZE = 2 * 1024 * 1024

    ALLOWED_ACTIONS = {
        "create",
        "replace"
    }

    BLOCKED_SUFFIXES = {
        ".db",
        ".sqlite",
        ".sqlite3"
    }

    def __init__(self, project_path):
        self.project_path = Path(
            project_path
        ).resolve()

    def _validate_path(self, relative_path):

        if not isinstance(
            relative_path,
            str
        ):
            raise ValueError(
                "Proposal path must be a string."
            )

        if not relative_path.strip():
            raise ValueError(
                "Proposal path cannot be empty."
            )

        path = Path(
            relative_path
        )

        if path.is_absolute():
            raise PermissionError(
                f"Absolute path is not allowed: {relative_path}"
            )

        if any(
            part == ".."
            for part in path.parts
        ):
            raise PermissionError(
                f"Path traversal is not allowed: {relative_path}"
            )

        if any(
            part.startswith(".env")
            for part in path.parts
        ):
            raise PermissionError(
                f"Environment files are not allowed: {relative_path}"
            )

        if path.suffix.lower() in self.BLOCKED_SUFFIXES:
            raise PermissionError(
                f"Database files are not allowed: {relative_path}"
            )

        target = (
            self.project_path /
            path
        ).resolve()

        try:
            target.relative_to(
                self.project_path
            )
        except ValueError:
            raise PermissionError(
                f"Path escaped sandbox: {relative_path}"
            )

        return target

    def validate(self, plan):

        if not isinstance(
            plan,
            dict
        ):
            raise ValueError(
                "Development plan must be a JSON object."
            )

        changes = plan.get(
            "changes"
        )

        if not isinstance(
            changes,
            list
        ):
            raise ValueError(
                "Development plan changes must be a list."
            )

        if not changes:
            raise ValueError(
                "Development plan contains no changes."
            )

        if len(changes) > self.MAX_FILES:
            raise ValueError(
                f"Development plan exceeds "
                f"{self.MAX_FILES} file limit."
            )

        validated = []
        seen = set()

        for index, change in enumerate(
            changes,
            start=1
        ):

            if not isinstance(
                change,
                dict
            ):
                raise ValueError(
                    f"Change #{index} must be an object."
                )

            relative_path = change.get(
                "path"
            )

            action = change.get(
                "action"
            )

            content = change.get(
                "content"
            )

            target = self._validate_path(
                relative_path
            )

            if relative_path in seen:
                raise ValueError(
                    f"Duplicate path: {relative_path}"
                )

            seen.add(
                relative_path
            )

            if action not in self.ALLOWED_ACTIONS:
                raise ValueError(
                    f"Unsupported action for "
                    f"{relative_path}: {action}"
                )

            if not isinstance(
                content,
                str
            ):
                raise ValueError(
                    f"Content must be a string: "
                    f"{relative_path}"
                )

            if len(
                content.encode("utf-8")
            ) > self.MAX_FILE_SIZE:

                raise ValueError(
                    f"File exceeds 2 MB: "
                    f"{relative_path}"
                )

            if action == "create" and target.exists():
                raise ValueError(
                    f"Create action targets existing file: "
                    f"{relative_path}"
                )

            if action == "replace" and not target.exists():
                raise ValueError(
                    f"Replace action targets missing file: "
                    f"{relative_path}"
                )

            validated.append(
                {
                    "path": relative_path,
                    "action": action,
                    "target": str(target)
                }
            )

        return {
            "valid": True,
            "changes": validated,
            "count": len(validated)
        }
