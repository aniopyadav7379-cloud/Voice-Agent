# ADR 001: Pre-tenant security events use a separate table, not a nullable `AuditLog.tenant_id`

**Status**: Accepted
**Date**: Session 8
**Context**: `AuditLog.tenant_id` is `NOT NULL` with a real foreign key to
`tenants.id`. Authentication failures that occur *before* a tenant can be
verified (tenant not found; participant identity malformed) therefore
cannot be written to `audit_logs` at all. Session 7 logged these to the
application logger only and flagged the gap.

## Security invariant this must satisfy

> NO AUDIT RECORD MAY CLAIM AN UNVERIFIED TENANT.

## Options considered

**Option 1 — keep `NOT NULL`, use application logs for pre-tenant failures.**
Satisfies the invariant. Rejected because application logs are not a
durable, queryable, retained security record. "Show me every failed
authentication attempt against this deployment in the last 30 days" is a
reasonable security question that grep-over-logs answers badly and that
log rotation can answer wrongly. The status quo, and not good enough.

**Option 2 — a separate model for pre-tenant-resolution security events.**
Satisfies the invariant structurally: a table with no tenant FK *cannot*
claim a verified tenant, because it has no field that asserts one.

**Option 3 — make `AuditLog.tenant_id` nullable.**
Rejected, and this is the important rejection. It looks like the smallest
change, but it is the one that actually breaks the invariant's intent:

- Every existing `audit_logs` consumer and every index
  (`ix_audit_logs_tenant_id_created_at`) is built on the guarantee that a
  row belongs to exactly one verified tenant. Nullable `tenant_id` turns
  every such query into one that must remember to handle `NULL`, and
  every tenant-scoped audit export into one that silently omits — or
  worse, silently includes — pre-auth noise.
- It conflates two genuinely different record types in one table:
  "tenant X's user did something" and "somebody unauthenticated failed to
  get in." Those have different retention needs, different access
  controls (a tenant admin may read their own audit trail; nobody's
  tenant admin should read global failed-auth telemetry), and different
  volumes (failed auth is attacker-controlled and can flood).
- `ON DELETE CASCADE` on the tenant FK means deleting a tenant currently
  removes their audit rows. Pre-auth events belong to no tenant and must
  not be swept up in, or orphaned by, tenant offboarding.

The task explicitly warned: *do not make `tenant_id` nullable merely to
make the test pass.* That warning is correct, and Option 3 is exactly the
shortcut it describes.

## Decision

**Option 2.** Add a `SecurityEvent` model (`security_events` table) for
events occurring before tenant verification. Key properties:

- **No foreign key to `tenants`, and no `tenant_id` column.** The
  invariant is enforced by schema shape, not by discipline. There is no
  field in which an unverified tenant *could* be claimed.
- `claimed_tenant_slug` / `claimed_tenant_id` are nullable **strings**,
  explicitly named "claimed" — they record what an unauthenticated party
  *asserted*, which is forensically valuable, while being structurally
  incapable of being mistaken for a verified relationship. A string is
  not a foreign key; nothing joins on it.
- `AuditLog` is left **completely unchanged** — still `NOT NULL`, still
  FK'd, still tenant-scoped. Every existing row, index, query, and test
  keeps its current meaning.

## Routing rule

| Situation | Table |
|---|---|
| Tenant verified (active, exists) — including rejections where the *user* was the problem | `audit_logs` |
| Tenant could not be verified (not found, or participant identity unparseable) | `security_events` |

Concretely, by exception type from `identity.py` / `livekit_identity.py`:

- `TenantNotFoundError`, `ParticipantIdentityError` → `security_events`
- `TenantInactiveError` → `audit_logs` (the tenant row was found and
  read; its status is a verified fact about a real tenant)
- `UserNotFoundError`, `UserInactiveError`, `UserTenantMismatchError` →
  `audit_logs` (tenant verification already succeeded)

## Consequences

- One new table, one Alembic migration, zero changes to existing audit
  data or behavior.
- Two places to look for authentication failures rather than one. Accepted
  deliberately: that split *is* the security boundary, and collapsing it
  for convenience is what Option 3 would have done.
- `security_events` has no tenant cascade, so it survives tenant deletion.
  It will need its own retention policy — **not implemented here**, and
  noted as a real remaining limitation rather than assumed handled.
