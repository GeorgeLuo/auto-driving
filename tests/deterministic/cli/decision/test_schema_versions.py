"""Mixed controller versions report their schema before incompatible key sets."""
import unittest

from autonomy.decision_cycle.action.result import ACTION_RESULT_SCHEMA, ActionResult
from autonomy.decision_cycle.action.values import AUTHORITY_RESULT_SCHEMA, AuthorityResult
from autonomy.decision_cycle.plan.values import ACTION_PLAN_SCHEMA, ActionPlan
from autonomy.decision_cycle.proposal.result import PROPOSAL_RESULT_SCHEMA, ProposalResult
from autonomy.decision_cycle.proposal.values import (
    ACTION_PROPOSAL_SCHEMA,
    PROPOSED_VEHICLE_COMMAND_SCHEMA,
    ActionProposal,
    ProposedVehicleCommand,
)


class SchemaVersionTests(unittest.TestCase):
    def test_schema_mismatch_precedes_missing_and_extra_keys(self):
        decoders = (
            (ProposedVehicleCommand.decode, PROPOSED_VEHICLE_COMMAND_SCHEMA),
            (ActionProposal.decode, ACTION_PROPOSAL_SCHEMA),
            (ActionPlan.decode, ACTION_PLAN_SCHEMA),
            (AuthorityResult.decode, AUTHORITY_RESULT_SCHEMA),
            (ProposalResult.decode, PROPOSAL_RESULT_SCHEMA),
            (ActionResult.decode, ACTION_RESULT_SCHEMA),
        )
        for decoder, schema in decoders:
            with self.subTest(schema=schema):
                with self.assertRaisesRegex(ValueError, "schema must be"):
                    decoder({"schema": "previous-version", "obsolete": {}}, field="value")
                with self.assertRaisesRegex(ValueError, "missing=.*extra=.*obsolete"):
                    decoder({"schema": schema, "obsolete": {}}, field="value")
