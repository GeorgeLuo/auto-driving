# Physical perception viability

- result: `PASS`
- vehicle: `piracer`
- endpoint: `http://piracer.local:8887`
- elapsed_s: 60.468

## Metrics

- fresh_results_per_s: 1.9018
- processed_results_per_s: 1.9018
- result_age_ms: `{'count': 110, 'min': 5.0, 'p50': 235.0, 'p95': 513.0, 'max': 553.0, 'mean': 237.364}`
- duration_ms: `{'count': 110, 'min': 286.0, 'p50': 316.0, 'p95': 340.0, 'max': 372.0, 'mean': 316.945}`
- skipped_count_delta: 462
- control_always_zero: True
- mode_always_user: True
- host: `{'sample_count': 56, 'rss_mb': {'count': 56, 'min': 195.895, 'p50': 196.027, 'p95': 206.398, 'max': 211.531, 'mean': 197.188}, 'cpu_percent': {'count': 56, 'min': 93.7, 'p50': 93.7, 'p95': 93.7, 'max': 93.7, 'mean': 93.7}, 'pid': 17452, 'errors': []}`

## Gates

- PASS `fresh_results_meet_configured_cadence`: fresh_results_per_s=1.9018 required>=1.8000 (design_target=2.0, configured_hz=2.0000, fraction=0.9)
- PASS `p95_result_age_at_most_1s`: p95_result_age_ms=513.0 required<=1000.0
- PASS `control_always_zero`: control_always_zero=True
- PASS `mode_always_user`: mode_always_user=True
- PASS `healthy_samples_present`: healthy_sample_count=110

## Limits

- Polls the publication endpoint; does not instrument in-process Donkey loop counters directly.
- Freshness uses frame_id transitions and published result_age_ms/duration_ms fields.
- Host RSS/CPU are sampled from the remote manage.py process when SSH metrics are available.
