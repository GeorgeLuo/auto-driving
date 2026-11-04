"""Implementation package for the automa CLI.

A capability is a package. The file name inside it is the responsibility:

- ``commands`` registers arguments, calls the operation, selects text or JSON, and maps the exit code.
- ``service`` performs the operation and returns data or events. A concrete name such as ``replay``, ``discovery``, or ``process`` stays when that name is the responsibility.
- ``models`` holds the requests, results, and events that cross those boundaries.
- ``render`` formats data it is given.
- ``store`` reads and writes that capability's records.
- ``process`` owns launch, identity checks, signals, and waits. ``worker`` is a background process entry. ``monitor`` observes a running process.
- ``registry`` selects one of the supported implementations by name.

Commands compose services and renderers. Services return outcomes and emit optional progress events; they do not take output streams or import command handlers or renderers. Renderers consume that data. Models do not depend on services, commands, or renderers. A parent command module may register the command modules of its children. Code used by more than one capability goes through an operation or through ``shared``. ``shared/cli`` holds argument and timeout adapters and does not import a feature package. Replay and releases keep their own packages.

Results and events follow one shape:

- Each operation returns its own result type from ``models``, with a ``Literal`` ``kind`` naming its outcomes, as ``hosts/runs/models`` does for start, stop, and restart. The renderer handles every kind and raises ``ValueError`` on one it does not know.
- An event carries a ``Literal`` ``kind`` and that kind's data. A test renders every kind, and emitting an unknown kind fails.
- An operation reads what a host sends into typed fields, so a renderer never parses a host's answer. Records the CLI builds itself, such as probes, receipts, and run records, may stay dictionaries.
- The module that builds a document sets its ``schema``: an operation for the records it returns or persists, a renderer for the receipts it assembles.
- Each package owns its errors: ``hosts/errors`` when a host cannot be found, staged, reached, or started, ``releases/errors`` when a controller release is missing or unreadable.

To add a command to an existing capability, register it in that capability's ``commands`` module and call the operation that already owns the work.
To add a capability, add a package with the roles it uses and register it from ``app`` or from the parent command module.
To add a provider, add its data in ``providers/metadata``, its probe callbacks in ``providers/registry``, and register the host operations it supports from the deployment service. Renderers read metadata without importing probes. An operation it does not support fails before the request changes a host.
To add an output format, teach the command to select it and the renderer to format the data the service already returns.
To share behavior across commands, put a generic helper in ``shared``, or a reused argument or output adapter in ``shared/cli``.

``plugins/step_formatting`` is the step text used by both the stream renderer and the inspect renderer. ``workbench/page_payloads`` builds the browser payloads. ``frontend`` keeps its server, loopback transport, and page modules, and the pages keep the asset names the browser requests. Host discovery reads existing endpoints; the run service explicitly asks ``local`` to start a local host. ``local_worker`` is its supervisor process entry. Host and step formatting helpers have no discovery, staging, or process dependencies. ``replay/models`` holds inputs and outcomes that presentation can import without loading a source or running a frame. The import roles are checked for every module in ``tests/deterministic/cli/test_module_boundaries.py``. Conventional filenames declare their role; classify each other filename explicitly there. Unknown files fail the check. Only ``app``, ``hosts/runs/worker``, and ``hosts/local_worker`` are process composition entrypoints. Package initializers hold documentation and metadata only.

The directory map for these packages is ``docs/reference/directory-structure.contract.json``.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
