import streamlit as st
import requests
import pandas as pd
import time
import os
from datetime import date, datetime
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
:root { color-scheme: light; }
@media (prefers-color-scheme: dark) {
    html, body, .stApp,
    [data-testid="stAppViewContainer"],
    section[data-testid="stSidebar"] {
        background-color: #F8FAFC !important;
        color: #1E293B !important;
    }
    section[data-testid="stSidebar"] * { color: #1E293B !important; }
}
html, body, [data-testid="stAppViewContainer"], .stApp {
    background-color: #F8FAFC !important;
    color: #1E293B !important;
}
* { -webkit-text-fill-color: inherit !important; }

section[data-testid="stSidebar"] {
    background-color: #FFFFFF !important;
    border-right: 1px solid #E6E9EF;
}
section[data-testid="stSidebar"] * { color: #1E293B !important; }
section[data-testid="stSidebar"] > div:first-child { padding-top: 0 !important; }

.profile-box {
    display: flex; align-items: center;
    padding: 18px 16px 14px;
    border-bottom: 1px solid #F1F3F7;
    margin-bottom: 6px;
}
.profile-pic { width:40px; height:40px; border-radius:50%; margin-right:11px; flex-shrink:0; }
.profile-name { font-weight:600; font-size:0.92rem; color:#1E293B !important; }
.profile-role { font-size:0.73rem; color:#64748B !important; }

.erp-nav { display:flex; flex-direction:column; padding:4px 8px; gap:1px; }
.erp-nav a.nav-top, .erp-nav a.nav-sub {
    display:flex; align-items:center; gap:9px; padding:8px 12px;
    border-radius:8px; text-decoration:none !important;
    font-size:0.875rem; font-weight:500; color:#374151 !important;
    transition:background 0.13s,color 0.13s; line-height:1.4; white-space:nowrap;
}
.erp-nav a.nav-top:hover, .erp-nav a.nav-sub:hover { background:#F1F5F9; color:#1E293B !important; }
.erp-nav a.nav-active { background:#EEF2FF !important; color:#4338CA !important; font-weight:600; }
.erp-nav a.nav-sub { font-size:0.845rem; font-weight:400; color:#4B5563 !important; padding:7px 12px 7px 14px; }
.nav-divider { height:1px; background:#F1F3F7; margin:6px 4px; }
.erp-nav a.nav-logout { color:#DC2626 !important; }
.erp-nav a.nav-logout:hover { background:#FEF2F2 !important; color:#B91C1C !important; }

.nav-group-wrap { display:flex; flex-direction:column; }
.nav-toggle-cb { display:none; }
.nav-group-label {
    display:flex; align-items:center; gap:9px; padding:8px 12px;
    border-radius:8px; font-size:0.875rem; font-weight:500;
    color:#374151 !important; cursor:pointer;
    transition:background 0.13s; user-select:none;
}
.nav-group-label:hover { background:#F1F5F9; }
.nav-group-label.grp-active { background:#F5F3FF; color:#4338CA !important; }
.nav-dropdown { display:none; flex-direction:column; gap:1px; padding-left:18px; }
.nav-toggle-cb:checked ~ .nav-dropdown { display:flex; }
.nav-arrow { margin-left:auto; font-size:0.75rem; color:#94A3B8 !important; transition:transform 0.15s; }
.nav-toggle-cb:checked + .nav-group-label .nav-arrow { transform:rotate(90deg); color:#4338CA !important; }
.nav-icon { font-size:1rem; width:18px; text-align:center; }

@media (max-width:768px) {
    html, body, .stApp { background-color:#F8FAFC !important; color:#1E293B !important; }
}

/* KPI Cards */
.kpi-card {
    background:#fff; border:1px solid #E2E8F0; border-radius:12px;
    padding:16px 20px; text-align:center;
}
.kpi-value { font-size:2rem; font-weight:700; color:#1E293B; }
.kpi-label { font-size:0.8rem; color:#64748B; margin-top:2px; }

/* Confirm dialog overlay via Streamlit state — no extra CSS needed */
</style>
""", unsafe_allow_html=True)

# ================= SESSION STATE =================
for key, default in [
    ("user", None), ("session_token", None),
    ("current_page", None), ("force_logout", False),
    ("confirm_action", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

if st.session_state.current_page is None:
    st.session_state.current_page = st.query_params.get("page", "Home")

ITEM_SELECTOR_PAGES = {"Record Issue", "Delete Item"}

# ================= CORE FUNCTIONS =================
def check_session():
    # If this tab already has a validated user, trust it — don't re-read the cookie.
    # st.session_state is tab-isolated, so each tab maintains its own identity.
    if st.session_state.user is not None:
        return
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
                st.session_state.user          = r.json()
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
    st.session_state.user          = None
    st.session_state.session_token = None
    st.session_state.current_page  = "Home"
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


def fetch(path, params=None):
    try:
        r = requests.get(f"{API_URL}/{path}", headers=HEADERS,
                         params=params, timeout=5)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def auth_headers():
    h     = dict(HEADERS)
    token = st.session_state.get("session_token")
    if token:
        h["X-Session-Token"] = token
    return h


def item_selector(item_type_filter=None):
    """
    Returns integer item ID or None.
    FIX: optionally filter by item_type ("RAW" or "FINAL").
    """
    items = fetch("items/")
    if not items:
        return None
    if item_type_filter:
        items = [i for i in items
                 if str(i.get("item_type", "")).upper() == item_type_filter.upper()]
    if not items:
        st.warning(f"No {item_type_filter} items found.")
        return None
    item_options = [f"{i['id']} | {i['item_name']}" for i in items]
    c1, c2, c3  = st.columns([1, 1, 1])
    sel_item     = c1.selectbox("Select Item", item_options, index=None,
                                placeholder="Select an item",
                                key="item_selector_main")
    if sel_item:
        return int(sel_item.split(" | ")[0])
    return None


def filter_df(df, search, columns):
    if not search or df.empty:
        return df
    mask = pd.Series([False] * len(df), index=df.index)
    for col in columns:
        if col in df.columns:
            mask |= df[col].astype(str).str.lower().str.contains(
                search.strip().lower(), na=False)
    return df[mask]


def paginate_df(df, key, page_sizes=(25, 50, 100)):
    """Client-side pagination. Returns sliced df and renders Prev/Next controls."""
    total = len(df)
    sz_key  = f"_{key}_sz"
    off_key = f"_{key}_off"
    if sz_key  not in st.session_state: st.session_state[sz_key]  = page_sizes[0]
    if off_key not in st.session_state: st.session_state[off_key] = 0

    lc, _ = st.columns([1, 4])
    new_sz = lc.selectbox("Per page", page_sizes, key=f"_{key}_sz_sel",
                          index=list(page_sizes).index(st.session_state[sz_key]))
    if new_sz != st.session_state[sz_key]:
        st.session_state[sz_key]  = new_sz
        st.session_state[off_key] = 0

    sz  = st.session_state[sz_key]
    off = st.session_state[off_key]
    off = min(off, max(0, total - 1))          # clamp after filter shrinks df
    st.session_state[off_key] = off

    sliced = df.iloc[off: off + sz]
    st.caption(f"{total} result(s) · showing {off+1}–{min(off+sz, total)}")

    return sliced, sz, off, off_key


def render_page_controls(key, sz, off, off_key, total):
    p1, p2, p3 = st.columns([1, 2, 1])
    with p1:
        if st.button("◀ Prev", key=f"_{key}_prev", disabled=(off == 0)):
            st.session_state[off_key] = max(0, off - sz)
            st.rerun()
    with p2:
        pg  = off // sz + 1
        tpg = max(1, -(-total // sz))
        st.markdown(f"<div style='text-align:center;padding-top:8px'>Page {pg} of {tpg}</div>",
                    unsafe_allow_html=True)
    with p3:
        if st.button("Next ▶", key=f"_{key}_next", disabled=(off + sz >= total)):
            st.session_state[off_key] = off + sz
            st.rerun()


def confirm_button(label, key, danger=True):
    """Two-click confirm pattern: first click arms, second click fires."""
    armed_key = f"_armed_{key}"
    if st.session_state.get(armed_key):
        c1, c2 = st.columns(2)
        if c1.button(f"⚠️ Confirm {label}", key=f"_confirm_{key}",
                     type="primary" if danger else "secondary"):
            st.session_state[armed_key] = False
            return True
        if c2.button("Cancel", key=f"_cancel_{key}"):
            st.session_state[armed_key] = False
            st.rerun()
        return False
    else:
        btn_type = "primary" if danger else "secondary"
        if st.button(label, key=key, type=btn_type):
            st.session_state[armed_key] = True
            st.rerun()
        return False


# ================= BACKEND WARM-UP =================
try:
    requests.get(f"{API_URL}/stock-report/", headers=HEADERS, timeout=1)
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

        # ── Switch-account confirmation ──
        if st.session_state.get("_pending_login"):
            pending = st.session_state["_pending_login"]
            existing_user = None
            existing_token = None
            try:
                existing_token = cookie_controller.get("erp_session_token")
            except TypeError:
                pass
            if existing_token:
                try:
                    rv = requests.get(f"{API_URL}/validate-session/{existing_token}", timeout=5)
                    if rv.status_code == 200:
                        existing_user = rv.json()
                except Exception:
                    pass

            if existing_user:
                st.warning(
                    f"⚠️ **{existing_user['username']}** ({existing_user['role']}) "
                    f"is already signed in on this browser.\n\n"
                    f"Switch to **{pending['username']}** ({pending['role']})?")
                col_yes, col_no = st.columns(2)
                if col_yes.button("✅ Yes, switch", use_container_width=True, type="primary"):
                    try:
                        requests.delete(f"{API_URL}/logout/{existing_token}",
                                        headers=HEADERS, timeout=3)
                    except Exception:
                        pass
                    st.session_state.user          = {"username": pending["username"],
                                                       "role":     pending["role"]}
                    st.session_state.session_token = pending["token"]
                    st.session_state.force_logout  = False
                    if pending["stay"]:
                        safe_set_cookie("erp_session_token", pending["token"], max_age=2592000)
                    else:
                        safe_set_cookie("erp_session_token", pending["token"])
                    del st.session_state["_pending_login"]
                    st.query_params["page"] = "Home"
                    st.rerun()
                if col_no.button("❌ Cancel", use_container_width=True):
                    del st.session_state["_pending_login"]
                    st.rerun()
            else:
                # Cookie expired or invalid — just log in directly
                st.session_state.user          = {"username": pending["username"],
                                                   "role":     pending["role"]}
                st.session_state.session_token = pending["token"]
                st.session_state.force_logout  = False
                if pending["stay"]:
                    safe_set_cookie("erp_session_token", pending["token"], max_age=2592000)
                else:
                    safe_set_cookie("erp_session_token", pending["token"])
                del st.session_state["_pending_login"]
                st.query_params["page"] = "Home"
                st.rerun()

        else:
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
                        # Check if a different account's cookie already exists
                        existing_token = None
                        try:
                            existing_token = cookie_controller.get("erp_session_token")
                        except TypeError:
                            pass
                        if existing_token and existing_token != data["token"]:
                            # Another session exists — store pending and show warning
                            st.session_state["_pending_login"] = {
                                "username": data["username"],
                                "role":     data["role"],
                                "token":    data["token"],
                                "stay":     stay,
                            }
                            st.rerun()
                        else:
                            # No conflict — log in directly
                            st.session_state.user          = {"username": data["username"],
                                                               "role":     data["role"]}
                            st.session_state.session_token = data["token"]
                            st.session_state.force_logout  = False
                            # Always write cookie so nav link reloads don't log the user out.
                            # "Stay Logged In" controls persistence only: 30 days vs session cookie.
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

    ALL_PAGES = {
        "Home", "Stock View", "Feasibility Check",
        "Record Inward", "Record Issue", "View Transactions",
        "Reports",
        "BOM Entry", "BOM View",
        "Add Item", "Edit Item", "Delete Item", "View Items",
        "Add Supplier", "Edit Supplier", "Delete Supplier", "View Supplier",
        "Add Category", "Edit Category", "View Categories", "Delete Category",
        "Manage Users", "Audit Logs",
    }

    _qp = st.query_params.get("page", None)
    if _qp and _qp in ALL_PAGES and _qp != st.session_state.current_page:
        if st.session_state.current_page in ITEM_SELECTOR_PAGES:
            for k in ["iid", "iname"]:
                st.session_state.pop(k, None)
        st.session_state.current_page = _qp
    elif _qp == "Logout":
        logout()

    if st.session_state.current_page not in ALL_PAGES:
        st.session_state.current_page = "Home"

    cur = st.session_state.current_page
    st.query_params["page"] = cur

    NAV_ICONS = {
        "Home": "🏠", "Stock View": "📊", "Feasibility Check": "✅",
        "Transactions": "🔄", "Record Inward": "📥", "Record Issue": "📤",
        "View Transactions": "👁️", "Reports": "📈", "BOM": "📋",
        "BOM View": "👁️", "BOM Entry": "✏️", "Items": "📦",
        "Add Item": "➕", "Edit Item": "✏️", "Delete Item": "🗑️",
        "View Items": "👁️", "Suppliers": "🚚", "Add Supplier": "➕",
        "Edit Supplier": "✏️", "Delete Supplier": "🗑️",
        "View Supplier": "👁️", "Categories": "🏷️", "Add Category": "➕",
        "Edit Category": "✏️", "View Categories": "👁️",
        "Delete Category": "🗑️", "Manage Users": "👥", "Audit Logs": "🔍",
        "Logout": "🚪",
    }

    _group_pages = {
        "Transactions": {"Record Inward", "Record Issue", "View Transactions"},
        "BOM":          {"BOM Entry", "BOM View"},
        "Items":        {"Add Item", "Edit Item", "Delete Item", "View Items"},
        "Suppliers":    {"Add Supplier", "Edit Supplier", "Delete Supplier",
                         "View Supplier"},
        "Categories":   {"Add Category", "Edit Category", "View Categories",
                         "Delete Category"},
    }

    def _link(label, page_key, sub=False):
        active = cur == page_key
        icon   = NAV_ICONS.get(label, "•")
        cls    = "nav-sub" if sub else "nav-top"
        if active:
            cls += " nav-active"
        href = f"?page={page_key.replace(' ', '+')}"
        return (f'<a href="{href}" class="{cls}" target="_self">'
                f'<span class="nav-icon">{icon}</span>{label}</a>')

    def _group(grp_label, children):
        icon       = NAV_ICONS.get(grp_label, "•")
        grp_active = cur in _group_pages.get(grp_label, set())
        cb_id      = f"nav-cb-{grp_label.lower().replace(' ', '-')}"
        lbl_cls    = "nav-group-label" + (" grp-active" if grp_active else "")
        checked    = "checked" if grp_active else ""
        child_html = "".join(_link(cl, cp, sub=True) for cl, cp in children)
        return (f'<div class="nav-group-wrap">'
                f'  <input type="checkbox" class="nav-toggle-cb" id="{cb_id}" {checked}>'
                f'  <label for="{cb_id}" class="{lbl_cls}">'
                f'    <span class="nav-icon">{icon}</span>{grp_label}'
                f'    <span class="nav-arrow">▸</span>'
                f'  </label>'
                f'  <div class="nav-dropdown">{child_html}</div>'
                f'</div>')

    def _build_nav():
        parts = []
        parts.append(_link("Home",             "Home"))
        parts.append(_link("Stock View",        "Stock View"))
        parts.append(_link("Feasibility Check", "Feasibility Check"))

        if role in ("Admin", "Manager"):
            parts.append(_group("Transactions", [
                ("Record Inward",     "Record Inward"),
                ("Record Issue",      "Record Issue"),
                ("View Transactions", "View Transactions"),
            ]))
            parts.append(_link("Reports", "Reports"))
            parts.append(_group("BOM", [
                ("BOM View",  "BOM View"),
                ("BOM Entry", "BOM Entry"),
            ]))

        if role == "Admin":
            parts.append(_group("Items", [
                ("Add Item",    "Add Item"),
                ("Edit Item",   "Edit Item"),
                ("Delete Item", "Delete Item"),
                ("View Items",  "View Items"),
            ]))
            parts.append(_group("Suppliers", [
                ("Add Supplier",    "Add Supplier"),
                ("Edit Supplier",   "Edit Supplier"),
                ("Delete Supplier", "Delete Supplier"),
                ("View Supplier",   "View Supplier"),
            ]))
            parts.append(_group("Categories", [
                ("Add Category",    "Add Category"),
                ("Edit Category",   "Edit Category"),
                ("View Categories", "View Categories"),
                ("Delete Category", "Delete Category"),
            ]))
            parts.append(_link("Manage Users", "Manage Users"))
            parts.append(_link("Audit Logs",   "Audit Logs"))

        if role == "Manager":
            parts.append(_link("View Categories", "View Categories"))

        return "\n".join(parts)

    # ================= SIDEBAR =================
    with st.sidebar:
        st.markdown(f"""
        <div class="profile-box">
            <img src="https://ui-avatars.com/api/?name={uname}&background=4477FF&color=fff" class="profile-pic">
            <div>
                <div class="profile-name">{uname}</div>
                <div class="profile-role">{role}</div>
            </div>
        </div>
        <nav class="erp-nav">
            {_build_nav()}
            <div class="nav-divider"></div>
            <a href="?page=Logout" class="nav-top nav-logout" target="_self">
                <span class="nav-icon">🚪</span>Logout
            </a>
        </nav>
        """, unsafe_allow_html=True)

    page = st.session_state.current_page

    ADMIN_ONLY_PAGES = {
        "Add Item", "Edit Item", "Delete Item", "View Items",
        "Add Supplier", "Edit Supplier", "Delete Supplier", "View Supplier",
        "Add Category", "Edit Category", "View Categories", "Delete Category",
        "Manage Users", "Audit Logs",
    }
    MANAGER_PLUS_PAGES = {
        "Record Inward", "Record Issue", "View Transactions",
        "Reports", "BOM Entry", "BOM View",
    }

    def access_denied():
        st.error("🚫 Access Denied — you do not have permission to view this page.")
        st.stop()

    if page in ADMIN_ONLY_PAGES and role != "Admin":
        access_denied()
    elif page in MANAGER_PLUS_PAGES and role not in ("Admin", "Manager"):
        access_denied()

    # ============================================================
    # ── HOME ────────────────────────────────────────────────────
    # ============================================================
    if page == "Home":
        st.title(f"🏭 Welcome back, {uname}")
        st.caption(f"System Online · Role: {role}")

        stock_data = fetch("stock-report/")

        # ── KPI Cards ──
        if stock_data:
            df_stock     = pd.DataFrame(stock_data)
            total_items  = len(df_stock)
            raw_items    = len(df_stock[df_stock["item_type"] == "RAW"])
            final_items  = len(df_stock[df_stock["item_type"] == "FINAL"])
            reorder_cnt  = int(df_stock["needs_reorder"].sum())
            # inventory value: current_stock × rate (only where rate is known)
            df_stock["value"] = df_stock.apply(
                lambda r: r["current_stock"] * r["rate"]
                if r.get("rate") and r["rate"] else 0, axis=1)
            total_value = df_stock["value"].sum()

            k1, k2, k3, k4, k5 = st.columns(5)
            for col, val, lbl in [
                (k1, total_items,   "Total Items"),
                (k2, raw_items,     "Raw Materials"),
                (k3, final_items,   "Finished Goods"),
                (k4, reorder_cnt,   "Need Reorder ⚠️"),
                (k5, f"₹{total_value:,.0f}", "Inventory Value"),
            ]:
                col.markdown(
                    f'<div class="kpi-card">'
                    f'<div class="kpi-value" style="font-size:1.3rem">{val}</div>'
                    f'<div class="kpi-label" style="font-size:0.7rem">{lbl}</div>'
                    f'</div>', unsafe_allow_html=True)

            st.divider()

            # ── Reorder Alert ──
            reorder_items = [r for r in stock_data if r["needs_reorder"]
                             and r["item_type"] == "RAW"]
            dismiss_key = f"alert_dismissed_{date.today()}"   # resets daily
            if reorder_items and not st.session_state.get(dismiss_key):
                with st.container(border=True):
                    st.error(f"⚠️ **Reorder Alert — {len(reorder_items)} item(s) need attention**")
                    for item in reorder_items:
                        st.write(
                            f"🔴 **{item['item_name']}** — "
                            f"Stock: `{int(item['current_stock'])}` | "
                            f"Reorder Point: `{item['reorder_point']}`"
                        )
                    if st.button("✅ Dismiss for today"):
                        st.session_state[dismiss_key] = True
                        st.rerun()
            elif not reorder_items:
                st.success("✅ All items are sufficiently stocked.")

            st.divider()
            # ── Quick Actions ──
            st.subheader("Quick Actions")
            qa1, qa2, qa3, qa4 = st.columns(4)
            if role in ("Admin", "Manager"):
                if qa1.button("📥 Record Inward", use_container_width=True):
                    st.session_state.current_page = "Record Inward"
                    st.query_params["page"] = "Record Inward"
                    st.rerun()
                if qa2.button("📤 Record Issue", use_container_width=True):
                    st.session_state.current_page = "Record Issue"
                    st.query_params["page"] = "Record Issue"
                    st.rerun()
            if qa3.button("📊 Stock View", use_container_width=True):
                st.session_state.current_page = "Stock View"
                st.query_params["page"] = "Stock View"
                st.rerun()
            if qa4.button("✅ Feasibility Check", use_container_width=True):
                st.session_state.current_page = "Feasibility Check"
                st.query_params["page"] = "Feasibility Check"
                st.rerun()
        else:
            st.warning("Could not load stock data. Is the backend running?")

    # ============================================================
    # ── STOCK VIEW ──────────────────────────────────────────────
    # ============================================================
    elif page == "Stock View":
        st.title("📊 Inventory Status")
        data = fetch("stock-report/")
        if not data:
            st.error("No stock data available.")
        else:
            df = pd.DataFrame(data)

            tab_raw, tab_final = st.tabs(["🔩 Raw Materials", "🏭 Finished Goods"])

            def _render_stock_tab(df_tab, is_raw):
                if df_tab.empty:
                    st.info("No items in this category.")
                    return

                if is_raw:
                    df_tab = df_tab.copy()
                    df_tab["Status"] = df_tab.apply(
                        lambda r: "🔴 REORDER" if r["needs_reorder"] else "🟢 OK",
                        axis=1)
                    # Inventory value column
                    df_tab["value"] = df_tab.apply(
                        lambda r: round(r["current_stock"] * r["rate"], 2)
                        if r.get("rate") else None, axis=1)
                    display_cols = [c for c in [
                        "item_id", "item_name", "category", "supplier",
                        "current_stock", "security_stock", "reorder_point",
                        "value", "Status"
                    ] if c in df_tab.columns]
                    df_tab = df_tab[display_cols].sort_values("Status", ascending=True)
                    col_cfg = {
                        "item_id":        st.column_config.NumberColumn("Item ID",         width="small"),
                        "item_name":      st.column_config.TextColumn("Item Name",          width="medium"),
                        "category":       st.column_config.TextColumn("Category",           width="medium"),
                        "supplier":       st.column_config.TextColumn("Supplier",           width="medium"),
                        "current_stock":  st.column_config.NumberColumn("Current Stock",    width="small"),
                        "security_stock": st.column_config.NumberColumn("Security Stock",   width="small"),
                        "reorder_point":  st.column_config.NumberColumn("Reorder Point",    width="small"),
                        "value":          st.column_config.NumberColumn("Value (₹)",        width="small", format="₹%.2f"),
                        "Status":         st.column_config.TextColumn("Status",             width="small"),
                    }
                else:
                    display_cols = [c for c in [
                        "item_id", "item_name", "current_stock"
                    ] if c in df_tab.columns]
                    df_tab   = df_tab[display_cols]
                    col_cfg  = {
                        "item_id":       st.column_config.NumberColumn("Item ID",      width="small"),
                        "item_name":     st.column_config.TextColumn("Item Name",      width="medium"),
                        "current_stock": st.column_config.NumberColumn("Current Stock", width="small"),
                    }

                search = st.text_input("🔍 Search", key=f"sv_search_{'raw' if is_raw else 'final'}")
                df_tab = filter_df(df_tab, search,
                                   ["item_id", "item_name", "category", "supplier"])
                pg_key = f"sv_{'raw' if is_raw else 'final'}"
                sliced, sz, off, off_key = paginate_df(df_tab, pg_key)
                st.data_editor(sliced, disabled=True, hide_index=True,
                               use_container_width=True, column_config=col_cfg)
                render_page_controls(pg_key, sz, off, off_key, len(df_tab))

                # Export
                csv = df_tab.to_csv(index=False).encode("utf-8")
                st.download_button("⬇️ Export CSV", csv,
                                   file_name=f"stock_{'raw' if is_raw else 'final'}_{date.today()}.csv",
                                   mime="text/csv")

            with tab_raw:
                _render_stock_tab(df[df["item_type"] == "RAW"], is_raw=True)
            with tab_final:
                _render_stock_tab(df[df["item_type"] == "FINAL"], is_raw=False)

    # ============================================================
    # ── RECORD INWARD ───────────────────────────────────────────
    # ============================================================
    elif page == "Record Inward":
        st.title("🚚 Inward Entry (Multi-Item Invoice)")
        items = fetch("items/")
        if not items:
            st.warning("No items found. Please add items first.")
        else:
            # Only RAW items can have inwards
            raw_items    = [i for i in items if str(i.get("item_type","")).upper() == "RAW"]
            item_options = [f"{i['id']} | {i['item_name']}" for i in raw_items]
            rate_map     = {i["id"]: i.get("rate") for i in raw_items}

            st.subheader("Invoice Details")
            c1, c2, c3 = st.columns(3)
            inv = c1.text_input("Invoice #")
            d1  = c2.date_input("Order Date",    max_value=date.today())
            d2  = c3.date_input("Received Date", value=date.today(),
                                max_value=date.today())
            st.divider()

            if "inward_rows" not in st.session_state:
                st.session_state.inward_rows = 1

            st.subheader("Items Received")
            row_data = []
            rows_to_remove = []
            for i in range(st.session_state.inward_rows):
                rc1, rc2, rc3, rc4 = st.columns([2.5, 1, 1, 0.3], vertical_alignment="bottom")
                sel_item = rc1.selectbox(f"Item {i+1}", item_options, index=None,
                                         placeholder="Select an item", key=f"item_{i}")
                # Default rate to last known rate for this item
                default_rate = 0.0
                if sel_item:
                    iid = int(sel_item.split(" | ")[0])
                    default_rate = float(rate_map.get(iid) or 0.0)
                rate = rc2.number_input("Rate", min_value=0.0, step=0.1,
                                        value=default_rate, key=f"rate_{i}")
                qty  = rc3.number_input("Quantity", min_value=1, step=1,
                                        key=f"qty_{i}")
                if rc4.button("🗑️", key=f"rm_row_{i}",
                              help="Remove row") and st.session_state.inward_rows > 1:
                    rows_to_remove.append(i)
                row_data.append({"selected_item": sel_item, "rate": rate, "qty": qty})

            if rows_to_remove:
                st.session_state.inward_rows = max(1,
                    st.session_state.inward_rows - len(rows_to_remove))
                st.rerun()

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
                    errors        = []
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
                                    "received_date":  str(d2),
                                },
                                headers=auth_headers(), timeout=5
                            )
                            if res.status_code == 200:
                                success_count += 1
                            else:
                                errors.append(
                                    f"Row {idx+1} failed: "
                                    f"{res.json().get('detail', 'Backend Error')}")
                        except Exception:
                            errors.append(f"Row {idx+1} failed: Could not connect.")
                    for e in errors:
                        st.error(e)
                    if success_count > 0:
                        msg.success(
                            f"✅ Recorded {success_count} item(s) under Invoice {inv}!")
                        time.sleep(1)
                        st.session_state.inward_rows = 1
                        st.rerun()

    # ============================================================
    # ── RECORD ISSUE ────────────────────────────────────────────
    # ============================================================
    elif page == "Record Issue":
        st.title("📦 Issue Entry")
        items = fetch("items/")
        if not items:
            st.warning("No items found. Please add items first.")
        else:
            # FIX: show only RAW items for issue
            # FIX: multi-row issue support
            # FIX: show current stock next to selector

            raw_items    = [i for i in items
                            if str(i.get("item_type","")).upper() == "RAW"]
            stock_data   = fetch("stock-report/")
            stock_map    = {r["item_id"]: float(r["current_stock"])
                            for r in stock_data} if stock_data else {}
            item_options = [
                f"{i['id']} | {i['item_name']} (stock: {int(stock_map.get(i['id'],0))})"
                for i in raw_items
            ]
            id_from_opt  = {opt: i["id"]
                            for opt, i in zip(item_options, raw_items)}

            st.caption("Only RAW material items are shown. Stock balance shown per item.")

            if "issue_rows" not in st.session_state:
                st.session_state.issue_rows = 1

            st.subheader("Items to Issue")
            issue_data = []
            for i in range(st.session_state.issue_rows):
                rc1, rc2, rc3 = st.columns([3, 1, 0.3], vertical_alignment="bottom")
                sel = rc1.selectbox(f"Item {i+1}", item_options, index=None,
                                    placeholder="Select item", key=f"iss_item_{i}")
                qty = rc2.number_input("Qty", min_value=1, step=1,
                                       key=f"iss_qty_{i}")
                if rc3.button("🗑️", key=f"iss_rm_{i}",
                              help="Remove") and st.session_state.issue_rows > 1:
                    st.session_state.issue_rows -= 1
                    st.rerun()
                issue_data.append({"sel": sel, "qty": qty})

            if st.button("➕ Add Another Item"):
                st.session_state.issue_rows += 1
                st.rerun()

            st.divider()
            c1, c2, c3 = st.columns(3)
            issued_to = c1.text_input("Issued To")
            issue_dt  = c2.date_input("Issue Date", value=date.today(),
                                      max_value=date.today())
            purpose   = c3.text_input("Purpose / Order # (Optional)")

            msg = st.empty()
            if st.button("💾 Post Issue", type="primary"):
                if not issued_to.strip():
                    msg.error("'Issued To' is required.")
                else:
                    success_count = 0
                    errors        = []
                    for idx, row in enumerate(issue_data):
                        if row["sel"] is None:
                            errors.append(f"Row {idx+1}: No item selected.")
                            continue
                        item_id = id_from_opt[row["sel"]]
                        avail   = stock_map.get(item_id, 0)
                        if row["qty"] > avail:
                            errors.append(
                                f"Row {idx+1}: Insufficient stock "
                                f"(available: {int(avail)}).")
                            continue
                        try:
                            res = requests.post(
                                f"{API_URL}/issues/",
                                json={
                                    "item_id":    item_id,
                                    "quantity":   row["qty"],
                                    "issue_date": str(issue_dt),
                                    "issued_to":  issued_to.strip(),
                                    "purpose":    purpose.strip() or None,
                                },
                                headers=auth_headers(), timeout=5
                            )
                            if res.status_code == 200:
                                success_count += 1
                            else:
                                errors.append(
                                    f"Row {idx+1}: "
                                    f"{res.json().get('detail', 'Error')}")
                        except Exception:
                            errors.append(f"Row {idx+1}: Could not connect.")
                    for e in errors:
                        st.error(e)
                    if success_count > 0:
                        msg.success(f"✅ {success_count} issue(s) recorded.")
                        time.sleep(1)
                        st.session_state.issue_rows = 1
                        st.rerun()

    # ============================================================
    # ── VIEW TRANSACTIONS ───────────────────────────────────────
    # ============================================================
    elif page == "View Transactions":
        st.title("🔄 View Transactions")

        items_data = fetch("items/")
        items_map  = {i["id"]: i["item_name"] for i in items_data} if items_data else {}

        tab_in, tab_out = st.tabs(["📥 Inwards", "📤 Issues"])

        # ── INWARDS ──
        with tab_in:
            st.subheader("Inward Transactions")

            fc1, fc2, fc3, fc4 = st.columns(4)
            s_item    = fc1.text_input("🔍 Item name / ID", key="inv_item")
            s_invoice = fc2.text_input("🔍 Invoice #",      key="inv_invoice")
            s_from    = fc3.date_input("From", value=None,  key="inv_from")
            s_to      = fc4.date_input("To",   value=None,  key="inv_to")

            lim_col, off_col = st.columns([1, 4])
            pg_limit  = lim_col.selectbox("Per page", [50, 100, 200], key="inv_limit")
            if "inv_offset" not in st.session_state:
                st.session_state.inv_offset = 0

            params = {"limit": pg_limit, "offset": st.session_state.inv_offset}
            if s_invoice:
                params["invoice"] = s_invoice
            if s_from:
                params["from_date"] = str(s_from)
            if s_to:
                params["to_date"] = str(s_to)

            result = fetch("inwards/", params=params)
            if isinstance(result, dict):
                total    = result.get("total", 0)
                rows_raw = result.get("rows", [])
            else:
                total, rows_raw = 0, []

            if rows_raw:
                df = pd.DataFrame(rows_raw)
                df.insert(2, "item_name",
                          df["item_id"].map(items_map).fillna(""))
                df = filter_df(df, s_item, ["item_id", "item_name"])

                st.caption(f"{total} total record(s) · showing page")
                st.data_editor(df, disabled=True, hide_index=True,
                               use_container_width=True,
                               column_config={
                                   "transaction_id": st.column_config.NumberColumn("Txn ID",       width="small"),
                                   "item_id":        st.column_config.NumberColumn("Item ID",       width="small"),
                                   "item_name":      st.column_config.TextColumn("Item Name",       width="medium"),
                                   "invoice_number": st.column_config.TextColumn("Invoice",         width="medium"),
                                   "quantity":       st.column_config.NumberColumn("Qty",           width="small"),
                                   "rate":           st.column_config.NumberColumn("Rate",          width="small"),
                                   "order_date":     st.column_config.TextColumn("Order Date",      width="small"),
                                   "received_date":  st.column_config.TextColumn("Received Date",   width="small"),
                               })

                # Pagination
                p1, p2, p3 = st.columns([1, 2, 1])
                off = st.session_state.inv_offset
                with p1:
                    if st.button("◀ Prev", key="inv_prev", disabled=(off == 0)):
                        st.session_state.inv_offset = max(0, off - pg_limit)
                        st.rerun()
                with p2:
                    pg  = off // pg_limit + 1
                    tpg = max(1, -(-total // pg_limit))
                    st.markdown(f"<div style='text-align:center;padding-top:8px'>Page {pg} of {tpg}</div>",
                                unsafe_allow_html=True)
                with p3:
                    if st.button("Next ▶", key="inv_next",
                                 disabled=(off + pg_limit >= total)):
                        st.session_state.inv_offset = off + pg_limit
                        st.rerun()

                # Void (Admin only)
                if role == "Admin":
                    st.divider()
                    with st.expander("🗑️ Void an Inward Transaction (Admin)"):
                        void_id = st.number_input("Transaction ID to void",
                                                  min_value=1, step=1,
                                                  key="void_inward_id")
                        if confirm_button("Void Inward", "void_inward_btn"):
                            res = requests.delete(
                                f"{API_URL}/inwards/{int(void_id)}",
                                headers=auth_headers(), timeout=5)
                            if res.status_code == 200:
                                st.success("Transaction voided.")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error(res.json().get("detail", "Failed"))
            else:
                st.info("No inward transactions found.")

        # ── ISSUES ──
        with tab_out:
            st.subheader("Issue Transactions")

            fc1, fc2, fc3, fc4 = st.columns(4)
            s_item2   = fc1.text_input("🔍 Item name / ID", key="iss_item_f")
            s_issto   = fc2.text_input("🔍 Issued To",       key="iss_to_f")
            s_from2   = fc3.date_input("From", value=None,   key="iss_from")
            s_to2     = fc4.date_input("To",   value=None,   key="iss_to")

            lim_col2, _ = st.columns([1, 4])
            pg_limit2   = lim_col2.selectbox("Per page", [50, 100, 200],
                                              key="iss_limit")
            if "iss_offset" not in st.session_state:
                st.session_state.iss_offset = 0

            params2 = {"limit": pg_limit2, "offset": st.session_state.iss_offset}
            if s_issto:
                params2["issued_to"] = s_issto
            if s_from2:
                params2["from_date"] = str(s_from2)
            if s_to2:
                params2["to_date"] = str(s_to2)

            result2 = fetch("issues/", params=params2)
            if isinstance(result2, dict):
                total2    = result2.get("total", 0)
                rows_raw2 = result2.get("rows", [])
            else:
                total2, rows_raw2 = 0, []

            if rows_raw2:
                df2 = pd.DataFrame(rows_raw2)
                df2.insert(2, "item_name",
                           df2["item_id"].map(items_map).fillna(""))
                df2 = filter_df(df2, s_item2, ["item_id", "item_name"])

                st.caption(f"{total2} total record(s)")
                st.data_editor(df2, disabled=True, hide_index=True,
                               use_container_width=True,
                               column_config={
                                   "transaction_id": st.column_config.NumberColumn("Txn ID",     width="small"),
                                   "item_id":        st.column_config.NumberColumn("Item ID",     width="small"),
                                   "item_name":      st.column_config.TextColumn("Item Name",     width="medium"),
                                   "quantity":       st.column_config.NumberColumn("Qty",         width="small"),
                                   "issue_date":     st.column_config.TextColumn("Issue Date",    width="small"),
                                   "issued_to":      st.column_config.TextColumn("Issued To",     width="small"),
                                   "purpose":        st.column_config.TextColumn("Purpose",       width="medium"),
                               })

                p1, p2, p3 = st.columns([1, 2, 1])
                off2 = st.session_state.iss_offset
                with p1:
                    if st.button("◀ Prev", key="iss_prev", disabled=(off2 == 0)):
                        st.session_state.iss_offset = max(0, off2 - pg_limit2)
                        st.rerun()
                with p2:
                    pg2  = off2 // pg_limit2 + 1
                    tpg2 = max(1, -(-total2 // pg_limit2))
                    st.markdown(f"<div style='text-align:center;padding-top:8px'>Page {pg2} of {tpg2}</div>",
                                unsafe_allow_html=True)
                with p3:
                    if st.button("Next ▶", key="iss_next",
                                 disabled=(off2 + pg_limit2 >= total2)):
                        st.session_state.iss_offset = off2 + pg_limit2
                        st.rerun()

                if role == "Admin":
                    st.divider()
                    with st.expander("🗑️ Void an Issue Transaction (Admin)"):
                        void_id2 = st.number_input("Transaction ID to void",
                                                   min_value=1, step=1,
                                                   key="void_issue_id")
                        if confirm_button("Void Issue", "void_issue_btn"):
                            res2 = requests.delete(
                                f"{API_URL}/issues/{int(void_id2)}",
                                headers=auth_headers(), timeout=5)
                            if res2.status_code == 200:
                                st.success("Transaction voided.")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error(res2.json().get("detail", "Failed"))
            else:
                st.info("No issue transactions found.")

    # ============================================================
    # ── MANAGE USERS ────────────────────────────────────────────
    # ============================================================
    elif page == "Manage Users":
        st.title("👥 User Management")
        t1, t2, t3 = st.tabs(["Users", "Create", "Active Sessions"])

        with t1:
            users = fetch("users/")
            if users:
                for u in users:
                    c1, c2, c3, c4 = st.columns([2, 2, 1.5, 1])
                    c1.write(u["username"])
                    c2.write(u["role"])
                    is_self = u["username"] == uname
                    # Edit role inline — disabled for own account
                    if is_self:
                        c3.caption(u["role"])
                    else:
                        new_role = c3.selectbox(
                            "Role", ["Admin", "Manager", "Viewer"],
                            index=["Admin", "Manager", "Viewer"].index(u["role"]),
                            key=f"role_{u['id']}",
                            label_visibility="collapsed"
                        )
                        if new_role != u["role"]:
                            if c3.button("Save", key=f"save_role_{u['id']}"):
                                requests.put(
                                    f"{API_URL}/users/{u['id']}",
                                    json={"role": new_role},
                                    headers=auth_headers()
                                )
                                st.rerun()
                    if not is_self:
                        if c4.button("Delete", key=f"d_{u['id']}"):
                            requests.delete(f"{API_URL}/users/{u['id']}",
                                            headers=auth_headers())
                            st.rerun()
                    else:
                        c4.caption("(you)")

        with t2:
            with st.form("create_user"):
                nu  = st.text_input("Username")
                np  = st.text_input("Password", type="password")
                nr  = st.selectbox("Role", ["Select", "Admin", "Manager", "Viewer"])
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
                            json={"username": nu.strip(), "password": np,
                                  "role": nr},
                            headers=auth_headers()
                        )
                        if res.status_code == 200:
                            msg.success("User Created")
                            time.sleep(1)
                            st.rerun()
                        else:
                            msg.error(res.json().get("detail", "Failed to create user"))

            st.divider()
            st.subheader("Reset User Password")
            with st.form("reset_pw"):
                users_list = fetch("users/")
                if users_list:
                    target_user = st.selectbox(
                        "User",
                        [u["username"] for u in users_list if u["username"] != uname]
                    )
                    new_pw = st.text_input("New Password", type="password")
                    if st.form_submit_button("Reset Password"):
                        uid = next(u["id"] for u in users_list
                                   if u["username"] == target_user)
                        res = requests.put(
                            f"{API_URL}/users/{uid}",
                            json={"new_password": new_pw},
                            headers=auth_headers()
                        )
                        if res.status_code == 200:
                            st.success(f"Password reset for {target_user}")
                        else:
                            st.error("Failed to reset password")

        with t3:
            st.subheader("Active Sessions")
            try:
                r = requests.get(f"{API_URL}/users/sessions/",
                                 headers=auth_headers(), timeout=5)
                sessions = r.json() if r.status_code == 200 else []
            except Exception:
                sessions = []

            if sessions:
                current_token = st.session_state.get("session_token", "")
                df_s = pd.DataFrame(sessions)
                df_s = df_s[["token", "username", "created_at",
                             "expires_at", "token_preview"]].copy()
                for col in ["created_at", "expires_at"]:
                    df_s[col] = pd.to_datetime(df_s[col], errors="coerce") \
                                  .dt.tz_localize("UTC") \
                                  .dt.tz_convert("Asia/Kolkata") \
                                  .dt.strftime("%d %b %Y %H:%M")
                df_s.insert(0, "Current",
                            df_s["token"].apply(
                                lambda t: "⭐ You" if t == current_token else ""))
                sliced_s, sz_s, off_s, off_key_s = paginate_df(df_s, "sess", (10, 25, 50))
                st.dataframe(sliced_s[["Current", "username", "created_at",
                                       "expires_at", "token_preview"]],
                             hide_index=True, use_container_width=True)
                render_page_controls("sess", sz_s, off_s, off_key_s, len(df_s))
                st.divider()
                other_sessions = [s for s in sessions if s["token"] != current_token]
                revoke_token = st.selectbox(
                    "Revoke Session",
                    ["Select…"] + [f"{s['username']} — {s['token_preview']}"
                                   for s in other_sessions]
                )
                if revoke_token != "Select…":
                    idx = [f"{s['username']} — {s['token_preview']}"
                           for s in other_sessions].index(revoke_token)
                    token_to_revoke = other_sessions[idx]["token"]
                    if confirm_button("Confirm", "revoke_sess", danger=True):
                        requests.delete(
                            f"{API_URL}/users/sessions/{token_to_revoke}",
                            headers=auth_headers()
                        )
                        st.success("Session revoked.")
                        time.sleep(1)
                        st.rerun()

                st.divider()
                if other_sessions:
                    if confirm_button("🔴 Log Out All Other Sessions", "logout_all_btn", danger=True):
                        for s in other_sessions:
                            try:
                                requests.delete(
                                    f"{API_URL}/users/sessions/{s['token']}",
                                    headers=auth_headers(), timeout=5
                                )
                            except Exception:
                                pass
                        st.success("All other sessions have been logged out.")
                        time.sleep(1)
                        st.rerun()
                else:
                    st.caption("No other active sessions.")
            else:
                st.info("No active sessions.")

    # ============================================================
    # ── ADD ITEM ────────────────────────────────────────────────
    # ============================================================
    elif page == "Add Item":
        st.title("➕ Add Item")
        category = fetch("category/")
        supps    = fetch("suppliers/")
        s_map    = {s["category"]: s["id"] for s in category}
        v_map    = {v["name"]: v["id"] for v in supps}

        c1, c2 = st.columns(2)
        name   = c1.text_input("Item Name")
        cat    = c2.selectbox("Item Type", ["RAW", "FINAL"], key="add_item_type")
        is_raw = (cat == "RAW")

        with st.form("item_form"):
            fc1, fc2 = st.columns(2)
            if is_raw:
                if not s_map or not v_map:
                    st.warning("Please add categories and suppliers first.")
                    st.form_submit_button("Save", disabled=True)
                else:
                    sp      = fc1.selectbox("Category", ["Select"] + list(s_map))
                    su      = fc2.selectbox("Supplier", ["Select"] + list(v_map))
                    lt      = fc1.number_input("Lead Time (Days)", min_value=0)
                    rate    = fc2.number_input("Rate",             min_value=0.0)
                    ss      = fc1.number_input("Security Stock",   min_value=0)
                    rack    = fc2.text_input("Rack (Optional)")
                    bin_    = fc1.text_input("Bin (Optional)")
                    part_id = fc2.text_input("Part ID (Optional)")
                    msg     = st.empty()
                    if st.form_submit_button("Save"):
                        if not name.strip():
                            msg.error("Item Name is required.")
                        elif sp == "Select":
                            msg.error("Please select a category.")
                        elif su == "Select":
                            msg.error("Please select a supplier.")
                        else:
                            res = requests.post(
                                f"{API_URL}/items/",
                                json={
                                    "item_name":      name.strip(),
                                    "item_type":      "RAW",
                                    "category_id":    s_map[sp],
                                    "supplier_id":    v_map[su],
                                    "lead_time":      lt,
                                    "rate":           rate,
                                    "security_stock": ss,
                                    "rack":           rack.strip(),
                                    "bin":            bin_.strip(),
                                    "part_id":        part_id.strip() or None,
                                },
                                headers=auth_headers(), timeout=5
                            )
                            if res.status_code == 200:
                                msg.success("Item Added")
                                time.sleep(1)
                                st.rerun()
                            else:
                                msg.error(res.json().get("detail", "Failed"))
            else:
                msg = st.empty()
                if st.form_submit_button("Save"):
                    if not name.strip():
                        msg.error("Item Name is required.")
                    else:
                        res = requests.post(
                            f"{API_URL}/items/",
                            json={"item_name": name.strip(), "item_type": "FINAL"},
                            headers=auth_headers(), timeout=5
                        )
                        if res.status_code == 200:
                            msg.success("Item Added")
                            time.sleep(1)
                            st.rerun()
                        else:
                            msg.error(res.json().get("detail", "Failed"))

    # ============================================================
    # ── EDIT ITEM ───────────────────────────────────────────────
    # ============================================================
    elif page == "Edit Item":
        st.title("✏️ Edit Item")
        items    = fetch("items/")
        category = fetch("category/")
        supps    = fetch("suppliers/")

        if not items:
            st.error("No items found.")
        else:
            s_map    = {s["category"]: s["id"] for s in category}
            v_map    = {v["name"]: v["id"] for v in supps}
            opt      = [f"{i['id']} | {i['item_name']}" for i in items]
            selected = st.selectbox("Select Item to Edit", ["Select…"] + opt)

            if selected != "Select…":
                iid  = int(selected.split(" | ")[0])
                item = next(i for i in items if i["id"] == iid)

                st.divider()
                st.subheader(f"Editing: {item['item_name']}  `({item['item_type']})`")

                with st.form("edit_item_form"):
                    fc1, fc2 = st.columns(2)
                    new_name = fc1.text_input("Item Name",
                                              value=item["item_name"])
                    is_raw   = str(item.get("item_type","")).upper() == "RAW"

                    if is_raw:
                        cur_cat = item.get("category") or "Select"
                        cur_sup = item.get("supplier") or "Select"
                        sp  = fc2.selectbox(
                            "Category",
                            ["Select"] + list(s_map),
                            index=(["Select"] + list(s_map)).index(cur_cat)
                            if cur_cat in s_map else 0
                        )
                        su  = fc1.selectbox(
                            "Supplier",
                            ["Select"] + list(v_map),
                            index=(["Select"] + list(v_map)).index(cur_sup)
                            if cur_sup in v_map else 0
                        )
                        lt   = fc2.number_input("Lead Time (Days)",
                                                min_value=0,
                                                value=item.get("lead_time") or 0)
                        rate = fc1.number_input("Rate",
                                                min_value=0.0,
                                                value=float(item.get("rate") or 0))
                        ss   = fc2.number_input("Security Stock",
                                                min_value=0,
                                                value=item.get("security_stock") or 0)
                        rack = fc1.text_input("Rack",
                                              value=item.get("rack") or "")
                        bin_ = fc2.text_input("Bin",
                                              value=item.get("bin") or "")
                        part_id = fc1.text_input("Part ID",
                                                  value=item.get("part_id") or "")

                    msg = st.empty()
                    if st.form_submit_button("💾 Save Changes"):
                        payload = {"item_name": new_name.strip()}
                        if is_raw:
                            if sp != "Select":
                                payload["category_id"] = s_map[sp]
                            if su != "Select":
                                payload["supplier_id"] = v_map[su]
                            payload.update({
                                "lead_time":      lt,
                                "rate":           rate,
                                "security_stock": ss,
                                "rack":           rack.strip(),
                                "bin":            bin_.strip(),
                                "part_id":        part_id.strip() or None,
                            })
                        res = requests.put(
                            f"{API_URL}/items/{iid}",
                            json=payload,
                            headers=auth_headers(), timeout=5
                        )
                        if res.status_code == 200:
                            msg.success("Item Updated")
                            time.sleep(1)
                            st.rerun()
                        else:
                            msg.error(res.json().get("detail", "Failed"))

    # ============================================================
    # ── DELETE ITEM ─────────────────────────────────────────────
    # ============================================================
    elif page == "Delete Item":
        st.title("🗑️ Delete Item")
        items = fetch("items/")
        if not items:
            st.error("No items found.")
        else:
            target = item_selector()
            if target is not None:
                item_name = next(
                    (i["item_name"] for i in items if i["id"] == target), "?"
                )
                st.warning(f"You are about to delete **{item_name}** (ID: {target}).")
                msg = st.empty()
                if confirm_button(f"Delete {item_name}", "del_item_btn"):
                    res = requests.delete(f"{API_URL}/items/{target}",
                                          headers=auth_headers())
                    if res.status_code == 200:
                        msg.success("Item Deleted")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error(res.json().get("detail", "Failed"))

    # ============================================================
    # ── VIEW ITEMS ──────────────────────────────────────────────
    # ============================================================
    elif page == "View Items":
        st.title("📦 Item List")
        data = fetch("items/")
        if data:
            df = pd.DataFrame(data)
            display_cols = [c for c in [
                "id", "item_name", "item_type", "category", "supplier",
                "part_id", "lead_time", "rate", "security_stock", "rack", "bin"
            ] if c in df.columns]
            df     = df[display_cols]
            search = st.text_input("🔍 Search", placeholder="Search…",
                                   key="vi_search")
            df     = filter_df(df, search,
                               ["id", "item_name", "item_type", "category",
                                "supplier"])
            sliced, sz, off, off_key = paginate_df(df, "vi")
            st.data_editor(sliced, disabled=True, hide_index=True,
                           use_container_width=True,
                           column_config={
                               "id":             st.column_config.NumberColumn("ID",               width="small"),
                               "item_name":      st.column_config.TextColumn("Item Name",           width="medium"),
                               "item_type":      st.column_config.TextColumn("Type",                width="small"),
                               "category":       st.column_config.TextColumn("Category",            width="medium"),
                               "supplier":       st.column_config.TextColumn("Supplier",            width="medium"),
                               "part_id":        st.column_config.TextColumn("Part ID",             width="small"),
                               "lead_time":      st.column_config.NumberColumn("Lead Time (d)",     width="small"),
                               "rate":           st.column_config.NumberColumn("Rate",              width="small"),
                               "security_stock": st.column_config.NumberColumn("Security Stock",    width="small"),
                               "rack":           st.column_config.TextColumn("Rack",                width="small"),
                               "bin":            st.column_config.TextColumn("Bin",                 width="small"),
                           })
            render_page_controls("vi", sz, off, off_key, len(df))
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Export CSV", csv,
                               file_name=f"items_{date.today()}.csv",
                               mime="text/csv")
        else:
            st.info("No items found.")

    # ============================================================
    # ── ADD SUPPLIER ────────────────────────────────────────────
    # ============================================================
    elif page == "Add Supplier":
        st.title("🤝 Add Supplier")
        with st.form("supplier"):
            c1, c2  = st.columns(2)
            sn      = c1.text_input("Supplier Name")
            sgst    = c2.text_input("GST # (Optional)")
            scont   = c1.text_input("Contact")
            slead   = c2.number_input("Lead Time (Days)", min_value=0)
            msg     = st.empty()
            if st.form_submit_button("Save"):
                if not sn.strip():
                    msg.error("Supplier Name is required.")
                else:
                    res = requests.post(
                        f"{API_URL}/suppliers/",
                        json={
                            "name":     sn.strip(),
                            "gst_no":   sgst.strip() or None,
                            "contact":  scont.strip(),
                            "lead_time": slead,
                        },
                        headers=auth_headers()
                    )
                    if res.status_code == 200:
                        msg.success("Supplier Added")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error(res.json().get("detail", "Failed"))

    # ============================================================
    # ── EDIT SUPPLIER ───────────────────────────────────────────
    # ============================================================
    elif page == "Edit Supplier":
        st.title("✏️ Edit Supplier")
        supps = fetch("suppliers/")
        if not supps:
            st.error("No suppliers found.")
        else:
            opt  = [f"{s['id']} | {s['name']}" for s in supps]
            sel  = st.selectbox("Select Supplier", ["Select…"] + opt)
            if sel != "Select…":
                sid    = int(sel.split(" | ")[0])
                supp   = next(s for s in supps if s["id"] == sid)
                with st.form("edit_supp"):
                    c1, c2 = st.columns(2)
                    new_name  = c1.text_input("Name",     value=supp["name"])
                    new_cont  = c2.text_input("Contact",  value=supp.get("contact") or "")
                    new_gst   = c1.text_input("GST #",    value=supp.get("gst_no") or "")
                    new_lead  = c2.number_input("Lead Time (Days)",
                                                min_value=0,
                                                value=supp.get("lead_time") or 0)
                    msg = st.empty()
                    if st.form_submit_button("💾 Save Changes"):
                        res = requests.put(
                            f"{API_URL}/suppliers/{sid}",
                            json={"name":     new_name.strip(),
                                  "contact":  new_cont.strip(),
                                  "gst_no":   new_gst.strip() or None,
                                  "lead_time": new_lead},
                            headers=auth_headers()
                        )
                        if res.status_code == 200:
                            msg.success("Supplier Updated")
                            time.sleep(1)
                            st.rerun()
                        else:
                            msg.error(res.json().get("detail", "Failed"))

    # ============================================================
    # ── DELETE SUPPLIER ─────────────────────────────────────────
    # ============================================================
    elif page == "Delete Supplier":
        st.title("🗑️ Delete Supplier")
        supps = fetch("suppliers/")
        if not supps:
            st.info("No suppliers to delete.")
        else:
            opt = [f"{s['id']} | {s['name']} ({s.get('item_count',0)} items)"
                   for s in supps]
            sel = st.selectbox("Select Supplier", ["Select…"] + opt)
            if sel != "Select…":
                sid  = int(sel.split(" | ")[0])
                name = sel.split(" | ")[1].split(" (")[0]
                st.warning(f"You are about to delete supplier **{name}**.")
                msg  = st.empty()
                if confirm_button(f"Delete {name}", "del_supp_btn"):
                    res = requests.delete(f"{API_URL}/suppliers/{sid}",
                                          headers=auth_headers())
                    if res.status_code == 200:
                        msg.success("Supplier Deleted")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error(res.json().get("detail", "Failed"))

    # ============================================================
    # ── VIEW SUPPLIER ───────────────────────────────────────────
    # ============================================================
    elif page == "View Supplier":
        st.title("🚚 Supplier List")
        data = fetch("suppliers/")
        if data:
            df = pd.DataFrame(data)
            sc1, sc2 = st.columns(2)
            s_name   = sc1.text_input("🔍 Supplier name", key="sup_name")
            df       = filter_df(df, s_name, ["name"])
            sliced, sz, off, off_key = paginate_df(df, "sup")
            st.data_editor(sliced, disabled=True, hide_index=True,
                           use_container_width=True,
                           column_config={
                               "id":                 st.column_config.NumberColumn("ID",                  width="small"),
                               "name":               st.column_config.TextColumn("Name",                  width="medium"),
                               "contact":            st.column_config.TextColumn("Contact",               width="medium"),
                               "gst_no":             st.column_config.TextColumn("GST No.",               width="small"),
                               "lead_time":          st.column_config.NumberColumn("Lead Time (Days)",    width="small"),
                               "last_purchase_date": st.column_config.TextColumn("Last Purchase Date",    width="small"),
                               "last_purchase_rate": st.column_config.NumberColumn("Last Purchase Rate",  width="small"),
                               "item_count":         st.column_config.NumberColumn("Items",               width="small"),
                           })
            render_page_controls("sup", sz, off, off_key, len(df))
        else:
            st.info("No suppliers found.")

    # ============================================================
    # ── ADD CATEGORY ────────────────────────────────────────────
    # ============================================================
    elif page == "Add Category":
        st.title("📜 Add Category")
        with st.form("category"):
            sp   = st.text_input("Category Name")
            desc = st.text_area("Description (Optional)")
            msg  = st.empty()
            if st.form_submit_button("Save"):
                if not sp.strip():
                    msg.error("Category name is required.")
                else:
                    res = requests.post(
                        f"{API_URL}/category/",
                        json={"category": sp.strip(), "description": desc},
                        headers=auth_headers()
                    )
                    if res.status_code == 200:
                        msg.success("Category Added")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error(res.json().get("detail", "Failed"))

    # ============================================================
    # ── EDIT CATEGORY ───────────────────────────────────────────
    # ============================================================
    elif page == "Edit Category":
        st.title("✏️ Edit Category")
        cats = fetch("category/")
        if not cats:
            st.info("No categories found.")
        else:
            opt = [f"{c['id']} | {c['category']}" for c in cats]
            sel = st.selectbox("Select Category", ["Select…"] + opt)
            if sel != "Select…":
                cid  = int(sel.split(" | ")[0])
                cat  = next(c for c in cats if c["id"] == cid)
                with st.form("edit_cat"):
                    new_name = st.text_input("Category Name",
                                             value=cat["category"])
                    new_desc = st.text_area("Description",
                                            value=cat.get("description") or "")
                    msg = st.empty()
                    if st.form_submit_button("💾 Save Changes"):
                        res = requests.put(
                            f"{API_URL}/category/{cid}",
                            json={"category":    new_name.strip(),
                                  "description": new_desc},
                            headers=auth_headers()
                        )
                        if res.status_code == 200:
                            msg.success("Category Updated")
                            time.sleep(1)
                            st.rerun()
                        else:
                            msg.error(res.json().get("detail", "Failed"))

    # ============================================================
    # ── VIEW CATEGORIES ─────────────────────────────────────────
    # ============================================================
    elif page == "View Categories":
        st.title("📜 Categories")
        data = fetch("category/")
        if data:
            df       = pd.DataFrame(data)
            s_cat    = st.text_input("🔍 Search", key="category_search")
            df       = filter_df(df, s_cat, ["category", "description"])
            sliced, sz, off, off_key = paginate_df(df, "cat")
            st.dataframe(sliced, use_container_width=True, hide_index=True)
            render_page_controls("cat", sz, off, off_key, len(df))
        else:
            st.info("No categories found.")

    # ============================================================
    # ── DELETE CATEGORY ─────────────────────────────────────────
    # ============================================================
    elif page == "Delete Category":
        st.title("🗑️ Delete Category")
        categories = fetch("category/")
        if not categories:
            st.info("No categories found.")
        else:
            s_map = {c["category"]: c["id"] for c in categories}
            info  = {c["category"]: c.get("item_count", 0) for c in categories}
            opt   = [f"{c} ({info[c]} items)" for c in s_map]
            sel   = st.selectbox("Select Category", ["Select…"] + opt)
            if sel != "Select…":
                cat_name = sel.split(" (")[0]
                cid      = s_map[cat_name]
                st.warning(f"You are about to delete category **{cat_name}**.")
                msg = st.empty()
                if confirm_button(f"Delete {cat_name}", "del_cat_btn"):
                    res = requests.delete(f"{API_URL}/category/{cid}",
                                          headers=auth_headers())
                    if res.status_code == 200:
                        msg.success("Category Deleted")
                        time.sleep(1)
                        st.rerun()
                    else:
                        msg.error(res.json().get("detail", "Failed"))

    # ============================================================
    # ── REPORTS ─────────────────────────────────────────────────
    # ============================================================
    elif page == "Reports":
        st.title("📊 Inventory Reports")
        tab1, tab2 = st.tabs(["📅 Daily Report", "🗓️ Monthly Report"])

        def _render_report(df, label):
            # FIX: optional filters
            c1, c2, c3 = st.columns(3)
            s_item  = c1.text_input("🔍 Item", key=f"rpt_item_{label}")
            t_filter = c2.selectbox("Item Type", ["All", "RAW", "FINAL"],
                                    key=f"rpt_type_{label}")
            hide_zero = c3.checkbox("Hide zero-activity rows",
                                    key=f"rpt_zero_{label}")
            df = filter_df(df, s_item, ["item_id", "item_name"])
            if t_filter != "All":
                df = df[df["item_type"].str.upper() == t_filter]
            if hide_zero:
                df = df[(df["total_inward"] != 0) | (df["total_issue"] != 0)]
            # Flag negative opening stock
            if "opening_stock" in df.columns:
                neg = df["opening_stock"] < 0
                if neg.any():
                    st.warning(f"⚠️ {neg.sum()} item(s) have negative opening stock — check transaction history.")
            st.caption(f"{len(df)} result(s)")
            st.data_editor(df, disabled=True, hide_index=True,
                           use_container_width=True,
                           column_config={
                               "item_id":       st.column_config.NumberColumn("Item ID",       width="small"),
                               "item_name":     st.column_config.TextColumn("Item Name",       width="medium"),
                               "item_type":     st.column_config.TextColumn("Type",            width="small"),
                               "opening_stock": st.column_config.NumberColumn("Opening Stock", width="small"),
                               "total_inward":  st.column_config.NumberColumn("Total Inward",  width="small"),
                               "total_issue":   st.column_config.NumberColumn("Total Issue",   width="small"),
                               "closing_stock": st.column_config.NumberColumn("Closing Stock", width="small"),
                           })
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", csv,
                               file_name=f"report_{label}_{date.today()}.csv",
                               mime="text/csv")

        with tab1:
            st.subheader("Daily Report")
            sel_date = st.date_input("Select Date", value=date.today(),
                                     max_value=date.today(), key="daily_date")
            if st.button("Generate", type="primary", key="gen_daily"):
                try:
                    r    = requests.get(f"{API_URL}/report/daily",
                                        params={"report_date": str(sel_date)},
                                        headers=HEADERS, timeout=5)
                    data = r.json() if r.status_code == 200 else []
                except Exception:
                    st.error("Could not connect to backend.")
                    data = []
                if data:
                    _render_report(pd.DataFrame(data), str(sel_date))
                else:
                    st.info("No data for this date.")

        with tab2:
            st.subheader("Monthly Report")
            c1, c2 = st.columns(2)
            sel_year  = c1.number_input("Year", min_value=2000,
                                         max_value=date.today().year,
                                         value=date.today().year)
            sel_month = c2.selectbox("Month", list(range(1, 13)),
                                      format_func=lambda m: date(2000, m, 1).strftime("%B"),
                                      index=date.today().month - 1)
            if st.button("Generate", type="primary", key="gen_monthly"):
                try:
                    r    = requests.get(f"{API_URL}/report/monthly",
                                        params={"year": sel_year, "month": sel_month},
                                        headers=HEADERS, timeout=5)
                    data = r.json() if r.status_code == 200 else []
                except Exception:
                    st.error("Could not connect to backend.")
                    data = []
                if data:
                    _render_report(pd.DataFrame(data),
                                   f"{sel_year}-{sel_month:02d}")
                else:
                    st.info("No data for this month.")

    # ============================================================
    # ── AUDIT LOGS ──────────────────────────────────────────────
    # ============================================================
    elif page == "Audit Logs":
        st.title("📋 Audit Logs")
        st.caption("All database actions (successes and failures).")

        with st.expander("🔍 Filter", expanded=True):
            fc1, fc2, fc3 = st.columns(3)
            fc4, fc5, fc6, fc7 = st.columns(4)

            from_date       = fc1.date_input("From Date",  value=None, key="log_from")
            to_date         = fc2.date_input("To Date",    value=None, key="log_to")
            action_filter   = fc3.selectbox(
                "Action",
                ["All", "CREATE", "DELETE", "UPDATE", "ISSUE", "INWARD",
                 "VOID", "LOGIN"],
                key="log_action"
            )
            table_filter    = fc4.selectbox(
                "Table",
                ["All", "items", "suppliers", "category_list", "inwards",
                 "issues", "users", "bom", "bom_substitutes"],
                key="log_table"
            )
            username_filter = fc5.text_input("Username", key="log_user")
            # FIX: filter by success/failure
            status_filter   = fc6.selectbox("Status",
                                             ["All", "✅ Success", "❌ Failure"],
                                             key="log_status")
            limit           = fc7.selectbox("Per page", [25, 50, 100, 200],
                                             key="log_limit")

        if "log_offset" not in st.session_state:
            st.session_state.log_offset = 0

        filter_sig = (f"{from_date}{to_date}{action_filter}{table_filter}"
                      f"{username_filter}{status_filter}{limit}")
        if st.session_state.get("log_filter_sig") != filter_sig:
            st.session_state.log_offset  = 0
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
        if status_filter == "✅ Success":
            params["success"] = "true"
        elif status_filter == "❌ Failure":
            params["success"] = "false"

        try:
            r = requests.get(f"{API_URL}/logs/", params=params,
                             headers=auth_headers(), timeout=5)
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
        st.markdown(f"**{total} total** — showing {showing_from}–{showing_to}")

        if logs:
            df = pd.DataFrame(logs, columns=[
                "id", "timestamp", "username", "action",
                "table_name", "record_id", "detail", "success"
            ])
            df["timestamp"] = pd.to_datetime(df["timestamp"]) \
                                .dt.tz_localize("UTC") \
                                .dt.tz_convert("Asia/Kolkata") \
                                .dt.strftime("%d %b %Y %H:%M")
            df["Status"] = df["success"].apply(
                lambda v: "✅" if v else "❌")
            df = df.rename(columns={
                "id": "Log ID", "timestamp": "Timestamp",
                "username": "User", "action": "Action",
                "table_name": "Table", "record_id": "Record ID",
                "detail": "Detail",
            })
            display_cols = ["Log ID", "Timestamp", "User", "Action",
                            "Table", "Record ID", "Status", "Detail"]
            st.data_editor(df[display_cols], disabled=True, hide_index=True,
                           use_container_width=True,
                           column_config={
                               "Log ID":    st.column_config.NumberColumn("Log ID",    width="small"),
                               "Timestamp": st.column_config.TextColumn("Timestamp",   width="medium"),
                               "User":      st.column_config.TextColumn("User",        width="small"),
                               "Action":    st.column_config.TextColumn("Action",      width="small"),
                               "Table":     st.column_config.TextColumn("Table",       width="small"),
                               "Record ID": st.column_config.NumberColumn("Record ID", width="small"),
                               "Status":    st.column_config.TextColumn("Status",      width="small"),
                               "Detail":    st.column_config.TextColumn("Detail",      width="large"),
                           })

            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", csv,
                               file_name=f"audit_logs_{date.today()}.csv",
                               mime="text/csv")
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
                    f"<div style='text-align:center;padding-top:8px'>"
                    f"Page {current_page} of {total_pages}</div>",
                    unsafe_allow_html=True)
            with pc3:
                if st.button("Next ▶", disabled=(showing_to >= total)):
                    st.session_state.log_offset = offset + limit
                    st.rerun()
        else:
            st.info("No log entries found.")

    # ============================================================
    # ── BOM ENTRY ───────────────────────────────────────────────
    # ============================================================
    elif page == "BOM Entry":
        st.title("🔧 BOM Entry")

        all_items   = fetch("items/")
        final_items = [i for i in all_items
                       if str(i.get("item_type","")).upper() in ("FINAL", "FINAL")]
        raw_items   = [i for i in all_items
                       if str(i.get("item_type","")).upper() == "RAW"]

        if not final_items:
            st.warning("No FINAL type items found.")
        elif not raw_items:
            st.warning("No RAW type items found.")
        else:
            df_final      = pd.DataFrame(final_items)
            raw_options   = {f"{i['id']} | {i['item_name']}": i["id"]
                             for i in raw_items}
            raw_opts_list = list(raw_options.keys())

            for k, v in [("be_iid", "Select ID"), ("be_iname", "Select Name")]:
                if k not in st.session_state:
                    st.session_state[k] = v

            def sync_be_name():
                if st.session_state.be_iid != "Select ID":
                    m = df_final[df_final["id"] == int(st.session_state.be_iid)]
                    if not m.empty:
                        st.session_state.be_iname = m.iloc[0]["item_name"]

            def sync_be_id():
                if st.session_state.be_iname != "Select Name":
                    m = df_final[df_final["item_name"] == st.session_state.be_iname]
                    if not m.empty:
                        st.session_state.be_iid = str(m.iloc[0]["id"])

            c1, c2 = st.columns(2)
            c1.selectbox("Finished Item — by ID",
                         ["Select ID"] + [str(x) for x in df_final["id"]],
                         key="be_iid", on_change=sync_be_name)
            c2.selectbox("Finished Item — by Name",
                         ["Select Name"] + df_final["item_name"].tolist(),
                         key="be_iname", on_change=sync_be_id)

            if (st.session_state.be_iname == "Select Name"
                    or st.session_state.be_iid == "Select ID"):
                st.info("Select a finished item to load or create its BOM.")
            else:
                finished_id   = int(st.session_state.be_iid)
                finished_name = st.session_state.be_iname
                bom_key       = f"be_rows_{finished_id}"
                loaded_key    = f"be_loaded_{finished_id}"

                if bom_key not in st.session_state or \
                        st.session_state.get(loaded_key) != finished_id:
                    try:
                        existing = requests.get(
                            f"{API_URL}/bom/{finished_id}",
                            headers=auth_headers(), timeout=5
                        ).json()
                    except Exception:
                        existing = []

                    if existing:
                        st.session_state[bom_key] = [
                            {
                                "raw":      next(
                                    (k for k, v in raw_options.items()
                                     if v == r["raw_item_id"]),
                                    raw_opts_list[0]
                                ),
                                "quantity":    r["quantity"],
                                "bom_id":      r["bom_id"],
                                "substitutes": [
                                    {
                                        "raw":      next(
                                            (k for k, v in raw_options.items()
                                             if v == s["substitute_item_id"]),
                                            raw_opts_list[0]
                                        ),
                                        "quantity": s["quantity"],
                                        "sub_id":   s["id"]
                                    }
                                    for s in r.get("substitutes", [])
                                ]
                            }
                            for r in existing
                        ]
                    else:
                        st.session_state[bom_key] = [
                            {"raw": raw_opts_list[0], "quantity": 1,
                             "bom_id": None, "substitutes": []}
                        ]
                    st.session_state[loaded_key] = finished_id

                rows = st.session_state[bom_key]
                st.divider()
                st.subheader(f"BOM — {finished_name}")
                st.caption(f"{len(rows)} material row(s)")

                if st.button("➕ Add Material Row"):
                    rows.append({"raw": raw_opts_list[0], "quantity": 1,
                                 "bom_id": None, "substitutes": []})
                    st.rerun()

                st.divider()
                rows_to_delete = []
                for idx, row in enumerate(rows):
                    with st.container(border=True):
                        c1, c2, c3, c4 = st.columns([0.4, 3.5, 1, 1.5])
                        if c1.button("🗑️", key=f"be_del_{idx}",
                                     help="Remove row"):
                            rows_to_delete.append(idx)

                        row["raw"] = c2.selectbox(
                            f"Raw Material {idx+1}", raw_opts_list,
                            index=raw_opts_list.index(row["raw"])
                            if row["raw"] in raw_opts_list else 0,
                            key=f"be_raw_{idx}",
                            label_visibility="collapsed"
                        )
                        row["quantity"] = c3.number_input(
                            "Qty", min_value=1, step=1,
                            value=int(row["quantity"]),
                            key=f"be_qty_{idx}",
                            label_visibility="collapsed"
                        )
                        if c4.button("➕ Substitute", key=f"be_addsub_{idx}"):
                            row["substitutes"].append(
                                {"raw": raw_opts_list[0], "quantity": 1,
                                 "sub_id": None})
                            st.rerun()

                        subs_to_delete = []
                        for s_idx, sub in enumerate(row["substitutes"]):
                            sc1, sc2, sc3, sc4 = st.columns([0.8, 3.2, 1, 0.4])
                            sc1.markdown("<span style='color:gray'>↳ Sub</span>",
                                         unsafe_allow_html=True)
                            # FIX: prevent sub == primary
                            sub_opts = [o for o in raw_opts_list
                                        if o != row["raw"]]
                            if not sub_opts:
                                sub_opts = raw_opts_list
                            cur_sub  = sub["raw"] if sub["raw"] in sub_opts \
                                else sub_opts[0]
                            sub["raw"] = sc2.selectbox(
                                f"Sub {s_idx+1}", sub_opts,
                                index=sub_opts.index(cur_sub),
                                key=f"be_sub_{idx}_{s_idx}",
                                label_visibility="collapsed"
                            )
                            sub["quantity"] = sc3.number_input(
                                "SQty", min_value=1, step=1,
                                value=int(sub["quantity"]),
                                key=f"be_sqty_{idx}_{s_idx}",
                                label_visibility="collapsed"
                            )
                            if sc4.button("🗑️", key=f"be_delsub_{idx}_{s_idx}"):
                                subs_to_delete.append(s_idx)

                        if subs_to_delete:
                            for s_i in sorted(subs_to_delete, reverse=True):
                                sub = row["substitutes"][s_i]
                                if sub.get("sub_id"):
                                    try:
                                        requests.delete(
                                            f"{API_URL}/bom/substitute/{sub['sub_id']}",
                                            headers=auth_headers(), timeout=5)
                                    except Exception:
                                        pass
                                row["substitutes"].pop(s_i)
                            st.rerun()

                if rows_to_delete:
                    for i in sorted(rows_to_delete, reverse=True):
                        bom_id = rows[i].get("bom_id")
                        if bom_id:
                            try:
                                requests.delete(f"{API_URL}/bom/{bom_id}",
                                                headers=auth_headers(), timeout=5)
                            except Exception:
                                pass
                        rows.pop(i)
                    st.rerun()

                st.divider()
                msg = st.empty()
                if st.button("💾 Save BOM", type="primary"):
                    primary_raws = [row["raw"] for row in rows]
                    if len(primary_raws) != len(set(primary_raws)):
                        msg.error("Duplicate raw materials in primary rows.")
                    else:
                        errors = []
                        success_count = 0
                        for idx, row in enumerate(rows):
                            if row["raw"] not in raw_options:
                                errors.append(f"Row {idx+1}: invalid raw material.")
                                continue
                            if not row.get("bom_id"):
                                try:
                                    res = requests.post(
                                        f"{API_URL}/bom/",
                                        json={
                                            "final_item_id": finished_id,
                                            "raw_item_id":   raw_options[row["raw"]],
                                            "quantity":      row["quantity"]
                                        },
                                        headers=auth_headers(), timeout=5
                                    )
                                    if res.status_code == 200:
                                        row["bom_id"] = res.json().get("bom_id")
                                        success_count += 1
                                    else:
                                        errors.append(
                                            f"Row {idx+1}: "
                                            f"{res.json().get('detail','Failed')}")
                                        continue
                                except Exception:
                                    errors.append(f"Row {idx+1}: connection error.")
                                    continue
                            else:
                                try:
                                    res = requests.put(
                                        f"{API_URL}/bom/{row['bom_id']}",
                                        json={"quantity": row["quantity"]},
                                        headers=auth_headers(), timeout=5
                                    )
                                    if res.status_code == 200:
                                        success_count += 1
                                    else:
                                        errors.append(
                                            f"Row {idx+1}: "
                                            f"{res.json().get('detail','Failed')}")
                                        continue
                                except Exception:
                                    errors.append(f"Row {idx+1}: connection error.")
                                    continue

                            for s_idx, sub in enumerate(row["substitutes"]):
                                if sub.get("sub_id"):
                                    try:
                                        requests.put(
                                            f"{API_URL}/bom/substitute/{sub['sub_id']}",
                                            json={"quantity": sub["quantity"]},
                                            headers=auth_headers(), timeout=5)
                                    except Exception:
                                        pass
                                    continue
                                if sub["raw"] not in raw_options:
                                    errors.append(
                                        f"Row {idx+1} Sub {s_idx+1}: invalid.")
                                    continue
                                try:
                                    sres = requests.post(
                                        f"{API_URL}/bom/substitute/",
                                        json={
                                            "bom_id":             row["bom_id"],
                                            "substitute_item_id": raw_options[sub["raw"]],
                                            "quantity":           sub["quantity"]
                                        },
                                        headers=auth_headers(), timeout=5
                                    )
                                    if sres.status_code == 200:
                                        sub["sub_id"] = sres.json().get("sub_id")
                                    else:
                                        errors.append(
                                            f"Sub {s_idx+1}: "
                                            f"{sres.json().get('detail','Failed')}")
                                except Exception:
                                    errors.append(
                                        f"Sub {s_idx+1}: connection error.")

                        for e in errors:
                            st.error(e)
                        if not errors:
                            msg.success("✅ BOM saved successfully!")
                            del st.session_state[bom_key]
                            del st.session_state[loaded_key]
                            time.sleep(1)
                            st.rerun()

    # ============================================================
    # ── BOM VIEW ────────────────────────────────────────────────
    # ============================================================
    elif page == "BOM View":
        st.title("📋 BOM View")

        all_items   = fetch("items/")
        final_items = [i for i in all_items
                       if str(i.get("item_type","")).upper() == "FINAL"]

        if not final_items:
            st.warning("No FINAL type items found.")
        else:
            df_final = pd.DataFrame(final_items)

            for k, v in [("bv_iid", "Select ID"), ("bv_iname", "Select Name")]:
                if k not in st.session_state:
                    st.session_state[k] = v

            def sync_bv_name():
                if st.session_state.bv_iid != "Select ID":
                    m = df_final[df_final["id"] == int(st.session_state.bv_iid)]
                    if not m.empty:
                        st.session_state.bv_iname = m.iloc[0]["item_name"]

            def sync_bv_id():
                if st.session_state.bv_iname != "Select Name":
                    m = df_final[df_final["item_name"] == st.session_state.bv_iname]
                    if not m.empty:
                        st.session_state.bv_iid = str(m.iloc[0]["id"])

            c1, c2 = st.columns(2)
            c1.selectbox("Filter by ID",
                         ["Select ID"] + [str(x) for x in df_final["id"]],
                         key="bv_iid", on_change=sync_bv_name)
            c2.selectbox("Filter by Name",
                         ["Select Name"] + df_final["item_name"].tolist(),
                         key="bv_iname", on_change=sync_bv_id)

            if (st.session_state.bv_iname == "Select Name"
                    or st.session_state.bv_iid == "Select ID"):
                st.info("Select a finished item to view its BOM.")
            else:
                fid  = int(st.session_state.bv_iid)
                name = st.session_state.bv_iname

                try:
                    bom = requests.get(f"{API_URL}/bom/{fid}",
                                       headers=auth_headers(), timeout=5).json()
                except Exception:
                    bom = []
                    st.error("Could not connect to backend.")

                st.divider()
                if not bom:
                    st.info(f"No BOM defined for **{name}** yet.")
                else:
                    st.subheader(f"BOM — {name}")
                    st.caption(f"{len(bom)} material(s)")

                    display_rows = []
                    for r in bom:
                        display_rows.append({
                            "Type":          "Primary",
                            "BOM ID":        r["bom_id"],
                            "Raw Item ID":   r["raw_item_id"],
                            "Raw Item Name": r["raw_item_name"],
                            "Qty / Unit":    r["quantity"],
                        })
                        for s in r.get("substitutes", []):
                            display_rows.append({
                                "Type":          "↳ Substitute",
                                "BOM ID":        s.get("id", ""),
                                "Raw Item ID":   s["substitute_item_id"],
                                "Raw Item Name": s["substitute_item_name"],
                                "Qty / Unit":    s["quantity"],
                            })

                    df_display = pd.DataFrame(display_rows)
                    sliced_bom, sz_bom, off_bom, off_key_bom = paginate_df(df_display, "bv")
                    st.data_editor(sliced_bom, disabled=True, hide_index=True,
                                   use_container_width=True,
                                   column_config={
                                       "Type":          st.column_config.TextColumn("Type",          width="small"),
                                       "BOM ID":        st.column_config.NumberColumn("BOM ID",      width="small"),
                                       "Raw Item ID":   st.column_config.NumberColumn("Raw Item ID", width="small"),
                                       "Raw Item Name": st.column_config.TextColumn("Raw Item Name", width="large"),
                                       "Qty / Unit":    st.column_config.NumberColumn("Qty / Unit",  width="small"),
                                   })
                    render_page_controls("bv", sz_bom, off_bom, off_key_bom, len(df_display))
                    csv = df_display.to_csv(index=False).encode("utf-8")
                    st.download_button("⬇️ Export BOM CSV", csv,
                                       file_name=f"bom_{name}_{date.today()}.csv",
                                       mime="text/csv")

    # ============================================================
    # ── FEASIBILITY CHECK ───────────────────────────────────────
    # ============================================================
    elif page == "Feasibility Check":
        st.title("🔍 Production Feasibility Check")

        all_items   = fetch("items/")
        final_items = [i for i in all_items
                       if str(i.get("item_type","")).upper() == "FINAL"]

        if not final_items:
            st.warning("No FINAL type items found.")
        else:
            df_final = pd.DataFrame(final_items)

            for k, v in [("fc_iid", "Select ID"), ("fc_iname", "Select Name")]:
                if k not in st.session_state:
                    st.session_state[k] = v

            def sync_fc_name():
                if st.session_state.fc_iid != "Select ID":
                    m = df_final[df_final["id"] == int(st.session_state.fc_iid)]
                    if not m.empty:
                        st.session_state.fc_iname = m.iloc[0]["item_name"]

            def sync_fc_id():
                if st.session_state.fc_iname != "Select Name":
                    m = df_final[df_final["item_name"] == st.session_state.fc_iname]
                    if not m.empty:
                        st.session_state.fc_iid = str(m.iloc[0]["id"])

            c1, c2, c3 = st.columns([2, 2, 1])
            c1.selectbox("Finished Item — by ID",
                         ["Select ID"] + [str(x) for x in df_final["id"]],
                         key="fc_iid", on_change=sync_fc_name)
            c2.selectbox("Finished Item — by Name",
                         ["Select Name"] + df_final["item_name"].tolist(),
                         key="fc_iname", on_change=sync_fc_id)
            produce_qty = c3.number_input("Qty to Produce", min_value=1,
                                          step=1, value=1, key="fc_qty")

            if (st.session_state.fc_iname == "Select Name"
                    or st.session_state.fc_iid == "Select ID"):
                st.info("Select a finished item and quantity to check feasibility.")
            else:
                fid  = int(st.session_state.fc_iid)
                name = st.session_state.fc_iname

                try:
                    bom = requests.get(f"{API_URL}/bom/{fid}",
                                       headers=auth_headers(), timeout=5).json()
                except Exception:
                    bom = []
                    st.error("Could not connect to backend.")

                st.divider()

                if not bom:
                    st.warning(f"No BOM defined for **{name}**.")
                else:
                    stock_data = fetch("stock-report/")
                    stock_map  = {r["item_id"]: float(r["current_stock"])
                                  for r in stock_data} if stock_data else {}

                    all_ok      = True
                    result_rows = []

                    for row in bom:
                        required   = row["quantity"] * produce_qty
                        available  = stock_map.get(row["raw_item_id"], 0)
                        primary_ok = available >= required
                        shortfall  = max(0, required - available)

                        sub_results = []
                        sub_covers  = False
                        for s in row.get("substitutes", []):
                            s_req = s["quantity"] * produce_qty
                            s_avl = stock_map.get(s["substitute_item_id"], 0)
                            s_ok  = s_avl >= s_req
                            sub_results.append({
                                "name":      s["substitute_item_name"],
                                "required":  s_req,
                                "available": s_avl,
                                "ok":        s_ok,
                            })
                            if s_ok:
                                sub_covers = True

                        row_feasible = primary_ok or sub_covers
                        if not row_feasible:
                            all_ok = False

                        result_rows.append({
                            "raw_item_id":   row["raw_item_id"],
                            "raw_item_name": row["raw_item_name"],
                            "required":      required,
                            "available":     available,
                            "primary_ok":    primary_ok,
                            "shortfall":     shortfall,
                            "sub_results":   sub_results,
                            "sub_covers":    sub_covers,
                            "row_feasible":  row_feasible,
                        })

                    if all_ok:
                        st.success(
                            f"✅ **Feasible** — All materials available to "
                            f"produce **{produce_qty}× {name}**.")
                    else:
                        st.error(
                            f"❌ **Not Feasible** — Insufficient materials for "
                            f"**{produce_qty}× {name}**.")

                    # ── "Max producible" calculation ──
                    max_producible = None
                    for row in result_rows:
                        if not row["raw_item_id"]:
                            continue
                        bom_qty = next(
                            (b["quantity"] for b in bom
                             if b["raw_item_id"] == row["raw_item_id"]), 1)
                        avl = row["available"]
                        # Also check best substitute
                        for s in row["sub_results"]:
                            if s["ok"] or s["available"] > avl:
                                sub_bom_qty = next(
                                    (ss["quantity"] for b in bom
                                     for ss in b.get("substitutes", [])
                                     if ss["substitute_item_name"] == s["name"]),
                                    bom_qty)
                                avl = max(avl, s["available"])
                                bom_qty = sub_bom_qty
                        if bom_qty > 0:
                            limit = int(avl // bom_qty)
                            max_producible = (limit if max_producible is None
                                              else min(max_producible, limit))

                    if max_producible is not None:
                        st.info(
                            f"📦 **Maximum producible with current stock:** "
                            f"`{max_producible}` unit(s) of {name}")

                    st.subheader("Material Breakdown")
                    st.caption(f"{len(result_rows)} BOM line(s) · "
                               f"producing {produce_qty}× {name}")

                    for r in result_rows:
                        if r["primary_ok"]:
                            icon, status = "🟢", "OK"
                        elif r["sub_covers"]:
                            icon, status = "🟡", "OK via Substitute"
                        else:
                            icon, status = "🔴", "SHORTAGE"

                        with st.container(border=True):
                            h1, h2, h3, h4, h5 = st.columns([3, 1.2, 1.2, 1.2, 1.5])
                            h1.markdown(
                                f"**{r['raw_item_name']}**  "
                                f"`ID {r['raw_item_id']}`")
                            h2.metric("Required", r["required"])
                            h3.metric("In Stock",  int(r["available"]))
                            h4.metric(
                                "Shortfall",
                                int(r["shortfall"]) if not r["primary_ok"] else 0,
                                delta=None if r["primary_ok"]
                                else f"-{int(r['shortfall'])}",
                                delta_color="inverse")
                            h5.markdown(
                                f"<div style='padding-top:8px;font-size:1.1rem'>"
                                f"{icon} <b>{status}</b></div>",
                                unsafe_allow_html=True)

                            if not r["primary_ok"] and r["sub_results"]:
                                st.caption("Substitutes:")
                                for s in r["sub_results"]:
                                    s_icon = "🟢" if s["ok"] else "🔴"
                                    st.markdown(
                                        f"&nbsp;&nbsp;&nbsp;&nbsp;{s_icon} "
                                        f"**{s['name']}** — "
                                        f"Required: `{s['required']}` | "
                                        f"Available: `{int(s['available'])}`",
                                        unsafe_allow_html=True)

                            if not r["primary_ok"] and not r["sub_results"]:
                                st.caption("No substitutes defined.")

                            # ── Quick action: go to Record Inward ──
                            if not r["row_feasible"] and role in ("Admin", "Manager"):
                                if st.button("📥 Record Inward for this item",
                                             key=f"fc_inward_{r['raw_item_id']}"):
                                    st.session_state.current_page = "Record Inward"
                                    st.query_params["page"] = "Record Inward"
                                    st.rerun()