from fastapi import FastAPI, Depends, HTTPException, Security, BackgroundTasks, Request
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, ConfigDict
from typing import Optional
from decimal import Decimal
from datetime import date, datetime
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
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

def verify_key(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

# ================= FAILURE LOG SETUP =================
os.makedirs("logs", exist_ok=True)

failure_logger = logging.getLogger("failures")
failure_logger.setLevel(logging.ERROR)
handler = logging.FileHandler("logs/failures.log")
handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
failure_logger.addHandler(handler)

# ================= AUDIT HELPERS =================
def get_username_from_request(request: Request, db: Session) -> str:
    """Extract username from X-Session-Token header. Returns 'anonymous' if missing/invalid."""
    token = request.headers.get("X-Session-Token")
    if not token:
        return "anonymous"
    session = db.query(models.UserSession).filter(
        models.UserSession.token == token
    ).first()
    if not session:
        return "anonymous"
    return session.username


def log_success(db: Session, username: str, action: str, table: str, record_id: Optional[int], detail: str):
    """Write a successful action to the audit_logs DB table."""
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


def log_failure(username: str, action: str, table: str, record_id: Optional[int], detail: str):
    """Write a failed action to the failures.log file."""
    failure_logger.error(
        f"user={username} | action={action} | table={table} | record_id={record_id} | detail={detail}"
    )

# ================= SCHEMAS =================
class UserLogin(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str
    password: str
    role: str

class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    model_config = ConfigDict(from_attributes=True)

class SpecCreate(BaseModel):
    spec: str
    description: Optional[str] = None

class SupplierCreate(BaseModel):
    name: str
    contact: Optional[str] = None
    gst_no: Optional[str] = None
    last_purchase_date: Optional[date] = None
    last_purchase_rate: Decimal = Decimal("0")
    lead_time: int = 0

class ItemCreate(BaseModel):
    item_name: str
    item_type: str
    spec_id: int
    lead_time: int
    security_stock: int
    supplier_id: int
    rack: Optional[str] = ""
    bin: Optional[str] = ""

class InwardCreate(BaseModel):
    item_id: int
    invoice_number: str
    quantity: int
    rate: Decimal
    order_date: date
    received_date: date

class IssueCreate(BaseModel):
    item_id: int
    quantity: int
    issue_date: date
    issued_to: str

class LogFilter(BaseModel):
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    action: Optional[str] = None
    table_name: Optional[str] = None
    username: Optional[str] = None

# ================= HELPERS =================
def send_reorder_email(items: list):
    try:
        yag = yagmail.SMTP(
            user=os.environ.get("EMAIL_SENDER"),
            password=os.environ.get("EMAIL_PASSWORD")
        )
        body = "The following items need reordering:\n\n"
        for item in items:
            body += f"• {item.item_name} — Current: {item.current_stock} | Security: {item.security_stock}\n"
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
    user = db.query(models.User).filter(models.User.username == user_data.username).first()
    if not user:
        log_failure("unknown", "LOGIN", "users", None, f"Failed login for username: {user_data.username}")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    try:
        ph.verify(user.password, user_data.password)
        session = models.UserSession(
            token=str(uuid.uuid4()),
            username=user.username,
            created_at=datetime.utcnow()
        )
        db.add(session)
        db.commit()
        return {
            "token": session.token,
            "username": user.username,
            "role": user.role
        }
    except VerifyMismatchError:
        log_failure(user_data.username, "LOGIN", "users", None, "Wrong password")
        raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/validate-session/{token}")
def validate_session(token: str, db: Session = Depends(get_db)):
    session = db.query(models.UserSession).filter(
        models.UserSession.token == token
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
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

@app.get("/users/", response_model=list[UserResponse], dependencies=[Depends(verify_key)])
def list_users(db: Session = Depends(get_db)):
    return db.query(models.User).all()

@app.post("/users/", response_model=UserResponse, dependencies=[Depends(verify_key)])
def create_user(request: Request, user: UserCreate, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    hashed = ph.hash(user.password)
    new_user = models.User(username=user.username, password=hashed, role=user.role)
    db.add(new_user)
    try:
        db.commit()
        db.refresh(new_user)
        log_success(db, username, "CREATE", "users", new_user.id, f"Created user: {user.username}")
        return new_user
    except IntegrityError:
        db.rollback()
        log_failure(username, "CREATE", "users", None, f"Duplicate username: {user.username}")
        raise HTTPException(status_code=400, detail="Username already exists")

@app.delete("/users/{user_id}", dependencies=[Depends(verify_key)])
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        log_failure(username, "DELETE", "users", user_id, "User not found")
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    log_success(db, username, "DELETE", "users", user_id, f"Deleted user: {user.username}")
    return {"status": "deleted"}

# ================= SPECS =================
@app.get("/specs/")
def get_specs(db: Session = Depends(get_db)):
    return db.query(models.SpecList).all()

@app.post("/specs/", dependencies=[Depends(verify_key)])
def create_spec(request: Request, spec: SpecCreate, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    new_spec = models.SpecList(spec=spec.spec, description=spec.description)
    db.add(new_spec)
    try:
        db.commit()
        log_success(db, username, "CREATE", "spec_list", new_spec.id, f"Created spec: {spec.spec}")
        return {"status": "success"}
    except IntegrityError:
        db.rollback()
        log_failure(username, "CREATE", "spec_list", None, f"Duplicate spec: {spec.spec}")
        raise HTTPException(status_code=400, detail="Spec already exists")

@app.delete("/specs/{spec_id}", dependencies=[Depends(verify_key)])
def delete_spec(spec_id: int, request: Request, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    spec = db.query(models.SpecList).filter(models.SpecList.id == spec_id).with_for_update().first()
    if not spec:
        log_failure(username, "DELETE", "spec_list", spec_id, "Spec not found")
        raise HTTPException(status_code=404, detail="Spec not found")
    item_count = db.query(models.Item).filter(models.Item.spec_id == spec_id).count()
    if item_count > 0:
        log_failure(username, "DELETE", "spec_list", spec_id,
                    f"Blocked delete — {item_count} item(s) using spec: {spec.spec}")
        raise HTTPException(status_code=400, detail=f"Cannot delete spec — {item_count} item(s) are using it")
    db.delete(spec)
    db.commit()
    log_success(db, username, "DELETE", "spec_list", spec_id, f"Deleted spec: {spec.spec}")
    return {"status": "deleted"}

# ================= SUPPLIERS =================
@app.get("/suppliers/")
def get_suppliers(db: Session = Depends(get_db)):
    return db.query(models.Supplier).all()

@app.post("/suppliers/", dependencies=[Depends(verify_key)])
def create_supplier(request: Request, supp: SupplierCreate, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    new_supp = models.Supplier(**supp.model_dump())
    db.add(new_supp)
    try:
        db.commit()
        log_success(db, username, "CREATE", "suppliers", new_supp.id, f"Created supplier: {supp.name}")
        return {"status": "success"}
    except IntegrityError:
        db.rollback()
        log_failure(username, "CREATE", "suppliers", None, f"Duplicate supplier: {supp.name}")
        raise HTTPException(status_code=400, detail="Supplier already exists")

@app.delete("/suppliers/{supplier_id}", dependencies=[Depends(verify_key)])
def delete_supplier(supplier_id: int, request: Request, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == supplier_id
    ).with_for_update().first()
    if not supplier:
        log_failure(username, "DELETE", "suppliers", supplier_id, "Supplier not found")
        raise HTTPException(status_code=404, detail="Supplier not found")
    item_count = db.query(models.Item).filter(models.Item.supplier_id == supplier_id).count()
    if item_count > 0:
        log_failure(username, "DELETE", "suppliers", supplier_id,
                    f"Blocked delete — {item_count} item(s) using supplier: {supplier.name}")
        raise HTTPException(status_code=400, detail=f"Cannot delete supplier — {item_count} item(s) are using it")
    db.delete(supplier)
    db.commit()
    log_success(db, username, "DELETE", "suppliers", supplier_id, f"Deleted supplier: {supplier.name}")
    return {"status": "deleted"}

# ================= ITEMS =================
@app.get("/items/")
def get_items(db: Session = Depends(get_db)):
    return db.query(models.Item).all()

@app.post("/items/", dependencies=[Depends(verify_key)])
def create_item(request: Request, item: ItemCreate, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    spec = db.query(models.SpecList).filter(models.SpecList.id == item.spec_id).first()
    if not spec:
        log_failure(username, "CREATE", "items", None, f"Invalid spec_id: {item.spec_id}")
        raise HTTPException(status_code=404, detail="Spec not found")
    supplier = db.query(models.Supplier).filter(models.Supplier.id == item.supplier_id).first()
    if not supplier:
        log_failure(username, "CREATE", "items", None, f"Invalid supplier_id: {item.supplier_id}")
        raise HTTPException(status_code=404, detail="Supplier not found")
    new_item = models.Item(**item.model_dump())
    db.add(new_item)
    try:
        db.commit()
        log_success(db, username, "CREATE", "items", new_item.id, f"Created item: {item.item_name}")
        return {"status": "success"}
    except IntegrityError:
        db.rollback()
        log_failure(username, "CREATE", "items", None, f"Integrity error creating item: {item.item_name}")
        raise HTTPException(status_code=400, detail="Item already exists or invalid reference")

@app.delete("/items/{item_id}", dependencies=[Depends(verify_key)])
def delete_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    item = db.query(models.Item).filter(models.Item.id == item_id).with_for_update().first()
    if not item:
        log_failure(username, "DELETE", "items", item_id, "Item not found")
        raise HTTPException(status_code=404, detail="Item not found")
    inward_count = db.query(models.Inward).filter(models.Inward.item_id == item_id).count()
    issue_count = db.query(models.Issue).filter(models.Issue.item_id == item_id).count()
    if inward_count > 0 or issue_count > 0:
        log_failure(username, "DELETE", "items", item_id,
                    f"Blocked delete — item {item.item_name} has {inward_count} inwards, {issue_count} issues")
        raise HTTPException(status_code=400, detail="Cannot delete item — it has transaction history (inwards/issues)")
    db.delete(item)
    db.commit()
    log_success(db, username, "DELETE", "items", item_id, f"Deleted item: {item.item_name}")
    return {"status": "deleted"}

# ================= INWARDS =================
@app.get("/inwards/", dependencies=[Depends(verify_key)])
def view_inward(db: Session = Depends(get_db)):
    return db.query(models.Inward).all()

@app.post("/inwards/", dependencies=[Depends(verify_key)])
def record_inward(request: Request, inw: InwardCreate, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)
    item = db.query(models.Item).filter(models.Item.id == inw.item_id).with_for_update().first()
    if not item:
        log_failure(username, "INWARD", "inwards", None, f"Item not found: item_id={inw.item_id}")
        raise HTTPException(status_code=404, detail="Item not found")

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
                    f"Inward {inw.quantity} units of item_id={inw.item_id} | invoice={inw.invoice_number}")
        return {"status": "success"}
    except IntegrityError:
        db.rollback()
        log_failure(username, "INWARD", "inwards", inw.item_id,
                    f"Duplicate invoice: {inw.invoice_number}")
        raise HTTPException(status_code=400, detail="Duplicate invoice number or invalid reference")

# ================= ISSUES =================
@app.get("/issues/", dependencies=[Depends(verify_key)])
def view_issue(db: Session = Depends(get_db)):
    return db.query(models.Issue).all()

@app.post("/issues/", dependencies=[Depends(verify_key)])
def record_issue(request: Request, iss: IssueCreate, bg_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    username = get_username_from_request(request, db)

    item = db.query(models.Item).filter(models.Item.id == iss.item_id).with_for_update().first()
    if not item:
        log_failure(username, "ISSUE", "issues", None, f"Item not found: item_id={iss.item_id}")
        raise HTTPException(status_code=404, detail="Item not found")

    if iss.quantity <= 0:
        log_failure(username, "ISSUE", "issues", iss.item_id,
                    f"Invalid quantity {iss.quantity} for item_id={iss.item_id}")
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    stock = db.query(models.StockStatus).filter(
        models.StockStatus.item_id == iss.item_id
    ).first()
    current_qty = stock.current_stock if stock else 0

    if current_qty < iss.quantity:
        db.rollback()
        log_failure(username, "ISSUE", "issues", iss.item_id,
                    f"Insufficient stock for item_id={iss.item_id} | requested={iss.quantity} | available={current_qty}")
        raise HTTPException(status_code=400, detail="Insufficient stock for this issue")

    db.add(models.Issue(**iss.model_dump()))
    db.commit()

    log_success(db, username, "ISSUE", "issues", iss.item_id,
                f"Issued {iss.quantity} units of item_id={iss.item_id} to {iss.issued_to}")

    updated_stock = db.query(models.StockStatus).filter(
        models.StockStatus.item_id == iss.item_id
    ).first()
    if updated_stock and updated_stock.current_stock <= updated_stock.security_stock:
        bg_tasks.add_task(send_reorder_email, [updated_stock])

    return {"status": "success"}

# ================= STOCK =================
@app.get("/stock-report/")
def get_stock(db: Session = Depends(get_db)):
    return db.query(models.StockStatus).all()

# ================= AUDIT LOGS =================
@app.get("/logs/", dependencies=[Depends(verify_key)])
def get_logs(
    db: Session = Depends(get_db),
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    action: Optional[str] = None,
    table_name: Optional[str] = None,
    username: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    query = db.query(models.AuditLog)

    if from_date:
        query = query.filter(models.AuditLog.timestamp >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        query = query.filter(models.AuditLog.timestamp <= datetime.combine(to_date, datetime.max.time()))
    if action:
        query = query.filter(models.AuditLog.action == action.upper())
    if table_name:
        query = query.filter(models.AuditLog.table_name == table_name.lower())
    if username:
        query = query.filter(models.AuditLog.username.ilike(f"%{username}%"))

    total = query.count()
    logs = query.order_by(models.AuditLog.timestamp.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "logs": logs
    }

# ================= REPORTS =================
@app.get("/report/daily", dependencies=[Depends(verify_key)])
def daily_report(report_date: date, db: Session = Depends(get_db)):
    items = db.query(models.Item).all()

    inward_stats = db.query(
        models.Inward.item_id,
        func.sum(case((models.Inward.received_date < report_date, models.Inward.quantity), else_=0)).label('before'),
        func.sum(case((models.Inward.received_date == report_date, models.Inward.quantity), else_=0)).label('today')
    ).group_by(models.Inward.item_id).all()
    inwards_map = {row.item_id: {"before": row.before or 0, "today": row.today or 0} for row in inward_stats}

    issue_stats = db.query(
        models.Issue.item_id,
        func.sum(case((models.Issue.issue_date < report_date, models.Issue.quantity), else_=0)).label('before'),
        func.sum(case((models.Issue.issue_date == report_date, models.Issue.quantity), else_=0)).label('today')
    ).group_by(models.Issue.item_id).all()
    issues_map = {row.item_id: {"before": row.before or 0, "today": row.today or 0} for row in issue_stats}

    report = []
    for item in items:
        inward_data = inwards_map.get(item.id, {"before": 0, "today": 0})
        issue_data = issues_map.get(item.id, {"before": 0, "today": 0})
        opening_stock = inward_data["before"] - issue_data["before"]
        closing_stock = opening_stock + inward_data["today"] - issue_data["today"]
        report.append({
            "item_id": item.id,
            "item_name": item.item_name,
            "date": str(report_date),
            "opening_stock": opening_stock,
            "total_inward": inward_data["today"],
            "total_issue": issue_data["today"],
            "closing_stock": closing_stock
        })
    return report

@app.get("/report/monthly", dependencies=[Depends(verify_key)])
def monthly_report(year: int, month: int, db: Session = Depends(get_db)):
    from datetime import date as dt
    import calendar

    first_day = dt(year, month, 1)
    last_day = dt(year, month, calendar.monthrange(year, month)[1])

    items = db.query(models.Item).all()

    inward_stats = db.query(
        models.Inward.item_id,
        func.sum(case((models.Inward.received_date < first_day, models.Inward.quantity), else_=0)).label('before'),
        func.sum(case(
            (models.Inward.received_date >= first_day,
             case((models.Inward.received_date <= last_day, models.Inward.quantity), else_=0)),
            else_=0
        )).label('month_total')
    ).group_by(models.Inward.item_id).all()
    inwards_map = {row.item_id: {"before": row.before or 0, "month_total": row.month_total or 0} for row in inward_stats}

    issue_stats = db.query(
        models.Issue.item_id,
        func.sum(case((models.Issue.issue_date < first_day, models.Issue.quantity), else_=0)).label('before'),
        func.sum(case(
            (models.Issue.issue_date >= first_day,
             case((models.Issue.issue_date <= last_day, models.Issue.quantity), else_=0)),
            else_=0
        )).label('month_total')
    ).group_by(models.Issue.item_id).all()
    issues_map = {row.item_id: {"before": row.before or 0, "month_total": row.month_total or 0} for row in issue_stats}

    report = []
    for item in items:
        inward_data = inwards_map.get(item.id, {"before": 0, "month_total": 0})
        issue_data = issues_map.get(item.id, {"before": 0, "month_total": 0})
        opening_stock = inward_data["before"] - issue_data["before"]
        closing_stock = opening_stock + inward_data["month_total"] - issue_data["month_total"]
        report.append({
            "item_id": item.id,
            "item_name": item.item_name,
            "month": f"{year}-{month:02d}",
            "opening_stock": opening_stock,
            "total_inward": inward_data["month_total"],
            "total_issue": issue_data["month_total"],
            "closing_stock": closing_stock
        })
    return report