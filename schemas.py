from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime

# ==========================================
# 1. USER / AUTH SCHEMAS
# ==========================================
class UserBase(BaseModel):
    email: EmailStr

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    id: str = Field(alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda v: v.isoformat()}

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

# ==========================================
# 2. SHIPMENT SCHEMAS
# ==========================================
class ShipmentBase(BaseModel):
    bl_number: str = Field(..., description="Bill of Lading number")
    container_id: str = Field(..., description="Container ID/Number")
    origin: str
    destination: str
    current_status: str = Field("In Transit", description="e.g., In Transit, Delayed, Customs Hold, Delivered")

class ShipmentCreate(ShipmentBase):
    pass

class ShipmentResponse(ShipmentBase):
    id: str = Field(alias="_id")
    created_at: datetime

    class Config:
        populate_by_name = True

# ==========================================
# 3. EXCEPTION RECORD SCHEMAS
# ==========================================
class ExceptionBase(BaseModel):
    shipment_id: str = Field(..., description="ID of the associated shipment in MongoDB")
    exception_type: str = Field(..., description="e.g., Shipment delay, Cargo damage, Reroute, Customs hold, Doc mismatch")
    severity: str = Field("Medium", description="Low, Medium, High, Critical")
    description: str
    delay_days: Optional[float] = Field(0.0, description="Estimated delay in days (if applicable)")

class ExceptionCreate(ExceptionBase):
    pass

class ExceptionResponse(ExceptionBase):
    id: str = Field(alias="_id")
    estimated_cost: float = Field(0.0, description="Estimated cost impact in USD")
    status_updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow, description="Last status update timestamp")
    escalation_path: Optional[str] = Field(None, description="Escalation path if severity is Critical")
    ai_draft_message: Optional[str] = None
    ai_recommendations: Optional[List[str]] = None
    status: str = Field("Open", description="Open, Resolving, Resolved, Escalated")
    created_at: datetime

    class Config:
        populate_by_name = True

# ==========================================
# 4. ESCALATION SCHEMAS
# ==========================================
class EscalationRequest(BaseModel):
    exception_id: Optional[str] = None
    shipment_id: Optional[str] = None
    carrier_name: Optional[str] = "Carrier Operations & Dispatch Management"
    urgency: Optional[str] = "Critical"
    additional_notes: Optional[str] = None

class EscalationResponse(BaseModel):
    carrier_name: str
    subject: str
    email_draft: str
    exception_id: Optional[str] = None
    shipment_id: Optional[str] = None
    severity: str
    escalated_at: datetime = Field(default_factory=datetime.utcnow)

