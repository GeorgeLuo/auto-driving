"""Hold owns its authority constraints; the action decoder enforces them."""

from copy import deepcopy
import unittest

from autonomy.decision_cycle.action.hold import idle_output
from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.action.values import AuthorityResult
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.proposal.values import ProposedVehicleCommand
from autonomy.runtime.report import decode_decision_records


def held_action():
    return ActionResult(
        frame_id="frame", status="ok",
        authority=AuthorityResult(
            frame_id="frame", gate_id="hold", proposed=None,
            cycle_status="ok", authorized_output=idle_output(),
        ),
    ).to_dict()


class HoldDecodeTests(unittest.TestCase):
    def test_hold_round_trip_and_a_non_hold_gate_remain_supported(self):
        payload = held_action()
        self.assertEqual(ActionResult.decode(payload).to_dict(), payload)
        payload["authority"]["gate_id"] = "custom_gate"
        payload["authority"]["authorized_output"]["steering"] = 0.5
        payload["authority"]["proposed_equals_authorized"] = False
        self.assertEqual(ActionResult.decode(payload).control.steering, 0.5)

    def test_a_forged_moving_hold_is_rejected_by_action_and_cycle_decoding(self):
        payload = held_action()
        payload["authority"]["authorized_output"]["steering"] = 0.5
        payload["authority"]["proposed_equals_authorized"] = False
        cycle = {"proposal": ProposalResult("frame", "ok").to_dict(), "plan": None, "action": payload}
        for decode, record in ((ActionResult.decode, payload), (decode_decision_records, cycle)):
            with self.subTest(decode=decode.__qualname__):
                with self.assertRaisesRegex(ValueError, "authorized_output"):
                    decode(deepcopy(record))

    def test_a_hold_cannot_claim_to_apply_even_an_idle_proposal(self):
        payload = held_action()
        payload["authority"].update(
            proposed=ProposedVehicleCommand(0, 0).to_dict(),
            proposed_applied=True, proposed_equals_authorized=True,
        )
        with self.assertRaisesRegex(ValueError, "proposed_applied must be false"):
            ActionResult.decode(payload)
