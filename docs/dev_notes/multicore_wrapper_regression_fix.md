# Multicore wrapper regression fix

## What broke

The earlier multicore wrapper was built from an older wrapper base.
It added `--workers`, but it **dropped the `input_changer.py` step** before the probabilistic run.

That means the run sequence became:

1. country siting
2. shipping
3. probabilistic model

instead of the intended sequence:

1. country siting
2. shipping
3. `input_changer.py`
4. probabilistic model

Because `input_changer.py` is the step that rewrites the live `input_parameters.json`, updates the model-side logistics file, and refreshes optimization/PPA outputs for the current country, skipping it can make multiple countries reuse stale inputs.

## What this fix does

This patch restores the correct wrapper flow **and** keeps multicore support.

The updated `trade_pipeline_spyder_wrapper.py`:

- runs `input_changer.py` for each country before the probabilistic model
- keeps the country-specific `--country-name`, `--country-iso3`, and `--output-dir` arguments
- passes `--workers <PROBABILISTIC_WORKERS>` into `probabilistic_model_runner.py`

The included `probabilistic_model_runner.py` is the multicore version.

## What to replace

Replace these two files in your project root:

- `trade_pipeline_spyder_wrapper.py`
- `probabilistic_model_runner.py`

with the files from this patch zip.

## Where to set the worker count

In `trade_pipeline_spyder_wrapper.py`, edit:

```python
PROBABILISTIC_WORKERS = max(1, min(4, (os.cpu_count() or 1)))
```

For exactly 4 logical cores, change it to:

```python
PROBABILISTIC_WORKERS = 4
```
