"""
Backend regression tests for the Zoom-meeting bot manager (v9.1 screen-share fix run).
Scope: dashboard backend APIs only — auth, workers admin/self, tasks lifecycle,
       and credential validation.

The worker code (Playwright) is NOT exercised here. We only ensure no dashboard
API broke after the worker-side patch in /app/worker/zoom_worker_pool.py.
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else None
if not BASE_URL:
    # fall back to frontend .env (test agent context)
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break

API = f"{BASE_URL}/api"
ADMIN_EMAIL = "admin@finalzoom.com"
ADMIN_PASSWORD = "Admin@FinalZoom2026"


# ---------------- shared session-scope state ----------------
STATE = {
    "admin_session": None,
    "worker_token": None,
    "worker_id": None,
    "worker_name": None,
    "task_id": None,
    "chunk_id": None,
}


# ---------------- fixtures ----------------
@pytest.fixture(scope="session")
def admin_session():
    if STATE["admin_session"] is not None:
        return STATE["admin_session"]
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    STATE["admin_session"] = s
    return s


# ---------------- 1. AUTH ----------------
class TestAuth:
    def test_login_admin(self, admin_session):
        r = admin_session.post(f"{API}/auth/login", json={
            "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
        })
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
        data = r.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        assert "id" in data and isinstance(data["id"], str)
        # cookies set
        assert "access_token" in admin_session.cookies, "access_token cookie missing"
        assert "refresh_token" in admin_session.cookies, "refresh_token cookie missing"

    def test_auth_me(self, admin_session):
        r = admin_session.get(f"{API}/auth/me")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"

    def test_auth_me_unauth(self):
        r = requests.get(f"{API}/auth/me")
        assert r.status_code in (401, 403), r.text


# ---------------- 2. WORKERS (admin) ----------------
class TestWorkersAdmin:
    def test_create_worker(self, admin_session):
        name = f"TestRDP1-{uuid.uuid4().hex[:6]}"
        r = admin_session.post(f"{API}/workers", json={"name": name, "capacity_max": 50})
        assert r.status_code == 200, f"create worker failed: {r.status_code} {r.text}"
        data = r.json()
        assert data["name"] == name
        assert data["capacity_max"] == 50
        assert "token" in data and "." in data["token"], "one-time token missing"
        assert data["status"] in ("online", "offline")
        STATE["worker_token"] = data["token"]
        STATE["worker_id"] = data["id"]
        STATE["worker_name"] = name

    def test_list_workers_contains_new(self, admin_session):
        assert STATE["worker_id"], "previous test must have created the worker"
        r = admin_session.get(f"{API}/workers")
        assert r.status_code == 200, r.text
        items = r.json()
        ids = [w["id"] for w in items]
        assert STATE["worker_id"] in ids


# ---------------- 3. WORKER SELF HEARTBEAT ----------------
class TestWorkerSelf:
    def _wsession(self):
        s = requests.Session()
        s.headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {STATE['worker_token']}",
        })
        return s

    def test_heartbeat_persists(self):
        assert STATE["worker_token"], "worker token required"
        s = self._wsession()
        payload = {
            "current_load": 0,
            "cpu_pct": 10,
            "ram_pct": 20,
            "hostname": "test-rdp-1",
            "os_info": "linux test (Playwright v9.1-screenshare-fix)",
        }
        r = s.post(f"{API}/workers/me/heartbeat", json=payload)
        assert r.status_code == 200, f"heartbeat failed: {r.status_code} {r.text}"
        data = r.json()
        assert data["hostname"] == "test-rdp-1"
        assert data["cpu_pct"] == 10.0
        assert data["ram_pct"] == 20.0
        assert data["last_heartbeat"] is not None
        # admin GET should also reflect the values (persistence verification)
        admin = STATE["admin_session"]
        r2 = admin.get(f"{API}/workers")
        assert r2.status_code == 200
        mine = next(w for w in r2.json() if w["id"] == STATE["worker_id"])
        assert mine["hostname"] == "test-rdp-1"
        assert mine["status"] == "online"

    def test_worker_self_get(self):
        s = self._wsession()
        r = s.get(f"{API}/workers/me")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["id"] == STATE["worker_id"]


# ---------------- 4. validate-credentials ----------------
class TestValidateCredentials:
    def test_valid_meeting_id(self, admin_session):
        r = admin_session.post(f"{API}/tasks/validate-credentials", json={
            "meeting_id": "1234567890", "meeting_password": "",
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True
        assert data.get("meeting_id") == "1234567890"

    def test_invalid_meeting_id_alpha(self, admin_session):
        r = admin_session.post(f"{API}/tasks/validate-credentials", json={
            "meeting_id": "abcd", "meeting_password": "",
        })
        assert r.status_code == 400, f"expected 400 got {r.status_code} {r.text}"
        detail = (r.json() or {}).get("detail", "")
        assert "Wrong Meeting" in detail


# ---------------- 5. TASKS lifecycle ----------------
class TestTasksLifecycle:
    def test_create_task(self, admin_session):
        r = admin_session.post(f"{API}/tasks", json={
            "meeting_id": "1234567890",
            "meeting_password": "",
            "members": 5,
            "name_source": "NamesIn",
            "timeout": 300,
            "participant_reactions": False,
        })
        assert r.status_code == 200, f"create task failed: {r.status_code} {r.text}"
        data = r.json()
        assert data["status"] == "active"
        assert data["members"] == 5
        assert data["meeting_id"] == "1234567890"
        STATE["task_id"] = data["id"]

    def test_worker_claim(self):
        assert STATE["worker_token"], "worker token required"
        assert STATE["task_id"], "task must be created"
        s = requests.Session()
        s.headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {STATE['worker_token']}",
        })
        # The claim endpoint relies on _get_online_worker_count cache. Send a
        # fresh heartbeat first so this worker is considered online.
        s.post(f"{API}/workers/me/heartbeat", json={
            "current_load": 0, "cpu_pct": 5, "ram_pct": 10,
        })
        # small wait so any 5s cache rolls forward if needed
        time.sleep(1.0)
        r = s.post(f"{API}/workers/me/claim?max_tasks=5")
        assert r.status_code == 200, f"claim failed: {r.status_code} {r.text}"
        data = r.json()
        assert "tasks" in data
        # find our task in the claim result
        ours = [t for t in data["tasks"] if t["id"] == STATE["task_id"]]
        assert ours, f"our task not returned in claim. got={data}"
        t = ours[0]
        assert t["members"] <= 5
        assert "names" in t and isinstance(t["names"], list) and len(t["names"]) == t["members"]
        assert "chunk_id" in t and t["chunk_id"]
        STATE["chunk_id"] = t["chunk_id"]
        STATE["claimed_members"] = t["members"]

    def test_progress_update(self):
        s = requests.Session()
        s.headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {STATE['worker_token']}",
        })
        r = s.patch(f"{API}/tasks/{STATE['task_id']}/progress", json={"joined_count": 3})
        assert r.status_code == 200, f"progress failed: {r.status_code} {r.text}"
        data = r.json()
        # backend aggregates joined_count across chunks for the parent task
        assert data["joined_count"] >= 3, f"expected aggregate >=3 got {data['joined_count']}"

    def test_complete_chunk(self, admin_session):
        s = requests.Session()
        s.headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {STATE['worker_token']}",
        })
        # If the worker only claimed a partial chunk (e.g. 5 of 5 → full), completing it
        # should mark parent task as completed when total members_claimed == members
        # and all chunks done.
        r = s.post(f"{API}/tasks/{STATE['task_id']}/complete", json={
            "success": True, "joined_count": STATE.get("claimed_members", 0),
        })
        assert r.status_code == 200, f"complete failed: {r.status_code} {r.text}"
        # Fetch the task list from the user to inspect status
        # Admin can use /api/tasks/active or /api/tasks/previous depending on outcome.
        prev = admin_session.get(f"{API}/tasks/previous").json()
        active = admin_session.get(f"{API}/tasks/active").json()
        found_prev = [t for t in prev if t["id"] == STATE["task_id"]]
        found_active = [t for t in active if t["id"] == STATE["task_id"]]
        # If the entire 5 members were claimed by our one worker, task should now be
        # completed (in /previous). If not all 5 were claimed (e.g. weighted gave less),
        # it could still be active. Both are acceptable; we just confirm the API works.
        assert found_prev or found_active, "task disappeared from both lists"
        if found_prev:
            assert found_prev[0]["status"] in ("completed", "failed")


# ---------------- 6. CLEANUP ----------------
class TestCleanup:
    def test_cleanup_worker(self, admin_session):
        if not STATE.get("worker_id"):
            pytest.skip("no worker to clean")
        r = admin_session.delete(f"{API}/workers/{STATE['worker_id']}")
        assert r.status_code == 200, r.text

    def test_cleanup_task(self, admin_session):
        if not STATE.get("task_id"):
            pytest.skip("no task to clean")
        r = admin_session.delete(f"{API}/tasks/{STATE['task_id']}")
        # Allowed: 200 if still present; 404 if already cleaned by complete
        assert r.status_code in (200, 404), r.text
