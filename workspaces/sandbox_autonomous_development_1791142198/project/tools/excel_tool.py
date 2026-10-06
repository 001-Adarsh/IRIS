import pandas as pd
from pathlib import Path
from typing import Any, List, Dict

def create_excel(filename: str, data: List[Dict[str, Any]], sheet_name: str = "Sheet1", **kwargs) -> str:
    """Create an Excel file from a list of dictionaries and save inside data/."""
    path = Path(filename)
    if not path.is_absolute():
        data_dir = Path.home() / "IRIS" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / path.name
    else:
        path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(data)
    df.to_excel(str(path), sheet_name=sheet_name, index=False)
    return f"Excel file successfully created at: {path}"
