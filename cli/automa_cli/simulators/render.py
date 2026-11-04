"""Format supplied simulator results without performing operations."""
from ..providers.metadata import CHASE_SIM
from ..providers.urls import chase_operator_url
from .models import SimulatorResult


def render_simulator_result(result: SimulatorResult) -> str:
    if result.kind == "error":
        return result.error or ""
    if result.kind == "create_error":
        return f"Cannot create a simulator: {result.error}"
    if result.kind == "created":
        return f"Created {CHASE_SIM.heading_at(result.url)}"
    if result.kind == "deleted":
        return f"Deleted {CHASE_SIM.heading_at(result.url)}" + (", killed" if result.killed else "")
    if result.kind == "missing":
        names = ", ".join(chase_operator_url(url) for url in result.urls)
        return f"No created simulator at {result.url}. " + (f"Created: {names}." if names else "None created.")
    if result.kind == "blocked":
        hosts = result.hosts
        subject = f"Host {hosts[0]} uses" if len(hosts) == 1 else f"Hosts {', '.join(hosts)} use"
        return f"{subject} the simulator at {chase_operator_url(result.url)}; delete {'it' if len(hosts) == 1 else 'them'} first."
    raise ValueError(f"Unknown simulator outcome: {result.kind}")
