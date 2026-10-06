import unittest
from core.fast_planner import FastPlanner

class TestFastPlanner(unittest.TestCase):
    def test_plan_returns_list(self):
        planner = FastPlanner()
        result = planner.plan()
        self.assertIsInstance(result, list)
        self.assertIn("quick_action", result)

if __name__ == "__main__":
    unittest.main()
