"""What a memory plugin may publish for later steps.

A memory plugin may publish the ``RetainedEvidence`` records it holds, as a
tuple at ``EVIDENCE_KEY``. Later steps read them there whichever plugin kept
them.

The key holds one value, the last one written. With several plugins that
publish evidence, later steps see only the last plugin's records; the others
keep theirs in their own ledgers, which appear in the memory report. The
report's ``evidence_publisher`` names the plugin whose records the key holds.
"""

EVIDENCE_KEY = "memory.evidence"
