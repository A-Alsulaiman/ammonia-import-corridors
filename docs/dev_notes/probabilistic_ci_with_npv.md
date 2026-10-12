# Probabilistic runner update: include carbon intensity alongside NPV

## What changed

The updated `probabilistic_model_runner.py` now writes carbon-intensity outputs in the normal probabilistic run by default.

It keeps the existing outputs and adds:

- `CI` sheets: full yearly carbon-intensity trajectories for each pathway/technology.
- `CI_same_year` sheet: one carbon-intensity value per pathway for the same `time` as each simulation row.
- `NPV_with_CI` sheet: the existing NPV table merged with the same-year CI values for each pathway.

## Main behavior change

Previously, CI outputs were only written when the runner was called with `--include-ci`.

Now CI is **on by default**.

You can still disable it manually with:

```bash
python probabilistic_model_runner.py --skip-ci
```

## Files produced

The normal workbook now contains at least these sheets:

- `NPV`
- `Locations`
- `CI_AP SMR`
- `CI_AP CCS`
- `CI_AP BH2S`
- `CI_AP AEC`
- `CI_same_year`
- `NPV_with_CI`

No wrapper change is required as long as your wrapper already calls `probabilistic_model_runner.py`.
