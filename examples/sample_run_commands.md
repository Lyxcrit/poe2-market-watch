# Sample Run Commands

## Scanner

```bash
python3 poe2checker.py \
  --budget-currency "Divine Orb" \
  --budget-amount 20 \
  --min-edge 0.08 \
  --fee-buffer 0.03 \
  --min-row-volume 50000 \
  --min-side-traded 25 \
  --min-stock 50 \
  --max-middle-units 500 \
  --min-exit-row-volume 100000 \
  --max-liquidity-use 0.10 \
  --gold-profile gold_profile.json \
  --max-gold 250000 \
  --sort profit_per_gold \
  --top 3
```

## Watcher

```bash
python3 poe2watcher.py \
  --budget-currency "Divine Orb" \
  --budget-amount 20 \
  --gold-profile gold_profile.json \
  --max-gold 250000 \
  --top 3 \
  --run-initial
```
