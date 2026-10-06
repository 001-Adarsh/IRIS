import tempfile
import unittest
from pathlib import Path

from core.development_workflow import IRISDevelopmentWorkflow


class TestDevelopmentWorkflow(unittest.TestCase):
    def test_sandbox_snapshot_excludes_historical_deployment_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            active_source = project_root / "core" / "planner.py"
            active_source.parent.mkdir()
            active_source.write_text("def plan():\n    return []\n", encoding="utf-8")

            historical_source = (
                project_root
                / "deployment_backups"
                / "old"
                / "core"
                / "planner.py"
            )
            historical_source.parent.mkdir(parents=True)
            historical_source.write_text(
                "def plan():\n    if True\n", encoding="utf-8"
            )

            workflow = object.__new__(IRISDevelopmentWorkflow)
            workflow.project_root = project_root
            snapshot = workflow._create_sandbox_snapshot("test")

            sandbox_project = Path(snapshot["project"])
            self.assertEqual(
                (sandbox_project / "core" / "planner.py").read_text(
                    encoding="utf-8"
                ),
                "def plan():\n    return []\n",
            )
            self.assertFalse(
                (sandbox_project / "deployment_backups").exists()
            )


if __name__ == "__main__":
    unittest.main()
