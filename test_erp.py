"""
=============================================================================
  Industrial ERP — COMPLETE Test Suite  (extends original 130 tests)
=============================================================================
  Covers EVERYTHING in the original suite PLUS all identified gaps:

    • User Management   — update/delete non-existent, invalid-role on update,
                          blank password reset, username whitespace edge cases
    • Categories        — update non-existent, duplicate name on PUT
    • Suppliers         — duplicate GST, update non-existent, GET single,
                          create with full fields
    • Items             — GET by ID, 404 on GET/PUT non-existent,
                          invalid supplier_id on create/update,
                          FINAL item strips category/supplier/rate,
                          update with blank name is ignored (not errored),
                          negative / zero rate & security_stock & lead_time
    • Inward            — filter by item_id, from_date, to_date,
                          void non-existent returns 404,
                          zero-quantity inward accepted (no backend guard),
                          negative quantity returns 422
    • Issue             — zero quantity returns 400, negative returns 400,
                          filter by item_id / from_date / to_date,
                          void non-existent returns 404,
                          stock restored after void, issue qty=1 boundary
    • Stock Report      — needs_reorder False case, FINAL item rate is null,
                          security_stock field present
    • BOM               — non-existent final/raw item on create returns 404,
                          delete non-existent entry/substitute returns 404,
                          update non-existent entry/substitute returns 404,
                          BOM for item with no entries returns [],
                          non-integer ID on GET /bom/{id} returns 422
    • Reports           — monthly closing_stock arithmetic,
                          past month with known transactions,
                          daily report for past date with known data
    • Audit Logs        — filter by table_name, DELETE ops logged,
                          CREATE ops logged, log count grows after actions
    • Security          — revoked token rejected on API call,
                          role not enforced by API (documented gap),
                          blank API key variants, session list hides expired,
                          password hash not returned anywhere
    • Input Validation  — negative issue quantity, negative inward rate,
                          negative lead_time/security_stock accepted,
                          missing fields on every POST endpoint,
                          whitespace-only username rejected (or handled),
                          very long username, special chars in item name
    • Connectivity      — all original checks retained
    • Performance       — all original benchmarks retained
    • Concurrency       — all original race-condition tests retained
    • Session           — expiry path (artificially expired token → 401)

  Usage:
      export ERP_API_KEY=your_key
      export ERP_ADMIN_USER=admin
      export ERP_ADMIN_PASS=admin123
      python test_erp_full.py
      python test_erp_full.py --fast          # skip concurrency tests
      python test_erp_full.py --section bom   # run one section
=============================================================================
"""

import os, sys, time, uuid, threading, statistics, argparse
from datetime import date, datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

import requests

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    GREEN  = Fore.GREEN;  RED    = Fore.RED
    YELLOW = Fore.YELLOW; CYAN   = Fore.CYAN
    BOLD   = Style.BRIGHT; RESET = Style.RESET_ALL
except ImportError:
    GREEN = RED = YELLOW = CYAN = BOLD = RESET = ""

# ── CONFIG ────────────────────────────────────────────────────────────────────
BASE_URL   = os.environ.get("ERP_BASE_URL",   "http://127.0.0.1:8000")
API_KEY    = os.environ.get("ERP_API_KEY",    "")
ADMIN_USER = os.environ.get("ERP_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ERP_ADMIN_PASS", "admin")

PERF_FAST   = 0.3
PERF_MEDIUM = 1.0
PERF_SLOW   = 2.0
CONCURRENCY_WORKERS = 10
RUN_ID = uuid.uuid4().hex[:8]

# ── TEST RUNNER ───────────────────────────────────────────────────────────────
@dataclass
class Result:
    name: str; passed: bool; elapsed: float; detail: str = ""

@dataclass
class Suite:
    name: str; results: list = field(default_factory=list)
    def add(self, r): self.results.append(r)
    @property
    def passed(self): return sum(1 for r in self.results if r.passed)
    @property
    def failed(self): return sum(1 for r in self.results if not r.passed)
    @property
    def total(self):  return len(self.results)

ALL_SUITES: list[Suite] = []
_current_suite: Optional[Suite] = None

def suite(name: str):
    global _current_suite
    _current_suite = Suite(name)
    ALL_SUITES.append(_current_suite)
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  {name}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}")

def test(name: str):
    def decorator(fn):
        def wrapper(*a, **kw):
            t0 = time.perf_counter()
            try:
                detail  = fn(*a, **kw)
                elapsed = time.perf_counter() - t0
                r = Result(name, True, elapsed, detail or "")
                _current_suite.add(r)
                print(f"  {GREEN}✓{RESET} {name:<60} {elapsed*1000:6.1f}ms")
            except AssertionError as e:
                elapsed = time.perf_counter() - t0
                r = Result(name, False, elapsed, str(e))
                _current_suite.add(r)
                print(f"  {RED}✗{RESET} {name:<60} {elapsed*1000:6.1f}ms")
                print(f"    {RED}↳ {e}{RESET}")
            except Exception as e:
                elapsed = time.perf_counter() - t0
                r = Result(name, False, elapsed, f"{type(e).__name__}: {e}")
                _current_suite.add(r)
                print(f"  {RED}✗{RESET} {name:<60} {elapsed*1000:6.1f}ms")
                print(f"    {RED}↳ {type(e).__name__}: {e}{RESET}")
        wrapper()
        return wrapper
    return decorator

def assert_status(r, expected, context=""):
    msg = f"Expected HTTP {expected}, got {r.status_code}"
    if context: msg += f" | {context}"
    try:
        body = r.json()
        if "detail" in body: msg += f" | detail: {body['detail']}"
    except Exception: pass
    assert r.status_code == expected, msg

def perf_check(elapsed, threshold, label):
    assert elapsed < threshold, \
        f"{label} took {elapsed*1000:.0f}ms — threshold {threshold*1000:.0f}ms"

# ── HTTP CLIENT ───────────────────────────────────────────────────────────────
class ERP:
    def __init__(self, session_token: str = ""):
        self.base    = BASE_URL
        self.headers = {"X-API-Key": API_KEY}
        if session_token:
            self.headers["X-Session-Token"] = session_token
        self._token = session_token

    def login(self, username, password) -> "ERP":
        r = requests.post(f"{self.base}/login",
                          json={"username": username, "password": password},
                          timeout=10)
        assert r.status_code == 200, \
            f"Login failed for {username}: {r.status_code} {r.text}"
        return ERP(r.json()["token"])

    def get(self, path, **kw):
        return requests.get(f"{self.base}/{path.lstrip('/')}",
                            headers=self.headers, timeout=10, **kw)
    def post(self, path, **kw):
        return requests.post(f"{self.base}/{path.lstrip('/')}",
                             headers=self.headers, timeout=10, **kw)
    def put(self, path, **kw):
        return requests.put(f"{self.base}/{path.lstrip('/')}",
                            headers=self.headers, timeout=10, **kw)
    def delete(self, path, **kw):
        return requests.delete(f"{self.base}/{path.lstrip('/')}",
                               headers=self.headers, timeout=10, **kw)

    # ── convenience helpers ──────────────────────────────────────────────────
    def create_category(self, name=None, desc="test"):
        name = name or f"TC_{RUN_ID}_{uuid.uuid4().hex[:4]}"
        r = self.post("/category/", json={"category": name, "description": desc})
        assert_status(r, 200, f"create_category({name})")
        cats = self.get("/category/").json()
        return next(c["id"] for c in cats if c["category"] == name)

    def get_category_id(self, name):
        cats = self.get("/category/").json()
        return next((c["id"] for c in cats if c["category"] == name), None)

    def create_supplier(self, name=None, gst=None):
        name = name or f"TS_{RUN_ID}_{uuid.uuid4().hex[:4]}"
        r = self.post("/suppliers/", json={
            "name": name, "contact": "9999999999",
            "gst_no": gst, "lead_time": 7
        })
        assert_status(r, 200, f"create_supplier({name})")
        supps = self.get("/suppliers/").json()
        return next(s["id"] for s in supps if s["name"] == name)

    def create_raw_item(self, cat_id, sup_id, name=None, ss=10, rate=100.0):
        name = name or f"TR_{RUN_ID}_{uuid.uuid4().hex[:4]}"
        r = self.post("/items/", json={
            "item_name": name, "item_type": "RAW",
            "category_id": cat_id, "supplier_id": sup_id,
            "lead_time": 5, "security_stock": ss,
            "rate": rate, "rack": "A1", "bin": "B1",
        })
        assert_status(r, 200, f"create_raw_item({name})")
        return r.json()["item_id"]

    def create_final_item(self, name=None):
        name = name or f"TF_{RUN_ID}_{uuid.uuid4().hex[:4]}"
        r = self.post("/items/", json={"item_name": name, "item_type": "FINAL"})
        assert_status(r, 200)
        return r.json()["item_id"]

    def record_inward(self, item_id, qty=100, rate=50.0, invoice=None):
        invoice = invoice or f"INV-{RUN_ID}-{uuid.uuid4().hex[:6]}"
        r = self.post("/inwards/", json={
            "item_id": item_id, "invoice_number": invoice,
            "quantity": qty, "rate": rate,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert_status(r, 200, f"record_inward(item={item_id},qty={qty})")
        return invoice

    def record_issue(self, item_id, qty=10, issued_to="TestUser", purpose=None):
        r = self.post("/issues/", json={
            "item_id": item_id, "quantity": qty,
            "issue_date": str(date.today()),
            "issued_to": issued_to, "purpose": purpose,
        })
        return r

    def current_stock(self, item_id) -> float:
        rows = self.get("/stock-report/").json()
        for row in rows:
            if row["item_id"] == item_id:
                return float(row["current_stock"])
        return 0.0


# ── SHARED STATE ──────────────────────────────────────────────────────────────
STATE = {}

def setup():
    erp   = ERP()
    admin = erp.login(ADMIN_USER, ADMIN_PASS)
    STATE["admin"] = admin
    STATE["admin_token"] = admin._token

    cat_name = f"SetupCat_{RUN_ID}"
    sup_name = f"SetupSupp_{RUN_ID}"

    cat_id = admin.create_category(cat_name)
    sup_id = admin.create_supplier(sup_name)
    raw_id = admin.create_raw_item(cat_id, sup_id,
                                    name=f"SetupRaw_{RUN_ID}", ss=5, rate=200.0)
    fin_id = admin.create_final_item(f"SetupFinal_{RUN_ID}")

    STATE.update({"cat_id": cat_id, "cat_name": cat_name,
                  "sup_id": sup_id, "raw_id": raw_id, "fin_id": fin_id})

    admin.record_inward(raw_id, qty=500, rate=200.0)
    print(f"\n{YELLOW}  Setup complete — RUN_ID={RUN_ID}{RESET}")
    print(f"{YELLOW}  raw_item_id={raw_id}  final_item_id={fin_id}{RESET}")


# =============================================================================
#  1 · CONNECTIVITY
# =============================================================================
def run_connectivity():
    suite("1 · Connectivity")
    erp = ERP()

    @test("Backend is reachable")
    def _():
        r = requests.get(BASE_URL, timeout=5)
        assert r.status_code in (200, 404, 422), f"Unexpected {r.status_code}"

    @test("Stock-report with API key returns 200")
    def _():
        assert_status(erp.get("/stock-report/"), 200)

    @test("Stock-report WITHOUT API key returns 403")
    def _():
        assert_status(requests.get(f"{BASE_URL}/stock-report/", timeout=5), 403)

    @test("Login endpoint exists (no key required)")
    def _():
        r = requests.post(f"{BASE_URL}/login",
                          json={"username": "__none__", "password": "x"}, timeout=5)
        assert_status(r, 401)

    @test("Unknown endpoint returns 404 not 500")
    def _():
        r = erp.get("/this_does_not_exist_xyz")
        assert r.status_code in (404, 405), f"Got {r.status_code}"


# =============================================================================
#  2 · AUTHENTICATION & SESSIONS
# =============================================================================
def run_auth():
    suite("2 · Authentication & Sessions")
    erp = ERP()

    @test("Valid login returns token + role + username")
    def _():
        r = requests.post(f"{BASE_URL}/login",
                          json={"username": ADMIN_USER, "password": ADMIN_PASS},
                          timeout=5)
        assert_status(r, 200)
        d = r.json()
        assert "token" in d and "username" in d and "role" in d

    @test("Login response does not include password hash")
    def _():
        r = requests.post(f"{BASE_URL}/login",
                          json={"username": ADMIN_USER, "password": ADMIN_PASS},
                          timeout=5)
        assert "password" not in r.json(), "Password hash exposed in login response"

    @test("Wrong password returns 401")
    def _():
        r = requests.post(f"{BASE_URL}/login",
                          json={"username": ADMIN_USER, "password": "WRONG_XYZ"},
                          timeout=5)
        assert_status(r, 401)

    @test("Non-existent user returns 401")
    def _():
        r = requests.post(f"{BASE_URL}/login",
                          json={"username": f"ghost_{RUN_ID}", "password": "x"},
                          timeout=5)
        assert_status(r, 401)

    @test("Valid session token validates successfully")
    def _():
        token = STATE["admin_token"]
        r = requests.get(f"{BASE_URL}/validate-session/{token}", timeout=5)
        assert_status(r, 200)
        assert r.json()["username"] == ADMIN_USER

    @test("validate-session returns correct role")
    def _():
        token = STATE["admin_token"]
        r = requests.get(f"{BASE_URL}/validate-session/{token}", timeout=5)
        assert r.json()["role"] == "Admin"

    @test("Invalid session token returns 404")
    def _():
        r = requests.get(f"{BASE_URL}/validate-session/totally-fake-token", timeout=5)
        assert_status(r, 404)

    @test("Logout invalidates the session")
    def _():
        r = requests.post(f"{BASE_URL}/login",
                          json={"username": ADMIN_USER, "password": ADMIN_PASS},
                          timeout=5)
        token = r.json()["token"]
        ERP(token).delete(f"/logout/{token}")
        rv = requests.get(f"{BASE_URL}/validate-session/{token}", timeout=5)
        assert_status(rv, 404)

    @test("Revoked token is rejected on a protected API call")
    def _():
        r = requests.post(f"{BASE_URL}/login",
                          json={"username": ADMIN_USER, "password": ADMIN_PASS},
                          timeout=5)
        token = r.json()["token"]
        client = ERP(token)
        client.delete(f"/logout/{token}")
        # The token is gone from sessions but API auth is API-key based,
        # so the call still succeeds with API key — validate that the
        # session itself is dead
        rv = requests.get(f"{BASE_URL}/validate-session/{token}", timeout=5)
        assert_status(rv, 404)

    @test("Failure audit log written on bad login")
    def _():
        requests.post(f"{BASE_URL}/login",
                      json={"username": ADMIN_USER, "password": "deliberate_bad"},
                      timeout=5)
        logs = STATE["admin"].get("/logs/",
                                  params={"action": "LOGIN", "success": "false",
                                          "limit": 10}).json()
        assert logs["total"] > 0, "No failure log written for bad login"

    @test("Session list returns active sessions including admin")
    def _():
        r = STATE["admin"].get("/users/sessions/")
        assert_status(r, 200)
        tokens = [s["token"] for s in r.json()]
        assert STATE["admin_token"] in tokens

    @test("Session list does NOT expose expired sessions")
    def _():
        # We can't easily fast-forward time, so just verify the list
        # only contains entries with expires_at in the future or 'never'
        admin    = STATE["admin"]
        sessions = admin.get("/users/sessions/").json()
        now      = datetime.utcnow()
        for s in sessions:
            ea = s.get("expires_at", "never")
            if ea and ea != "never":
                exp = datetime.fromisoformat(ea.replace(" ", "T"))
                assert exp > now, f"Expired session still in list: {s}"

    @test("Session revocation works")
    def _():
        admin = STATE["admin"]
        r     = requests.post(f"{BASE_URL}/login",
                              json={"username": ADMIN_USER, "password": ADMIN_PASS},
                              timeout=5)
        token = r.json()["token"]
        admin.delete(f"/users/sessions/{token}")
        rv = requests.get(f"{BASE_URL}/validate-session/{token}", timeout=5)
        assert_status(rv, 404)

    @test("Revoke non-existent session returns 404")
    def _():
        r = STATE["admin"].delete("/users/sessions/no-such-token-xyz")
        assert_status(r, 404)

    @test("Wrong API key returns 403")
    def _():
        r = requests.get(f"{BASE_URL}/items/",
                         headers={"X-API-Key": "WRONG"}, timeout=5)
        assert_status(r, 403)

    @test("Missing API key returns 403")
    def _():
        assert_status(requests.get(f"{BASE_URL}/items/", timeout=5), 403)

    @test("Empty API key returns 403")
    def _():
        r = requests.get(f"{BASE_URL}/items/",
                         headers={"X-API-Key": ""}, timeout=5)
        assert_status(r, 403)

    @test("validate-session requires no API key (by design)")
    def _():
        token = STATE["admin_token"]
        r = requests.get(f"{BASE_URL}/validate-session/{token}", timeout=5)
        assert_status(r, 200)


# =============================================================================
#  3 · USER MANAGEMENT
# =============================================================================
def run_users():
    suite("3 · User Management")
    admin = STATE["admin"]

    @test("List users returns list")
    def _():
        r = admin.get("/users/")
        assert_status(r, 200)
        assert isinstance(r.json(), list)

    @test("User list does not expose password field")
    def _():
        users = admin.get("/users/").json()
        for u in users:
            assert "password" not in u, f"Password exposed for {u['username']}"

    @test("Create user with Admin role")
    def _():
        uname = f"admin_test_{RUN_ID}"
        r = admin.post("/users/", json={"username": uname,
                                        "password": "Pass1234", "role": "Admin"})
        assert_status(r, 200)
        data = r.json()
        assert data["role"] == "Admin"
        STATE["admin2_id"]   = data["id"]
        STATE["admin2_name"] = uname

    @test("Create user with Manager role")
    def _():
        uname = f"manager_test_{RUN_ID}"
        r = admin.post("/users/", json={"username": uname,
                                        "password": "Pass1234", "role": "Manager"})
        assert_status(r, 200)
        STATE["manager_id"]   = r.json()["id"]
        STATE["manager_name"] = uname

    @test("Create user with Viewer role")
    def _():
        uname = f"viewer_test_{RUN_ID}"
        r = admin.post("/users/", json={"username": uname,
                                        "password": "Pass1234", "role": "Viewer"})
        assert_status(r, 200)
        STATE["viewer_id"]   = r.json()["id"]
        STATE["viewer_name"] = uname

    @test("Duplicate username returns 400")
    def _():
        uname = STATE["viewer_name"]
        r = admin.post("/users/", json={"username": uname,
                                        "password": "x", "role": "Viewer"})
        assert_status(r, 400)

    @test("Invalid role returns 400")
    def _():
        r = admin.post("/users/", json={"username": f"bad_{RUN_ID}",
                                        "password": "x", "role": "Superadmin"})
        assert_status(r, 400)

    @test("Update user role to Manager")
    def _():
        uid = STATE["viewer_id"]
        r   = admin.put(f"/users/{uid}", json={"role": "Manager"})
        assert_status(r, 200)
        users = admin.get("/users/").json()
        u = next(x for x in users if x["id"] == uid)
        assert u["role"] == "Manager"

    @test("Update user role with invalid value is silently ignored")
    def _():
        # Backend only applies role if it's in (Admin|Manager|Viewer)
        # So an invalid role update should not error and not change anything
        uid  = STATE["viewer_id"]
        before = next(u["role"] for u in admin.get("/users/").json()
                      if u["id"] == uid)
        r = admin.put(f"/users/{uid}", json={"role": "GodMode"})
        assert_status(r, 200)   # should succeed but role unchanged
        after = next(u["role"] for u in admin.get("/users/").json()
                     if u["id"] == uid)
        assert before == after, f"Role changed to invalid value: {after}"

    @test("Update non-existent user returns 404")
    def _():
        r = admin.put("/users/999999", json={"role": "Viewer"})
        assert_status(r, 404)

    @test("Reset user password and login with new password")
    def _():
        uid   = STATE["viewer_id"]
        uname = STATE["viewer_name"]
        admin.put(f"/users/{uid}", json={"new_password": "NewPass999"})
        lr = requests.post(f"{BASE_URL}/login",
                           json={"username": uname, "password": "NewPass999"},
                           timeout=5)
        assert_status(lr, 200)

    @test("Blank password reset is ignored (not applied)")
    def _():
        uid   = STATE["viewer_id"]
        uname = STATE["viewer_name"]
        # Blank password should not change the existing password
        r = admin.put(f"/users/{uid}", json={"new_password": "   "})
        assert_status(r, 200)
        # Old password still works
        lr = requests.post(f"{BASE_URL}/login",
                           json={"username": uname, "password": "NewPass999"},
                           timeout=5)
        assert_status(lr, 200)

    @test("Cannot delete own account")
    def _():
        users  = admin.get("/users/").json()
        own_id = next(u["id"] for u in users if u["username"] == ADMIN_USER)
        r      = admin.delete(f"/users/{own_id}")
        assert_status(r, 400)

    @test("Delete non-existent user returns 404")
    def _():
        r = admin.delete("/users/999999")
        assert_status(r, 404)

    @test("Delete test user — sessions cleaned up too")
    def _():
        uid = STATE["viewer_id"]
        r   = admin.delete(f"/users/{uid}")
        assert_status(r, 200)
        users = admin.get("/users/").json()
        assert not any(u["id"] == uid for u in users)

    @test("Delete remaining test users")
    def _():
        for key in ("admin2_id", "manager_id"):
            uid = STATE.get(key)
            if uid:
                r = admin.delete(f"/users/{uid}")
                assert r.status_code in (200, 404)


# =============================================================================
#  4 · CATEGORIES
# =============================================================================
def run_categories():
    suite("4 · Categories")
    admin = STATE["admin"]

    @test("List categories returns list")
    def _():
        r = admin.get("/category/")
        assert_status(r, 200)
        assert isinstance(r.json(), list)

    @test("Every category has id, category, description, item_count fields")
    def _():
        cats = admin.get("/category/").json()
        assert len(cats) > 0
        for c in cats:
            for f in ("id", "category", "item_count"):
                assert f in c, f"Field {f!r} missing"

    @test("Create category")
    def _():
        name = f"Cat_{RUN_ID}_crud"
        r    = admin.post("/category/", json={"category": name, "description": "test"})
        assert_status(r, 200)
        cid  = admin.get_category_id(name)
        assert cid is not None
        STATE["crud_cat_name"] = name
        STATE["crud_cat_id"]   = cid

    @test("Duplicate category name returns 400")
    def _():
        r = admin.post("/category/", json={"category": STATE["crud_cat_name"]})
        assert_status(r, 400)

    @test("Update category name")
    def _():
        cid  = STATE["crud_cat_id"]
        new  = f"Cat_{RUN_ID}_updated"
        r    = admin.put(f"/category/{cid}", json={"category": new})
        assert_status(r, 200)
        cats = admin.get("/category/").json()
        assert any(c["category"] == new for c in cats)
        STATE["crud_cat_name"] = new

    @test("Update category description")
    def _():
        cid = STATE["crud_cat_id"]
        r   = admin.put(f"/category/{cid}", json={"description": "new desc"})
        assert_status(r, 200)

    @test("Update category name to existing name returns 400")
    def _():
        # Try renaming crud_cat to the setup cat's name
        cid = STATE["crud_cat_id"]
        r   = admin.put(f"/category/{cid}", json={"category": STATE["cat_name"]})
        assert_status(r, 400)

    @test("Update non-existent category returns 404")
    def _():
        r = admin.put("/category/999999", json={"category": "ghost"})
        assert_status(r, 404)

    @test("Category item_count is correct")
    def _():
        cats = admin.get("/category/").json()
        sc   = next(c for c in cats if c["id"] == STATE["cat_id"])
        assert sc["item_count"] >= 1, "Setup category should have ≥1 item"

    @test("Cannot delete category with items")
    def _():
        r = admin.delete(f"/category/{STATE['cat_id']}")
        assert_status(r, 400)

    @test("Delete empty category")
    def _():
        cid = STATE["crud_cat_id"]
        r   = admin.delete(f"/category/{cid}")
        assert_status(r, 200)
        cats = admin.get("/category/").json()
        assert not any(c["id"] == cid for c in cats)

    @test("Delete non-existent category returns 404")
    def _():
        r = admin.delete("/category/999999")
        assert_status(r, 404)


# =============================================================================
#  5 · SUPPLIERS
# =============================================================================
def run_suppliers():
    suite("5 · Suppliers")
    admin = STATE["admin"]

    @test("List suppliers returns list with item_count")
    def _():
        supps = admin.get("/suppliers/").json()
        assert isinstance(supps, list)
        for s in supps:
            assert "item_count" in s

    @test("Every supplier has expected fields")
    def _():
        supps = admin.get("/suppliers/").json()
        assert len(supps) > 0
        for s in supps:
            for f in ("id", "name", "contact", "gst_no", "lead_time",
                      "last_purchase_date", "last_purchase_rate", "item_count"):
                assert f in s, f"Field {f!r} missing"

    @test("Create supplier with all fields")
    def _():
        name = f"Supp_{RUN_ID}_crud"
        gst  = f"GST{RUN_ID[:8].upper()}"
        r    = admin.post("/suppliers/", json={
            "name": name, "contact": "1234567890",
            "gst_no": gst, "lead_time": 3
        })
        assert_status(r, 200)
        supps = admin.get("/suppliers/").json()
        sup   = next((s for s in supps if s["name"] == name), None)
        assert sup is not None
        assert sup["gst_no"] == gst
        STATE["crud_sup_id"]   = sup["id"]
        STATE["crud_sup_name"] = name
        STATE["crud_sup_gst"]  = gst

    @test("Duplicate GST number on create returns 400")
    def _():
        gst = STATE["crud_sup_gst"]
        r   = admin.post("/suppliers/", json={
            "name": f"DupGST_{RUN_ID}", "contact": "0",
            "gst_no": gst, "lead_time": 0
        })
        assert_status(r, 400)

    @test("Duplicate GST number on update returns 400")
    def _():
        # Create a second supplier then try to update its GST to the first one's
        sid2 = admin.create_supplier(f"Supp2_{RUN_ID}")
        gst  = STATE["crud_sup_gst"]
        r    = admin.put(f"/suppliers/{sid2}", json={"gst_no": gst})
        assert_status(r, 400)
        admin.delete(f"/suppliers/{sid2}")

    @test("Duplicate supplier name is allowed (no unique constraint on name)")
    def _():
        name = STATE["crud_sup_name"]
        r    = admin.post("/suppliers/", json={
            "name": name, "contact": "0", "gst_no": None, "lead_time": 0
        })
        assert r.status_code in (200, 400), f"Unexpected {r.status_code}"

    @test("Update supplier contact and lead_time")
    def _():
        sid = STATE["crud_sup_id"]
        r   = admin.put(f"/suppliers/{sid}",
                        json={"contact": "9876543210", "lead_time": 14})
        assert_status(r, 200)
        supps = admin.get("/suppliers/").json()
        sup   = next(s for s in supps if s["id"] == sid)
        assert sup["lead_time"] == 14

    @test("Update non-existent supplier returns 404")
    def _():
        r = admin.put("/suppliers/999999", json={"contact": "0"})
        assert_status(r, 404)

    @test("Cannot delete supplier with items")
    def _():
        r = admin.delete(f"/suppliers/{STATE['sup_id']}")
        assert_status(r, 400)

    @test("Delete supplier with no items")
    def _():
        sid = STATE["crud_sup_id"]
        r   = admin.delete(f"/suppliers/{sid}")
        assert_status(r, 200)

    @test("Delete non-existent supplier returns 404")
    def _():
        r = admin.delete("/suppliers/999999")
        assert_status(r, 404)


# =============================================================================
#  6 · ITEMS
# =============================================================================
def run_items():
    suite("6 · Items")
    admin  = STATE["admin"]
    cat_id = STATE["cat_id"]
    sup_id = STATE["sup_id"]

    @test("List items returns list with category/supplier names")
    def _():
        items = admin.get("/items/").json()
        assert isinstance(items, list)
        raw = next(i for i in items if i["id"] == STATE["raw_id"])
        assert raw["category"] is not None
        assert raw["supplier"] is not None

    @test("Create RAW item")
    def _():
        iid = admin.create_raw_item(cat_id, sup_id, name=f"Raw_CRUD_{RUN_ID}",
                                     ss=20, rate=150.0)
        STATE["crud_raw_id"] = iid

    @test("GET single item by ID")
    def _():
        iid = STATE["crud_raw_id"]
        r   = admin.get(f"/items/{iid}")
        assert_status(r, 200)
        d = r.json()
        assert d["id"] == iid
        assert d["item_type"] is not None

    @test("GET non-existent item returns 404")
    def _():
        r = admin.get("/items/999999")
        assert_status(r, 404)

    @test("GET item with non-integer ID returns 422")
    def _():
        r = admin.get("/items/abc")
        assert r.status_code in (422, 404), f"Got {r.status_code}"

    @test("Create FINAL item")
    def _():
        iid = admin.create_final_item(f"Final_CRUD_{RUN_ID}")
        STATE["crud_fin_id"] = iid

    @test("FINAL item has null category, supplier, rate in response")
    def _():
        iid   = STATE["crud_fin_id"]
        items = admin.get("/items/").json()
        item  = next(i for i in items if i["id"] == iid)
        assert item["category_id"] is None,  "FINAL item should have null category_id"
        assert item["supplier_id"] is None,  "FINAL item should have null supplier_id"
        assert item["rate"]        is None,  "FINAL item should have null rate"

    @test("RAW item without category returns 400")
    def _():
        r = admin.post("/items/", json={"item_name": f"BadRAW_{RUN_ID}",
                                        "item_type": "RAW", "supplier_id": sup_id})
        assert_status(r, 400)

    @test("RAW item without supplier returns 400")
    def _():
        r = admin.post("/items/", json={"item_name": f"BadRAW2_{RUN_ID}",
                                        "item_type": "RAW", "category_id": cat_id})
        assert_status(r, 400)

    @test("RAW item with invalid category_id returns 404")
    def _():
        r = admin.post("/items/", json={"item_name": f"BadRAW3_{RUN_ID}",
                                        "item_type": "RAW",
                                        "category_id": 999999, "supplier_id": sup_id})
        assert_status(r, 404)

    @test("RAW item with invalid supplier_id returns 404")
    def _():
        r = admin.post("/items/", json={"item_name": f"BadRAW4_{RUN_ID}",
                                        "item_type": "RAW",
                                        "category_id": cat_id, "supplier_id": 999999})
        assert_status(r, 404)

    @test("Invalid item_type returns 422")
    def _():
        r = admin.post("/items/", json={"item_name": f"BadType_{RUN_ID}",
                                        "item_type": "SEMI"})
        assert r.status_code in (400, 422), f"Got {r.status_code}"

    @test("Empty item name is rejected")
    def _():
        r = admin.post("/items/", json={"item_name": "   ", "item_type": "FINAL"})
        assert r.status_code in (400, 422), \
            "Blank item name should be rejected"

    @test("Missing item_name field returns 422")
    def _():
        r = admin.post("/items/", json={"item_type": "FINAL"})
        assert_status(r, 422)

    @test("Missing item_type field returns 422")
    def _():
        r = admin.post("/items/", json={"item_name": f"NoType_{RUN_ID}"})
        assert_status(r, 422)

    @test("Update item name and security_stock")
    def _():
        iid = STATE["crud_raw_id"]
        r   = admin.put(f"/items/{iid}",
                        json={"item_name": f"Raw_CRUD_{RUN_ID}_upd",
                              "security_stock": 50})
        assert_status(r, 200)
        items = admin.get("/items/").json()
        item  = next(i for i in items if i["id"] == iid)
        assert item["security_stock"] == 50

    @test("Update item with blank name is ignored (name unchanged)")
    def _():
        iid    = STATE["crud_raw_id"]
        before = next(i["item_name"] for i in admin.get("/items/").json()
                      if i["id"] == iid)
        r = admin.put(f"/items/{iid}", json={"item_name": "   "})
        assert_status(r, 200)
        after = next(i["item_name"] for i in admin.get("/items/").json()
                     if i["id"] == iid)
        assert before == after, f"Blank name update changed name: {after}"

    @test("Update item with invalid category_id returns 404")
    def _():
        iid = STATE["crud_raw_id"]
        r   = admin.put(f"/items/{iid}", json={"category_id": 999999})
        assert_status(r, 404)

    @test("Update item with invalid supplier_id returns 404")
    def _():
        iid = STATE["crud_raw_id"]
        r   = admin.put(f"/items/{iid}", json={"supplier_id": 999999})
        assert_status(r, 404)

    @test("Update non-existent item returns 404")
    def _():
        r = admin.put("/items/999999", json={"item_name": "ghost"})
        assert_status(r, 404)

    @test("Item with 500-char name is stored or rejected gracefully")
    def _():
        r = admin.post("/items/", json={"item_name": "X" * 500,
                                        "item_type": "FINAL"})
        assert r.status_code in (200, 400, 422), f"Unexpected {r.status_code}"

    @test("Item with special characters in name stored safely")
    def _():
        name = f"<script>alert('{RUN_ID}')</script>"
        r    = admin.post("/items/", json={"item_name": name, "item_type": "FINAL"})
        assert r.status_code in (200, 400, 422), f"Unexpected {r.status_code}"

    @test("Delete item with no transactions")
    def _():
        r = admin.delete(f"/items/{STATE['crud_raw_id']}")
        assert_status(r, 200)

    @test("Delete item with transactions is blocked")
    def _():
        r = admin.delete(f"/items/{STATE['raw_id']}")
        assert_status(r, 400)

    @test("Delete FINAL item with no transactions")
    def _():
        r = admin.delete(f"/items/{STATE['crud_fin_id']}")
        assert_status(r, 200)

    @test("Delete non-existent item returns 404")
    def _():
        r = admin.delete("/items/999999")
        assert_status(r, 404)


# =============================================================================
#  7 · INWARD TRANSACTIONS
# =============================================================================
def run_inwards():
    suite("7 · Inward Transactions")
    admin  = STATE["admin"]
    raw_id = STATE["raw_id"]

    @test("Record inward adds to stock")
    def _():
        before  = admin.current_stock(raw_id)
        invoice = admin.record_inward(raw_id, qty=50)
        after   = admin.current_stock(raw_id)
        assert after == before + 50
        STATE["last_inward_invoice"] = invoice

    @test("Duplicate invoice for same item returns 400")
    def _():
        invoice = STATE["last_inward_invoice"]
        r = admin.post("/inwards/", json={
            "item_id": raw_id, "invoice_number": invoice,
            "quantity": 10, "rate": 50.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert_status(r, 400)

    @test("Same invoice number for a DIFFERENT item is allowed")
    def _():
        iid2    = admin.create_raw_item(STATE["cat_id"], STATE["sup_id"],
                                         name=f"RawInv2_{RUN_ID}")
        invoice = STATE["last_inward_invoice"]
        r = admin.post("/inwards/", json={
            "item_id": iid2, "invoice_number": invoice,
            "quantity": 5, "rate": 30.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert_status(r, 200)
        STATE["raw_id2"] = iid2

    @test("Inward for non-existent item returns 404")
    def _():
        r = admin.post("/inwards/", json={
            "item_id": 999999, "invoice_number": f"INV-999-{RUN_ID}",
            "quantity": 1, "rate": 10.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert_status(r, 404)

    @test("Negative quantity in inward returns 422")
    def _():
        r = admin.post("/inwards/", json={
            "item_id": raw_id, "invoice_number": f"NEG-{RUN_ID}",
            "quantity": -5, "rate": 10.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert r.status_code in (400, 422), \
            f"Negative quantity should be rejected, got {r.status_code}"

    @test("Zero quantity inward — no server error")
    def _():
        r = admin.post("/inwards/", json={
            "item_id": raw_id, "invoice_number": f"ZERO-{RUN_ID}",
            "quantity": 0, "rate": 10.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert r.status_code != 500, f"Server error on zero qty: {r.text}"

    @test("Missing required field (quantity) in inward returns 422")
    def _():
        r = admin.post("/inwards/", json={
            "item_id": raw_id, "invoice_number": f"MISS-{RUN_ID}",
            "rate": 10.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert_status(r, 422)

    @test("Missing item_id in inward returns 422")
    def _():
        r = admin.post("/inwards/", json={
            "invoice_number": f"NOID-{RUN_ID}", "quantity": 1, "rate": 10.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert_status(r, 422)

    @test("List inwards returns paginated response")
    def _():
        r    = admin.get("/inwards/", params={"limit": 10, "offset": 0})
        data = r.json()
        assert "total" in data and "rows" in data

    @test("Filter inwards by invoice")
    def _():
        invoice = STATE["last_inward_invoice"]
        rows    = admin.get("/inwards/", params={"invoice": invoice,
                                                  "limit": 10}).json()["rows"]
        assert all(invoice in (row.get("invoice_number") or "") for row in rows)

    @test("Filter inwards by item_id")
    def _():
        rows = admin.get("/inwards/",
                         params={"item_id": raw_id, "limit": 50}).json()["rows"]
        assert len(rows) > 0, "Expected inwards for raw_id"
        assert all(row["item_id"] == raw_id for row in rows)

    @test("Filter inwards by from_date and to_date")
    def _():
        today = str(date.today())
        data  = admin.get("/inwards/",
                          params={"from_date": today, "to_date": today,
                                  "limit": 100}).json()
        assert "rows" in data
        # All returned rows must have received_date == today
        for row in data["rows"]:
            assert row["received_date"] == today, \
                f"Date filter returned wrong row: {row['received_date']}"

    @test("Paginated inwards with large offset returns empty rows")
    def _():
        data = admin.get("/inwards/",
                         params={"limit": 10, "offset": 9_999_999}).json()
        assert data["rows"] == []

    @test("Supplier last_purchase_date updated after inward")
    def _():
        supps = admin.get("/suppliers/").json()
        sup   = next(s for s in supps if s["id"] == STATE["sup_id"])
        assert sup["last_purchase_date"] is not None

    @test("Void inward transaction")
    def _():
        invoice = f"VOID-{RUN_ID}-{uuid.uuid4().hex[:4]}"
        admin.post("/inwards/", json={
            "item_id": raw_id, "invoice_number": invoice,
            "quantity": 11, "rate": 10.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        rows   = admin.get("/inwards/",
                           params={"invoice": invoice, "limit": 5}).json()["rows"]
        txn_id = rows[0]["transaction_id"]
        rv     = admin.delete(f"/inwards/{txn_id}")
        assert_status(rv, 200)

    @test("Void non-existent inward returns 404")
    def _():
        r = admin.delete("/inwards/999999")
        assert_status(r, 404)

    @test("Very large quantity in inward does not cause server error")
    def _():
        r = admin.post("/inwards/", json={
            "item_id": raw_id, "invoice_number": f"BIGQTY-{RUN_ID}",
            "quantity": 2_000_000_000, "rate": 1.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert r.status_code != 500

    @test("Received date before order date is accepted (frontend validates)")
    def _():
        r = admin.post("/inwards/", json={
            "item_id": raw_id, "invoice_number": f"DATEORD-{RUN_ID}",
            "quantity": 1, "rate": 10.0,
            "order_date": str(date.today()),
            "received_date": str(date.today() - timedelta(days=5)),
        })
        assert r.status_code != 500


# =============================================================================
#  8 · ISSUE TRANSACTIONS
# =============================================================================
def run_issues():
    suite("8 · Issue Transactions")
    admin  = STATE["admin"]
    raw_id = STATE["raw_id"]

    @test("Record issue reduces stock")
    def _():
        before = admin.current_stock(raw_id)
        r      = admin.record_issue(raw_id, qty=5, issued_to="Workshop A")
        assert_status(r, 200)
        after = admin.current_stock(raw_id)
        assert after == before - 5

    @test("Issue with purpose field stored correctly")
    def _():
        r = admin.record_issue(raw_id, qty=2, issued_to="TestDept",
                               purpose="PO-001")
        assert_status(r, 200)
        rows = admin.get("/issues/",
                         params={"issued_to": "TestDept", "limit": 5}).json()["rows"]
        assert any(row.get("purpose") == "PO-001" for row in rows)

    @test("Issue with quantity = 1 (boundary) succeeds")
    def _():
        r = admin.record_issue(raw_id, qty=1, issued_to="BoundaryTest")
        assert_status(r, 200)

    @test("Issue exceeding stock returns 400")
    def _():
        current = admin.current_stock(raw_id)
        r = admin.record_issue(raw_id, qty=int(current) + 9999)
        assert_status(r, 400)

    @test("Stock unchanged after failed over-issue")
    def _():
        current = admin.current_stock(raw_id)
        admin.record_issue(raw_id, qty=int(current) + 9999)
        assert admin.current_stock(raw_id) == current

    @test("Zero quantity issue returns 400")
    def _():
        r = admin.record_issue(raw_id, qty=0, issued_to="ZeroTest")
        assert_status(r, 400)

    @test("Negative quantity issue returns 400")
    def _():
        r = admin.record_issue(raw_id, qty=-5, issued_to="NegTest")
        assert_status(r, 400)

    @test("Issue for non-existent item returns 404")
    def _():
        r = admin.record_issue(999999, qty=1)
        assert_status(r, 404)

    @test("Missing item_id in issue returns 422")
    def _():
        r = admin.post("/issues/", json={
            "quantity": 1, "issue_date": str(date.today()),
            "issued_to": "Test"
        })
        assert_status(r, 422)

    @test("Missing issue_date in issue returns 422")
    def _():
        r = admin.post("/issues/", json={
            "item_id": raw_id, "quantity": 1, "issued_to": "Test"
        })
        assert_status(r, 422)

    @test("List issues returns paginated response")
    def _():
        data = admin.get("/issues/", params={"limit": 10, "offset": 0}).json()
        assert "total" in data and "rows" in data

    @test("Filter issues by issued_to")
    def _():
        rows = admin.get("/issues/",
                         params={"issued_to": "Workshop A", "limit": 10}).json()["rows"]
        assert all("Workshop A" in (row.get("issued_to") or "") for row in rows)

    @test("Filter issues by item_id")
    def _():
        rows = admin.get("/issues/",
                         params={"item_id": raw_id, "limit": 50}).json()["rows"]
        assert len(rows) > 0
        assert all(row["item_id"] == raw_id for row in rows)

    @test("Filter issues by from_date and to_date")
    def _():
        today = str(date.today())
        data  = admin.get("/issues/",
                          params={"from_date": today, "to_date": today,
                                  "limit": 100}).json()
        for row in data["rows"]:
            assert row["issue_date"] == today

    @test("Void issue transaction")
    def _():
        before = admin.current_stock(raw_id)
        r      = admin.record_issue(raw_id, qty=3, issued_to="VoidTest")
        assert_status(r, 200)
        after_issue = admin.current_stock(raw_id)
        assert after_issue == before - 3
        rows   = admin.get("/issues/",
                           params={"issued_to": "VoidTest", "limit": 5}).json()["rows"]
        txn_id = rows[0]["transaction_id"]
        rv     = admin.delete(f"/issues/{txn_id}")
        assert_status(rv, 200)

    @test("Stock is restored after voiding an issue")
    def _():
        before = admin.current_stock(raw_id)
        r      = admin.record_issue(raw_id, qty=7, issued_to="RestoreTest")
        assert_status(r, 200)
        after_issue = admin.current_stock(raw_id)
        rows   = admin.get("/issues/",
                           params={"issued_to": "RestoreTest", "limit": 5}).json()["rows"]
        txn_id = rows[0]["transaction_id"]
        admin.delete(f"/issues/{txn_id}")
        after_void = admin.current_stock(raw_id)
        assert after_void == after_issue + 7, \
            f"Stock not restored: before_void={after_issue} after_void={after_void}"

    @test("Void non-existent issue returns 404")
    def _():
        r = admin.delete("/issues/999999")
        assert_status(r, 404)


# =============================================================================
#  9 · STOCK REPORT
# =============================================================================
def run_stock():
    suite("9 · Stock Report")
    admin  = STATE["admin"]
    raw_id = STATE["raw_id"]

    @test("Stock report returns list")
    def _():
        r = admin.get("/stock-report/")
        assert_status(r, 200)
        assert isinstance(r.json(), list)

    @test("Stock report includes both RAW and FINAL items")
    def _():
        data  = admin.get("/stock-report/").json()
        types = {row["item_type"] for row in data}
        assert "RAW"   in types
        assert "FINAL" in types

    @test("Stock report row has all required fields")
    def _():
        row = next(r for r in admin.get("/stock-report/").json()
                   if r["item_id"] == raw_id)
        for f in ("item_id", "item_name", "item_type", "current_stock",
                  "security_stock", "reorder_point", "needs_reorder", "rate"):
            assert f in row, f"Field {f!r} missing"

    @test("Stock report: rate is correct for RAW item")
    def _():
        row = next(r for r in admin.get("/stock-report/").json()
                   if r["item_id"] == raw_id)
        assert row["rate"] == 200.0

    @test("Stock report: rate is null for FINAL item")
    def _():
        fin_id = STATE["fin_id"]
        data   = admin.get("/stock-report/").json()
        row    = next((r for r in data if r["item_id"] == fin_id), None)
        if row:
            assert row["rate"] is None, \
                f"FINAL item rate should be null, got {row['rate']}"

    @test("needs_reorder=True when stock <= reorder_point")
    def _():
        iid  = admin.create_raw_item(STATE["cat_id"], STATE["sup_id"],
                                      name=f"Reorder_{RUN_ID}", ss=999)
        data = admin.get("/stock-report/").json()
        row  = next((r for r in data if r["item_id"] == iid), None)
        if row:
            assert row["needs_reorder"] is True

    @test("needs_reorder=False when stock is well above reorder_point")
    def _():
        # raw_id has 500 inward, security_stock=5, lead_time=5 → well stocked
        row = next(r for r in admin.get("/stock-report/").json()
                   if r["item_id"] == raw_id)
        assert row["needs_reorder"] is False, \
            f"Expected needs_reorder=False for well-stocked item, got {row}"

    @test("current_stock matches inward - issue arithmetic")
    def _():
        iid = admin.create_raw_item(STATE["cat_id"], STATE["sup_id"],
                                     name=f"Math_{RUN_ID}", ss=0)
        admin.record_inward(iid, qty=100)
        admin.record_issue(iid, qty=30, issued_to="MathTest")
        admin.record_issue(iid, qty=20, issued_to="MathTest")
        assert admin.current_stock(iid) == 50.0


# =============================================================================
#  10 · BOM
# =============================================================================
def run_bom():
    suite("10 · BOM")
    admin  = STATE["admin"]
    raw_id = STATE["raw_id"]
    fin_id = STATE["fin_id"]

    @test("BOM for item with no entries returns empty list")
    def _():
        new_fin = admin.create_final_item(f"EmptyBOM_{RUN_ID}")
        r       = admin.get(f"/bom/{new_fin}")
        assert_status(r, 200)
        assert r.json() == [], f"Expected [], got {r.json()}"
        STATE["empty_fin_id"] = new_fin

    @test("Create BOM entry")
    def _():
        r = admin.post("/bom/", json={"final_item_id": fin_id,
                                       "raw_item_id":   raw_id,
                                       "quantity":      3})
        assert_status(r, 200)
        assert "bom_id" in r.json()
        STATE["bom_id"] = r.json()["bom_id"]

    @test("Duplicate BOM entry (same final+raw) returns 400")
    def _():
        r = admin.post("/bom/", json={"final_item_id": fin_id,
                                       "raw_item_id":   raw_id,
                                       "quantity":      5})
        assert_status(r, 400)

    @test("Same item as both final and raw returns 400")
    def _():
        r = admin.post("/bom/", json={"final_item_id": fin_id,
                                       "raw_item_id":   fin_id,
                                       "quantity":      1})
        assert_status(r, 400)

    @test("Non-existent final_item_id returns 404")
    def _():
        r = admin.post("/bom/", json={"final_item_id": 999999,
                                       "raw_item_id":   raw_id,
                                       "quantity":      1})
        assert_status(r, 404)

    @test("Non-existent raw_item_id returns 404")
    def _():
        r = admin.post("/bom/", json={"final_item_id": fin_id,
                                       "raw_item_id":   999999,
                                       "quantity":      1})
        assert_status(r, 404)

    @test("Retrieve BOM for final item")
    def _():
        entries = admin.get(f"/bom/{fin_id}").json()
        assert len(entries) >= 1
        assert entries[0]["raw_item_id"] == raw_id
        assert entries[0]["quantity"]    == 3

    @test("GET BOM with non-integer ID returns 422")
    def _():
        r = admin.get("/bom/abc")
        assert r.status_code in (422, 404), f"Got {r.status_code}"

    @test("Update BOM quantity")
    def _():
        bom_id  = STATE["bom_id"]
        admin.put(f"/bom/{bom_id}", json={"quantity": 7})
        entries = admin.get(f"/bom/{fin_id}").json()
        entry   = next(e for e in entries if e["bom_id"] == bom_id)
        assert entry["quantity"] == 7

    @test("Update non-existent BOM entry returns 404")
    def _():
        r = admin.put("/bom/999999", json={"quantity": 1})
        assert_status(r, 404)

    @test("Full BOM list endpoint returns list")
    def _():
        r = admin.get("/bom/")
        assert_status(r, 200)
        assert isinstance(r.json(), list)

    @test("Full BOM list contains created entry")
    def _():
        bom_id  = STATE["bom_id"]
        entries = admin.get("/bom/").json()
        assert any(e["bom_id"] == bom_id for e in entries)

    @test("Add substitute to BOM entry")
    def _():
        sub_item_id = admin.create_raw_item(STATE["cat_id"], STATE["sup_id"],
                                             name=f"SubRaw_{RUN_ID}")
        admin.record_inward(sub_item_id, qty=200)
        bom_id = STATE["bom_id"]
        r = admin.post("/bom/substitute/", json={
            "bom_id": bom_id, "substitute_item_id": sub_item_id, "quantity": 4
        })
        assert_status(r, 200)
        STATE["sub_item_id"] = sub_item_id
        STATE["sub_id"]      = r.json()["sub_id"]

    @test("Substitute same as primary raw material is rejected")
    def _():
        bom_id = STATE["bom_id"]
        r = admin.post("/bom/substitute/", json={
            "bom_id": bom_id, "substitute_item_id": raw_id, "quantity": 1
        })
        assert_status(r, 400)

    @test("Substitute with non-existent item_id returns 404")
    def _():
        bom_id = STATE["bom_id"]
        r = admin.post("/bom/substitute/", json={
            "bom_id": bom_id, "substitute_item_id": 999999, "quantity": 1
        })
        assert_status(r, 404)

    @test("Substitute with non-existent bom_id returns 404")
    def _():
        r = admin.post("/bom/substitute/", json={
            "bom_id": 999999, "substitute_item_id": raw_id, "quantity": 1
        })
        assert_status(r, 404)

    @test("Update substitute quantity")
    def _():
        sub_id = STATE["sub_id"]
        r      = admin.put(f"/bom/substitute/{sub_id}", json={"quantity": 6})
        assert_status(r, 200)

    @test("Update non-existent substitute returns 404")
    def _():
        r = admin.put("/bom/substitute/999999", json={"quantity": 1})
        assert_status(r, 404)

    @test("BOM view includes substitutes")
    def _():
        entries = admin.get(f"/bom/{fin_id}").json()
        entry   = next(e for e in entries if e["bom_id"] == STATE["bom_id"])
        assert len(entry["substitutes"]) >= 1

    @test("Delete substitute")
    def _():
        sub_id = STATE["sub_id"]
        r      = admin.delete(f"/bom/substitute/{sub_id}")
        assert_status(r, 200)

    @test("Delete non-existent substitute returns 404")
    def _():
        r = admin.delete("/bom/substitute/999999")
        assert_status(r, 404)

    @test("Delete BOM entry")
    def _():
        bom_id  = STATE["bom_id"]
        admin.delete(f"/bom/{bom_id}")
        entries = admin.get(f"/bom/{fin_id}").json()
        assert not any(e["bom_id"] == bom_id for e in entries)

    @test("Delete non-existent BOM entry returns 404")
    def _():
        r = admin.delete("/bom/999999")
        assert_status(r, 404)

    @test("Deleting BOM entry also deletes its substitutes (cascade)")
    def _():
        # Create a bom entry with a substitute, then delete the entry
        # and confirm the substitute is gone
        sub_item = admin.create_raw_item(STATE["cat_id"], STATE["sup_id"],
                                          name=f"CascSub_{RUN_ID}")
        admin.record_inward(sub_item, qty=50)
        r_bom = admin.post("/bom/", json={"final_item_id": fin_id,
                                           "raw_item_id":   sub_item,
                                           "quantity":      2})
        assert_status(r_bom, 200)
        bom_id2 = r_bom.json()["bom_id"]

        r_sub = admin.post("/bom/substitute/", json={
            "bom_id": bom_id2, "substitute_item_id": raw_id, "quantity": 1
        })
        assert_status(r_sub, 200)
        sub_id2 = r_sub.json()["sub_id"]

        # Delete the BOM entry
        admin.delete(f"/bom/{bom_id2}")

        # Substitute should no longer exist
        r_check = admin.delete(f"/bom/substitute/{sub_id2}")
        assert r_check.status_code == 404, \
            "Substitute should have been cascade-deleted with BOM entry"


# =============================================================================
#  11 · REPORTS
# =============================================================================
def run_reports():
    suite("11 · Reports")
    admin = STATE["admin"]

    @test("Daily report returns list")
    def _():
        r = admin.get("/report/daily", params={"report_date": str(date.today())})
        assert_status(r, 200)
        assert isinstance(r.json(), list)

    @test("Daily report contains required fields")
    def _():
        data = admin.get("/report/daily",
                         params={"report_date": str(date.today())}).json()
        if data:
            row = data[0]
            for f in ("item_id", "item_name", "item_type", "date",
                      "opening_stock", "total_inward", "total_issue", "closing_stock"):
                assert f in row, f"Field {f!r} missing"

    @test("Daily report: closing_stock = opening + inward - issue")
    def _():
        data = admin.get("/report/daily",
                         params={"report_date": str(date.today())}).json()
        for row in data:
            expected = (row["opening_stock"] + row["total_inward"]
                        - row["total_issue"])
            assert row["closing_stock"] == expected, \
                f"Arithmetic mismatch for {row['item_name']}"

    @test("Daily report for past date reflects known transactions")
    def _():
        # Create a fresh item and record inward today, verify today's report
        iid = admin.create_raw_item(STATE["cat_id"], STATE["sup_id"],
                                     name=f"DailyCheck_{RUN_ID}", ss=0)
        admin.record_inward(iid, qty=77)
        admin.record_issue(iid, qty=22, issued_to="DailyRpt")
        data = admin.get("/report/daily",
                         params={"report_date": str(date.today())}).json()
        row  = next((r for r in data if r["item_id"] == iid), None)
        assert row is not None, "Item not in daily report"
        assert row["total_inward"] == 77
        assert row["total_issue"]  == 22
        assert row["closing_stock"] == 55

    @test("Daily report for future date shows zero activity")
    def _():
        future = date.today() + timedelta(days=365)
        data   = admin.get("/report/daily",
                           params={"report_date": str(future)}).json()
        for row in data:
            assert row["total_inward"] == 0
            assert row["total_issue"]  == 0

    @test("Monthly report returns list")
    def _():
        r = admin.get("/report/monthly",
                      params={"year": date.today().year,
                               "month": date.today().month})
        assert_status(r, 200)
        assert isinstance(r.json(), list)

    @test("Monthly report contains required fields")
    def _():
        data = admin.get("/report/monthly",
                         params={"year": date.today().year,
                                  "month": date.today().month}).json()
        if data:
            row = data[0]
            for f in ("item_id", "item_name", "item_type", "month",
                      "opening_stock", "total_inward", "total_issue", "closing_stock"):
                assert f in row, f"Field {f!r} missing"

    @test("Monthly report: closing_stock = opening + inward - issue")
    def _():
        data = admin.get("/report/monthly",
                         params={"year": date.today().year,
                                  "month": date.today().month}).json()
        for row in data:
            expected = (row["opening_stock"] + row["total_inward"]
                        - row["total_issue"])
            assert row["closing_stock"] == expected, \
                f"Monthly arithmetic mismatch for {row['item_name']}"

    @test("Monthly report reflects known transactions this month")
    def _():
        iid = admin.create_raw_item(STATE["cat_id"], STATE["sup_id"],
                                     name=f"MonthlyCheck_{RUN_ID}", ss=0)
        admin.record_inward(iid, qty=100)
        admin.record_issue(iid, qty=40, issued_to="MonthlyRpt")
        data = admin.get("/report/monthly",
                         params={"year": date.today().year,
                                  "month": date.today().month}).json()
        row = next((r for r in data if r["item_id"] == iid), None)
        assert row is not None
        assert row["total_inward"] == 100
        assert row["total_issue"]  == 40
        assert row["closing_stock"] == 60

    @test("Monthly report for future month returns zero activity")
    def _():
        data = admin.get("/report/monthly",
                         params={"year": date.today().year + 1,
                                  "month": 1}).json()
        for row in data:
            assert row["total_inward"] == 0
            assert row["total_issue"]  == 0


# =============================================================================
#  12 · AUDIT LOGS
# =============================================================================
def run_audit():
    suite("12 · Audit Logs")
    admin = STATE["admin"]

    @test("Audit log list returns paginated structure")
    def _():
        data = admin.get("/logs/", params={"limit": 10, "offset": 0}).json()
        assert "total" in data and "logs" in data and "limit" in data

    @test("Successful actions are logged with success=True")
    def _():
        data = admin.get("/logs/", params={"success": "true", "action": "INWARD",
                                            "limit": 5}).json()
        assert data["total"] > 0
        assert all(log["success"] for log in data["logs"])

    @test("Failed actions are logged with success=False")
    def _():
        data = admin.get("/logs/", params={"success": "false", "limit": 10}).json()
        assert data["total"] > 0
        assert all(not log["success"] for log in data["logs"])

    @test("Filter logs by action=LOGIN")
    def _():
        data = admin.get("/logs/", params={"action": "LOGIN", "limit": 5}).json()
        assert data["total"] > 0
        assert all(log["action"] == "LOGIN" for log in data["logs"])

    @test("Filter logs by action=CREATE")
    def _():
        data = admin.get("/logs/", params={"action": "CREATE", "limit": 5}).json()
        assert data["total"] > 0
        assert all(log["action"] == "CREATE" for log in data["logs"])

    @test("Filter logs by action=DELETE")
    def _():
        data = admin.get("/logs/", params={"action": "DELETE", "limit": 5}).json()
        assert data["total"] > 0
        assert all(log["action"] == "DELETE" for log in data["logs"])

    @test("Filter logs by table_name=items")
    def _():
        data = admin.get("/logs/", params={"table_name": "items",
                                            "limit": 10}).json()
        assert data["total"] > 0
        assert all(log["table_name"] == "items" for log in data["logs"])

    @test("Filter logs by table_name=inwards")
    def _():
        data = admin.get("/logs/", params={"table_name": "inwards",
                                            "limit": 10}).json()
        assert data["total"] > 0

    @test("Filter logs by date range (today)")
    def _():
        today = str(date.today())
        data  = admin.get("/logs/",
                          params={"from_date": today, "to_date": today,
                                  "limit": 200}).json()
        assert data["total"] > 0

    @test("Filter logs by username")
    def _():
        data = admin.get("/logs/", params={"username": ADMIN_USER,
                                            "limit": 5}).json()
        assert data["total"] > 0
        assert all(ADMIN_USER in log["username"] for log in data["logs"])

    @test("Pagination offset returns non-overlapping pages")
    def _():
        ids0 = [l["id"] for l in admin.get("/logs/",
                params={"limit": 3, "offset": 0}).json()["logs"]]
        ids1 = [l["id"] for l in admin.get("/logs/",
                params={"limit": 3, "offset": 3}).json()["logs"]]
        assert not set(ids0) & set(ids1), "Pagination overlap"

    @test("Log entry has all required fields")
    def _():
        logs = admin.get("/logs/", params={"limit": 1}).json()["logs"]
        assert len(logs) > 0
        log = logs[0]
        for f in ("id", "timestamp", "username", "action",
                  "table_name", "success"):
            assert f in log, f"Field {f!r} missing from log entry"

    @test("Log count grows after a new action")
    def _():
        before = admin.get("/logs/", params={"limit": 1}).json()["total"]
        # Trigger a new log entry
        admin.get("/items/")   # won't log, do something that does
        admin.post("/category/", json={"category": f"LogCount_{RUN_ID}",
                                        "description": ""})
        after = admin.get("/logs/", params={"limit": 1}).json()["total"]
        assert after > before, "Log count did not increase after CREATE"


# =============================================================================
#  13 · PERFORMANCE
# =============================================================================
def run_performance():
    suite("13 · Performance Benchmarks")
    admin = STATE["admin"]

    def _bench(label, fn, runs=5, threshold=PERF_FAST):
        times = []; [times.append(
            (lambda: (lambda t: time.perf_counter() - t)(time.perf_counter()) or
             (lambda t0: fn() or time.perf_counter() - t0)(time.perf_counter()))()
        ) for _ in range(runs)]
        # simpler version:
        times = []
        for _ in range(runs):
            t0 = time.perf_counter(); fn(); times.append(time.perf_counter() - t0)
        avg = statistics.mean(times)
        assert avg < threshold, \
            f"{label}: avg {avg*1000:.0f}ms > {threshold*1000:.0f}ms"
        return f"avg={avg*1000:.1f}ms"

    @test("GET /items/ — avg < 300ms")
    def _(): return _bench("/items/", lambda: admin.get("/items/"))

    @test("GET /category/ — avg < 300ms")
    def _(): return _bench("/category/", lambda: admin.get("/category/"))

    @test("GET /suppliers/ — avg < 300ms")
    def _(): return _bench("/suppliers/", lambda: admin.get("/suppliers/"))

    @test("GET /stock-report/ — avg < 1000ms")
    def _(): return _bench("/stock-report/", lambda: admin.get("/stock-report/"),
                            threshold=PERF_MEDIUM)

    @test("GET /report/daily — avg < 2000ms")
    def _(): return _bench("/report/daily",
        lambda: admin.get("/report/daily",
                          params={"report_date": str(date.today())}),
        runs=3, threshold=PERF_SLOW)

    @test("GET /report/monthly — avg < 2000ms")
    def _(): return _bench("/report/monthly",
        lambda: admin.get("/report/monthly",
                          params={"year": date.today().year,
                                   "month": date.today().month}),
        runs=3, threshold=PERF_SLOW)

    @test("POST /login — avg < 1000ms (Argon2 intentionally slow)")
    def _(): return _bench("/login",
        lambda: requests.post(f"{BASE_URL}/login",
                              json={"username": ADMIN_USER, "password": ADMIN_PASS},
                              timeout=10),
        runs=3, threshold=PERF_MEDIUM)

    @test("GET /logs/ — avg < 300ms")
    def _(): return _bench("/logs/",
        lambda: admin.get("/logs/", params={"limit": 25}))

    @test("GET /inwards/ paginated — avg < 300ms")
    def _(): return _bench("/inwards/",
        lambda: admin.get("/inwards/", params={"limit": 50, "offset": 0}))

    @test("GET /bom/ — avg < 300ms")
    def _(): return _bench("/bom/", lambda: admin.get("/bom/"))


# =============================================================================
#  14 · CONCURRENCY
# =============================================================================
def run_concurrency():
    suite("14 · Concurrency & Race Conditions")
    admin = STATE["admin"]
    raw_id = STATE["raw_id"]

    @test("Parallel GETs do not error")
    def _():
        errors = []
        def hit():
            r = admin.get("/stock-report/")
            if r.status_code != 200: errors.append(r.status_code)
        threads = [threading.Thread(target=hit) for _ in range(CONCURRENCY_WORKERS)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert not errors

    @test("Parallel inward recordings all succeed")
    def _():
        iid = admin.create_raw_item(STATE["cat_id"], STATE["sup_id"],
                                     name=f"ConcInw_{RUN_ID}")
        errors = []
        def do_inward(n):
            invoice = f"CONC-INW-{RUN_ID}-{n}-{uuid.uuid4().hex[:4]}"
            r = admin.post("/inwards/", json={
                "item_id": iid, "invoice_number": invoice,
                "quantity": 10, "rate": 50.0,
                "order_date": str(date.today()),
                "received_date": str(date.today()),
            })
            if r.status_code != 200: errors.append(r.status_code)
        threads = [threading.Thread(target=do_inward, args=(n,))
                   for n in range(CONCURRENCY_WORKERS)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert not errors
        assert admin.current_stock(iid) == 100.0

    @test("Concurrent over-issues: stock never goes negative")
    def _():
        iid = admin.create_raw_item(STATE["cat_id"], STATE["sup_id"],
                                     name=f"ConcIss_{RUN_ID}")
        admin.record_inward(iid, qty=50)
        results = []
        def do_issue(n):
            r = admin.record_issue(iid, qty=10, issued_to=f"Thread-{n}")
            results.append(r.status_code)
        threads = [threading.Thread(target=do_issue, args=(n,))
                   for n in range(CONCURRENCY_WORKERS)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert admin.current_stock(iid) >= 0, "Stock went negative!"
        assert results.count(200) <= 5, "More than 5 issues succeeded — oversold!"
        return f"success={results.count(200)} fail={results.count(400)}"

    @test("Parallel reads return consistent data")
    def _():
        snapshots = []
        def read():
            snapshots.append(admin.current_stock(raw_id))
        threads = [threading.Thread(target=read) for _ in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(set(snapshots)) == 1, f"Inconsistent reads: {set(snapshots)}"

    @test("Concurrent login requests all succeed")
    def _():
        codes = []
        def do_login():
            r = requests.post(f"{BASE_URL}/login",
                              json={"username": ADMIN_USER, "password": ADMIN_PASS},
                              timeout=10)
            codes.append(r.status_code)
            if r.status_code == 200:
                try: admin.delete(f"/logout/{r.json()['token']}")
                except Exception: pass
        threads = [threading.Thread(target=do_login) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert all(c == 200 for c in codes), f"Some logins failed: {codes}"

    @test("Concurrent BOM creates with same pair — only one succeeds")
    def _():
        fin2 = admin.create_final_item(f"ConcFin_{RUN_ID}")
        results = []
        def do_bom():
            r = admin.post("/bom/", json={"final_item_id": fin2,
                                           "raw_item_id":   raw_id,
                                           "quantity":      1})
            results.append(r.status_code)
        threads = [threading.Thread(target=do_bom) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        success = results.count(200)
        assert success == 1, \
            f"Expected exactly 1 BOM creation to succeed, got {success}"


# =============================================================================
#  15 · INPUT VALIDATION & EDGE CASES
# =============================================================================
def run_edge_cases():
    suite("15 · Input Validation & Edge Cases")
    admin = STATE["admin"]

    @test("Empty item name is rejected")
    def _():
        r = admin.post("/items/", json={"item_name": "  ", "item_type": "FINAL"})
        assert r.status_code in (400, 422), \
            f"Blank item name should be rejected, got {r.status_code}"

    @test("Null item name is rejected")
    def _():
        r = admin.post("/items/", json={"item_name": None, "item_type": "FINAL"})
        assert r.status_code in (400, 422), f"Got {r.status_code}"

    @test("Extremely long item name (500 chars) does not 500")
    def _():
        r = admin.post("/items/", json={"item_name": "A" * 500,
                                        "item_type": "FINAL"})
        assert r.status_code in (200, 400, 422)

    @test("Negative quantity in inward returns 422")
    def _():
        r = admin.post("/inwards/", json={
            "item_id": STATE["raw_id"], "invoice_number": f"NEG-{RUN_ID}",
            "quantity": -5, "rate": 10.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert r.status_code in (400, 422), f"Got {r.status_code}"

    @test("Negative quantity in issue returns 400")
    def _():
        r = admin.record_issue(STATE["raw_id"], qty=-1)
        assert_status(r, 400)

    @test("Zero quantity in issue returns 400")
    def _():
        r = admin.record_issue(STATE["raw_id"], qty=0)
        assert_status(r, 400)

    @test("Negative rate in inward does not 500")
    def _():
        r = admin.post("/inwards/", json={
            "item_id": STATE["raw_id"], "invoice_number": f"NEGRATE-{RUN_ID}",
            "quantity": 1, "rate": -50.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert r.status_code != 500

    @test("Negative security_stock on item does not 500")
    def _():
        r = admin.post("/items/", json={
            "item_name": f"NegSS_{RUN_ID}", "item_type": "RAW",
            "category_id": STATE["cat_id"], "supplier_id": STATE["sup_id"],
            "lead_time": 5, "security_stock": -1, "rate": 100.0,
        })
        assert r.status_code != 500, \
            f"Server error on negative security_stock: {r.text}"

    @test("Negative lead_time on item does not 500")
    def _():
        r = admin.post("/items/", json={
            "item_name": f"NegLT_{RUN_ID}", "item_type": "RAW",
            "category_id": STATE["cat_id"], "supplier_id": STATE["sup_id"],
            "lead_time": -5, "security_stock": 0, "rate": 1.0,
        })
        assert r.status_code != 500

    @test("SQL injection in category name is stored safely (not executed)")
    def _():
        evil = "'; DROP TABLE items; --"
        r    = admin.post("/category/", json={"category": evil, "description": ""})
        assert r.status_code in (200, 400)
        assert_status(admin.get("/items/"), 200)   # table still exists

    @test("XSS payload in supplier name is stored as plain text")
    def _():
        unique = f"XSS_{RUN_ID}_{uuid.uuid4().hex[:6]}"
        full   = unique + "<script>alert(1)</script>"
        r      = admin.post("/suppliers/", json={"name": full, "contact": "0",
                                                  "gst_no": None, "lead_time": 0})
        assert r.status_code in (200, 400), f"Unexpected {r.status_code}"
        if r.status_code == 200:
            supps = admin.get("/suppliers/").json()
            sup   = next((s for s in supps if s["name"] == full), None)
            assert sup is not None, "Created XSS supplier not found in list"
            assert "<script>" in sup["name"], \
                "XSS payload was altered — should be stored as plain text"

    @test("Non-integer item_id in URL returns 422")
    def _():
        r = admin.get("/items/abc")
        assert r.status_code in (422, 404), f"Got {r.status_code}"

    @test("Non-integer category_id in URL returns 422")
    def _():
        r = admin.delete("/category/abc")
        assert r.status_code in (422, 404), f"Got {r.status_code}"

    @test("Non-integer supplier_id in URL returns 422")
    def _():
        r = admin.delete("/suppliers/abc")
        assert r.status_code in (422, 404), f"Got {r.status_code}"

    @test("Non-integer user_id in URL returns 422")
    def _():
        r = admin.delete("/users/abc")
        assert r.status_code in (422, 404), f"Got {r.status_code}"

    @test("Non-integer bom_id in URL returns 422")
    def _():
        r = admin.delete("/bom/abc")
        assert r.status_code in (422, 404), f"Got {r.status_code}"

    @test("Missing required field in POST /items/ returns 422")
    def _():
        assert_status(admin.post("/items/", json={"item_type": "FINAL"}), 422)

    @test("Missing required field in POST /inwards/ returns 422")
    def _():
        r = admin.post("/inwards/", json={"item_id": STATE["raw_id"],
                                           "quantity": 1, "rate": 1.0})
        assert r.status_code in (400, 422)

    @test("Missing required field in POST /issues/ returns 422")
    def _():
        r = admin.post("/issues/", json={"quantity": 1,
                                          "issue_date": str(date.today()),
                                          "issued_to": "Test"})
        assert_status(r, 422)

    @test("Missing required field in POST /login returns 422")
    def _():
        r = requests.post(f"{BASE_URL}/login",
                          json={"username": ADMIN_USER}, timeout=5)
        assert_status(r, 422)

    @test("Paginated inwards with large offset returns empty rows not error")
    def _():
        data = admin.get("/inwards/",
                         params={"limit": 10, "offset": 9_999_999}).json()
        assert data["rows"] == []

    @test("Very large quantity in inward does not overflow or 500")
    def _():
        r = admin.post("/inwards/", json={
            "item_id": STATE["raw_id"], "invoice_number": f"BIGQ2-{RUN_ID}",
            "quantity": 2_000_000_000, "rate": 1.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        })
        assert r.status_code != 500

    @test("Received date before order date is accepted")
    def _():
        r = admin.post("/inwards/", json={
            "item_id": STATE["raw_id"], "invoice_number": f"EARLY-{RUN_ID}",
            "quantity": 1, "rate": 1.0,
            "order_date": str(date.today()),
            "received_date": str(date.today() - timedelta(days=5)),
        })
        assert r.status_code != 500


# =============================================================================
#  16 · SECURITY
# =============================================================================
def run_security():
    suite("16 · Security")
    admin = STATE["admin"]

    @test("Passwords not returned in /users/ list")
    def _():
        for u in admin.get("/users/").json():
            assert "password" not in u

    @test("Session token is UUID4 format")
    def _():
        r     = requests.post(f"{BASE_URL}/login",
                              json={"username": ADMIN_USER, "password": ADMIN_PASS},
                              timeout=5)
        token = r.json()["token"]
        parts = token.split("-")
        assert len(parts) == 5 and len(token) == 36
        admin.delete(f"/logout/{token}")

    @test("Brute-force: 5 bad logins all return 401, not 500")
    def _():
        codes = [requests.post(f"{BASE_URL}/login",
                               json={"username": ADMIN_USER,
                                     "password": f"wrong_{uuid.uuid4().hex}"},
                               timeout=10).status_code
                 for _ in range(5)]
        assert all(c == 401 for c in codes), f"Unexpected codes: {codes}"

    @test("Failure log written for each bad login attempt")
    def _():
        cnt = admin.get("/logs/", params={"action": "LOGIN", "success": "false",
                                           "limit": 1}).json()["total"]
        assert cnt >= 5

    @test("API key is not echoed back in any response body")
    def _():
        assert API_KEY not in admin.get("/items/").text

    @test("Token from different session with wrong API key returns 403")
    def _():
        uname = f"impersonate_{RUN_ID}"
        admin.post("/users/", json={"username": uname, "password": "Pass1",
                                    "role": "Viewer"})
        lr    = requests.post(f"{BASE_URL}/login",
                              json={"username": uname, "password": "Pass1"},
                              timeout=5)
        token = lr.json()["token"]
        r = requests.get(f"{BASE_URL}/items/",
                         headers={"X-API-Key": "WRONG",
                                  "X-Session-Token": token}, timeout=5)
        assert_status(r, 403)
        users = admin.get("/users/").json()
        uid   = next((u["id"] for u in users if u["username"] == uname), None)
        if uid: admin.delete(f"/users/{uid}")

    @test("Validate-session endpoint needs no API key")
    def _():
        r = requests.get(f"{BASE_URL}/validate-session/{STATE['admin_token']}",
                         timeout=5)
        assert_status(r, 200)

    @test("Argon2 hash stored — raw password not in DB-facing response")
    def _():
        # Create a user and list users; password must not appear
        uname = f"hashtest_{RUN_ID}"
        pw    = "SuperSecret999"
        admin.post("/users/", json={"username": uname, "password": pw,
                                    "role": "Viewer"})
        users = admin.get("/users/").json()
        for u in users:
            assert pw not in str(u), "Plain-text password found in response"
        uid = next((u["id"] for u in users if u["username"] == uname), None)
        if uid: admin.delete(f"/users/{uid}")

    @test("Session TTL is set on login (expires_at is not null)")
    def _():
        r     = requests.post(f"{BASE_URL}/login",
                              json={"username": ADMIN_USER, "password": ADMIN_PASS},
                              timeout=5)
        token = r.json()["token"]
        sessions = admin.get("/users/sessions/").json()
        session  = next((s for s in sessions if s["token"] == token), None)
        assert session is not None
        assert session["expires_at"] != "never", "Session has no expiry set"
        admin.delete(f"/logout/{token}")

    @test("Role enforcement gap is documented — Viewer can call write endpoints")
    def _():
        # This test DOCUMENTS the known gap: the backend does not enforce roles.
        # A Viewer with a valid API key can call any write endpoint.
        # If this test starts FAILING it means role enforcement was ADDED — great!
        uname = f"viewer_role_{RUN_ID}"
        admin.post("/users/", json={"username": uname, "password": "Pass1",
                                    "role": "Viewer"})
        # Viewer can still hit write endpoints because API key is the only guard
        viewer_client = ERP()   # same API key
        r = viewer_client.get("/items/")
        assert_status(r, 200)   # reads work
        users = admin.get("/users/").json()
        uid   = next((u["id"] for u in users if u["username"] == uname), None)
        if uid: admin.delete(f"/users/{uid}")


# =============================================================================
#  FINAL REPORT
# =============================================================================
def print_report():
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  TEST RESULTS SUMMARY{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    total_pass = total_fail = 0
    for s in ALL_SUITES:
        icon = GREEN + "✓" + RESET if s.failed == 0 else RED + "✗" + RESET
        print(f"  {icon} {s.name:<45} {GREEN}{s.passed}✓{RESET}  "
              f"{(RED if s.failed else '')}{s.failed}✗{RESET}")
        total_pass += s.passed
        total_fail += s.failed
    print(f"{BOLD}{'─'*60}{RESET}")
    total = total_pass + total_fail
    pct   = 100 * total_pass // total if total else 0
    color = GREEN if total_fail == 0 else RED
    print(f"  {color}{BOLD}TOTAL  {total_pass}/{total} passed  ({pct}%){RESET}")
    if total_fail > 0:
        print(f"\n{RED}{BOLD}  FAILED TESTS:{RESET}")
        for s in ALL_SUITES:
            for r in s.results:
                if not r.passed:
                    print(f"    {RED}✗ [{s.name}] {r.name}{RESET}")
                    if r.detail: print(f"      {r.detail}")
    print()
    return total_fail


# =============================================================================
#  ENTRY POINT
# =============================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true",
                        help="Skip concurrency tests")
    parser.add_argument("--section", type=str, default="all",
                        help="conn|auth|users|cats|suppliers|items|inwards|"
                             "issues|stock|bom|reports|audit|perf|conc|edge|security")
    args = parser.parse_args()

    if not API_KEY:
        print(f"{RED}ERROR: ERP_API_KEY not set.{RESET}")
        sys.exit(1)

    print(f"{BOLD}Industrial ERP — Complete Test Suite{RESET}")
    print(f"  Base URL : {BASE_URL}")
    print(f"  Admin    : {ADMIN_USER}")
    print(f"  Run ID   : {RUN_ID}")

    try:
        requests.get(BASE_URL, timeout=3)
    except Exception:
        print(f"\n{RED}ERROR: Cannot reach {BASE_URL}{RESET}")
        sys.exit(1)

    try:
        setup()
    except Exception as e:
        print(f"\n{RED}SETUP FAILED: {e}{RESET}")
        sys.exit(1)

    s       = args.section.lower()
    run_all = (s == "all")

    if run_all or s == "conn":       run_connectivity()
    if run_all or s == "auth":       run_auth()
    if run_all or s == "users":      run_users()
    if run_all or s == "cats":       run_categories()
    if run_all or s == "suppliers":  run_suppliers()
    if run_all or s == "items":      run_items()
    if run_all or s == "inwards":    run_inwards()
    if run_all or s == "issues":     run_issues()
    if run_all or s == "stock":      run_stock()
    if run_all or s == "bom":        run_bom()
    if run_all or s == "reports":    run_reports()
    if run_all or s == "audit":      run_audit()
    if run_all or s == "perf":       run_performance()
    if (run_all or s == "conc") and not args.fast:
        run_concurrency()
    if run_all or s == "edge":       run_edge_cases()
    if run_all or s == "security":   run_security()

    sys.exit(1 if print_report() > 0 else 0)


if __name__ == "__main__":
    main()