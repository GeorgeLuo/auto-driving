# FastSAM Region Proposals

This candidate runs pretrained `FastSAM-s` locally with no VLM and no training.
It emits class-agnostic `region_proposal` records with normalized bounds,
outer polygons, centroids, area, confidence, and lower-image contact.

The candidate intentionally does not claim that a region is an obstacle,
traversable, persistent, or at a known depth. Those meanings require separate
evidence and evaluation.

The environment and model are provisioned through Automa and remain ignored.
Ultralytics code and weights require an AGPL or commercial-license review before
this candidate can be promoted or embedded.

```sh
./cli/automa vehicles perception setup fastsam
./cli/automa vehicles perception apply path/to/images --candidate fastsam
```

The manifest pins `ultralytics==8.4.31` and the official v8.4.0
`FastSAM-s.pt` asset. Setup verifies the manifest-pinned SHA-256 before the
model becomes runnable.
