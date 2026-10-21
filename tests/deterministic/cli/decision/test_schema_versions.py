"""Mixed controller versions report their schema before incompatible key sets."""
import unittest

from cli.automa_cli.decision import (
    DecisionSurfaceError, _strict_decode_authority, _strict_decode_command,
    _strict_decode_plan, _strict_decode_proposal,
)
from autonomy.decision_cycle.action.values import AUTHORITY_RESULT_SCHEMA
from autonomy.decision_cycle.plan.values import ACTION_PLAN_SCHEMA
from autonomy.decision_cycle.proposal.values import ACTION_PROPOSAL_SCHEMA, PROPOSED_VEHICLE_COMMAND_SCHEMA


class SchemaVersionTests(unittest.TestCase):
    def test_schema_mismatch_precedes_missing_and_extra_keys(self):
        decoders = (
            (_strict_decode_command, PROPOSED_VEHICLE_COMMAND_SCHEMA),
            (_strict_decode_proposal, ACTION_PROPOSAL_SCHEMA),
            (_strict_decode_plan, ACTION_PLAN_SCHEMA),
            (_strict_decode_authority, AUTHORITY_RESULT_SCHEMA),
        )
        for decoder, schema in decoders:
            with self.subTest(decoder=decoder.__name__):
                with self.assertRaisesRegex(DecisionSurfaceError, "schema must be"):
                    decoder({"schema": "previous-version", "obsolete": {}}, field="value")
                with self.assertRaisesRegex(DecisionSurfaceError, "key set is not exact export"):
                    decoder({"schema": schema, "obsolete": {}}, field="value")
