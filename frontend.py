import streamlit as st
import requests
import pandas as pd
import time
import os
from datetime import date
from streamlit_option_menu import option_menu
from streamlit_cookies_controller import CookieController
from dotenv import load_dotenv
load_dotenv()

# ================= CONFIG =================
API_URL = "http://127.0.0.1:8000"
st.set_page_config(page_title="Industrial ERP", layout="wide")

# FIX: API key sent with every request so backend routes are protected
API_KEY = os.environ.get("ERP_API_KEY")
HEADERS = {"X-API-Key": API_KEY}

cookie_controller = CookieController()

# ================= COOKIE HELPER =================
def safe_set_cookie(name, value, max_age=None):
    """Safely sets a cookie, patching the library's NoneType bug."""
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

# ================= SESSION STATE SETUP =================
if "user" not in st.session_state:
    st.session_state.user = None

# FIX: Store the opaque session token instead of the username
if "session_token" not in st.session_state:
    st.session_state.session_token = None

if "current_page" not in st.session_state:
    st.session_state.current_page = st.query_params.get("page", "Home")

if "force_logout" not in st.session_state:
    st.session_state.force_logout = False

# Pages that use item_selector — used to clear state on navigation
ITEM_SELECTOR_PAGES = {"Record Issue", "Delete Item"}

# ================= CORE FUNCTIONS =================
def check_session():
    """Checks for a valid session token cookie and restores the user session."""
    if st.session_state.user is None:
        if st.session_state.get("force_logout", False):
            return

        token = None

        # Try native Streamlit cookies first (instant, no rerun needed)
        if hasattr(st, "context") and hasattr(st.context, "cookies"):
            token = st.context.cookies.get("erp_session_token")

        # Fallback to the React component
        if not token:
            try:
                token = cookie_controller.get("erp_session_token")
            except TypeError:
                pass

        if token:
            try:
                # FIX: Validate the opaque token, not a username
                r = requests.get(
                    f"{API_URL}/validate-session/{token}",
                    timeout=5
                )
                if r.status_code == 200:
                    st.session_state.user = r.json()
                    st.session_state.session_token = token
                else:
                    # Token is stale/invalid — delete the cookie
                    safe_set_cookie("erp_session_token", "", max_age=0)
            except Exception:
                st.error("📡 Cannot connect to the backend server. Is it running?")


def logout():
    """Wipes the server-side session token, cookie, and local state."""
    st.session_state.force_logout = True

    # FIX: Delete the session server-side so the token is truly invalidated
    token = st.session_state.get("session_token")
    if token:
        try:
            requests.delete(
                f"{API_URL}/logout/{token}",
                headers=HEADERS,
                timeout=3
            )
        except Exception:
            pass  # Best-effort — cookie is still cleared below

    # Clear the cookie
    safe_set_cookie("erp_session_token", "", max_age=0)

    # Clear local state
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
            # Login does NOT send the API key — it's a public endpoint
            return requests.post(f"{API_URL}/login", json=payload, timeout=2)
        except Exception:
            time.sleep(0.5)
    return None


def fetch(path):
    """GET helper — sends API key header on every request."""
    try:
        r = requests.get(f"{API_URL}/{path}", headers=HEADERS, timeout=3)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


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
            st.session_state.iid = str(
                df[df["item_name"] == st.session_state.iname].iloc[0]["id"]
            )

    def sync_name():
        if st.session_state.iid != "Select ID":
            st.session_state.iname = df[
                df["id"] == int(st.session_state.iid)
            ].iloc[0]["item_name"]

    c1, c2 = st.columns(2)
    c1.selectbox(
        "Filter by ID",
        ["Select ID"] + [str(x) for x in df["id"]],
        key="iid",
        on_change=sync_name
    )
    c2.selectbox(
        "Filter by Name",
        ["Select Name"] + df["item_name"].tolist(),
        key="iname",
        on_change=sync_id
    )

    return (
        df[df["item_name"] == st.session_state.iname].iloc[0]
        if st.session_state.iname != "Select Name"
        else None
    )

# ================= BACKEND WARM-UP =================
try:
    requests.get(f"{API_URL}/stock-report/", timeout=1)
except Exception:
    pass

# Restore session from cookie before drawing any UI
check_session()

# ================= APP ROUTING =================
if st.session_state.user is None:

    # Ensure ghost cookie is cleared after logout
    if st.session_state.get("force_logout", False):
        safe_set_cookie("erp_session_token", "", max_age=0)

    # --- LOGIN SCREEN ---
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.title("Dynolt Technologies")
        st.subheader("Inventory Login")
        with st.form("login"):
            u = st.text_input("Username").strip()
            p = st.text_input("Password", type="password")
            stay = st.checkbox("Stay Logged In")

            if st.form_submit_button("Sign In"):
                res = try_login({"username": u, "password": p})

                if res is None:
                    st.error("📡 Backend not responding. Please check your server.")
                elif res.status_code == 200:
                    data = res.json()

                    # FIX: Store the opaque token in state and cookie, not the username
                    st.session_state.user = {
                        "username": data["username"],
                        "role": data["role"]
                    }
                    st.session_state.session_token = data["token"]
                    st.session_state.force_logout = False

                    if stay:
                        safe_set_cookie(
                            "erp_session_token", data["token"], max_age=2592000
                        )
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
    # --- MAIN APPLICATION ---
    role = st.session_state.user["role"]
    uname = st.session_state.user["username"]

    menu = ["Home", "Stock View"]
    icons = ["house", "grid"]

    if role in ["Admin", "Manager"]:
        menu += ["Record Inward", "Record Issue", "View Transactions"]
        icons += ["box-arrow-in-right", "box-arrow-right", "eye"]

    if role == "Admin":
        menu += [
            "Add Item", "Delete Item", "Add Supplier", "View Supplier",
            "Add Spec", "View Specs", "Delete Spec", "Manage Users"
        ]
        icons += [
            "plus-circle", "trash", "person-plus", "eye",
            "file-earmark-plus", "file-earmark-text", "x-circle", "people"
        ]

    if role == "Manager":
        menu += ["View Specs"]
        icons += ["file-earmark-text"]

    menu += ["Logout"]
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
            None,
            menu,
            icons=icons,
            default_index=menu.index(st.session_state.current_page),
            key="menu"
        )

    # Navigation interceptor
    if selected != st.session_state.current_page:
        if selected == "Logout":
            logout()
        else:
            # FIX: Clear item selector state when leaving pages that use it
            if st.session_state.current_page in ITEM_SELECTOR_PAGES:
                for key in ["iid", "iname"]:
                    if key in st.session_state:
                        del st.session_state[key]

            st.session_state.current_page = selected
            st.query_params["page"] = selected
            st.rerun()

    page = st.session_state.current_page

    # ================= PAGES =================
    if page == "Home":
        st.title(f"🏭 Welcome back, {uname}")
        st.info(f"System Online | Role: {role}")

    elif page == "Stock View":
        st.title("📊 Inventory Status")
        data = fetch("stock-report/")
        if data:
            df = pd.DataFrame(
                data,
                columns=[
                    "item_id", "item_name", "item_type",
                    "total_inward", "total_issue", "current_stock", "security_stock"
                ]
            )
            df["Status"] = [
                "🔴 REORDER" if c <= s else "🟢 OK"
                for c, s in zip(df["current_stock"], df["security_stock"])
            ]
            st.data_editor(
                df, disabled=True, hide_index=True,
                column_config={
                    "item_id":        st.column_config.NumberColumn("Item ID",        width="small"),
                    "item_name":      st.column_config.TextColumn("Item Name",        width="small"),
                    "item_type":      st.column_config.TextColumn("Item Type",        width="small"),
                    "total_inward":   st.column_config.NumberColumn("Total Inward",   width="small"),
                    "total_issue":    st.column_config.NumberColumn("Total Issue",     width="small"),
                    "current_stock":  st.column_config.NumberColumn("Current Stock",  width="small"),
                    "security_stock": st.column_config.NumberColumn("Security Stock", width="small"),
                    "Status":         st.column_config.TextColumn("Status",           width="small"),
                }
            )
        else:
            st.error("No data to view")

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
            d1 = c2.date_input("Order Date", max_value=date.today())
            d2 = c3.date_input("Received Date", value=date.today(), max_value=date.today())

            st.divider()

            if "inward_rows" not in st.session_state:
                st.session_state.inward_rows = 1

            st.subheader("Items Received")
            row_data = []

            for i in range(st.session_state.inward_rows):
                rc1, rc2, rc3 = st.columns([2, 1, 1])
                sel_item = rc1.selectbox(
                    f"Item {i+1}", item_options,
                    index=None, placeholder="Select an item", key=f"item_{i}"
                )
                rate = rc2.number_input(f"Rate",     min_value=0.0, step=0.1, key=f"rate_{i}")
                qty  = rc3.number_input(f"Quantity", min_value=1,   step=1,   key=f"qty_{i}")
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
                                    "item_id":        target_id,
                                    "invoice_number": inv.strip(),
                                    "quantity":       row["qty"],
                                    "rate":           row["rate"],
                                    "order_date":     str(d1),
                                    "received_date":  str(d2)
                                },
                                headers=HEADERS,
                                timeout=5
                            )
                            if res.status_code == 200:
                                success_count += 1
                            else:
                                errors.append(
                                    f"Row {idx+1} failed: "
                                    f"{res.json().get('detail', 'Backend Error')}"
                                )
                        except Exception:
                            errors.append(f"Row {idx+1} failed: Could not connect to backend.")

                    for e in errors:
                        st.error(e)

                    if success_count > 0:
                        msg.success(
                            f"Successfully recorded {success_count} item(s) "
                            f"under Invoice {inv}!"
                        )
                        time.sleep(1)
                        st.session_state.inward_rows = 1
                        st.rerun()

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
                    # FIX: Proper validation guard — no accidental POST on error
                    if target is None:
                        msg.error("Please select an item first.")
                    elif not (to.strip() and d and qty):
                        msg.error("All fields are required.")
                    else:
                        res = requests.post(
                            f"{API_URL}/issues/",
                            json={
                                "item_id":    int(target["id"]),
                                "quantity":   qty,
                                "issue_date": str(d),
                                "issued_to":  to.strip()
                            },
                            headers=HEADERS,
                            timeout=5
                        )
                        if res.status_code != 200:
                            msg.error(
                                res.json().get("detail", "Not enough stock for this issue")
                            )
                        else:
                            msg.success("Issue Recorded")
                            time.sleep(1)
                            st.rerun()

    elif page == "View Transactions":
        st.title("View Transactions")
        data = fetch("inwards/")
        if data:
            st.subheader("Inward Transactions")
            df = pd.DataFrame(
                data,
                columns=[
                    "transaction_id", "item_id", "invoice_number",
                    "quantity", "rate", "order_date", "received_date"
                ]
            )
            st.data_editor(
                df, disabled=True, hide_index=True, use_container_width=True,
                column_config={
                    "transaction_id": st.column_config.NumberColumn("Transaction ID", width="small"),
                    "item_id":        st.column_config.NumberColumn("Item ID",        width="small"),
                    "invoice_number": st.column_config.TextColumn("Invoice No.",      width="medium"),
                    "quantity":       st.column_config.NumberColumn("Quantity",        width="small"),
                    "rate":           st.column_config.NumberColumn("Rate",            width="small"),
                    "order_date":     st.column_config.TextColumn("Order Date",        width="small"),
                    "received_date":  st.column_config.TextColumn("Received Date",     width="small"),
                }
            )
        else:
            st.error("No Inward Transactions Found!")

        data1 = fetch("issues/")
        if data1:
            st.subheader("Issue Transactions")
            df1 = pd.DataFrame(
                data1,
                columns=["transaction_id", "item_id", "quantity", "issue_date", "issued_to"]
            )
            st.data_editor(
                df1, disabled=True, hide_index=True, use_container_width=True,
                column_config={
                    "transaction_id": st.column_config.NumberColumn("Transaction ID", width="small"),
                    "item_id":        st.column_config.NumberColumn("Item ID",        width="small"),
                    "quantity":       st.column_config.NumberColumn("Quantity",        width="small"),
                    "issue_date":     st.column_config.TextColumn("Issue Date",        width="small"),
                    "issued_to":      st.column_config.TextColumn("Issued To",         width="small"),
                }
            )
        else:
            st.error("No Issue Transactions Found!")

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
                        requests.delete(
                            f"{API_URL}/users/{u['id']}", headers=HEADERS
                        )
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
                            headers=HEADERS
                        )
                        if res.status_code == 200:
                            msg.success("User Created")
                            time.sleep(1)
                            st.rerun()
                        else:
                            msg.error("Failed to create user")
                            time.sleep(1)
                            msg.empty()

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
                cat  = c2.selectbox("Category", ["Select"] + ["Raw", "Final"])
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
                                "item_name":      name.strip(),
                                "item_type":      cat,
                                "spec_id":        s_map[sp],
                                "lead_time":      lt,
                                "security_stock": ss,
                                "supplier_id":    v_map[su],
                                "rack": "",
                                "bin":  ""
                            },
                            headers=HEADERS
                        )
                        if res.status_code == 200:
                            msg.success("Item Added")
                            time.sleep(1)
                            st.rerun()
                        else:
                            msg.error("Failed to add item")
                            time.sleep(1)
                            msg.empty()

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
                    res = requests.delete(
                        f"{API_URL}/items/{target['id']}", headers=HEADERS
                    )
                    if res.status_code == 200:
                        msg.success("Item Deleted")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error("Failed to delete item")
                        time.sleep(1)
                        msg.empty()

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
                # FIX: Only require name and contact — GST is labelled optional
                if not (sn.strip() and scont.strip()):
                    msg.error("Supplier Name and Contact are required.")
                    time.sleep(1)
                    msg.empty()
                else:
                    res = requests.post(
                        f"{API_URL}/suppliers/",
                        json={
                            "name":      sn.strip(),
                            "gst_no":    sgst.strip() if sgst.strip() else None,
                            "contact":   scont.strip(),
                            "lead_time": slead
                        },
                        headers=HEADERS
                    )
                    if res.status_code == 200:
                        msg.success("Supplier Added")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error("Failed to add supplier")
                        time.sleep(1)
                        msg.empty()

    elif page == "View Supplier":
        st.title("Supplier List")
        data = fetch("suppliers/")
        if data:
            df = pd.DataFrame(
                data,
                columns=[
                    "id", "name", "contact", "gst_no",
                    "lead_time", "last_purchase_date", "last_purchase_rate"
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.error("No suppliers added")

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
                        headers=HEADERS
                    )
                    if res.status_code == 200:
                        msg.success("Specification Added")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error("Failed to add specification")
                        time.sleep(1)
                        msg.empty()

    elif page == "View Specs":
        st.title("📜 Specifications")
        data = fetch("specs/")
        if data:
            df = pd.DataFrame(data)
            st.dataframe(df, use_container_width=True, hide_index=True)

    elif page == "Delete Spec":
        st.title("🗑️ Delete Specification")
        specs = fetch("specs/")
        if specs:
            s_map = {s["spec"]: s["id"] for s in specs}
            sp    = st.selectbox("Select Spec to Delete", ["Select"] + list(s_map))
            msg   = st.empty()

            if st.button("Delete Spec"):
                if sp == "Select":
                    msg.error("Please select a specification to delete.")
                else:
                    res = requests.delete(
                        f"{API_URL}/specs/{s_map[sp]}", headers=HEADERS
                    )
                    if res.status_code == 200:
                        msg.success("Specification Deleted")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error("Failed to delete specification")
                        time.sleep(1)
                        msg.empty()