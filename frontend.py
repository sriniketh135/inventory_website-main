import streamlit as st
import requests
import pandas as pd
import time
import os
from datetime import date, datetime
from streamlit_option_menu import option_menu
from streamlit_cookies_controller import CookieController
from dotenv import load_dotenv
load_dotenv()

# ================= CONFIG =================
API_URL = "http://127.0.0.1:8000"
st.set_page_config(page_title="Industrial ERP", layout="wide")

API_KEY = os.environ.get("ERP_API_KEY")
HEADERS = {"X-API-Key": API_KEY}

cookie_controller = CookieController()

# ================= COOKIE HELPER =================
def safe_set_cookie(name, value, max_age=None):
    try:
        if max_age is not None:
            cookie_controller.set(name, value, max_age=max_age)
        else:
            cookie_controller.set(name, value)
    except TypeError:
        cookie_controller._CookieController__cookies = {}
        if max_age is not None:
            cookie_controller.set(name, value, max_age=max_age)
        else:
            cookie_controller.set(name, value)

# ================= CUSTOM CSS =================
st.markdown("""
<style>
section[data-testid="stSidebar"] { background-color: white !important; border-right: 1px solid #E6E9EF; }
.profile-box { display: flex; align-items: center; padding: 20px 10px; margin-bottom: 20px; }
.profile-pic { width: 45px; height: 45px; border-radius: 50%; margin-right: 12px; }
.profile-name { font-weight: 600; font-size: 1.1rem; color: #1e293b; }
.stApp { background-color: #F8FAFC; }
</style>
""", unsafe_allow_html=True)

# ================= SESSION STATE =================
if "user" not in st.session_state:
    st.session_state.user = None
if "session_token" not in st.session_state:
    st.session_state.session_token = None
if "current_page" not in st.session_state:
    st.session_state.current_page = st.query_params.get("page", "Home")
if "force_logout" not in st.session_state:
    st.session_state.force_logout = False

ITEM_SELECTOR_PAGES = {"Record Issue", "Delete Item"}

# ================= CORE FUNCTIONS =================
def check_session():
    if st.session_state.user is None:
        if st.session_state.get("force_logout", False):
            return
        token = None
        if hasattr(st, "context") and hasattr(st.context, "cookies"):
            token = st.context.cookies.get("erp_session_token")
        if not token:
            try:
                token = cookie_controller.get("erp_session_token")
            except TypeError:
                pass
        if token:
            try:
                r = requests.get(f"{API_URL}/validate-session/{token}", timeout=5)
                if r.status_code == 200:
                    st.session_state.user = r.json()
                    st.session_state.session_token = token
                else:
                    safe_set_cookie("erp_session_token", "", max_age=0)
            except Exception:
                st.error("📡 Cannot connect to the backend server. Is it running?")


def logout():
    st.session_state.force_logout = True
    token = st.session_state.get("session_token")
    if token:
        try:
            requests.delete(f"{API_URL}/logout/{token}", headers=HEADERS, timeout=3)
        except Exception:
            pass
    safe_set_cookie("erp_session_token", "", max_age=0)
    st.session_state.user = None
    st.session_state.session_token = None
    st.session_state.current_page = "Home"
    st.query_params.clear()
    if "menu" in st.session_state:
        del st.session_state["menu"]
    st.rerun()


def try_login(payload, retries=2):
    for _ in range(retries):
        try:
            return requests.post(f"{API_URL}/login", json=payload, timeout=2)
        except Exception:
            time.sleep(0.5)
    return None


def fetch(path):
    try:
        r = requests.get(f"{API_URL}/{path}", headers=HEADERS, timeout=3)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def auth_headers():
    """Returns headers with both API key and session token."""
    h = dict(HEADERS)
    token = st.session_state.get("session_token")
    if token:
        h["X-Session-Token"] = token
    return h


def item_selector():
    items = fetch("items/")
    if not items:
        return None
    df = pd.DataFrame(items)
    if "iid" not in st.session_state:
        st.session_state.iid = "Select ID"
    if "iname" not in st.session_state:
        st.session_state.iname = "Select Name"

    def sync_id():
        if st.session_state.iname != "Select Name":
            st.session_state.iid = str(df[df["item_name"] == st.session_state.iname].iloc[0]["id"])

    def sync_name():
        if st.session_state.iid != "Select ID":
            st.session_state.iname = df[df["id"] == int(st.session_state.iid)].iloc[0]["item_name"]

    c1, c2 = st.columns(2)
    c1.selectbox("Filter by ID",   ["Select ID"]   + [str(x) for x in df["id"]],  key="iid",   on_change=sync_name)
    c2.selectbox("Filter by Name", ["Select Name"] + df["item_name"].tolist(),      key="iname", on_change=sync_id)
    return (
        df[df["item_name"] == st.session_state.iname].iloc[0]
        if st.session_state.iname != "Select Name" else None
    )


def filter_df(df, search, columns):
    """Return rows where any of the given columns contain the search string (case-insensitive)."""
    if not search or df.empty:
        return df
    mask = pd.Series([False] * len(df), index=df.index)
    for col in columns:
        if col in df.columns:
            mask |= df[col].astype(str).str.lower().str.contains(search.strip().lower(), na=False)
    return df[mask]

# ================= BACKEND WARM-UP =================
try:
    requests.get(f"{API_URL}/stock-report/", timeout=1)
except Exception:
    pass

check_session()

# ================= LOGIN =================
if st.session_state.user is None:
    if st.session_state.get("force_logout", False):
        safe_set_cookie("erp_session_token", "", max_age=0)

    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.title("Dynolt Technologies")
        st.subheader("Inventory Login")
        with st.form("login"):
            u    = st.text_input("Username").strip()
            p    = st.text_input("Password", type="password")
            stay = st.checkbox("Stay Logged In")
            if st.form_submit_button("Sign In"):
                res = try_login({"username": u, "password": p})
                if res is None:
                    st.error("📡 Backend not responding. Please check your server.")
                elif res.status_code == 200:
                    data = res.json()
                    st.session_state.user = {"username": data["username"], "role": data["role"]}
                    st.session_state.session_token = data["token"]
                    st.session_state.force_logout = False
                    if stay:
                        safe_set_cookie("erp_session_token", data["token"], max_age=2592000)
                    else:
                        safe_set_cookie("erp_session_token", data["token"])
                    time.sleep(0.5)
                    st.query_params["page"] = "Home"
                    st.rerun()
                elif res.status_code == 401:
                    st.error("❌ Invalid Username or Password")
                else:
                    st.error(f"⚠️ Unexpected Error: {res.status_code}")

else:
    role  = st.session_state.user["role"]
    uname = st.session_state.user["username"]

    menu  = ["Home", "Stock View"]
    icons = ["house", "grid"]

    if role in ["Admin", "Manager"]:
        menu  += ["Record Inward", "Record Issue", "View Transactions", "Reports"]
        icons += ["box-arrow-in-right", "box-arrow-right", "eye", "file-earmark-bar-graph"]

    if role == "Admin":
        menu  += [
            "Add Item", "Delete Item", "Add Supplier", "View Supplier",
            "Add Spec", "View Specs", "Delete Spec", "Manage Users", "Audit Logs"
        ]
        icons += [
            "plus-circle", "trash", "person-plus", "eye",
            "file-earmark-plus", "file-earmark-text", "x-circle", "people", "journal-text"
        ]

    if role == "Manager":
        menu  += ["View Specs"]
        icons += ["file-earmark-text"]

    menu  += ["Logout"]
    icons += ["door-open"]

    if st.session_state.current_page not in menu:
        st.session_state.current_page = "Home"

    # ================= SIDEBAR =================
    with st.sidebar:
        st.markdown(f"""
        <div class="profile-box">
            <img src="https://ui-avatars.com/api/?name={uname}&background=4477FF&color=fff" class="profile-pic">
            <div class="profile-name">{uname} ({role})</div>
        </div>
        """, unsafe_allow_html=True)

        selected = option_menu(
            None, menu, icons=icons,
            default_index=menu.index(st.session_state.current_page),
            key="menu"
        )

    if selected != st.session_state.current_page:
        if selected == "Logout":
            logout()
        else:
            if st.session_state.current_page in ITEM_SELECTOR_PAGES:
                for key in ["iid", "iname"]:
                    if key in st.session_state:
                        del st.session_state[key]
            st.session_state.current_page = selected
            st.query_params["page"] = selected
            st.rerun()

    page = st.session_state.current_page

    # ================= PAGES =================

    # ── HOME ────────────────────────────────────────────────────────
    if page == "Home":
        st.title(f"🏭 Welcome back, {uname}")
        st.info(f"System Online | Role: {role}")
        data = fetch("stock-report/")
        if data:
            reorder_items = [r for r in data if r["current_stock"] <= r["security_stock"]]
            if reorder_items and not st.session_state.get("alert_dismissed"):
                with st.container(border=True):
                    st.error(f"⚠️ **Reorder Alert — {len(reorder_items)} item(s) need attention**")
                    for item in reorder_items:
                        st.write(f"🔴 **{item['item_name']}** — Current: `{item['current_stock']}` | Security Stock: `{item['security_stock']}`")
                    if st.button("✅ Dismiss"):
                        st.session_state.alert_dismissed = True
                        st.rerun()
            elif not reorder_items:
                st.success("✅ All items are sufficiently stocked.")

    # ── STOCK VIEW ──────────────────────────────────────────────────
    elif page == "Stock View":
        st.title("📊 Inventory Status")
        data = fetch("stock-report/")
        if data:
            df = pd.DataFrame(data, columns=[
                "item_id", "item_name", "item_type",
                "total_inward", "total_issue", "current_stock", "security_stock"
            ])
            df["Status"] = ["🔴 REORDER" if c <= s else "🟢 OK"
                            for c, s in zip(df["current_stock"], df["security_stock"])]

            search = st.text_input("🔍 Search by item name or ID", placeholder="e.g. Widget or 5", key="sv_search")
            df = filter_df(df, search, ["item_id", "item_name"])
            st.caption(f"{len(df)} result(s)")

            st.data_editor(df, disabled=True, hide_index=True, use_container_width=True,
                column_config={
                    "item_id":        st.column_config.NumberColumn("Item ID",        width="small"),
                    "item_name":      st.column_config.TextColumn("Item Name",        width="small"),
                    "item_type":      st.column_config.TextColumn("Item Type",        width="small"),
                    "total_inward":   st.column_config.NumberColumn("Total Inward",   width="small"),
                    "total_issue":    st.column_config.NumberColumn("Total Issue",     width="small"),
                    "current_stock":  st.column_config.NumberColumn("Current Stock",  width="small"),
                    "security_stock": st.column_config.NumberColumn("Security Stock", width="small"),
                    "Status":         st.column_config.TextColumn("Status",           width="small"),
                })
        else:
            st.error("No data to view")

    # ── RECORD INWARD ───────────────────────────────────────────────
    elif page == "Record Inward":
        st.title("🚚 Inward Entry (Multi-Item Invoice)")
        items = fetch("items/")
        if not items:
            st.warning("No items found. Please add items first.")
        else:
            item_options = [f"{i['id']} | {i['item_name']}" for i in items]
            st.subheader("Invoice Details")
            c1, c2, c3 = st.columns(3)
            inv = c1.text_input("Invoice #")
            d1  = c2.date_input("Order Date",    max_value=date.today())
            d2  = c3.date_input("Received Date", value=date.today(), max_value=date.today())
            st.divider()

            if "inward_rows" not in st.session_state:
                st.session_state.inward_rows = 1

            st.subheader("Items Received")
            row_data = []
            for i in range(st.session_state.inward_rows):
                rc1, rc2, rc3 = st.columns([2, 1, 1])
                sel_item = rc1.selectbox(f"Item {i+1}", item_options, index=None, placeholder="Select an item", key=f"item_{i}")
                rate     = rc2.number_input("Rate",     min_value=0.0, step=0.1, key=f"rate_{i}")
                qty      = rc3.number_input("Quantity", min_value=1,   step=1,   key=f"qty_{i}")
                row_data.append({"selected_item": sel_item, "rate": rate, "qty": qty})

            if st.button("➕ Add Another Item"):
                st.session_state.inward_rows += 1
                st.rerun()
            st.divider()

            msg = st.empty()
            if st.button("💾 Submit Invoice", type="primary"):
                if not inv.strip():
                    msg.error("Invoice number is required.")
                elif d1 > d2:
                    msg.error("Received Date cannot be before Order Date.")
                else:
                    success_count = 0
                    errors = []
                    for idx, row in enumerate(row_data):
                        if row["selected_item"] is None:
                            errors.append(f"Row {idx+1} skipped: No item selected.")
                            continue
                        target_id = int(row["selected_item"].split(" | ")[0])
                        try:
                            res = requests.post(
                                f"{API_URL}/inwards/",
                                json={
                                    "item_id": target_id, "invoice_number": inv.strip(),
                                    "quantity": row["qty"], "rate": row["rate"],
                                    "order_date": str(d1), "received_date": str(d2)
                                },
                                headers=auth_headers(), timeout=5
                            )
                            if res.status_code == 200:
                                success_count += 1
                            else:
                                errors.append(f"Row {idx+1} failed: {res.json().get('detail', 'Backend Error')}")
                        except Exception:
                            errors.append(f"Row {idx+1} failed: Could not connect to backend.")
                    for e in errors:
                        st.error(e)
                    if success_count > 0:
                        msg.success(f"Successfully recorded {success_count} item(s) under Invoice {inv}!")
                        time.sleep(1)
                        st.session_state.inward_rows = 1
                        st.rerun()

    # ── RECORD ISSUE ────────────────────────────────────────────────
    elif page == "Record Issue":
        st.title("📦 Issue Entry")
        items = fetch("items/")
        if not items:
            st.warning("No items found. Please add items first.")
        else:
            target = item_selector()
            with st.form("issue"):
                qty = st.number_input("Quantity", min_value=1)
                to  = st.text_input("Issued To")
                d   = st.date_input("Issue Date", value=date.today(), max_value=date.today())
                msg = st.empty()
                if st.form_submit_button("Post"):
                    if target is None:
                        msg.error("Please select an item first.")
                    elif not (to.strip() and d and qty):
                        msg.error("All fields are required.")
                    else:
                        res = requests.post(
                            f"{API_URL}/issues/",
                            json={"item_id": int(target["id"]), "quantity": qty,
                                  "issue_date": str(d), "issued_to": to.strip()},
                            headers=auth_headers(), timeout=5
                        )
                        if res.status_code != 200:
                            msg.error(res.json().get("detail", "Not enough stock for this issue"))
                        else:
                            msg.success("Issue Recorded")
                            time.sleep(1)
                            st.rerun()

    # ── VIEW TRANSACTIONS ───────────────────────────────────────────
    elif page == "View Transactions":
        st.title("View Transactions")

        items_data = fetch("items/")
        items_map  = {i["id"]: i["item_name"] for i in items_data} if items_data else {}

        # --- INWARDS ---
        data = fetch("inwards/")
        if data:
            st.subheader("Inward Transactions")
            df = pd.DataFrame(data, columns=[
                "transaction_id", "item_id", "invoice_number",
                "quantity", "rate", "order_date", "received_date"
            ])
            df.insert(2, "item_name", df["item_id"].map(items_map).fillna(""))

            sc1, sc2, sc3 = st.columns(3)
            s_item    = sc1.text_input("🔍 Item name or ID",             key="inv_item",    placeholder="e.g. Widget or 5")
            s_invoice = sc2.text_input("🔍 Invoice number",              key="inv_invoice", placeholder="e.g. INV-001")
            s_date    = sc3.text_input("🔍 Date (YYYY-MM-DD or partial)", key="inv_date",   placeholder="e.g. 2026-02")

            df = filter_df(df, s_item,    ["item_id", "item_name"])
            df = filter_df(df, s_invoice, ["invoice_number"])
            df = filter_df(df, s_date,    ["order_date", "received_date"])

            st.caption(f"{len(df)} result(s)")
            st.data_editor(df, disabled=True, hide_index=True, use_container_width=True,
                column_config={
                    "transaction_id": st.column_config.NumberColumn("Transaction ID", width="small"),
                    "item_id":        st.column_config.NumberColumn("Item ID",        width="small"),
                    "item_name":      st.column_config.TextColumn("Item Name",        width="medium"),
                    "invoice_number": st.column_config.TextColumn("Invoice No.",      width="medium"),
                    "quantity":       st.column_config.NumberColumn("Quantity",        width="small"),
                    "rate":           st.column_config.NumberColumn("Rate",            width="small"),
                    "order_date":     st.column_config.TextColumn("Order Date",        width="small"),
                    "received_date":  st.column_config.TextColumn("Received Date",     width="small"),
                })
        else:
            st.error("No Inward Transactions Found!")

        st.divider()

        # --- ISSUES ---
        data1 = fetch("issues/")
        if data1:
            st.subheader("Issue Transactions")
            df1 = pd.DataFrame(data1, columns=[
                "transaction_id", "item_id", "quantity", "issue_date", "issued_to"
            ])
            df1.insert(2, "item_name", df1["item_id"].map(items_map).fillna(""))

            sc4, sc5 = st.columns(2)
            s_item2 = sc4.text_input("🔍 Item name or ID",              key="iss_item", placeholder="e.g. Widget or 5")
            s_date2 = sc5.text_input("🔍 Date (YYYY-MM-DD or partial)", key="iss_date", placeholder="e.g. 2026-02")

            df1 = filter_df(df1, s_item2, ["item_id", "item_name"])
            df1 = filter_df(df1, s_date2, ["issue_date"])

            st.caption(f"{len(df1)} result(s)")
            st.data_editor(df1, disabled=True, hide_index=True, use_container_width=True,
                column_config={
                    "transaction_id": st.column_config.NumberColumn("Transaction ID", width="small"),
                    "item_id":        st.column_config.NumberColumn("Item ID",        width="small"),
                    "item_name":      st.column_config.TextColumn("Item Name",        width="medium"),
                    "quantity":       st.column_config.NumberColumn("Quantity",        width="small"),
                    "issue_date":     st.column_config.TextColumn("Issue Date",        width="small"),
                    "issued_to":      st.column_config.TextColumn("Issued To",         width="small"),
                })
        else:
            st.error("No Issue Transactions Found!")
    # ── MANAGE USERS ────────────────────────────────────────────────
    elif page == "Manage Users":
        st.title("👥 User Management")
        t1, t2 = st.tabs(["Users", "Create"])

        with t1:
            for u in fetch("users/"):
                c1, c2, c3 = st.columns([2, 2, 1])
                c1.write(u["username"])
                c2.write(u["role"])
                if c3.button("Delete", key=f"d_{u['id']}"):
                    if u["username"] != uname:
                        requests.delete(f"{API_URL}/users/{u['id']}", headers=auth_headers())
                        st.rerun()
                    else:
                        st.error("Cannot delete yourself")

        with t2:
            with st.form("create_user"):
                nu = st.text_input("Username")
                np = st.text_input("Password", type="password")
                nr = st.selectbox("Role", ["Select", "Admin", "Manager", "Viewer"])
                msg = st.empty()
                if st.form_submit_button("Create"):
                    if not nu.strip():
                        msg.error("Username is required.")
                    elif not np.strip():
                        msg.error("Password is required.")
                    elif nr == "Select":
                        msg.error("Please select a role.")
                    else:
                        res = requests.post(
                            f"{API_URL}/users/",
                            json={"username": nu, "password": np, "role": nr},
                            headers=auth_headers()
                        )
                        if res.status_code == 200:
                            msg.success("User Created")
                            time.sleep(1)
                            st.rerun()
                        else:
                            msg.error("Failed to create user")
                            time.sleep(1)
                            msg.empty()

    # ── ADD ITEM ────────────────────────────────────────────────────
    elif page == "Add Item":
        st.title("➕ Add Item")
        specs = fetch("specs/")
        supps = fetch("suppliers/")
        s_map = {s["spec"]: s["id"] for s in specs}
        v_map = {v["name"]: v["id"] for v in supps}

        if not s_map or not v_map:
            st.warning("Please add specifications and suppliers first.")
        else:
            with st.form("item"):
                c1, c2 = st.columns(2)
                name = c1.text_input("Item Name")
                cat  = c2.selectbox("Category", ["Select", "RAW", "FINAL"])
                sp   = c1.selectbox("Spec",     ["Select"] + list(s_map))
                su   = c2.selectbox("Supplier", ["Select"] + list(v_map))
                lt   = c1.number_input("Lead Time",      min_value=0)
                ss   = c2.number_input("Security Stock", min_value=0)
                msg  = st.empty()

                if st.form_submit_button("Save"):
                    if not name.strip():
                        msg.error("Item Name is required.")
                    elif cat == "Select":
                        msg.error("Please select a category.")
                    elif sp == "Select":
                        msg.error("Please select a specification.")
                    elif su == "Select":
                        msg.error("Please select a supplier.")
                    else:
                        res = requests.post(
                            f"{API_URL}/items/",
                            json={
                                "item_name": name.strip(), "item_type": cat,
                                "spec_id": s_map[sp], "lead_time": lt,
                                "security_stock": ss, "supplier_id": v_map[su],
                                "rack": "", "bin": ""
                            },
                            headers=auth_headers()
                        )
                        if res.status_code == 200:
                            msg.success("Item Added")
                            time.sleep(1)
                            st.rerun()
                        else:
                            msg.error(res.json().get("detail", "Failed to add item"))
                            time.sleep(1)
                            msg.empty()

    # ── DELETE ITEM ─────────────────────────────────────────────────
    elif page == "Delete Item":
        st.title("🗑️ Delete Item")
        item = fetch("items/")
        if not item:
            st.error("No items found. Please add items first.")
        else:
            target = item_selector()
            if target is not None:
                msg = st.empty()
                if st.button("Delete Item"):
                    res = requests.delete(f"{API_URL}/items/{target['id']}", headers=auth_headers())
                    if res.status_code == 200:
                        msg.success("Item Deleted")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error(res.json().get("detail", "Failed to delete item"))
                        time.sleep(1)
                        msg.empty()

    # ── ADD SUPPLIER ────────────────────────────────────────────────
    elif page == "Add Supplier":
        st.title("🤝 Add Supplier")
        with st.form("supplier"):
            c1, c2 = st.columns(2)
            sn    = c1.text_input("Supplier Name")
            sgst  = c2.text_input("GST # (Optional)")
            scont = c1.text_input("Contact")
            slead = c2.number_input("Lead Time", min_value=0)
            msg   = st.empty()
            if st.form_submit_button("Save"):
                if not (sn.strip() and scont.strip()):
                    msg.error("Supplier Name and Contact are required.")
                    time.sleep(1)
                    msg.empty()
                else:
                    res = requests.post(
                        f"{API_URL}/suppliers/",
                        json={
                            "name": sn.strip(),
                            "gst_no": sgst.strip() if sgst.strip() else None,
                            "contact": scont.strip(), "lead_time": slead
                        },
                        headers=auth_headers()
                    )
                    if res.status_code == 200:
                        msg.success("Supplier Added")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error("Failed to add supplier")
                        time.sleep(1)
                        msg.empty()

    # ── VIEW SUPPLIER ───────────────────────────────────────────────
    elif page == "View Supplier":
        st.title("Supplier List")
        data = fetch("suppliers/")
        if data:
            df = pd.DataFrame(data, columns=[
                "id", "name", "contact", "gst_no",
                "lead_time", "last_purchase_date", "last_purchase_rate"
            ])

            sc1, sc2 = st.columns(2)
            s_name = sc1.text_input("🔍 Supplier name", key="sup_name", placeholder="e.g. Tata")
            s_date = sc2.text_input("🔍 Last purchase date (partial)", key="sup_date", placeholder="e.g. 2026-01")

            df = filter_df(df, s_name, ["name"])
            df = filter_df(df, s_date, ["last_purchase_date"])

            st.caption(f"{len(df)} result(s)")
            st.data_editor(df, column_config={
                "id": st.column_config.NumberColumn("ID", width="small"),
                "name": st.column_config.TextColumn("Name", width="medium"),
                "contact": st.column_config.TextColumn("Contact", width="medium"),
                "gst_no": st.column_config.TextColumn("GST No.", width="small"),
                "lead_time": st.column_config.NumberColumn("Lead Time (Days)", width="small"),
                "last_purchase_date": st.column_config.TextColumn("Last Purchase Date", width="small"),
                "last_purchase_rate": st.column_config.NumberColumn("Last Purchase Rate", width="small")
                }, disabled=True, use_container_width=True, hide_index=True)
        else:
            st.error("No suppliers added")

    # ── ADD SPEC ────────────────────────────────────────────────────
    elif page == "Add Spec":
        st.title("📜 Add Specification")
        with st.form("spec"):
            sp   = st.text_input("Specification")
            desc = st.text_area("Description (Optional)")
            msg  = st.empty()
            if st.form_submit_button("Save"):
                if not sp.strip():
                    msg.error("Specification field is required.")
                    time.sleep(1)
                    msg.empty()
                else:
                    res = requests.post(
                        f"{API_URL}/specs/",
                        json={"spec": sp.strip(), "description": desc},
                        headers=auth_headers()
                    )
                    if res.status_code == 200:
                        msg.success("Specification Added")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error("Failed to add specification")
                        time.sleep(1)
                        msg.empty()

    # ── VIEW SPECS ──────────────────────────────────────────────────
    elif page == "View Specs":
        st.title("📜 Specifications")
        data = fetch("specs/")
        if data:
            df = pd.DataFrame(data)
            c1,c2,c3 = st.columns(3)
            s_spec = c1.text_input("🔍 Search by spec name or description", key="spec_search", placeholder="e.g. Steel or IS:2062")
            df = filter_df(df, s_spec, ["spec", "description"])
            st.caption(f"{len(df)} result(s)")
            st.dataframe(df, use_container_width=True, hide_index=True)

    # ── DELETE SPEC ─────────────────────────────────────────────────
    elif page == "Delete Spec":
        st.title("🗑️ Delete Specification")
        specs = fetch("specs/")
        c1, c2, c3 = st.columns(3)
        if specs:
            s_map = {s["spec"]: s["id"] for s in specs}
            sp    = c1.selectbox("Select Spec to Delete", ["Select"] + list(s_map))
            msg   = st.empty()
            if st.button("Delete Spec"):
                if sp == "Select":
                    msg.error("Please select a specification to delete.")
                else:
                    res = requests.delete(f"{API_URL}/specs/{s_map[sp]}", headers=auth_headers())
                    if res.status_code == 200:
                        msg.success("Specification Deleted")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error(res.json().get("detail", "Failed to delete specification"))
                        time.sleep(1)
                        msg.empty()

    # ── REPORTS ─────────────────────────────────────────────────────
    elif page == "Reports":
        st.title("📊 Inventory Reports")
        tab1, tab2 = st.tabs(["📅 Daily Report", "🗓️ Monthly Report"])

        with tab1:
            st.subheader("Daily Report")
            selected_date = st.date_input("Select Date", value=date.today(), max_value=date.today(), key="daily_date")
            if st.button("Generate Daily Report", type="primary", key="gen_daily"):
                try:
                    r = requests.get(f"{API_URL}/report/daily",
                                     params={"report_date": str(selected_date)},
                                     headers=HEADERS, timeout=5)
                    data = r.json() if r.status_code == 200 else []
                except Exception:
                    st.error("Could not connect to backend.")
                    data = []

                if data:
                    df = pd.DataFrame(data, columns=[
                        "item_id", "item_name", "date",
                        "opening_stock", "total_inward", "total_issue", "closing_stock"
                    ])
                    s_item = st.text_input("🔍 Search by item name or ID", key="daily_search", placeholder="e.g. Widget or 5")
                    df = filter_df(df, s_item, ["item_id", "item_name"])
                    st.caption(f"{len(df)} result(s)")

                    st.data_editor(df, disabled=True, hide_index=True, use_container_width=True,
                        column_config={
                            "item_id":       st.column_config.NumberColumn("Item ID",       width="small"),
                            "item_name":     st.column_config.TextColumn("Item Name",       width="medium"),
                            "date":          st.column_config.TextColumn("Date",            width="small"),
                            "opening_stock": st.column_config.NumberColumn("Opening Stock", width="small"),
                            "total_inward":  st.column_config.NumberColumn("Total Inward",  width="small"),
                            "total_issue":   st.column_config.NumberColumn("Total Issue",   width="small"),
                            "closing_stock": st.column_config.NumberColumn("Closing Stock", width="small"),
                        })
                    csv = df.to_csv(index=False).encode("utf-8")
                    st.download_button("⬇️ Download CSV", csv,
                                       file_name=f"daily_report_{selected_date}.csv", mime="text/csv")
                else:
                    st.info("No data found for this date.")

        with tab2:
            st.subheader("Monthly Report")
            c1, c2 = st.columns(2)
            selected_year  = c1.number_input("Year", min_value=2000, max_value=date.today().year, value=date.today().year)
            selected_month = c2.selectbox("Month", list(range(1, 13)),
                format_func=lambda m: date(2000, m, 1).strftime("%B"),
                index=date.today().month - 1)

            if st.button("Generate Monthly Report", type="primary", key="gen_monthly"):
                try:
                    r = requests.get(f"{API_URL}/report/monthly",
                                     params={"year": selected_year, "month": selected_month},
                                     headers=HEADERS, timeout=5)
                    data = r.json() if r.status_code == 200 else []
                except Exception:
                    st.error("Could not connect to backend.")
                    data = []

                if data:
                    df = pd.DataFrame(data, columns=[
                        "item_id", "item_name", "month",
                        "opening_stock", "total_inward", "total_issue", "closing_stock"
                    ])
                    s_item = st.text_input("🔍 Search by item name or ID", key="monthly_search", placeholder="e.g. Widget or 5")
                    df = filter_df(df, s_item, ["item_id", "item_name"])
                    st.caption(f"{len(df)} result(s)")

                    st.data_editor(df, disabled=True, hide_index=True, use_container_width=True,
                        column_config={
                            "item_id":       st.column_config.NumberColumn("Item ID",       width="small"),
                            "item_name":     st.column_config.TextColumn("Item Name",       width="medium"),
                            "month":         st.column_config.TextColumn("Month",           width="small"),
                            "opening_stock": st.column_config.NumberColumn("Opening Stock", width="small"),
                            "total_inward":  st.column_config.NumberColumn("Total Inward",  width="small"),
                            "total_issue":   st.column_config.NumberColumn("Total Issue",   width="small"),
                            "closing_stock": st.column_config.NumberColumn("Closing Stock", width="small"),
                        })
                    csv = df.to_csv(index=False).encode("utf-8")
                    st.download_button("⬇️ Download CSV", csv,
                                       file_name=f"monthly_report_{selected_year}_{selected_month:02d}.csv",
                                       mime="text/csv")
                else:
                    st.info("No data found for this month.")

    # ── AUDIT LOGS ──────────────────────────────────────────────────
    elif page == "Audit Logs":
        st.title("📋 Audit Logs")
        st.caption("All successful database actions. Failures are stored in logs/failures.log on the server.")

        with st.expander("🔍 Search & Filter", expanded=True):
            fc1, fc2, fc3 = st.columns(3)
            fc4, fc5, fc6 = st.columns(3)

            from_date       = fc1.date_input("From Date", value=None, key="log_from")
            to_date         = fc2.date_input("To Date",   value=None, key="log_to")
            action_filter   = fc3.selectbox("Action", ["All", "CREATE", "DELETE", "ISSUE", "INWARD", "LOGIN"], key="log_action")
            table_filter    = fc4.selectbox("Table",  ["All", "items", "suppliers", "spec_list", "inwards", "issues", "users"], key="log_table")
            username_filter = fc5.text_input("Username (partial match)", key="log_user")
            limit           = fc6.selectbox("Results per page", [25, 50, 100, 200], key="log_limit")

        if "log_offset" not in st.session_state:
            st.session_state.log_offset = 0

        filter_sig = f"{from_date}{to_date}{action_filter}{table_filter}{username_filter}{limit}"
        if st.session_state.get("log_filter_sig") != filter_sig:
            st.session_state.log_offset = 0
            st.session_state.log_filter_sig = filter_sig

        params = {"limit": limit, "offset": st.session_state.log_offset}
        if from_date:
            params["from_date"] = str(from_date)
        if to_date:
            params["to_date"] = str(to_date)
        if action_filter != "All":
            params["action"] = action_filter
        if table_filter != "All":
            params["table_name"] = table_filter
        if username_filter.strip():
            params["username"] = username_filter.strip()

        try:
            r = requests.get(f"{API_URL}/logs/", params=params, headers=auth_headers(), timeout=5)
            if r.status_code == 200:
                result = r.json()
                logs   = result.get("logs", [])
                total  = result.get("total", 0)
            else:
                logs, total = [], 0
                st.error(f"Failed to fetch logs: {r.status_code}")
        except Exception:
            logs, total = [], 0
            st.error("Could not connect to backend.")

        offset       = st.session_state.log_offset
        showing_from = offset + 1 if total > 0 else 0
        showing_to   = min(offset + limit, total)
        st.markdown(f"**{total} total records** — showing {showing_from}–{showing_to}")

        if logs:
            df = pd.DataFrame(logs, columns=["id", "timestamp", "username", "action", "table_name", "record_id", "detail"])
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
            df = df.rename(columns={
                "id": "Log ID", "timestamp": "Timestamp", "username": "User",
                "action": "Action", "table_name": "Table", "record_id": "Record ID", "detail": "Detail"
            })
            st.data_editor(df, disabled=True, hide_index=True, use_container_width=True,
                column_config={
                    "Log ID":    st.column_config.NumberColumn("Log ID",    width="small"),
                    "Timestamp": st.column_config.TextColumn("Timestamp",   width="medium"),
                    "User":      st.column_config.TextColumn("User",        width="small"),
                    "Action":    st.column_config.TextColumn("Action",      width="small"),
                    "Table":     st.column_config.TextColumn("Table",       width="small"),
                    "Record ID": st.column_config.NumberColumn("Record ID", width="small"),
                    "Detail":    st.column_config.TextColumn("Detail",      width="large"),
                })

            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", csv,
                               file_name=f"audit_logs_{date.today()}.csv", mime="text/csv")

            st.divider()
            pc1, pc2, pc3 = st.columns([1, 2, 1])
            with pc1:
                if st.button("◀ Previous", disabled=(offset == 0)):
                    st.session_state.log_offset = max(0, offset - limit)
                    st.rerun()
            with pc2:
                total_pages  = max(1, -(-total // limit))
                current_page = offset // limit + 1
                st.markdown(
                    f"<div style='text-align:center;padding-top:8px'>Page {current_page} of {total_pages}</div>",
                    unsafe_allow_html=True
                )
            with pc3:
                if st.button("Next ▶", disabled=(showing_to >= total)):
                    st.session_state.log_offset = offset + limit
                    st.rerun()
        else:
            st.info("No log entries found matching your filters.")