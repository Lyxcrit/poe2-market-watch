# Gold Model

The gold model estimates exchange gold cost by the item received on each leg.
This matches the way the profile is normally maintained: each output item can
have a per-output-unit gold price.

Formula:

```text
gold_cost_for_leg = output_quantity_received * per_output_unit_gold_price
```

Example:

```text
134 Chaos Orb received * 160 gold = 21,440 gold
```

The scanner adds the estimated gold cost for every leg in a route. If
`--max-gold` is set, routes that exceed the cap are rejected.

## Updating gold_profile.json

Edit `gold_profile.json` and update values under `per_output_unit`.

Example:

```json
{
  "per_output_unit": {
    "Chaos Orb": 160,
    "Divine Orb": 800,
    "Exalted Orb": 120
  }
}
```

Keys must match the item names reported by the snapshot data. If a route uses an
item that is not present in `per_output_unit`, the script falls back to
`default_per_output_unit`.

The profile also supports:

- `default_per_trade`: fixed gold added for every leg.
- `default_per_input_unit`: gold per input item spent.
- `default_per_output_unit`: gold per output item received.
- `multiplier`: a global multiplier applied after the leg cost is computed.

Keep the profile conservative. Gold prices can change, and snapshot market data
does not guarantee live execution depth.
