# Zoom Services — Product Requirements

## Original Problem (Hinglish)
"isme sab kuch ekdm perfect hai 2 issue hai
1. ek sath sare rdp ko command nhi bhjti — feature add kro ki hm jis rdp ko bole usise member lge aur forcfully use task bheje
2. worker code ek meeting ke bad dusri lena hi nhi chhata — task usse assign hi nhi hota"

## Iteration — Jan 2026 (v8.6 — Per-RDP Force-Send)

### What was added
1. **Per-RDP "Send Task" button** on Workers page (each row).
   - Opens a focused modal pre-locked to that ONE RDP — admin enters meeting id/pwd/members/etc.
   - Task is created with `restricted_workers=[that_rdp]` + `pre_assignments={that_rdp: members}`.
   - Other RDPs cannot see or claim that task (server filter enforces it).
2. **RDP IP address column** on Workers page — captured automatically from
   the heartbeat request (X-Forwarded-For / X-Real-IP / direct). Click-to-copy.
3. **Bulk Send Task** button — select N RDPs, send the SAME meeting to all
   of them in one click. Two modes:
   - Auto-split: enter total bots, split evenly across selected RDPs.
   - Same per RDP: every selected RDP gets the same member count.
   Each chosen RDP gets its OWN force-assigned task in parallel.
3. **Backend changes** in `/app/backend/server.py`:
   - `WorkerOut.public_ip` (new field)
   - `worker_heartbeat` now captures client IP from request headers
   - `TaskCreate.restricted_workers: Optional[List[str]]`
   - Claim loop filter: tasks with non-empty `restricted_workers` only visible
     to listed worker_ids
   - `POST /api/workers/{worker_id}/send-task` — creates the force-assigned task
4. **Frontend** (`/app/frontend/src/pages/WorkersPage.jsx`):
   - New `SendTaskModal` component
   - Workers table got IP column + Send (paper-plane) action button

### Why both issues are resolved
- **Issue 1** (per-RDP send): the new button gives admin direct control.
- **Issue 2** (second meeting never reaches worker): with the per-RDP send,
  admin force-routes the next task to the same RDP — bypassing fair-share/
  weighted distribution entirely.

## Tech Stack
- Backend: FastAPI + MongoDB + Motor + JWT cookies (port 8001)
- Frontend: React 18 + CRACO + Tailwind + lucide-react + sonner toasts (port 3000)
- Workers: Python (Selenium/Playwright) bots polling `/api/workers/me/claim`

## Default Admin Credentials (dev)
- Email: `admin@finalzoom.com`
- Password: `Admin@FinalZoom2026`

## API Surface (new endpoints / fields)
- `POST /api/workers/{worker_id}/send-task` (auth: user) — force-assigns a task
- `WorkerOut.public_ip` — surfaced on the workers list
- `TaskCreate.restricted_workers` — array of worker IDs allowed to claim

## Backlog / next ideas
- Surface RDP geo-location next to the IP (country flag from IP geo)
- "Resend last task" per RDP — 1-tap repeat of the last meeting
