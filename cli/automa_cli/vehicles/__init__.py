"""``automa vehicles``: the commands, and the vehicle code hosts and simulators share.

- ``commands``: the ``vehicles`` parser and its handlers.
- ``discovery``: ``vehicles discover`` and ``remove``, and the vehicles status lists.
- ``store``: the discovered-vehicle records those commands keep.
- ``status``: collecting ``vehicles status``.
- ``render``: the text for a status payload the caller already collected.

What differs between a simulator and a PiCar, and how each is probed, lives in
``providers``. Records of simulators ``simulators create`` started live in
``simulators``.
"""
