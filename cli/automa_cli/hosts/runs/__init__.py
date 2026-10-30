"""Start, stop, monitor, and report host automation runs.

``commands`` registers run, stop, restart, and status. ``service`` starts,
stops, and restarts a run. ``worker`` is the background process. ``monitor``
watches the running host. ``store`` persists the run record. ``status``
collects host status, and ``render`` formats that status and the live run
transcript. The monitor emits optional data events and returns its outcome
without presentation. ``process`` launches, identifies, waits for, and
terminates workers. ``models`` holds startup, stop, restart and monitor
outcomes, along with the worker identities stop recognizes.
"""
