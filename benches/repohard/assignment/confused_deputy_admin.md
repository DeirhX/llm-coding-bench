# Internal export ignores tenant boundary

`GET /internal/export` (via `api.internal.export` →
`service.admin_service.export_invoices`) lets a non-admin caller pass
`tenant_id` in the body and dump another tenant's invoices.

## Expected behavior

Non-admin callers may only export their own tenant. Exporting another
tenant requires `is_admin`. Admin export of any tenant remains allowed.

## Constraints

- Fix authorization in service and/or API layer.
- Do not remove the internal route.
