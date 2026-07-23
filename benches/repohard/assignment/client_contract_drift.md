# Client misreads invoice amounts

Sibling services using `client.LedgerClient` / `InvoiceDTO` show invoice
amounts 100× too large. The public API serializes `amount_cents` (int).
The client treats that field as major-unit dollars.

## Expected behavior

`InvoiceDTO.from_api` must interpret `amount_cents` as integer cents and
expose `amount` as major units (`cents / 100.0`). If both `amount` and
`amount_cents` are present, prefer the cents field for consistency with
the API.

## Constraints

- Fix the client package; API shape (`amount_cents`) stays.
