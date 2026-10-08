# Session-First Kiosk Integration Contract

This contract is the first joint boundary between Justin's kiosk UI and the Odoo backend.

## Invariant

Check-in is always session-first:

live session -> actual roster -> member identification -> eligibility -> attendance command -> receipt -> privacy clear

The kiosk must never identify a member first and guess which class attendance record to create.

## UI state

The selected session ID is established before member identification and remains immutable for the check-in attempt.

Minimum client state:
```ts
type KioskCheckInState = {
  sessionId: string;
  memberId?: string;
  status:
    | "selecting-session"
    | "loading-roster"
    | "identifying-member"
    | "checking-eligibility"
    | "confirming"
    | "submitting"
    | "success"
    | "error"
    | "offline";
};
```

## Command envelope

```ts
export interface CommandEnvelope<T> {
  idempotencyKey: string;
  correlationId: string;
  expectedVersion?: number;
  payload: T;
}

export type AttendanceCheckInPayload = {
  memberId: string;
  sessionId: string;
};
```

Browser-provided organization/company/database identifiers are not trusted as tenant authority.

## Success receipt

```ts
export type AttendanceReceipt = {
  attendanceId: string;
  memberId: string;
  sessionId: string;
  checkedInAt: string;
  status: "present" | "late";
  replayed?: boolean;
  correlationId: string;
};
```

The UI only shows success after the backend returns a receipt.

## Required problem codes

At minimum, the UI must handle:

- INVALID_COMMAND
- FORBIDDEN
- SESSION_UNAVAILABLE
- MEMBER_UNAVAILABLE
- NOT_ON_ROSTER
- MEMBERSHIP_INACTIVE
- ENTITLEMENT_INELIGIBLE
- ALREADY_CHECKED_IN or idempotent success replay
- IDEMPOTENCY_CONFLICT
- VERSION_CONFLICT
- BACKEND_UNAVAILABLE

Errors shown on the unattended kiosk must not disclose debt, billing details, medical notes, private guardian data, or internal diagnostics.

## Backend rules

- Session must exist and be in the permitted company/location context.
- Member must be authorized for the device/session context.
- Existing registered roster entry is required for ordinary kiosk attendance.
- Capacity does not block attendance for a student who already owns a registered roster seat.
- Eligibility uses the subscription/entitlement that covers the selected class.
- Attendance write and command receipt must be duplicate-safe.
- AI is not in the required path.
- Rank or billing mutations cannot originate from this kiosk command.
- Offline billing is never allowed.

## Privacy reset

After successful receipt, cancel, timeout, or explicit finish:
- clear member identity
- clear family context
- clear eligibility result
- clear private errors
- preserve only non-private session selection if the kiosk product decision allows it

## Demo test matrix

| Case | Expected |
| --- | --- |
| Registered eligible member | success receipt |
| Same request replayed | same result, no duplicate attendance |
| Same idempotency key, different command | conflict |
| Member not on selected roster | reject |
| Cancelled/paused member | reject |
| Wrong company/location | reject |
| Session closed/unavailable | reject |
| Backend offline before write | error/offline state, no false success |
| AI unavailable | check-in still works |
