"""Nested shutdown deadlines: outer supervisors must outlive the web drain."""
GOAL_DRAIN_TIMEOUT_SECONDS = 30.0
COMPONENT_STOP_TIMEOUT_SECONDS = 5.0
WEB_STOP_TIMEOUT_SECONDS = GOAL_DRAIN_TIMEOUT_SECONDS + 5.0
# Web plus operator/Telegram, with a small supervisor scheduling margin.
SERVICE_STOP_TIMEOUT_SECONDS = WEB_STOP_TIMEOUT_SECONDS + 2 * COMPONENT_STOP_TIMEOUT_SECONDS + 5.0
