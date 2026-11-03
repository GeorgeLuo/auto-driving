"""``automa workbench``: replay local images through the controller steps.

- ``commands``: the top-level parser and replay handler.
- ``replay``: the CLI replay operation. It reports snapshots; it does not format them.
- ``render``: terminal text for a snapshot the command already has.
- ``presentation``: browser payloads for a frame. The page reads these, not the terminal text.
- ``runner``: replay state and playback controls, also used by the runtime viewer.
- ``plugins``: the selectable plugin catalog.

Image sources and frame execution live in the ``replay`` package. The viewer's
server, contracts and page assets live in ``frontend``.
"""
