# Secret Grant and Task Controls Design

## Goal

Prevent workflow attempts from becoming stuck in `waiting_for_secret` when the
desktop host grants a summary API credential, and make supported task controls
clear and actionable inside the expanded task details.

The current workflow `wf_0fe602679e24` must resume from its existing transcript
checkpoint after the fix; transcription must not be repeated.

## Credential Grant Boundary

Electron Main will convert a `credentials_required` event into a
`secret.provide` request through a small, explicit parameter builder. The
builder will copy only the fields accepted by the v2 protocol:

- `workflow_id`
- `expected_attempt_id`, derived from the event's `attempt_id`
- `secret_request_id`
- `profile_id`
- `profile_version`
- `credential_ref`
- `purpose`
- `provider_binding_sha256`
- `secret`
- `lease_scope`, fixed to `attempt`

Event-only metadata such as `attempt_id` and `expires_at` will not cross the
request boundary. The Python protocol remains strict; it will not be relaxed to
accept unknown fields.

Credential lookup or `secret.provide` failures will be written to the existing
persistent Electron log with non-secret context. API keys and decrypted secrets
must never appear in logs.

## Expanded Task Controls

Controls remain inside the expanded task details. The task row does not gain
always-visible action buttons.

The supported actions are:

| Workflow status | Actions |
| --- | --- |
| `queued` | Cancel |
| `running` | Pause, Cancel |
| `paused` | Resume, Cancel |
| `waiting_for_secret` | Cancel |
| Terminal states | Existing retry/clear actions only |

While a control command is pending, the relevant task controls are disabled and
their labels reflect the pending action. A failed command is shown in the
expanded details for that workflow instead of relying only on the page-level
error area.

## Data Flow

1. Python emits `credentials_required` and persists `waiting_for_secret`.
2. Electron sends the event to the renderer and resolves the matching encrypted
   credential from the trusted profile snapshot.
3. The explicit builder produces a protocol-safe `secret.provide` request.
4. Python validates and accepts the request, marks the secret granted, and
   continues summary generation.
5. Workflow events update the Pinia store and expanded task details.

Task controls continue through the existing flow:

`WorkflowView -> workflowStore.control -> preload -> Electron Main -> workflow.control -> workflow event -> store`.

## Error Handling

- A credential grant failure is persisted in the Electron log without secret
  material.
- The workflow remains inspectable as `waiting_for_secret`; it is not falsely
  presented as an active model request.
- Control-command failures are attached to the selected workflow's expanded
  UI state and cleared after a later successful control command.
- Cancellation remains available while waiting for a credential.

## Tests and Validation

Implementation follows a red-green sequence.

1. Add an Electron-side regression test using a realistic
   `credentials_required` payload that contains `attempt_id` and `expires_at`.
   It must fail against the current spread-based request construction and pass
   only when unknown event fields are excluded.
2. Add focused UI logic coverage for the status-to-action mapping, including
   cancellation from `waiting_for_secret`.
3. Run the focused tests and `npm run typecheck` from
   `apps/desktop-electron`.
4. Start the corrected Electron/Python runtime and retry
   `wf_0fe602679e24` from `summarizing`, using the latest non-stale transcript
   artifact.
5. Verify the workflow leaves `waiting_for_secret`, produces summary/final
   artifacts, and reaches a terminal state. Visually verify the expanded task
   controls in the running, paused, and waiting-for-secret states when those
   states can be observed safely.

## Out of Scope

- Relaxing the Python v2 protocol validator.
- Moving controls onto collapsed task rows.
- Redesigning workflow scheduling or pause semantics.
- Re-transcribing the current recording.
- Fixing the separate initial-list/event merge race in the Pinia store.
