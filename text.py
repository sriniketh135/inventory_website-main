from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi.testclient import TestClient
from datetime import date
import os
import time
import threading

from main import app, get_db, models, engine
from sqlalchemy.orm import sessionmaker

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

client = TestClient(app)
API_KEY = os.environ.get("ERP_API_KEY", "test-api-key")
HEADERS = {"X-API-Key": API_KEY}


def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


def make_names():
    ts = int(time.time() * 1000)
    return {
        "supplier": f"Supplier {ts}",
        "spec": f"Spec {ts}",
        "item": f"Item {ts}",
        "invoice": f"INV-{ts}",
    }


def setup_full(names, quantity=10):
    client.post("/suppliers/", json={"name": names["supplier"]}, headers=HEADERS)
    client.post("/specs/", json={"spec": names["spec"]}, headers=HEADERS)

    db = TestingSessionLocal()
    supplier = db.query(models.Supplier).filter(models.Supplier.name == names["supplier"]).first()
    spec = db.query(models.SpecList).filter(models.SpecList.spec == names["spec"]).first()
    db.close()

    client.post("/items/", json={
        "item_name": names["item"],
        "item_type": "RAW",
        "spec_id": spec.id,
        "lead_time": 5,
        "security_stock": 2,
        "supplier_id": supplier.id,
    }, headers=HEADERS)

    db = TestingSessionLocal()
    item = db.query(models.Item).filter(models.Item.item_name == names["item"]).first()
    db.close()

    client.post("/inwards/", json={
        "item_id": item.id,
        "invoice_number": names["invoice"],
        "quantity": quantity,
        "rate": 50.0,
        "order_date": str(date.today()),
        "received_date": str(date.today()),
    }, headers=HEADERS)

    return {"supplier_id": supplier.id, "spec_id": spec.id, "item_id": item.id}


def get_stock(item_id):
    report = client.get("/stock-report/", headers=HEADERS).json()
    return next((i for i in report if i["item_id"] == item_id), None)


# ============================================================
# TEST 1: 100 concurrent reads (stock report)
# ============================================================
def test_100_concurrent_reads():
    print("\n[test_100_concurrent_reads]")
    USERS = 100
    results = []
    lock = threading.Lock()

    def read_stock():
        res = client.get("/stock-report/", headers=HEADERS)
        with lock:
            results.append(res.status_code)

    start = time.time()
    with ThreadPoolExecutor(max_workers=USERS) as executor:
        futures = [executor.submit(read_stock) for _ in range(USERS)]
        for f in as_completed(futures):
            f.result()
    elapsed = time.time() - start

    success = results.count(200)
    failed = [c for c in results if c != 200]

    print(f"  {success}/100 succeeded in {elapsed:.2f}s")
    print(f"  Avg response time: {elapsed/USERS*1000:.1f}ms per request")
    if failed:
        print(f"  Failed status codes: {set(failed)}")

    assert success == USERS, f"Only {success}/100 reads succeeded"
    print("PASS")


# ============================================================
# TEST 2: 100 concurrent issue attempts — only some should succeed
# ============================================================
def test_100_concurrent_issues_stock_integrity():
    print("\n[test_100_concurrent_issues_stock_integrity]")
    USERS = 100
    STOCK = 50       # we have 50 units
    ISSUE_QTY = 1    # each user wants 1 unit — so exactly 50 should succeed

    names = make_names()
    ids = setup_full(names, quantity=STOCK)
    item_id = ids["item_id"]

    results = []
    lock = threading.Lock()

    def make_issue():
        res = client.post("/issues/", json={
            "item_id": item_id,
            "quantity": ISSUE_QTY,
            "issue_date": str(date.today()),
            "issued_to": "Load Test User",
        }, headers=HEADERS)
        with lock:
            results.append(res.status_code)

    start = time.time()
    with ThreadPoolExecutor(max_workers=USERS) as executor:
        futures = [executor.submit(make_issue) for _ in range(USERS)]
        for f in as_completed(futures):
            f.result()
    elapsed = time.time() - start

    success = results.count(200)
    failed = results.count(400)

    print(f"  Total requests: {USERS}")
    print(f"  Succeeded (200): {success}")
    print(f"  Rejected (400):  {failed}")
    print(f"  Completed in:    {elapsed:.2f}s")

    stock = get_stock(item_id)
    print(f"  Final stock:     {stock['current_stock']} (expected 0)")

    # Exactly 50 should have succeeded (we had 50 units, each took 1)
    assert success == STOCK, f"Expected {STOCK} successes, got {success}"
    assert failed == USERS - STOCK, f"Expected {USERS - STOCK} failures, got {failed}"
    assert stock["current_stock"] == 0, \
        f"Stock integrity violated! Expected 0, got {stock['current_stock']}"
    print("PASS")


# ============================================================
# TEST 3: 100 concurrent large issue attempts — only ONE should win
# ============================================================
def test_100_concurrent_issues_only_one_wins():
    print("\n[test_100_concurrent_issues_only_one_wins]")
    USERS = 100
    STOCK = 10
    ISSUE_QTY = 10   # everyone wants ALL the stock — only 1 should win

    names = make_names()
    ids = setup_full(names, quantity=STOCK)
    item_id = ids["item_id"]

    results = []
    lock = threading.Lock()

    def make_issue():
        res = client.post("/issues/", json={
            "item_id": item_id,
            "quantity": ISSUE_QTY,
            "issue_date": str(date.today()),
            "issued_to": "Load Test User",
        }, headers=HEADERS)
        with lock:
            results.append(res.status_code)

    start = time.time()
    with ThreadPoolExecutor(max_workers=USERS) as executor:
        futures = [executor.submit(make_issue) for _ in range(USERS)]
        for f in as_completed(futures):
            f.result()
    elapsed = time.time() - start

    success = results.count(200)
    failed = results.count(400)

    print(f"  Total requests: {USERS}")
    print(f"  Succeeded (200): {success}")
    print(f"  Rejected (400):  {failed}")
    print(f"  Completed in:    {elapsed:.2f}s")

    stock = get_stock(item_id)
    print(f"  Final stock:     {stock['current_stock']} (expected 0)")

    assert success == 1, f"Expected exactly 1 success, got {success} — race condition!"
    assert failed == USERS - 1, f"Expected {USERS-1} failures, got {failed}"
    assert stock["current_stock"] == 0, \
        f"Stock integrity violated! Expected 0, got {stock['current_stock']}"
    print("PASS")


# ============================================================
# TEST 4: 100 concurrent inwards — all should succeed and add up correctly
# ============================================================
def test_100_concurrent_inwards():
    print("\n[test_100_concurrent_inwards]")
    USERS = 100
    QTY_EACH = 3   # each user inwards 3 units → total should be 300

    names = make_names()
    # Setup with 0 quantity first inward so we can track cleanly
    ids = setup_full(names, quantity=0)
    item_id = ids["item_id"]
    ts = int(time.time() * 1000)

    results = []
    lock = threading.Lock()

    def make_inward(i):
        res = client.post("/inwards/", json={
            "item_id": item_id,
            "invoice_number": f"INV-LOAD-{ts}-{i}",
            "quantity": QTY_EACH,
            "rate": 10.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        }, headers=HEADERS)
        with lock:
            results.append(res.status_code)

    start = time.time()
    with ThreadPoolExecutor(max_workers=USERS) as executor:
        futures = [executor.submit(make_inward, i) for i in range(USERS)]
        for f in as_completed(futures):
            f.result()
    elapsed = time.time() - start

    success = results.count(200)
    failed = [c for c in results if c != 200]

    print(f"  Total requests:  {USERS}")
    print(f"  Succeeded (200): {success}")
    print(f"  Completed in:    {elapsed:.2f}s")
    if failed:
        print(f"  Failed codes:    {set(failed)}")

    stock = get_stock(item_id)
    expected_stock = USERS * QTY_EACH  # 100 * 3 = 300
    print(f"  Final stock:     {stock['current_stock']} (expected {expected_stock})")

    assert success == USERS, f"Only {success}/100 inwards succeeded"
    assert stock["current_stock"] == expected_stock, \
        f"Stock mismatch! Expected {expected_stock}, got {stock['current_stock']}"
    print("PASS")


# ============================================================
# TEST 5: 100 concurrent mixed reads and writes
# ============================================================
def test_100_concurrent_mixed_read_write():
    print("\n[test_100_concurrent_mixed_read_write]")
    USERS = 100
    STOCK = 30

    names = make_names()
    ids = setup_full(names, quantity=STOCK)
    item_id = ids["item_id"]
    ts = int(time.time() * 1000)

    results = {"reads": [], "issues": [], "inwards": []}
    lock = threading.Lock()

    def do_read(i):
        res = client.get("/stock-report/", headers=HEADERS)
        with lock:
            results["reads"].append(res.status_code)

    def do_issue(i):
        res = client.post("/issues/", json={
            "item_id": item_id,
            "quantity": 1,
            "issue_date": str(date.today()),
            "issued_to": f"User {i}",
        }, headers=HEADERS)
        with lock:
            results["issues"].append(res.status_code)

    def do_inward(i):
        res = client.post("/inwards/", json={
            "item_id": item_id,
            "invoice_number": f"INV-MIX-{ts}-{i}",
            "quantity": 1,
            "rate": 10.0,
            "order_date": str(date.today()),
            "received_date": str(date.today()),
        }, headers=HEADERS)
        with lock:
            results["inwards"].append(res.status_code)

    # Mix: 40 reads, 40 issues, 20 inwards
    tasks = (
        [(do_read, i) for i in range(40)] +
        [(do_issue, i) for i in range(40)] +
        [(do_inward, i) for i in range(20)]
    )

    start = time.time()
    with ThreadPoolExecutor(max_workers=USERS) as executor:
        futures = [executor.submit(fn, i) for fn, i in tasks]
        for f in as_completed(futures):
            f.result()
    elapsed = time.time() - start

    read_ok = results["reads"].count(200)
    issue_ok = results["issues"].count(200)
    issue_fail = results["issues"].count(400)
    inward_ok = results["inwards"].count(200)

    print(f"  Completed in: {elapsed:.2f}s")
    print(f"  Reads:    {read_ok}/40 succeeded")
    print(f"  Issues:   {issue_ok} succeeded, {issue_fail} rejected (insufficient stock)")
    print(f"  Inwards:  {inward_ok}/20 succeeded")

    stock = get_stock(item_id)
    # stock = initial(30) + inwards(20) - successful_issues
    expected = STOCK + inward_ok - issue_ok
    print(f"  Final stock: {stock['current_stock']} (expected {expected})")

    assert read_ok == 40, f"Read failures detected: {read_ok}/40"
    assert inward_ok == 20, f"Inward failures: {inward_ok}/20"
    assert issue_ok + issue_fail == 40, "Some issue requests went missing"
    assert stock["current_stock"] == expected, \
        f"Stock integrity violated! Expected {expected}, got {stock['current_stock']}"
    print("PASS")