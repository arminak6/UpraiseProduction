"""Public application and policy labels shared by execution and C5."""

APPLICATION_VERSION = "0.1.0"
INTERRUPT_PIPELINE_VERSION = "learner-interrupt-v1"
COGNITIVE_LOAD_POLICY_VERSION = "test-v1"
COGNITIVE_LOAD_THRESHOLD = 0.5


def cognitive_load_decision(score: float) -> str:
    return "HIGH_COGNITIVE_LOAD" if score > COGNITIVE_LOAD_THRESHOLD else "PROCEED"
