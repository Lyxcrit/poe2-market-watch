# Workflow

This project is a terminal-based decision-support tool for Path of Exile 2
currency exchange market snapshots. It is not a bot and it does not automate
the game client.

## Scanner Mode

`poe2checker.py` loads POE2 Scout SnapshotPairs data, either directly from the
snapshot API or from a local file with `--snapshot-file`.

The scanner builds a filtered exchange graph, searches for three-leg routes that
start and end in the selected budget currency, applies integer route planning,
estimates gold cost, and ranks the best candidates.

Useful scanner controls include:

- `--budget-currency` and `--budget-amount`
- `--min-edge` and `--fee-buffer`
- liquidity filters such as `--min-row-volume`, `--min-side-traded`,
  `--min-stock`, `--max-middle-units`, `--min-exit-row-volume`, and
  `--max-liquidity-use`
- gold controls such as `--gold-profile`, `--max-gold`, and `--sort profit_per_gold`
- `--json` for machine-readable output
- `--snapshot-file` for replaying a saved snapshot

## Watcher Mode

`poe2watcher.py` polls the same SnapshotPairs endpoint, hashes the raw snapshot,
and only runs `poe2checker.py` when a fresh snapshot hash appears. It writes
snapshot files under `.poe2watcher/` and renders a compact Rich terminal UI.

Use `--run-initial` when you want a first-run scan immediately instead of
waiting for the next snapshot change.

## POC Workflow

The successful proof-of-concept workflow is:

1. Snapshot discovery: wait for a fresh market snapshot.
2. Gold-aware ranking: rank routes by expected profit, estimated gold cost, and
   liquidity filters.
3. Manual validation: check the candidate route in-game before any trade.
4. Manual execution: place any trades yourself only after validation.

Manual validation should happen in this order:

1. Exit leg first.
2. Middle leg second.
3. Start leg last.

This reduces the chance of buying into a route that no longer has a viable exit.

## Manual Only

The application does not click, type, scrape the screen, read memory, hook the
client, or place trades. Snapshot data is not live order-book depth, so every
leg must be manually validated in-game.
