"""``plugins stream``: poll one step on a running host.

``commands`` registers the parser, chooses text or JSON, and clears the
terminal. ``service`` dispatches the step. ``perception``, ``memory`` and
``decision`` each interpret that step's probe. ``polling`` is the shared
refresh loop and does not open a view. ``session`` owns the local view a
continuous text stream serves. ``render`` turns a probe and the served view's
addresses into text or JSON.
"""
