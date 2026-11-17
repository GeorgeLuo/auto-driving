"""Host frames parse step records once and reject incomplete older records."""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.memory.interface import MemoryReport
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.runtime.frame_loop import FramePublication
from cli.automa_cli.hosts.publications.frame_publication import perception_text_from_publication
from cli.automa_cli.hosts.publications.host_publications import fetch_frame_publication
from cli.automa_cli.plugins.stream.perception import perception_publication
from tests.deterministic.autonomy.test_documents import DOCUMENTS


class ReportCycleTests(unittest.TestCase):
    def publication(self):
        # This registry sample has all six steps present, so none can escape parsing.
        return DOCUMENTS["autonomy.runtime.frame_loop.FramePublication"].samples()[1].to_dict()

    def test_each_step_is_parsed_once_and_repeated_reads_keep_the_values(self):
        payload = self.publication()
        with ExitStack() as stack:
            parsers = [stack.enter_context(patch.object(record_type, "from_dict", wraps=record_type.from_dict))
                       for record_type in (PerceptionText, Observation, MemoryReport, ProposalResult, ActionPlan, ActionResult)]
            publication = FramePublication.from_dict(payload)
            for _ in range(3):
                self.assertEqual(perception_text_from_publication(publication), publication.report.cycle.perception.text.strip())
                self.assertEqual(perception_publication(publication).thing_count, 1)
            for parse in parsers:
                parse.assert_called_once()
        self.assertEqual(publication.to_dict(), payload)

    def test_incomplete_older_memory_record_names_the_field_in_the_host_error(self):
        payload = self.publication()
        del payload["report"]["cycle"]["memory"]["beliefs"]
        with patch("cli.automa_cli.hosts.publications.host_publications._get_json", return_value=payload):
            with self.assertRaises(ConnectionError) as raised:
                fetch_frame_publication("http://fixture.invalid")
        self.assertEqual(str(raised.exception), "Invalid host report: report.cycle.memory.beliefs is missing")
