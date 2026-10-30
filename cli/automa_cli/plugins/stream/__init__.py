"""``plugins stream``: poll one step on a running host.

``commands`` registers the parser. ``service`` dispatches the step. ``perception``,
``memory`` and ``decision`` each interpret that step's probe. ``polling`` is the
shared refresh loop. ``render`` turns a probe into text or JSON.
"""
