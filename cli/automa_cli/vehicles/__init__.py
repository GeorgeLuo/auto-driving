"""``automa vehicles``: the commands, and the vehicle code hosts and simulators share.

- ``commands``: the ``vehicles`` parser and its handlers.
- ``status``: ``vehicles status``.
- ``discovery``: ``vehicles discover`` and ``remove``, the vehicles status lists, and the
  records of the vehicles discovered and the simulators ``simulators create`` started.

What differs between a simulator and a PiCar, and how each is probed, lives in
``providers``.
"""
