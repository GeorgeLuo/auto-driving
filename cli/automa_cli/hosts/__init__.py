"""``automa hosts`` commands and the host services other commands use.

``commands`` registers the CLI. ``deployment`` manages host releases;
``runs`` starts, stops, monitors and reports sessions; ``publications`` reads
and validates host output; and ``views`` serves that output to viewers and
plugin streams. ``discovery`` finds hosts and running endpoints, ``staging``
reads the staged host configuration, and ``local`` supervises a Chase host
process. Step activations stay in ``step_activations``. A host's controller
bundle lives in ``releases``.
"""
