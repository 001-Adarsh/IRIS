from pathlib import Path
from core.development_engine import IRISDevelopmentEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_engine = IRISDevelopmentEngine(PROJECT_ROOT)

def create_sandbox_workspace(name="development"):
    snapshot = _engine.create_development_workspace(name)

    return (
        f"Sandbox workspace created.`n"
        f"Workspace: {snapshot['workspace']}`n"
        f"Project: {snapshot['project']}`n"
        f"Production modified: {snapshot['production_modified']}"
    )

def test_sandbox_workspace(project_path):
    return _engine.test_workspace(project_path)

def create_upgrade_proposal(objective, workspace, changes, test_results):
    return _engine.propose_upgrade(
        objective=objective,
        workspace=workspace,
        changes=changes,
        test_results=test_results
    )
