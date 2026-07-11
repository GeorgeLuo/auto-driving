from __future__ import annotations

from autonomy.perception.interface import PerceivedThing


def thing_line(thing: PerceivedThing) -> str:
    bbox = thing.location.bbox_xyxy_norm
    bbox_text = "none" if bbox is None else ",".join(f"{value:.4f}" for value in bbox)
    prop_text = " ".join(
        f"{key}={value}"
        for key, value in sorted(thing.properties.items())
        if isinstance(value, (str, int, float, bool))
    )
    suffix = f" {prop_text}" if prop_text else ""
    return (
        f"thing id={thing.thing_id} kind={thing.kind} "
        f"zone={thing.location.zone} frame={thing.location.frame} "
        f"bbox_xyxy_norm={bbox_text} confidence={thing.confidence:.3f}"
        f"{suffix}"
    )
