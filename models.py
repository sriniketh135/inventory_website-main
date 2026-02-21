from sqlalchemy import Column, Boolean, Text, Integer, String, Date, DateTime, Numeric, ForeignKey, Enum, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base
import enum
from datetime import datetime

class UserSession(Base):
    """
    Stores opaque session tokens server-side.
    The browser cookie holds only the token string — never the username.
    On logout the token row is deleted, invalidating the session even if
    someone still has the old cookie value.
    """
    __tablename__ = "user_sessions"

    token      = Column(String, primary_key=True)          # UUID4 string
    username   = Column(String, ForeignKey("users.username"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)  # Stores the hashed string
    role = Column(String, nullable=False)      # Admin, Manager, or Viewer

class ItemType(str, enum.Enum):
    RAW = "Raw"
    FINAL = "Final"

class StockStatus(Base):
    __tablename__ = "stock_status"
    item_id = Column(Integer, primary_key=True)
    item_name = Column(String)
    item_type = Column(String)
    security_stock = Column(Integer)
    total_inward = Column(Numeric)
    total_issue = Column(Numeric)
    current_stock = Column(Numeric)

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    gst_no = Column(String, unique=True)
    contact = Column(String)
    lead_time = Column(Integer)
    last_purchase_date = Column(Date)
    last_purchase_rate = Column(Numeric(10, 2))
    items = relationship("Item", back_populates="supplier")

class SpecList(Base):
    __tablename__ = "spec_list"
    # id remains the primary key (auto-incrementing integer)
    id = Column(Integer, primary_key=True, index=True) 
    # Use the existing 'spec' field for the name
    spec = Column(String, unique=True, index=True, nullable=False) 
    description = Column(String)

    items = relationship("Item", back_populates="spec_detail")


class Item(Base):
    __tablename__ = "items"
    id             = Column(Integer, primary_key=True, index=True)
    item_name      = Column(String, nullable=False)
    item_type      = Column(Enum(ItemType), nullable=False)
    spec_id        = Column(Integer, ForeignKey("spec_list.id", ondelete="RESTRICT"), nullable=True)
    supplier_id    = Column(Integer, ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=True)
    lead_time      = Column(Integer, nullable=True)
    security_stock = Column(Integer, nullable=True)
    rate           = Column(Numeric(10, 2), nullable=True)
    rack           = Column(String, nullable=True)
    bin            = Column(String, nullable=True)

    spec_detail     = relationship("SpecList", back_populates="items")
    supplier        = relationship("Supplier", back_populates="items")
    inwards         = relationship("Inward", back_populates="item")
    issues          = relationship("Issue", back_populates="item")
    bom_as_final    = relationship("Bom", foreign_keys="[Bom.final_item_id]", back_populates="final_item")
    bom_as_raw      = relationship("Bom", foreign_keys="[Bom.raw_item_id]",   back_populates="raw_item")


class Bom(Base):
    __tablename__ = "bom"
    id            = Column(Integer, primary_key=True, index=True)
    final_item_id = Column(Integer, ForeignKey("items.id", ondelete="RESTRICT"), nullable=False)
    raw_item_id   = Column(Integer, ForeignKey("items.id", ondelete="RESTRICT"), nullable=False)
    quantity      = Column(Integer, nullable=False)

    final_item  = relationship("Item", foreign_keys=[final_item_id], back_populates="bom_as_final")
    raw_item    = relationship("Item", foreign_keys=[raw_item_id],   back_populates="bom_as_raw")
    substitutes = relationship("BomSubstitute", back_populates="bom_entry", cascade="all, delete-orphan")


class BomSubstitute(Base):
    __tablename__ = "bom_substitutes"
    id                 = Column(Integer, primary_key=True, index=True)
    bom_id             = Column(Integer, ForeignKey("bom.id", ondelete="CASCADE"), nullable=False)
    substitute_item_id = Column(Integer, ForeignKey("items.id", ondelete="RESTRICT"), nullable=False)
    quantity           = Column(Integer, nullable=False)

    bom_entry       = relationship("Bom",  back_populates="substitutes")
    substitute_item = relationship("Item", foreign_keys=[substitute_item_id])

class Inward(Base):
    __tablename__ = "inwards"
    transaction_id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="SET NULL"), nullable=True)
    invoice_number = Column(String)
    quantity = Column(Integer, nullable=False)
    rate = Column(Numeric(10, 2))
    order_date = Column(Date)
    received_date = Column(Date)
    item = relationship("Item", back_populates="inwards")

class Issue(Base):
    __tablename__ = "issues"
    transaction_id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="SET NULL"), nullable=True)
    issue_date = Column(Date, nullable=False)
    quantity = Column(Integer, nullable=False)
    issued_to = Column(String)
    item = relationship("Item", back_populates="issues")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id         = Column(Integer, primary_key=True, index=True)
    timestamp  = Column(DateTime, nullable=False, index=True)
    username   = Column(String, nullable=False, index=True)
    action     = Column(String, nullable=False, index=True)
    table_name = Column(String, nullable=False, index=True)
    record_id  = Column(Integer, nullable=True)
    detail     = Column(Text, nullable=True)
    success    = Column(Boolean, default=True)