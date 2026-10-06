import unittest
from datetime import datetime, timezone
from core.utils import format_timestamp

class TestUtils(unittest.TestCase):
    def test_format_timestamp_none(self):
        res = format_timestamp(None)
        self.assertIsInstance(res, str)
        self.assertEqual(len(res), 19)

    def test_format_timestamp_datetime(self):
        dt = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(format_timestamp(dt), "2026-09-21 12:00:00")

    def test_format_timestamp_numeric(self):
        res = format_timestamp(1700000000, fmt="%Y")
        self.assertEqual(res, "2023")

    def test_format_timestamp_string(self):
        res = format_timestamp("2026-09-21 12:34:56")
        self.assertEqual(res, "2026-09-21 12:34:56")

    def test_format_timestamp_invalid(self):
        with self.assertRaises(ValueError):
            format_timestamp("not-a-timestamp")

if __name__ == "__main__":
    unittest.main()
