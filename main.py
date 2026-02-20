from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from typing import Optional
from decimal import Decimal
from datetime import date, datetime
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import os
import uuid
from dotenv import load_dotenv
load_dotenv()

import models
from database import engine, get_db

# ================= APP & SECURITY =================
ph = PasswordHasher()
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Industrial ERP")

# --- API Key Auth ---
API_KEY = os.environ.get("ERP_API_KEY")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

def verify_key(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

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
    last_purchase_rate: Decimal = 0
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

# ================= AUTH & USERS =================

# --- FIX: Public routes (no API key needed) ---
@app.post("/login")
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == user_data.username).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    try:
        ph.verify(user.password, user_data.password)
        # FIX: Create an opaque session token instead of returning username as cookie value
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
        raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/validate-session/{token}")
def validate_session(token: str, db: Session = Depends(get_db)):
    """Validates an opaque session token and returns user info."""
    session = db.query(models.UserSession).filter(
        models.UserSession.token == token
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    user = db.query(models.User).filter(
        models.User.username == session.username
    ).first()
    if not user:
        # Orphaned session — clean it up
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=404, detail="User not found")
    return {"username": user.username, "role": user.role}

# --- FIX: All mutating routes protected by API key ---
@app.delete("/logout/{token}", dependencies=[Depends(verify_key)])
def logout_session(token: str, db: Session = Depends(get_db)):
    """Deletes a session token from the DB, invalidating it server-side."""
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
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    hashed = ph.hash(user.password)
    new_user = models.User(username=user.username, password=hashed, role=user.role)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.delete("/users/{user_id}", dependencies=[Depends(verify_key)])
def delete_user(user_id: int, db: Session = Depends(get_db)):
    u = db.query(models.User).filter(models.User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(u)
    db.commit()
    return {"status": "deleted"}

# ================= INVENTORY CORE =================
@app.get("/stock-report/")
def get_stock(db: Session = Depends(get_db)):
    return db.query(models.StockStatus).all()

@app.get("/items/")
def get_items(db: Session = Depends(get_db)):
    return db.query(models.Item).all()

@app.post("/items/", dependencies=[Depends(verify_key)])
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    db.add(models.Item(**item.model_dump()))
    db.commit()
    return {"status": "success"}

@app.delete("/items/{item_id}", dependencies=[Depends(verify_key)])
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return {"status": "deleted"}

@app.get("/suppliers/")
def get_suppliers(db: Session = Depends(get_db)):
    return db.query(models.Supplier).all()

@app.post("/suppliers/", dependencies=[Depends(verify_key)])
def create_supplier(supp: SupplierCreate, db: Session = Depends(get_db)):
    db.add(models.Supplier(**supp.model_dump()))
    db.commit()
    return {"status": "success"}

@app.get("/specs/")
def get_specs(db: Session = Depends(get_db)):
    return db.query(models.SpecList).all()

@app.post("/specs/", dependencies=[Depends(verify_key)])
def create_spec(spec: SpecCreate, db: Session = Depends(get_db)):
    db.add(models.SpecList(spec=spec.spec, description=spec.description))
    db.commit()
    return {"status": "success"}

@app.delete("/specs/{spec_id}", dependencies=[Depends(verify_key)])
def delete_spec(spec_id: int, db: Session = Depends(get_db)):
    speci = db.query(models.SpecList).filter(models.SpecList.id == spec_id).first()
    if not speci:
        raise HTTPException(status_code=404, detail="Spec not found")
    db.delete(speci)
    db.commit()
    return {"status": "deleted"}

# ================= TRANSACTIONS =================
@app.post("/inwards/", dependencies=[Depends(verify_key)])
def record_inward(inw: InwardCreate, db: Session = Depends(get_db)):
    db.add(models.Inward(**inw.model_dump()))

    # Auto-update supplier ledger
    item = db.query(models.Item).filter(models.Item.id == inw.item_id).first()
    if item and item.supplier_id:
        supp = db.query(models.Supplier).filter(
            models.Supplier.id == item.supplier_id
        ).first()
        if supp:
            supp.last_purchase_date = inw.received_date
            supp.last_purchase_rate = inw.rate

    db.commit()
    return {"status": "success"}

@app.get("/inwards/", dependencies=[Depends(verify_key)])
def view_inward(db: Session = Depends(get_db)):
    return db.query(models.Inward).all()

@app.post("/issues/", dependencies=[Depends(verify_key)])
def record_issue(iss: IssueCreate, db: Session = Depends(get_db)):
    stock = db.query(models.StockStatus).filter(
        models.StockStatus.item_id == iss.item_id
    ).first()
    if not stock:
        raise HTTPException(status_code=400, detail="No item found with this ID")
    if stock.current_stock < iss.quantity:
        # FIX: Use 400 (Bad Request) not 401 (Unauthorized) for business logic errors
        raise HTTPException(status_code=400, detail="Insufficient stock for this issue")
    db.add(models.Issue(**iss.model_dump()))
    db.commit()
    return {"status": "success"}

@app.get("/issues/", dependencies=[Depends(verify_key)])
def view_issue(db: Session = Depends(get_db)):
    return db.query(models.Issue).all()