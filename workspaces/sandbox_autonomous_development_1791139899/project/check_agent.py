from pathlib import Path

path = Path("core/agent.py")
text = path.read_text(encoding="utf-8")

print("task_id occurrences:", text.count('task_id = "TASK-"'))
print(
    "IRIS Task ID print occurrences:",
    text.count('print(f"IRIS Task ID: {task_id}")')
)
