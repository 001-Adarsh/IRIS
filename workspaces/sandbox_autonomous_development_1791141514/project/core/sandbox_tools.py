from pathlib import Path
import difflib

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_ROOT = (PROJECT_ROOT / "sandbox").resolve()


def _safe_path(path: str) -> Path:
    requested = (SANDBOX_ROOT / path).resolve()

    try:
        requested.relative_to(SANDBOX_ROOT)
    except ValueError:
        raise PermissionError(
            "Sandbox safety violation: path is outside the sandbox."
        )

    return requested


def sandbox_list_files(path: str = ".") -> str:
    target = _safe_path(path)

    if not target.exists():
        return f"Path does not exist: {path}"

    if not target.is_dir():
        return f"Not a directory: {path}"

    files = [
        str(item.relative_to(SANDBOX_ROOT))
        for item in sorted(target.rglob("*"))
        if item.is_file()
    ]

    return "\n".join(files) if files else "No files found."


def sandbox_read_file(path: str) -> str:
    target = _safe_path(path)

    if not target.exists():
        return f"File does not exist: {path}"

    if not target.is_file():
        return f"Not a file: {path}"

    return target.read_text(encoding="utf-8")


def sandbox_write_file(path: str, content: str) -> str:
    target = _safe_path(path)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    return f"Sandbox file written: {target.relative_to(SANDBOX_ROOT)}"


def sandbox_create_file(path: str, content: str = "") -> str:
    target = _safe_path(path)

    if target.exists():
        return f"File already exists: {path}"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    return f"Sandbox file created: {target.relative_to(SANDBOX_ROOT)}"


def sandbox_diff(original_path: str, modified_path: str) -> str:
    original = _safe_path(original_path)
    modified = _safe_path(modified_path)

    if not original.is_file():
        return f"Original file does not exist: {original_path}"

    if not modified.is_file():
        return f"Modified file does not exist: {modified_path}"

    old_lines = original.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = modified.read_text(encoding="utf-8").splitlines(keepends=True)

    result = "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=str(original.relative_to(SANDBOX_ROOT)),
            tofile=str(modified.relative_to(SANDBOX_ROOT))
        )
    )

    return result if result else "No differences detected."
