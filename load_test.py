"""
ERP Load Test — NFR-01 / NFR-03
=================================
Tests that all API endpoints respond within 2 seconds under 50 concurrent users.

Install:
    pip install locust

Run (headless, 50 users, 1 min):
    locust -f tests/load_test.py --headless -u 50 -r 5 -t 60s \
           --host http://localhost:8000 \
           --html reports/load_report.html

Run (with web UI at http://localhost:8089):
    locust -f tests/load_test.py --host http://localhost:8000

Environment variables:
    ERP_API_KEY         — your API key
    TEST_ADMIN_USER     — admin username
    TEST_ADMIN_PASS     — admin password
"""

import os
from locust import HttpUser, task, between, events
from dotenv import load_dotenv

load_dotenv()

API_KEY      = os.environ.get("ERP_API_KEY", "")
ADMIN_USER   = os.environ.get("TEST_ADMIN_USER", "admin")
ADMIN_PASS   = os.environ.get("TEST_ADMIN_PASS", "admin123")

# Track response time violations
violations = []

@events.request.add_listener
def on_request(response_time, name, request_type, response_length, exception, **kwargs):
    if response_time > 2000 and not exception:  # 2000ms = 2s
        violations.append(f"{request_type} {name}: {response_time:.0f}ms")

@events.quitting.add_listener
def on_quit(environment, **kwargs):
    if violations:
        print(f"\n⚠️  NFR-01 VIOLATIONS — {len(violations)} responses exceeded 2s:")
        for v in violations[:20]:
            print(f"   {v}")
        environment.process_exit_code = 1
    else:
        print("\n✅ NFR-01 PASSED — all responses under 2s")


class ERPUser(HttpUser):
    """
    Simulates a typical ERP user browsing the system.
    wait_time: 1-3 seconds between tasks (realistic think time).
    """
    wait_time = between(1, 3)

    def on_start(self):
        """Log in and store the session token + API headers."""
        res = self.client.post("/login", json={
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
        })
        if res.status_code == 200:
            self.token = res.json().get("token", "")
        else:
            self.token = ""

        self.headers = {
            "X-API-Key":       API_KEY,
            "X-Session-Token": self.token,
        }

    def on_stop(self):
        if self.token:
            self.client.delete(f"/logout/{self.token}", headers=self.headers)

    # ── Read endpoints (high frequency — most common user actions) ──────────

    @task(5)
    def view_stock_report(self):
        """NFR-01 + NFR-02: Stock report with batch lookup."""
        self.client.get("/stock-report/", headers=self.headers,
                        name="GET /stock-report/")

    @task(4)
    def view_items(self):
        """NFR-01: Item master list."""
        self.client.get("/items/", headers=self.headers,
                        name="GET /items/")

    @task(3)
    def view_inwards(self):
        """NFR-01: Inward transaction list."""
        self.client.get("/inwards/", headers=self.headers,
                        name="GET /inwards/")

    @task(3)
    def view_issues(self):
        """NFR-01: Issue transaction list."""
        self.client.get("/issues/", headers=self.headers,
                        name="GET /issues/")

    @task(2)
    def view_categories(self):
        """NFR-01: Category list."""
        self.client.get("/category/", headers=self.headers,
                        name="GET /category/")

    @task(2)
    def view_suppliers(self):
        """NFR-01: Supplier list."""
        self.client.get("/suppliers/", headers=self.headers,
                        name="GET /suppliers/")

    @task(2)
    def view_bom(self):
        """NFR-01: Full BOM list."""
        self.client.get("/bom/", headers=self.headers,
                        name="GET /bom/")

    @task(2)
    def view_audit_logs(self):
        """NFR-01: Audit log with pagination."""
        self.client.get("/logs/?limit=25&offset=0", headers=self.headers,
                        name="GET /logs/")

    @task(1)
    def daily_report(self):
        """NFR-01: Daily stock report generation."""
        from datetime import date
        self.client.get(f"/report/daily?report_date={date.today().isoformat()}",
                        headers=self.headers, name="GET /report/daily")

    @task(1)
    def monthly_report(self):
        """NFR-01: Monthly stock report generation."""
        from datetime import date
        d = date.today()
        self.client.get(f"/report/monthly?year={d.year}&month={d.month}",
                        headers=self.headers, name="GET /report/monthly")

    @task(1)
    def validate_session(self):
        """NFR-01: Session validation (called on every page load in frontend)."""
        if self.token:
            self.client.get(f"/validate-session/{self.token}",
                            name="GET /validate-session/")
