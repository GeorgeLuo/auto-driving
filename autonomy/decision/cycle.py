"""Decision-cycle import path.

Defined in ``autonomy.decision_cycle.cycle``. This path is that module, so
attribute access and patching reach the defining module.
"""

import sys

from autonomy.decision_cycle import cycle as _cycle

sys.modules[__name__] = _cycle
