from fastapi import FastAPI, Depends, HTTPException, Security, BackgroundTasks, Request
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, ConfigDict
from typing import Optional
from decimal import Decimal
from datetime import date, datetime, timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import os
import uuid
import yagmail
import logging
from dotenv import load_dotenv

load_dotenv()
import models
from database import engine, get_db

# ================= APP & SECURITY =================
ph = PasswordHasher()
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Industrial ERP")

API_KEY = os.environ.get("ERP_API_KEY")

# SESSION TTL — sessions older than this are expired (30 days)
SESSION_TTL_DAYS = 30

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_key(key: str = Security(api_key_header)):
    if not key or key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")


# ================= STARTUP WARMUP =================
@app.on_event("startup")
def warmup_db():
    from database import SessionLocal
    try:
        db = SessionLocal()
        db.execute(models.StockStatus.__table__.select().limit(1))
        db.close()
    except Exception:
        pass


# ================= FAILURE LOG SETUP =================
os.makedirs("logs", exist_ok=True)

failure_logger = logging.getLogger("failures")
failure_logger.setLevel(logging.ERROR)
handler = logging.FileHandler("logs/failures.log")
handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
failure_logger.addHandler(handler)


# ================= AUDIT HELPERS =================
def get_username_from_request(request: Request, db: Session) -> str:
    token = request.headers.get("X-Session-Token")
    if not token:
        return "anonymous"
    session = db.query(models.UserSession).filter(
        models.UserSession.token == token
    ).first()
    if not session:
        return "anonymous"
    return session.username


def log_success(db: Session, username: str, action: str, table: str,
                record_id: Optional[int], detail: str):
    entry = models.AuditLog(
        timestamp=datetime.utcnow(),
        username=username,
        action=action,
        table_name=table,
        record_id=record_id,
        detail=detail,
        success=True
    )
    db.add(entry)
    db.commit()


def log_failure(db: Session, username: str, action: str, table: str,
                record_id: Optional[int], detail: str):
    """
    FIX: failures are now written to DB (success=False) AND to the flat log file.
    Previously only the flat file was written, making failures invisible in the UI.
    """
    entry = models.AuditLog(
        timestamp=datetime.utcnow(),
        username=username,
        action=action,
        table_name=table,
        record_id=record_id,
        detail=detail,
        success=False
    )
    db.add(entry)
    try:
        db.commit()
    except Exception:
        db.rollback()
    failure_logger.error(
        f"user={username} | action={action} | table={table} | "
        f"record_id={record_id} | detail={detail}"
    )


# ================= REORDER POINT HELPER =================
def compute_reorder_points_bulk(db: Session, item_ids: list[int],
                                 days: int = 30) -> dict[int, float]:
    """
    FIX: compute reorder points for ALL items in 2 queries instead of N queries.
    Previous code called get_reorder_point() per item inside a loop — O(N) queries.
    """
    if not item_ids:
        return {}
    cutoff = date.today() - timedelta(days=days)

    # Fetch item metadata in one query
    items = db.query(models.Item).filter(models.Item.id.in_(item_ids)).all()
    item_map = {i.id: i for i in items}

    # Fetch 30-day issue sums in one query
    issue_sums = (
        db.query(
            models.Issue.item_id,
            func.coalesce(func.sum(models.Issue.quantity), 0).label("total")
        )
        .filter(
            models.Issue.item_id.in_(item_ids),
            models.Issue.issue_date >= cutoff,
        )
        .group_by(models.Issue.item_id)
        .all()
    )
    issue_map = {row.item_id: float(row.total) for row in issue_sums}

    result = {}
    for iid in item_ids:
        item = item_map.get(iid)
        if not item:
            result[iid] = 0.0
            continue
        security_stock   = item.security_stock or 0
        lead_time        = item.lead_time or 0
        if lead_time == 0:
            result[iid] = float(security_stock)
        else:
            avg_daily = issue_map.get(iid, 0.0) / days
            result[iid] = float(security_stock) + avg_daily * lead_time
    return result


# ================= SCHEMAS =================
class UserLogin(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str


class UserUpdate(BaseModel):
    role:         Optional[str] = None
    new_password: Optional[str] = None


class UserResponse(BaseModel):
    id:       int
    username: str
    role:     str
    model_config = ConfigDict(from_attributes=True)


class CategoryCreate(BaseModel):
    id:          Optional[int] = None
    category:    str
    description: Optional[str] = None


class CategoryUpdate(BaseModel):
    category:    Optional[str] = None
    description: Optional[str] = None


class SupplierCreate(BaseModel):
    name:               str
    contact:            Optional[str]  = None
    gst_no:             Optional[str]  = None
    last_purchase_date: Optional[date] = None
    last_purchase_rate: Decimal        = Decimal("0")
    lead_time:          int            = 0


class SupplierUpdate(BaseModel):
    name:      Optional[str] = None
    contact:   Optional[str] = None
    gst_no:    Optional[str] = None
    lead_time: Optional[int] = None


class ItemCreate(BaseModel):
    item_name:      str
    item_type:      str
    category_id:    Optional[int]   = None
    supplier_id:    Optional[int]   = None
    lead_time:      Optional[int]   = None
    security_stock: Optional[int]   = None
    rate:           Optional[float] = None
    rack:           Optional[str]   = ""
    bin:            Optional[str]   = ""
    part_id:        Optional[str]   = None


class ItemUpdate(BaseModel):
    item_name:      Optional[str]   = None
    category_id:    Optional[int]   = None
    supplier_id:    Optional[int]   = None
    lead_time:      Optional[int]   = None
    security_stock: Optional[int]   = None
    rate:           Optional[float] = None
    rack:           Optional[str]   = None
    bin:            Optional[str]   = None
    part_id:        Optional[str]   = None


class InwardCreate(BaseModel):
    item_id:        int
    invoice_number: str
    quantity:       int
    rate:           Decimal
    order_date:     date
    received_date:  date


class IssueCreate(BaseModel):
    item_id:    int
    quantity:   int
    issue_date: date
    issued_to:  str
    purpose:    Optional[str] = None   # NEW field


class LogFilter(BaseModel):
    from_date:  Optional[date] = None
    to_date:    Optional[date] = None
    action:     Optional[str]  = None
    table_name: Optional[str]  = None
    username:   Optional[str]  = None


class BomCreate(BaseModel):
    final_item_id: int
    raw_item_id:   int
    quantity:      int


class BomSubstituteCreate(BaseModel):
    bom_id:             int
    substitute_item_id: int
    quantity:           int


class BomUpdate(BaseModel):
    quantity: int


class BomSubstituteUpdate(BaseModel):
    quantity: int


# ================= EMAIL HELPER =================
def send_reorder_email(items: list[dict]):
    try:
        yag = yagmail.SMTP(
            user=os.environ.get("EMAIL_SENDER"),
            password=os.environ.get("EMAIL_PASSWORD")
        )
        body = "The following items have fallen below their reorder point:\n\n"
        for item in items:
            body += (
                f"• {item['item_name']}\n"
                f"  Current Stock : {item['current_stock']}\n"
                f"  Security Stock: {item['security_stock']}\n"
                f"  Lead Time : {item['lead_time']} days\n"
                f"  Reorder Point: {round(item['reorder_point'], 1)}\n\n"
            )
        yag.send(
            to=os.environ.get("EMAIL_RECEIVER"),
            subject="⚠️ ERP Reorder Alert",
            contents=body
        )
    except Exception as e:
        print(f"Email failed: {e}")


# ================= AUTH & USERS =================
@app.post("/login")
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(
        models.User.username == user_data.username
    ).first()
    if not user:
        log_failure(db, "unknown", "LOGIN", "users", None,
                    f"Failed login for username: {user_data.username}")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    try:
        ph.verify(user.password, user_data.password)
        expires = datetime.utcnow() + timedelta(days=SESSION_TTL_DAYS)
        session = models.UserSession(
            token=str(uuid.uuid4()),
            username=user.username,
            created_at=datetime.utcnow(),
            expires_at=expires
        )
        db.add(session)
        db.commit()
        log_success(db, user.username, "LOGIN", "users", user.id,
                    f"Logged in: {user.username}")
        return {"token": session.token, "username": user.username,
                "role": user.role}
    except VerifyMismatchError:
        log_failure(db, user_data.username, "LOGIN", "users", None,
                    "Wrong password")
        raise HTTPException(status_code=401, detail="Invalid credentials")


@app.get("/validate-session/{token}")
def validate_session(token: str, db: Session = Depends(get_db)):
    session = db.query(models.UserSession).filter(
        models.UserSession.token == token
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # FIX: check session expiry
    if session.expires_at and datetime.utcnow() > session.expires_at:
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired")
    user = db.query(models.User).filter(
        models.User.username == session.username
    ).first()
    if not user:
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=404, detail="User not found")
    return {"username": user.username, "role": user.role}


@app.delete("/logout/{token}", dependencies=[Depends(verify_key)])
def logout_session(token: str, db: Session = Depends(get_db)):
    session = db.query(models.UserSession).filter(
        models.UserSession.token == token
    ).first()
    if session:
        db.delete(session)
        db.commit()
    return {"status": "logged out"}


@app.get("/users/", response_model=list[UserResponse],
         dependencies=[Depends(verify_key)])
def list_users(db: Session = Depends(get_db)):
    return db.query(models.User).all()


@app.get("/users/sessions/", dependencies=[Depends(verify_key)])
def list_sessions(db: Session = Depends(get_db)):
    """Return all active (non-expired) sessions — for Admin session management."""
    now = datetime.utcnow()
    sessions = db.query(models.UserSession).all()
    result = []
    for s in sessions:
        expired = s.expires_at and now > s.expires_at
        if not expired:
            result.append({
                "token_preview": s.token[:8] + "…",
                "token": s.token,
                "username": s.username,
                "created_at": str(s.created_at),
                "expires_at": str(s.expires_at) if s.expires_at else "never",
            })
    return result


@app.delete("/users/sessions/{token}", dependencies=[Depends(verify_key)])
def revoke_session(token: str, db: Session = Depends(get_db)):
    """Admin: revoke any active session by token."""
    session = db.query(models.UserSession).filter(
        models.UserSession.token == token
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()
    return {"status": "revoked"}


@app.post("/users/", response_model=UserResponse,
          dependencies=[Depends(verify_key)])
def create_user(request: Request, user: UserCreate,
                db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    if user.role not in ("Admin", "Manager", "Viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")
    hashed   = ph.hash(user.password)
    new_user = models.User(username=user.username.strip(),
                           password=hashed, role=user.role)
    db.add(new_user)
    try:
        db.commit()
        db.refresh(new_user)
        log_success(db, username, "CREATE", "users", new_user.id,
                    f"Created user: {user.username}")
        return new_user
    except IntegrityError:
        db.rollback()
        log_failure(db, username, "CREATE", "users", None,
                    f"Duplicate username: {user.username}")
        raise HTTPException(status_code=400, detail="Username already exists")


@app.put("/users/{user_id}", dependencies=[Depends(verify_key)])
def update_user(user_id: int, request: Request, update: UserUpdate,
                db: Session = Depends(get_db)):
    """FIX: allow role change and password reset."""
    requester = get_username_from_request(request, db)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    changes = []
    if update.role and update.role in ("Admin", "Manager", "Viewer"):
        user.role = update.role
        changes.append(f"role={update.role}")
    if update.new_password and update.new_password.strip():
        user.password = ph.hash(update.new_password.strip())
        changes.append("password_reset")
    db.commit()
    log_success(db, requester, "UPDATE", "users", user_id,
                f"Updated user {user.username}: {', '.join(changes)}")
    return {"status": "updated"}


@app.delete("/users/{user_id}", dependencies=[Depends(verify_key)])
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        log_failure(db, username, "DELETE", "users", user_id, "User not found")
        raise HTTPException(status_code=404, detail="User not found")
    if user.username == username:
        log_failure(db, username, "DELETE", "users", user_id,
                    "Attempted self-deletion")
        raise HTTPException(status_code=400,
                            detail="You cannot delete your own account")
    # Delete all sessions belonging to this user before deleting the user
    db.query(models.UserSession).filter(
        models.UserSession.username == user.username
    ).delete()
    db.delete(user)
    db.commit()
    log_success(db, username, "DELETE", "users", user_id,
                f"Deleted user: {user.username}")
    return {"status": "deleted"}


# ================= CATEGORIES =================
@app.get("/category/", dependencies=[Depends(verify_key)])
def get_category(db: Session = Depends(get_db)):
    cats = db.query(models.CategoryList).all()
    result = []
    for c in cats:
        item_count = db.query(models.Item).filter(
            models.Item.category_id == c.id
        ).count()
        result.append({
            "id": c.id,
            "category": c.category,
            "description": c.description,
            "item_count": item_count,
        })
    return result


@app.post("/category/", dependencies=[Depends(verify_key)])
def create_category(request: Request, category: CategoryCreate,
                    db: Session = Depends(get_db)):
    username     = get_username_from_request(request, db)
    new_category = models.CategoryList(
        category=category.category.strip(),
        description=category.description
    )
    db.add(new_category)
    try:
        db.commit()
        log_success(db, username, "CREATE", "category_list", new_category.id,
                    f"Created category: {category.category}")
        return {"status": "success"}
    except IntegrityError:
        db.rollback()
        log_failure(db, username, "CREATE", "category_list", None,
                    f"Duplicate category: {category.category}")
        raise HTTPException(status_code=400, detail="Category already exists")


@app.put("/category/{category_id}", dependencies=[Depends(verify_key)])
def update_category(category_id: int, request: Request, update: CategoryUpdate,
                    db: Session = Depends(get_db)):
    """FIX: allow editing category name/description."""
    username = get_username_from_request(request, db)
    cat = db.query(models.CategoryList).filter(
        models.CategoryList.id == category_id
    ).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if update.category and update.category.strip():
        cat.category = update.category.strip()
    if update.description is not None:
        cat.description = update.description
    try:
        db.commit()
        log_success(db, username, "UPDATE", "category_list", category_id,
                    f"Updated category id={category_id}")
        return {"status": "updated"}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400,
                            detail="Category name already exists")


@app.delete("/category/{category_id}", dependencies=[Depends(verify_key)])
def delete_category(category_id: int, request: Request,
                    db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    category = db.query(models.CategoryList).filter(
        models.CategoryList.id == category_id
    ).with_for_update().first()
    if not category:
        log_failure(db, username, "DELETE", "category_list", category_id,
                    "Category not found")
        raise HTTPException(status_code=404, detail="Category not found")
    item_count = db.query(models.Item).filter(
        models.Item.category_id == category_id
    ).count()
    if item_count > 0:
        log_failure(db, username, "DELETE", "category_list", category_id,
                    f"Blocked — {item_count} item(s) using it")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete category — {item_count} item(s) are using it"
        )
    db.delete(category)
    db.commit()
    log_success(db, username, "DELETE", "category_list", category_id,
                f"Deleted category: {category.category}")
    return {"status": "deleted"}


# ================= SUPPLIERS =================
@app.get("/suppliers/", dependencies=[Depends(verify_key)])
def get_suppliers(db: Session = Depends(get_db)):
    supps = db.query(models.Supplier).all()
    result = []
    for s in supps:
        item_count = db.query(models.Item).filter(
            models.Item.supplier_id == s.id
        ).count()
        result.append({
            "id":                 s.id,
            "name":               s.name,
            "contact":            s.contact,
            "gst_no":             s.gst_no,
            "lead_time":          s.lead_time,
            "last_purchase_date": str(s.last_purchase_date) if s.last_purchase_date else None,
            "last_purchase_rate": float(s.last_purchase_rate) if s.last_purchase_rate else None,
            "item_count":         item_count,
        })
    return result


@app.post("/suppliers/", dependencies=[Depends(verify_key)])
def create_supplier(request: Request, supp: SupplierCreate,
                    db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    new_supp = models.Supplier(**supp.model_dump())
    db.add(new_supp)
    try:
        db.commit()
        log_success(db, username, "CREATE", "suppliers", new_supp.id,
                    f"Created supplier: {supp.name}")
        return {"status": "success"}
    except IntegrityError:
        db.rollback()
        log_failure(db, username, "CREATE", "suppliers", None,
                    f"Duplicate supplier: {supp.name}")
        raise HTTPException(status_code=400, detail="Supplier already exists")


@app.put("/suppliers/{supplier_id}", dependencies=[Depends(verify_key)])
def update_supplier(supplier_id: int, request: Request, update: SupplierUpdate,
                    db: Session = Depends(get_db)):
    """FIX: allow editing supplier details."""
    username = get_username_from_request(request, db)
    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == supplier_id
    ).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    if update.name and update.name.strip():
        supplier.name = update.name.strip()
    if update.contact is not None:
        supplier.contact = update.contact.strip()
    if update.gst_no is not None:
        supplier.gst_no = update.gst_no.strip() or None
    if update.lead_time is not None:
        supplier.lead_time = update.lead_time
    try:
        db.commit()
        log_success(db, username, "UPDATE", "suppliers", supplier_id,
                    f"Updated supplier id={supplier_id}")
        return {"status": "updated"}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400,
                            detail="GST number already exists for another supplier")


@app.delete("/suppliers/{supplier_id}", dependencies=[Depends(verify_key)])
def delete_supplier(supplier_id: int, request: Request,
                    db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == supplier_id
    ).with_for_update().first()
    if not supplier:
        log_failure(db, username, "DELETE", "suppliers", supplier_id,
                    "Supplier not found")
        raise HTTPException(status_code=404, detail="Supplier not found")
    item_count = db.query(models.Item).filter(
        models.Item.supplier_id == supplier_id
    ).count()
    if item_count > 0:
        log_failure(db, username, "DELETE", "suppliers", supplier_id,
                    f"Blocked — {item_count} item(s) using supplier")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete supplier — {item_count} item(s) are using it"
        )
    db.delete(supplier)
    db.commit()
    log_success(db, username, "DELETE", "suppliers", supplier_id,
                f"Deleted supplier: {supplier.name}")
    return {"status": "deleted"}


# ================= ITEMS =================
@app.get("/items/", dependencies=[Depends(verify_key)])
def get_items(db: Session = Depends(get_db)):
    items = db.query(models.Item).all()
    result = []
    for item in items:
        result.append({
            "id":             item.id,
            "item_name":      item.item_name,
            "item_type":      item.item_type,
            "category":       item.category_detail.category if item.category_detail else None,
            "category_id":    item.category_id,
            "supplier":       item.supplier.name if item.supplier else None,
            "supplier_id":    item.supplier_id,
            "lead_time":      item.lead_time,
            "rate":           float(item.rate) if item.rate is not None else None,
            "security_stock": item.security_stock,
            "rack":           item.rack,
            "bin":            item.bin,
            "part_id":        item.part_id,
        })
    return result


@app.post("/items/", dependencies=[Depends(verify_key)])
def create_item(request: Request, item: ItemCreate,
                db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    if not item.item_name or not item.item_name.strip():
        raise HTTPException(status_code=422, detail="Item name cannot be blank")
    if item.item_type.upper() not in ("RAW", "FINAL"):
        raise HTTPException(status_code=422,
                            detail="item_type must be 'RAW' or 'FINAL'")
    if item.item_type.upper() == "RAW":
        if item.category_id is None:
            log_failure(db, username, "CREATE", "items", None,
                        "category_id missing for RAW item")
            raise HTTPException(status_code=400,
                                detail="Category is required for RAW items")
        if not db.query(models.CategoryList).filter(
            models.CategoryList.id == item.category_id
        ).first():
            raise HTTPException(status_code=404, detail="Category not found")
        if item.supplier_id is None:
            raise HTTPException(status_code=400,
                                detail="Supplier is required for RAW items")
        if not db.query(models.Supplier).filter(
            models.Supplier.id == item.supplier_id
        ).first():
            raise HTTPException(status_code=404, detail="Supplier not found")

    new_item = models.Item(
        item_name      = item.item_name.strip(),
        item_type      = item.item_type,
        category_id    = item.category_id    if item.item_type.upper() == "RAW" else None,
        supplier_id    = item.supplier_id    if item.item_type.upper() == "RAW" else None,
        lead_time      = item.lead_time      if item.item_type.upper() == "RAW" else None,
        security_stock = item.security_stock if item.item_type.upper() == "RAW" else None,
        rate           = item.rate           if item.item_type.upper() == "RAW" else None,
        rack           = item.rack or "",
        bin            = item.bin  or "",
        part_id        = item.part_id.strip() if item.part_id and item.item_type.upper() == "RAW" else None
    )
    db.add(new_item)
    try:
        db.commit()
        log_success(db, username, "CREATE", "items", new_item.id,
                    f"Created item: {item.item_name} ({item.item_type})")
        return {"status": "success", "item_id": new_item.id}
    except IntegrityError:
        db.rollback()
        log_failure(db, username, "CREATE", "items", None,
                    f"Integrity error: {item.item_name}")
        raise HTTPException(status_code=400,
                            detail="Item already exists or invalid reference")


@app.get("/items/{item_id}", dependencies=[Depends(verify_key)])
def get_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return {
        "id":             item.id,
        "item_name":      item.item_name,
        "item_type":      item.item_type,
        "category":       item.category_detail.category if item.category_detail else None,
        "category_id":    item.category_id,
        "supplier":       item.supplier.name if item.supplier else None,
        "supplier_id":    item.supplier_id,
        "lead_time":      item.lead_time,
        "rate":           float(item.rate) if item.rate is not None else None,
        "security_stock": item.security_stock,
        "rack":           item.rack,
        "bin":            item.bin,
        "part_id":        item.part_id,
    }


@app.put("/items/{item_id}", dependencies=[Depends(verify_key)])
def update_item(item_id: int, request: Request, update: ItemUpdate,
                db: Session = Depends(get_db)):
    """FIX: allow editing item details."""
    username = get_username_from_request(request, db)
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if update.item_name and update.item_name.strip():
        item.item_name = update.item_name.strip()
    if update.category_id is not None:
        if not db.query(models.CategoryList).filter(
            models.CategoryList.id == update.category_id
        ).first():
            raise HTTPException(status_code=404, detail="Category not found")
        item.category_id = update.category_id
    if update.supplier_id is not None:
        if not db.query(models.Supplier).filter(
            models.Supplier.id == update.supplier_id
        ).first():
            raise HTTPException(status_code=404, detail="Supplier not found")
        item.supplier_id = update.supplier_id
    if update.lead_time is not None:
        item.lead_time = update.lead_time
    if update.security_stock is not None:
        item.security_stock = update.security_stock
    if update.rate is not None:
        item.rate = update.rate
    if update.rack is not None:
        item.rack = update.rack
    if update.bin is not None:
        item.bin = update.bin
    if update.part_id is not None:
        item.part_id = update.part_id.strip() or None
    db.commit()
    log_success(db, username, "UPDATE", "items", item_id,
                f"Updated item: {item.item_name}")
    return {"status": "updated"}


@app.delete("/items/{item_id}", dependencies=[Depends(verify_key)])
def delete_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    item = db.query(models.Item).filter(
        models.Item.id == item_id
    ).with_for_update().first()
    if not item:
        log_failure(db, username, "DELETE", "items", item_id, "Item not found")
        raise HTTPException(status_code=404, detail="Item not found")
    inward_count = db.query(models.Inward).filter(
        models.Inward.item_id == item_id
    ).count()
    issue_count = db.query(models.Issue).filter(
        models.Issue.item_id == item_id
    ).count()
    if inward_count > 0 or issue_count > 0:
        log_failure(db, username, "DELETE", "items", item_id,
                    f"Blocked — {inward_count} inwards, {issue_count} issues")
        raise HTTPException(
            status_code=400,
            detail="Cannot delete item — it has transaction history (inwards/issues)"
        )
    db.delete(item)
    db.commit()
    log_success(db, username, "DELETE", "items", item_id,
                f"Deleted item: {item.item_name}")
    return {"status": "deleted"}


# ================= INWARDS =================
@app.get("/inwards/", dependencies=[Depends(verify_key)])
def view_inward(
    db: Session = Depends(get_db),
    limit: int = 100,
    offset: int = 0,
    item_id: Optional[int] = None,
    invoice: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
):
    query = db.query(models.Inward)
    if item_id:
        query = query.filter(models.Inward.item_id == item_id)
    if invoice:
        query = query.filter(models.Inward.invoice_number.ilike(f"%{invoice}%"))
    if from_date:
        query = query.filter(models.Inward.received_date >= from_date)
    if to_date:
        query = query.filter(models.Inward.received_date <= to_date)
    total = query.count()
    rows  = query.order_by(models.Inward.transaction_id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "rows": [
        {
            "transaction_id": r.transaction_id,
            "item_id":        r.item_id,
            "invoice_number": r.invoice_number,
            "quantity":       r.quantity,
            "rate":           float(r.rate) if r.rate else None,
            "order_date":     str(r.order_date),
            "received_date":  str(r.received_date),
        }
        for r in rows
    ]}


@app.post("/inwards/", dependencies=[Depends(verify_key)])
def record_inward(request: Request, inw: InwardCreate,
                  db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    item = db.query(models.Item).filter(
        models.Item.id == inw.item_id
    ).with_for_update().first()
    if not item:
        log_failure(db, username, "INWARD", "inwards", None,
                    f"Item not found: item_id={inw.item_id}")
        raise HTTPException(status_code=404, detail="Item not found")
    existing_invoice = db.query(models.Inward).filter(
        models.Inward.item_id       == inw.item_id,
        models.Inward.invoice_number == inw.invoice_number
    ).first()
    if existing_invoice:
        log_failure(db, username, "INWARD", "inwards", inw.item_id,
                    f"Duplicate invoice: {inw.invoice_number}")
        raise HTTPException(status_code=400,
                            detail="Duplicate invoice number for this item")
    db.add(models.Inward(**inw.model_dump()))
    if item.supplier_id:
        supp = db.query(models.Supplier).filter(
            models.Supplier.id == item.supplier_id
        ).with_for_update().first()
        if supp:
            if not supp.last_purchase_date or inw.received_date >= supp.last_purchase_date:
                supp.last_purchase_date = inw.received_date
                supp.last_purchase_rate = inw.rate
    try:
        db.commit()
        log_success(db, username, "INWARD", "inwards", inw.item_id,
                    f"Inward {inw.quantity} units of {item.item_name} | invoice={inw.invoice_number}")
        return {"status": "success"}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400,
                            detail="Duplicate invoice number or invalid reference")


@app.delete("/inwards/{transaction_id}", dependencies=[Depends(verify_key)])
def void_inward(transaction_id: int, request: Request,
                db: Session = Depends(get_db)):
    """FIX: allow admins to void inward transactions."""
    username = get_username_from_request(request, db)
    txn = db.query(models.Inward).filter(
        models.Inward.transaction_id == transaction_id
    ).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    detail = (f"Voided inward txn_id={transaction_id} item_id={txn.item_id} "
              f"qty={txn.quantity} invoice={txn.invoice_number}")
    db.delete(txn)
    db.commit()
    log_success(db, username, "VOID", "inwards", transaction_id, detail)
    return {"status": "voided"}


# ================= ISSUES =================
@app.get("/issues/", dependencies=[Depends(verify_key)])
def view_issue(
    db: Session = Depends(get_db),
    limit: int = 100,
    offset: int = 0,
    item_id: Optional[int] = None,
    issued_to: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
):
    query = db.query(models.Issue)
    if item_id:
        query = query.filter(models.Issue.item_id == item_id)
    if issued_to:
        query = query.filter(models.Issue.issued_to.ilike(f"%{issued_to}%"))
    if from_date:
        query = query.filter(models.Issue.issue_date >= from_date)
    if to_date:
        query = query.filter(models.Issue.issue_date <= to_date)
    total = query.count()
    rows  = query.order_by(models.Issue.transaction_id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "rows": [
        {
            "transaction_id": r.transaction_id,
            "item_id":        r.item_id,
            "quantity":       r.quantity,
            "issue_date":     str(r.issue_date),
            "issued_to":      r.issued_to,
            "purpose":        r.purpose,
        }
        for r in rows
    ]}


@app.post("/issues/", dependencies=[Depends(verify_key)])
def record_issue(request: Request, iss: IssueCreate,
                 bg_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    item = db.query(models.Item).filter(
        models.Item.id == iss.item_id
    ).with_for_update().first()
    if not item:
        log_failure(db, username, "ISSUE", "issues", None,
                    f"Item not found: item_id={iss.item_id}")
        raise HTTPException(status_code=404, detail="Item not found")
    if iss.quantity <= 0:
        raise HTTPException(status_code=400,
                            detail="Quantity must be greater than zero")
    stock = db.query(models.StockStatus).filter(
        models.StockStatus.item_id == iss.item_id
    ).first()
    current_qty = float(stock.current_stock) if stock else 0
    if current_qty < iss.quantity:
        log_failure(db, username, "ISSUE", "issues", iss.item_id,
                    f"Insufficient stock item_id={iss.item_id} "
                    f"requested={iss.quantity} available={current_qty}")
        raise HTTPException(status_code=400,
                            detail=f"Insufficient stock — available: {int(current_qty)}")
    db.add(models.Issue(**iss.model_dump()))
    db.commit()
    log_success(db, username, "ISSUE", "issues", iss.item_id,
                f"Issued {iss.quantity} units of {item.item_name} to {iss.issued_to}"
                + (f" for {iss.purpose}" if iss.purpose else ""))
    updated_stock = db.query(models.StockStatus).filter(
        models.StockStatus.item_id == iss.item_id
    ).first()
    if updated_stock:
        rp_map        = compute_reorder_points_bulk(db, [iss.item_id])
        reorder_point = rp_map.get(iss.item_id, 0)
        if float(updated_stock.current_stock) <= reorder_point:
            bg_tasks.add_task(send_reorder_email, [{
                "item_name":      item.item_name,
                "current_stock":  updated_stock.current_stock,
                "security_stock": item.security_stock or 0,
                "lead_time":      item.lead_time or 0,
                "reorder_point":  reorder_point,
            }])
    return {"status": "success"}


@app.delete("/issues/{transaction_id}", dependencies=[Depends(verify_key)])
def void_issue(transaction_id: int, request: Request,
               db: Session = Depends(get_db)):
    """FIX: allow admins to void issue transactions."""
    username = get_username_from_request(request, db)
    txn = db.query(models.Issue).filter(
        models.Issue.transaction_id == transaction_id
    ).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    detail = (f"Voided issue txn_id={transaction_id} item_id={txn.item_id} "
              f"qty={txn.quantity} issued_to={txn.issued_to}")
    db.delete(txn)
    db.commit()
    log_success(db, username, "VOID", "issues", transaction_id, detail)
    return {"status": "voided"}


# ================= STOCK =================
@app.get("/stock-report/", dependencies=[Depends(verify_key)])
def get_stock(db: Session = Depends(get_db)):
    """
    FIX: Uses bulk reorder point computation (2 queries total) instead of
    one DB query per item.  Also returns FINAL items so the frontend can
    show finished-goods stock.
    """
    rows     = db.query(models.StockStatus).all()
    items    = db.query(models.Item).all()
    item_map = {i.id: i for i in items}

    item_ids     = [r.item_id for r in rows]
    rp_map       = compute_reorder_points_bulk(db, item_ids)

    result = []
    for row in rows:
        rp   = rp_map.get(row.item_id, 0)
        item = item_map.get(row.item_id)
        result.append({
            "item_id":        row.item_id,
            "item_name":      row.item_name,
            "item_type":      row.item_type,
            "category":       item.category_detail.category if item and item.category_detail else None,
            "supplier":       item.supplier.name if item and item.supplier else None,
            "current_stock":  float(row.current_stock) if row.current_stock else 0,
            "security_stock": row.security_stock or 0,
            "reorder_point":  round(rp, 1),
            "needs_reorder":  float(row.current_stock or 0) <= rp,
            "rate":           float(item.rate) if item and item.rate else None,
        })
    return result


# ================= AUDIT LOGS =================
@app.get("/logs/", dependencies=[Depends(verify_key)])
def get_logs(
    db: Session = Depends(get_db),
    from_date:  Optional[date] = None,
    to_date:    Optional[date] = None,
    action:     Optional[str]  = None,
    table_name: Optional[str]  = None,
    username:   Optional[str]  = None,
    success:    Optional[bool] = None,   # NEW: filter by success/failure
    limit:  int = 100,
    offset: int = 0
):
    query = db.query(models.AuditLog)
    if from_date:
        query = query.filter(
            models.AuditLog.timestamp >= datetime.combine(from_date, datetime.min.time())
        )
    if to_date:
        query = query.filter(
            models.AuditLog.timestamp <= datetime.combine(to_date, datetime.max.time())
        )
    if action:
        query = query.filter(models.AuditLog.action == action.upper())
    if table_name:
        query = query.filter(models.AuditLog.table_name == table_name.lower())
    if username:
        query = query.filter(models.AuditLog.username.ilike(f"%{username}%"))
    if success is not None:
        query = query.filter(models.AuditLog.success == success)
    total = query.count()
    logs  = query.order_by(
        models.AuditLog.timestamp.desc()
    ).offset(offset).limit(limit).all()
    return {"total": total, "offset": offset, "limit": limit, "logs": logs}


# ================= REPORTS =================
@app.get("/report/daily", dependencies=[Depends(verify_key)])
def daily_report(report_date: date, db: Session = Depends(get_db)):
    items        = db.query(models.Item).all()
    inward_stats = db.query(
        models.Inward.item_id,
        func.sum(case((models.Inward.received_date < report_date,
                       models.Inward.quantity), else_=0)).label("before"),
        func.sum(case((models.Inward.received_date == report_date,
                       models.Inward.quantity), else_=0)).label("today")
    ).group_by(models.Inward.item_id).all()
    inwards_map = {
        row.item_id: {"before": row.before or 0, "today": row.today or 0}
        for row in inward_stats
    }
    issue_stats = db.query(
        models.Issue.item_id,
        func.sum(case((models.Issue.issue_date < report_date,
                       models.Issue.quantity), else_=0)).label("before"),
        func.sum(case((models.Issue.issue_date == report_date,
                       models.Issue.quantity), else_=0)).label("today")
    ).group_by(models.Issue.item_id).all()
    issues_map = {
        row.item_id: {"before": row.before or 0, "today": row.today or 0}
        for row in issue_stats
    }
    report = []
    for item in items:
        inward_data   = inwards_map.get(item.id, {"before": 0, "today": 0})
        issue_data    = issues_map.get(item.id,  {"before": 0, "today": 0})
        opening_stock = int(inward_data["before"]) - int(issue_data["before"])
        closing_stock = opening_stock + int(inward_data["today"]) - int(issue_data["today"])
        report.append({
            "item_id":       item.id,
            "item_name":     item.item_name,
            "item_type":     str(item.item_type),
            "date":          str(report_date),
            "opening_stock": opening_stock,
            "total_inward":  int(inward_data["today"]),
            "total_issue":   int(issue_data["today"]),
            "closing_stock": closing_stock,
        })
    return report


@app.get("/report/monthly", dependencies=[Depends(verify_key)])
def monthly_report(year: int, month: int, db: Session = Depends(get_db)):
    import calendar
    first_day    = date(year, month, 1)
    last_day     = date(year, month, calendar.monthrange(year, month)[1])
    items        = db.query(models.Item).all()
    inward_stats = db.query(
        models.Inward.item_id,
        func.sum(case((models.Inward.received_date < first_day,
                       models.Inward.quantity), else_=0)).label("before"),
        func.sum(case(
            (models.Inward.received_date >= first_day,
             case((models.Inward.received_date <= last_day,
                   models.Inward.quantity), else_=0)),
            else_=0
        )).label("month_total")
    ).group_by(models.Inward.item_id).all()
    inwards_map = {
        row.item_id: {"before": row.before or 0, "month_total": row.month_total or 0}
        for row in inward_stats
    }
    issue_stats = db.query(
        models.Issue.item_id,
        func.sum(case((models.Issue.issue_date < first_day,
                       models.Issue.quantity), else_=0)).label("before"),
        func.sum(case(
            (models.Issue.issue_date >= first_day,
             case((models.Issue.issue_date <= last_day,
                   models.Issue.quantity), else_=0)),
            else_=0
        )).label("month_total")
    ).group_by(models.Issue.item_id).all()
    issues_map = {
        row.item_id: {"before": row.before or 0, "month_total": row.month_total or 0}
        for row in issue_stats
    }
    report = []
    for item in items:
        inward_data   = inwards_map.get(item.id, {"before": 0, "month_total": 0})
        issue_data    = issues_map.get(item.id,  {"before": 0, "month_total": 0})
        opening_stock = int(inward_data["before"]) - int(issue_data["before"])
        closing_stock = (opening_stock + int(inward_data["month_total"])
                         - int(issue_data["month_total"]))
        report.append({
            "item_id":       item.id,
            "item_name":     item.item_name,
            "item_type":     str(item.item_type),
            "month":         f"{year}-{month:02d}",
            "opening_stock": opening_stock,
            "total_inward":  int(inward_data["month_total"]),
            "total_issue":   int(issue_data["month_total"]),
            "closing_stock": closing_stock,
        })
    return report


# ================= BOM =================
@app.post("/bom/", dependencies=[Depends(verify_key)])
def create_bom_entry(bom: BomCreate, request: Request,
                     db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    if not db.query(models.Item).filter(
        models.Item.id == bom.final_item_id
    ).first():
        raise HTTPException(status_code=404, detail="Final item not found")
    if not db.query(models.Item).filter(
        models.Item.id == bom.raw_item_id
    ).first():
        raise HTTPException(status_code=404, detail="Raw item not found")
    # FIX: validate substitute != primary
    if bom.final_item_id == bom.raw_item_id:
        raise HTTPException(status_code=400,
                            detail="Final and raw item cannot be the same")
    # Explicit duplicate check (UniqueConstraint alone may silently succeed on some DBs)
    existing_bom = db.query(models.Bom).filter(
        models.Bom.final_item_id == bom.final_item_id,
        models.Bom.raw_item_id   == bom.raw_item_id,
    ).first()
    if existing_bom:
        raise HTTPException(status_code=400,
                            detail="BOM entry already exists for this raw material")
    new_entry = models.Bom(
        final_item_id=bom.final_item_id,
        raw_item_id=bom.raw_item_id,
        quantity=bom.quantity
    )
    db.add(new_entry)
    try:
        db.commit()
        log_success(db, username, "CREATE", "bom", new_entry.id,
                    f"BOM: final={bom.final_item_id}, raw={bom.raw_item_id}, qty={bom.quantity}")
        return {"status": "success", "bom_id": new_entry.id}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400,
                            detail="BOM entry already exists for this raw material")


@app.delete("/bom/{bom_id}", dependencies=[Depends(verify_key)])
def delete_bom_entry(bom_id: int, request: Request,
                     db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    entry    = db.query(models.Bom).filter(models.Bom.id == bom_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="BOM entry not found")
    db.delete(entry)
    db.commit()
    log_success(db, username, "DELETE", "bom", bom_id,
                f"Deleted BOM entry id={bom_id}")
    return {"status": "deleted"}


@app.put("/bom/{bom_id}", dependencies=[Depends(verify_key)])
def update_bom_entry(bom_id: int, bom: BomUpdate, request: Request,
                     db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    entry    = db.query(models.Bom).filter(models.Bom.id == bom_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="BOM entry not found")
    entry.quantity = bom.quantity
    db.commit()
    log_success(db, username, "UPDATE", "bom", bom_id,
                f"Updated BOM id={bom_id} quantity={bom.quantity}")
    return {"status": "updated"}


@app.put("/bom/substitute/{sub_id}", dependencies=[Depends(verify_key)])
def update_bom_substitute(sub_id: int, sub: BomSubstituteUpdate,
                           request: Request, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    entry    = db.query(models.BomSubstitute).filter(
        models.BomSubstitute.id == sub_id
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Substitute not found")
    entry.quantity = sub.quantity
    db.commit()
    log_success(db, username, "UPDATE", "bom_substitutes", sub_id,
                f"Updated substitute id={sub_id} quantity={sub.quantity}")
    return {"status": "updated"}


@app.get("/bom/", dependencies=[Depends(verify_key)])
def get_full_bom(db: Session = Depends(get_db)):
    entries = db.query(models.Bom).all()
    return [
        {
            "bom_id":          e.id,
            "final_item_id":   e.final_item_id,
            "final_item_name": e.final_item.item_name if e.final_item else None,
            "raw_item_id":     e.raw_item_id,
            "raw_item_name":   e.raw_item.item_name if e.raw_item else None,
            "quantity":        e.quantity,
        }
        for e in entries
    ]


@app.get("/bom/{final_item_id}", dependencies=[Depends(verify_key)])
def get_bom(final_item_id: int, db: Session = Depends(get_db)):
    entries = db.query(models.Bom).filter(
        models.Bom.final_item_id == final_item_id
    ).all()
    result = []
    for entry in entries:
        result.append({
            "bom_id":        entry.id,
            "raw_item_id":   entry.raw_item_id,
            "raw_item_name": entry.raw_item.item_name if entry.raw_item else None,
            "quantity":      entry.quantity,
            "substitutes": [
                {
                    "id":                   s.id,
                    "substitute_item_id":   s.substitute_item_id,
                    "substitute_item_name": s.substitute_item.item_name
                                            if s.substitute_item else None,
                    "quantity":             s.quantity
                }
                for s in entry.substitutes
            ]
        })
    return result


@app.post("/bom/substitute/", dependencies=[Depends(verify_key)])
def create_bom_substitute(sub: BomSubstituteCreate, request: Request,
                           db: Session = Depends(get_db)):
    username  = get_username_from_request(request, db)
    bom_entry = db.query(models.Bom).filter(
        models.Bom.id == sub.bom_id
    ).first()
    if not bom_entry:
        raise HTTPException(status_code=404, detail="BOM entry not found")
    # FIX: prevent substitute == primary raw material
    if sub.substitute_item_id == bom_entry.raw_item_id:
        raise HTTPException(
            status_code=400,
            detail="Substitute cannot be the same as the primary raw material"
        )
    if not db.query(models.Item).filter(
        models.Item.id == sub.substitute_item_id
    ).first():
        raise HTTPException(status_code=404, detail="Substitute item not found")
    new_sub = models.BomSubstitute(
        bom_id=sub.bom_id,
        substitute_item_id=sub.substitute_item_id,
        quantity=sub.quantity
    )
    db.add(new_sub)
    try:
        db.commit()
        log_success(db, username, "CREATE", "bom_substitutes", new_sub.id,
                    f"Substitute: bom_id={sub.bom_id}, item_id={sub.substitute_item_id}, qty={sub.quantity}")
        return {"status": "success", "sub_id": new_sub.id}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400,
                            detail="Substitute already exists or invalid reference")


@app.delete("/bom/substitute/{sub_id}", dependencies=[Depends(verify_key)])
def delete_bom_substitute(sub_id: int, request: Request,
                           db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    sub      = db.query(models.BomSubstitute).filter(
        models.BomSubstitute.id == sub_id
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Substitute not found")
    detail = f"Deleted substitute: bom_id={sub.bom_id}, item_id={sub.substitute_item_id}"
    db.delete(sub)
    db.commit()
    log_success(db, username, "DELETE", "bom_substitutes", sub_id, detail)
    return {"status": "deleted"}