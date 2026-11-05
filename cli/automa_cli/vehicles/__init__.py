"""``automa vehicles``: the commands, and the vehicle code hosts and simulators share.

- ``commands``: the ``vehicles`` parser and its handlers.
- ``discovery``: ``vehicles discover`` and ``remove``, and the vehicles status lists.
- ``store``: the discovered-vehicle records those commands keep.
- ``status``: collecting ``vehicles status``.
- ``models``: discovery and removal outcomes.
- ``render``: status and operation text from supplied facts.

What differs between a simulator and a PiCar, and how each is probed, lives in
``providers``. Records of simulators ``simulators create`` started live in
``simulators``.
"""
