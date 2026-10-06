from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def create_file(filename: str, content: str) -> str:

    file_path = DATA_DIR / filename

    file_path.write_text(content, encoding="utf-8")

    return str(file_path)


def read_file(*args, **kwargs) -> str:
    filename = (
        kwargs.get("filename")
        or kwargs.get("file_path")
        or kwargs.get("path")
        or kwargs.get("filepath")
        or (args[0] if len(args) > 0 else "")
    )
    if not filename:
        return "Error: No file specified."

    file_path = DATA_DIR / filename

    if not file_path.exists():
        return f"File not found: {filename}"

    return file_path.read_text(encoding="utf-8")


def list_files(*args, **kwargs) -> list[str]:
    # Extract pattern
    pattern = (
        kwargs.get("pattern")
        or kwargs.get("glob")
        or (args[0] if len(args) > 0 and isinstance(args[0], str) and "*" in args[0] else "*")
    )
    if not isinstance(pattern, str) or not pattern.strip():
        pattern = "*"

    # Extract target directory
    dir_arg = (
        kwargs.get("directory")
        or kwargs.get("path")
        or kwargs.get("dir")
        or kwargs.get("folder")
        or (args[0] if len(args) > 0 and isinstance(args[0], str) and "*" not in args[0] else None)
    )

    from pathlib import Path
    target_dir = Path(dir_arg) if dir_arg else DATA_DIR

    # If relative path, resolve relative to project root or DATA_DIR
    if not target_dir.is_absolute():
        project_root = Path(__file__).resolve().parent.parent
        candidate = (project_root / target_dir).resolve()
        if candidate.exists() and candidate.is_dir():
            target_dir = candidate
        else:
            target_dir = (DATA_DIR / target_dir).resolve()

    if not target_dir.exists() or not target_dir.is_dir():
        return []

    return [
        file.name
        for file in target_dir.glob(pattern)
        if file.is_file()
    ]

