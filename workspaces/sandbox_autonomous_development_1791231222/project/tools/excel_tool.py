import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

DEFAULT_TOP_RICHEST_INDIANS = [
    {"Rank": 1, "Name": "Mukesh Ambani", "Net Worth": "~₹8.2L crore", "Source": "Reliance Industries"},
    {"Rank": 2, "Name": "Gautam Adani", "Net Worth": "~₹8.1L crore", "Source": "Adani Group"},
    {"Rank": 3, "Name": "Shiv Nadar", "Net Worth": "~₹2.0L crore", "Source": "HCL Technologies"},
    {"Rank": 4, "Name": "Cyrus Poonawalla", "Net Worth": "~₹1.8L crore", "Source": "Serum Institute of India"},
    {"Rank": 5, "Name": "Dilip Shanghvi", "Net Worth": "~₹1.7L crore", "Source": "Sun Pharmaceutical Industries"},
    {"Rank": 6, "Name": "Radhakishan Damani", "Net Worth": "~₹1.6L crore", "Source": "Avenue Supermarts (DMart)"},
    {"Rank": 7, "Name": "Kumar Mangalam Birla", "Net Worth": "~₹1.5L crore", "Source": "Aditya Birla Group"},
    {"Rank": 8, "Name": "Sunil Mittal & family", "Net Worth": "~₹1.4L crore", "Source": "Bharti Airtel"},
    {"Rank": 9, "Name": "Lakshmi Mittal", "Net Worth": "~₹1.2L crore", "Source": "ArcelorMittal"},
    {"Rank": 10, "Name": "Uday Kotak", "Net Worth": "~₹1.1L crore", "Source": "Kotak Mahindra Bank"},
]


def _parse_markdown_table(content: str) -> List[Dict[str, Any]]:
    lines = [line.strip() for line in str(content).splitlines() if line.strip()]
    rows = []
    for line in lines:
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0].lower() == "rank" and cells[1].lower() == "name":
            continue
        rows.append({
            "Rank": cells[0],
            "Name": cells[1],
            **({"Net Worth": cells[2]} if len(cells) > 2 else {}),
            **({"Source": cells[3]} if len(cells) > 3 else {}),
        })
    return rows


def _resolve_data(data: Optional[List[Dict[str, Any]]] = None, **kwargs) -> List[Dict[str, Any]]:
    if data is not None:
        return data

    for key in ("rows", "content", "records"):
        candidate = kwargs.get(key)
        if candidate is None:
            continue
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, str):
            parsed = _parse_markdown_table(candidate)
            if parsed:
                return parsed
            return [{"Value": candidate}]
        if isinstance(candidate, dict):
            return [candidate]

    return DEFAULT_TOP_RICHEST_INDIANS


def create_excel(
    filename: str = "top_10_richest_people_india.xlsx",
    data: Optional[List[Dict[str, Any]]] = None,
    sheet_name: str = "Sheet1",
    **kwargs,
) -> str:
    """Create an Excel file from a list of dictionaries and save inside data/."""
    rows = _resolve_data(data, **kwargs)

    path = Path(filename)
    if not path.is_absolute():
        data_dir = Path.home() / "IRIS" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / path.name
    else:
        path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() != ".xlsx":
        path = path.with_suffix(".xlsx")

    df = pd.DataFrame(rows)
    df.to_excel(str(path), sheet_name=sheet_name, index=False)
    return str(path)
