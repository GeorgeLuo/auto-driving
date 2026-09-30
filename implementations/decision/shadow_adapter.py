"""Engine spec path for the hold action engine.

Existing decision activations name this module. It is the same module object as
``implementations.runtime.engines.hold_action``, so reloading the engine through
this spec reloads the engine code.
"""

import sys

from implementations.runtime.engines import hold_action

sys.modules[__name__] = hold_action
