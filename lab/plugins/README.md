# Plugin Workbench

Candidate plugins live under the stable stage they may eventually implement.
Each candidate owns its manifest, source, configuration, dependency declaration,
and research notes. The CLI harness tests the shared worker and output contract;
candidate-specific tests can remain beside the candidate. Generated environments,
weights, and run artifacts remain ignored.

Promotion is a deliberate move into `implementations/`; the runtime never
imports every lab candidate automatically.
