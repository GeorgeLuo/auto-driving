# Perception Candidates

Every candidate receives normalized front-camera images and emits the stable
`PerceptionText` shape through the generic worker. Candidate dependencies run
inside a candidate-local virtual environment so experiments do not expand the
core runtime requirements.

Use `automa vehicles perception candidates` for readiness and
`automa vehicles perception compare <image-dir>` for a no-write comparison of
all ready candidates. Add `--record` only when review artifacts are needed.
