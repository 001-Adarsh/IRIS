from core.planner import Planner

class FastPlanner(Planner):
    def plan(self, *args, **kwargs):
        # Return a simple plan immediately
        return ["quick_action"]
