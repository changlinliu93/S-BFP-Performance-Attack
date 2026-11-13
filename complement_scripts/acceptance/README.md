# Acceptance and context classification

[classify.py](classify.py) defines technical-error exclusions, incorrect-password
exclusions, context evidence and decision denominators. It is called by
[statistics.py](../statistics.py).

```sh
python -m complement_scripts.reproduce
```

This prints acceptance and exclusion tables and writes `acceptance_rates.csv`,
`technical_error_counts.csv`, `wrong_password_counts.csv`, `sessions.csv` and
`excluded_sessions.csv` under the root-level `complement_result/` directory.

Final server results determine acceptance. A changed seed relative to enrollment
is an expected-rejection context under the study protocol; platform evidence is
used only when linked to the relevant seed. Unknown context is excluded from
context-policy correctness denominators. Wrong passwords and technical errors
are not counted as fingerprint rejections. Relayed changed-context rejections
are expected policy outcomes, not evidence that a relay was detected.
