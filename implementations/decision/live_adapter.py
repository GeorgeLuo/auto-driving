"""Engine spec path for the mode-gated action engine.

Existing decision activations name this module. It is the same module object as
``implementations.runtime.engines.mode_gated_action``, so reloading the engine through
this spec reloads the engine code.
"""

import sys

from implementations.runtime.engines import mode_gated_action

sys.modules[__name__] = mode_gated_action
