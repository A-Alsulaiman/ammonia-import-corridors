# Capacity-factor table fix

## What failed

The wrapper successfully reused siting and shipping outputs and `input_changer.py` successfully prepared the country-specific input files. The failure occurred inside `optimization.py` while building the capacity-factor table.

The traceback was:

```text
ValueError: Length mismatch: Expected axis has 8760 elements, new values have 1 elements
```

That happens because this legacy block:

```python
capacity_factor_df = pd.DataFrame(capacity_factors).transpose()
columns_names = ['CFs']
capacity_factor_df.columns = columns_names
capacity_factor_df.sort_index(inplace=True)
```

now expands each 8760-hour capacity-factor list into 8760 DataFrame columns. The rest of the model expects one column called `CFs`, where each cell contains the hourly list.

## Fix

Replace that block with:

```python
capacity_factor_df = pd.Series(capacity_factors, name='CFs').to_frame()
if not isinstance(capacity_factor_df.index, pd.MultiIndex):
    capacity_factor_df.index = pd.MultiIndex.from_tuples(
        capacity_factor_df.index,
        names=['year', 'city', 'country', 'type'],
    )
else:
    capacity_factor_df.index = capacity_factor_df.index.set_names(['year', 'city', 'country', 'type'])
capacity_factor_df.sort_index(inplace=True)
```

## Files in this patch

- `input_changer.py` — updated to patch `optimization.py` automatically before running it.
- `patch_optimization_capacity_factor.py` — standalone patcher for `optimization.py`.
- `optimization_capacity_factor_fix.diff` — manual diff.

## Recommended use

Copy the patched `input_changer.py` into the model folder, replacing the current one. Then rerun:

```python
runfile('C:/Users/user/Documents/Project/AP_IRA_Model/trade_pipeline_spyder_wrapper.py',
        wdir='C:/Users/user/Documents/Project/AP_IRA_Model')
```

The wrapper should reuse the existing siting and shipping outputs, then continue from `input_changer.py`.
