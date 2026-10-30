"""``automa workbench``: replay local images through the controller steps.

- ``commands``: the top-level parser and replay handler.
- ``replay``: CLI replay lifecycle and terminal output.
- ``runner``: replay state and playback controls, also used by the runtime viewer.
- ``plugins`` and ``presentation``: selectable plugins and displayed step results.

Image sources and frame execution live in ``shared.replay``. The viewer's server,
contracts and page assets live in ``frontend``.
"""
