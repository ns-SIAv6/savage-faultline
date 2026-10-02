class Orchestrator:
    """Drives the pipeline: load, transform, persist, report."""

    def __init__(self, config):
        self.config = config
        self.state = {}

    def run(self):
        self.state["started"] = True
        return self.state
