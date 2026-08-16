# Clinic Platform Backend

FastAPI + MongoDB control plane for the Apollo Clinic voice receptionist ([main write-up](https://github.com/AbhisekOmkar/clinic-voice-worker#readme)): the scheduling engine, patient records, per-call conversation state, the **mock PMS write-back API**, and LiveKit room/dispatch for web calls.

## The correctness core

**Real datastore.** MongoDB 7 (docker-compose). Clinic catalog is **real, sourced data**: Apollo Clinic's Indiranagar and HSR Layout branches, with practitioner names, specialties and session timings modelled from the clinic's public listings (apolloclinic.com / Practo / Apollo 247). One name is stored ALL-CAPS on purpose (`DR. MEERA SHRIDHAR`) to exercise the natural-pronunciation test case. Seed: `scripts/seed_clinic.py`.

**Double-booking is impossible at write time, not merely checked before it:**

1. Bookings only exist on each practitioner's fixed slot grid (validated server-side against their weekly schedule at that branch, in Asia/Kolkata).
2. A **partial unique index** on `(practitioner_id, start_utc)` for `status="confirmed"` makes two racing writes for one slot physically un-committable — the loser gets `409 SLOT_TAKEN` **with fresh alternatives** in the payload.
3. Buffer rules (e.g. 5-min gap after derm consults, 10-min after ortho/OB-GYN) are enforced by an overlap check before insert **and** re-verified after insert; if a concurrent neighbour slipped inside the buffer window, our insert rolls itself back and returns 409.

`tests/test_booking.py::test_concurrent_race_only_one_wins` fires 4 simultaneous bookings at one slot and asserts exactly one row exists.

**Timezone discipline.** Storage is naive-UTC; every user-facing computation goes through `app/utils/timeutil.py` in `Asia/Kolkata`. "Today" is IST-today (regression-tested), so a 11:55 PM booking can't drift to tomorrow.

**Fees only in the policy window.** ₹250 reschedule/cancel fee applies only within 4 h of the appointment; the API returns `change_fee.applies` so the agent can mention it *only* when true.

## Mock PMS (Cliniko-shaped), `/pms/api/v1`

A deliberately separate "external system": own auth header (`X-PMS-Key`), own collection, own ids.

- `POST /appointments` requires **`Idempotency-Key`**; replaying a key returns the original record (`replayed: true`) and creates nothing — retry storms cannot duplicate.
- **Defined failure behaviour:** chaos injection via `X-Chaos: fail` or `PMS_CHAOS_FAILURE_RATE`. A failed write never fails the caller's booking — the appointment stays confirmed with `pms.status="pending"` and a background outbox retries (idempotently) until synced or `failed` after N attempts, at which point it's surfaced on the dashboard for manual follow-up. Cancellations that fail set a tombstone and are retried the same way.
- The main backend calls the PMS over real HTTP with the appointment id as the idempotency key. Tested end-to-end in `tests/test_pms.py` (outage → pending → recovery → exactly one PMS record).

## Call-state APIs (what makes the hard scenarios work)

- `GET /agent/call-context?phone=` — one round-trip giving the worker: all patients on that number (family lines are one-to-many by design), upcoming appointments (with ids for reschedule), the latest **resumable dropped session** (≤30 min), and any **pending callback** (unanswered outbound call ≤48 h).
- `PUT /call-sessions/{call_id}` — the worker persists stage + collected facts + language every turn; a call that ends without completing flips to `dropped` and becomes resumable.
- `POST /outbound-calls` — records outbound attempts so a return call is recognised as a callback.
- `POST /followups` — escalation log (human request / clinical concern / billing) with an explicit "promise a callback, never a live transfer" contract.

## API surface (`/api/v1`)

`branches` · `practitioners` · `specialties` · `availability` (grid−bookings−buffers, weekday/window/near-time filters, `scope=earliest` across all practitioners+branches, always fresh with `as_of`) · `appointments` (book / reschedule / cancel / list / get) · `agent/call-context` · `call-sessions` · `outbound-calls` · `followups` · `calls` + transcripts · `call-latency-metrics` · `webcall` (room + token + agent dispatch) · `admin/reset` (eval isolation; local-only). Worker auth via `X-Internal-Service-Key` when set.

## Run

```bash
cp .env.example .env      # add LIVEKIT_* for web calls
make mongodb              # docker compose mongo:7
make install
make seed-fresh           # wipe + seed real clinic + create indexes
make run                  # :4226  (docs at /docs)
make test                 # 25 integration tests (real Mongo, in-process PMS)
```

`scripts/setup_sip_inbound.py +91XXXXXXXXXX` attaches a PSTN number (LiveKit inbound trunk + dispatch rule) once a SIP provider points at the LiveKit project.
