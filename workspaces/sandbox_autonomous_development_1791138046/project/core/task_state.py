from pathlib import Path
import json
from datetime import datetime


class IRISTaskState:

    def __init__(self, state_dir=None):
        self.project_root = Path(__file__).resolve().parent.parent
        self.state_dir = Path(
            state_dir or self.project_root / "data" / "tasks"
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id):
        safe_id = str(task_id).replace("..", "_").replace("/", "_").replace("\\", "_")
        return self.state_dir / f"{safe_id}.json"

    def create(self, task_id, objective):
        state = {
            "task_id": task_id,
            "objective": objective,
            "status": "planning",
            "step": 0,
            "attempts": 0,
            "last_tool": None,
            "last_result": None,
            "verification": None,
            "next_action": None,
            "history": [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        self.save(state)
        return state

    def load(self, task_id):
        path = self._path(task_id)

        if not path.is_file():
            return None

        return json.loads(
            path.read_text(encoding="utf-8")
        )

    def save(self, state):
        state["updated_at"] = datetime.now().isoformat()

        path = self._path(
            state["task_id"]
        )

        path.write_text(
            json.dumps(
                state,
                indent=2,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

        return state

    def update(
        self,
        task_id,
        status=None,
        step=None,
        attempts=None,
        last_tool=None,
        last_result=None,
        verification=None,
        next_action=None
    ):
        state = self.load(task_id)

        if state is None:
            raise ValueError(
                f"Task does not exist: {task_id}"
            )

        if status is not None:
            state["status"] = status

        if step is not None:
            state["step"] = step

        if attempts is not None:
            state["attempts"] = attempts

        if last_tool is not None:
            state["last_tool"] = last_tool

        if last_result is not None:
            state["last_result"] = last_result

        if verification is not None:
            state["verification"] = verification

        if next_action is not None:
            state["next_action"] = next_action

        self.save(state)

        return state

    def add_history(self, task_id, entry):
        state = self.load(task_id)

        if state is None:
            raise ValueError(
                f"Task does not exist: {task_id}"
            )

        state["history"].append({
            "timestamp": datetime.now().isoformat(),
            **entry
        })

        self.save(state)

        return state

    def complete(self, task_id, result=None):
        return self.update(
            task_id,
            status="completed",
            verification={
                "passed": True,
                "reason": "Task completed."
            },
            last_result=result,
            next_action=None
        )

    def fail(self, task_id, reason):
        return self.update(
            task_id,
            status="failed",
            verification={
                "passed": False,
                "reason": reason
            },
            next_action=None
        )
