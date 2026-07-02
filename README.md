# poe2-market-watch

Manual Path of Exile 2 currency market snapshot analysis and arbitrage decision
support.

This project scans POE2 Scout SnapshotPairs data for possible three-leg currency
exchange routes, estimates integer execution, applies gold-cost and liquidity
filters, and prints terminal output that helps you decide what to manually
validate in-game.

## Current Status

- v0.1 POC
- Working scanner
- Working watcher
- Gold-aware routing
- Liquidity-aware filtering
- Manual validation required
- Realized trade logging not yet implemented

## What It Does

- Fetches public POE2 Scout SnapshotPairs data.
- Reads a saved snapshot with `--snapshot-file`.
- Searches for routes that start and end in your budget currency.
- Plans whole-number trade quantities.
- Estimates gold costs with `gold_profile.json`.
- Filters routes by apparent liquidity and stock.
- Outputs human-readable terminal results or JSON with `--json`.
- Watches for fresh snapshot hashes and reruns the scanner when data changes.

## What It Does Not Do

- Does not automate the Path of Exile 2 client.
- Does not place trades.
- Does not click or type.
- Does not read game memory.
- Does not inspect game files.
- Does not screen-scrape or use OCR.
- Does not provide live order-book depth.

## Safety Disclaimer

This is manual decision support only. Snapshot data can be stale and does not
guarantee executable in-game trade depth. Always manually validate every route
leg in-game before buying anything.

Recommended validation order:

1. Exit leg first.
2. Middle leg second.
3. Start leg last.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Quick Start

Run the scanner:

```bash
python3 poe2checker.py --gold-profile gold_profile.json --max-gold 250000 --top 3
```

Run the watcher:

```bash
python3 poe2watcher.py --gold-profile gold_profile.json --max-gold 250000 --top 3 --run-initial
```

## Example Scanner Command

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

## Example Watcher Command

```bash
python3 poe2watcher.py \
  --budget-currency "Divine Orb" \
  --budget-amount 20 \
  --gold-profile gold_profile.json \
  --max-gold 250000 \
  --top 3 \
  --run-initial
```

## Gold Profile

`gold_profile.json` defines estimated exchange gold cost. The common model is
per output item received:

```text
gold_cost_for_leg = output_quantity_received * per_output_unit_gold_price
```

For example:

```text
134 Chaos Orb received * 160 gold = 21,440 gold
```

Update `per_output_unit` values when in-game gold costs change. See
[docs/gold_model.md](docs/gold_model.md) for details.

## Manual Validation Workflow

The scanner and watcher rank possible routes from snapshot data. Before any
manual execution, validate in-game:

1. Exit leg first: confirm the route can be unwound back to the budget currency.
2. Middle leg second: confirm the intermediate conversion.
3. Start leg last: only then consider starting the route.

This order avoids buying the first leg before confirming the later legs still
exist at usable quantities.

## Snapshot Data Caveat

SnapshotPairs data is aggregate snapshot market data, not live order-book depth.
It may be stale, rounded, incomplete, or already consumed by other players.

## Validation

Basic local validation does not require live API calls:

```bash
./scripts/validate_cli.sh
```

This checks:

```bash
python3 poe2checker.py --help
python3 poe2watcher.py --help
```

## Documentation

- [Workflow](docs/workflow.md)
- [Gold model](docs/gold_model.md)
- [Safety and terms](docs/safety_and_terms.md)

## Roadmap

- Realized trade logging
- Expected vs actual P&L tracking
- Missing gold price rejection
- Interactive live validation mode
- Session loot tracking
- Better route confidence scoring
