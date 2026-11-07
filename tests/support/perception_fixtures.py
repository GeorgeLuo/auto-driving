"""A perception plugin for tests, so a selection holds whatever the catalog offers."""

from __future__ import annotations

from autonomy.decision_cycle.activation import StepActivation, step_activation
from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch
from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract

LOCAL_PERCEPTION_SPEC = "tests.support.perception_fixtures:LocalPerception"


class LocalPerception:
    """A perception plugin under its configured id; it reports nothing."""

    contract = PerceptionPluginContract()

    def __init__(self, *, plugin_id: str, **_config) -> None:
        self.plugin_id = plugin_id

    def perceive(self, inputs) -> PerceptionEvidenceBatch:
        return PerceptionEvidenceBatch(signals=(), things=())


def local_perception(
    *plugin_ids: str, preset: str | None = None, configs: dict | None = None,
) -> StepActivation:
    """A perception activation of ``plugin_ids``; its specs offer a, b and c, as a catalog's do."""

    return step_activation(
        "perception", list(plugin_ids), dict.fromkeys("abc", LOCAL_PERCEPTION_SPEC),
        {plugin_id: {"plugin_id": plugin_id} for plugin_id in "abc"} | (configs or {}),
        metadata=None if preset is None else {"preset": preset},
    )
