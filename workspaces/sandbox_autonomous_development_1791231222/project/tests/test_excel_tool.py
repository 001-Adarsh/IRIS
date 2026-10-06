import unittest
from pathlib import Path

from tools.excel_tool import create_excel


class TestExcelTool(unittest.TestCase):
    def test_create_excel_with_data(self):
        file_path = create_excel(
            filename="test_excel_output.xlsx",
            data=[{"Rank": 1, "Name": "Mukesh Ambani"}],
            sheet_name="Richest",
        )
        self.assertTrue(Path(file_path).exists())
        self.assertTrue(Path(file_path).name.endswith(".xlsx"))

    def test_create_excel_defaults_to_top_10_india_dataset(self):
        file_path = create_excel(filename="top_10_richest_people_india.xlsx")
        self.assertTrue(Path(file_path).exists())
        self.assertIn("top_10_richest_people_india", Path(file_path).name)


if __name__ == "__main__":
    unittest.main()
