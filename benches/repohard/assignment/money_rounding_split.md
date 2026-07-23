# Money.split loses cents

`pkg.money.Money.split(parts)` is used when splitting credits across line
items. Finance noticed that split parts **do not sum** back to the original
cent amount (money disappears).

## Expected behavior

For any positive `parts`, `sum(p.cents for p in money.split(parts)) == money.cents`.
Parts may differ by at most 1 cent (remainder distribution).

## Constraints

- Keep integer cents (no floats in the final arithmetic).
- Preserve currency on each part.
