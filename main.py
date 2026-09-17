import os
import re
import hashlib
import jwt
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
from bson import ObjectId
from dotenv import load_dotenv

# Import local modules
from database import db, test_connection
from schemas import (
    UserCreate,
    UserResponse,
    Token,
    ShipmentCreate,
    ShipmentResponse,
    ExceptionCreate,
    ExceptionResponse,
    EscalationRequest,
    EscalationResponse
)
from graph import compiled_graph, draft_carrier_escalation_email

load_dotenv()

app = FastAPI(title="Cargo Exception Manager API")

# Configure CORS so our future frontend can talk to the backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allows all origins for local testing/demo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def serve_index():
    return FileResponse("index.html")

# JWT Secret & Constants
JWT_SECRET = os.getenv("JWT_SECRET", "super_secret_fallback_key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Helper functions for BSON ObjectId to String conversions
def clean_mongo_doc(doc):
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

# Password Hashing Helper (SHA-256 for simple system-agnostic setup)
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# Startup Event to test DB Connection
@app.on_event("startup")
async def startup_db_client():
    await test_connection()

# ==========================================
# 1. AUTHENTICATION ENDPOINTS
# ==========================================

@app.post("/api/auth/signup", response_model=UserResponse)
async def signup(user: UserCreate):
    # Check if user already exists
    existing_user = await db.users.find_one({"email": user.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Hash password and insert user
    hashed_pass = hash_password(user.password)
    new_user = {
        "email": user.email,
        "password": hashed_pass,
        "created_at": datetime.utcnow()
    }
    result = await db.users.insert_one(new_user)
    
    # Retrieve and return created user
    created_user = await db.users.find_one({"_id": result.inserted_id})
    return clean_mongo_doc(created_user)


@app.post("/api/auth/login")
async def login(user: UserCreate):
    db_user = await db.users.find_one({"email": user.email})
    if not db_user or db_user["password"] != hash_password(user.password):
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    
    # Generate JWT Token
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": user.email, "exp": expire}
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)
    
    return {"access_token": encoded_jwt, "token_type": "bearer"}


async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        return email
    except jwt.PyJWTError:
        raise credentials_exception

# ==========================================
# 2. SHIPMENT ENDPOINTS
# ==========================================

@app.post("/api/shipments", response_model=ShipmentResponse)
async def create_shipment(shipment: ShipmentCreate):
    new_ship = shipment.dict()
    new_ship["created_at"] = datetime.utcnow()
    
    result = await db.shipments.insert_one(new_ship)
    inserted = await db.shipments.find_one({"_id": result.inserted_id})
    return clean_mongo_doc(inserted)


@app.get("/api/shipments", response_model=List[ShipmentResponse])
async def list_shipments():
    cursor = db.shipments.find().sort("created_at", -1)
    shipments = await cursor.to_list(length=100)
    return [clean_mongo_doc(s) for s in shipments]

# ==========================================
# 3. EXCEPTION HANDLING ENDPOINTS (LANGGRAPH TRIGGER)
# ==========================================

@app.post("/api/exceptions", response_model=ExceptionResponse)
async def process_exception(payload: ExceptionCreate):
    # 1. Verify associated shipment exists
    try:
        ship_id = ObjectId(payload.shipment_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shipment ID format")
        
    shipment = await db.shipments.find_one({"_id": ship_id})
    if not shipment:
        raise HTTPException(status_code=404, detail="Associated shipment not found")
    
    # Calculate delay_days & Cost-Impact Logic
    delay_days = payload.delay_days or 0.0
    if delay_days <= 0.0:
        match_days = re.search(r'(\d+(?:\.\d+)?)\s*(?:day|days|d\b)', payload.description, re.IGNORECASE)
        match_hrs = re.search(r'(\d+(?:\.\d+)?)\s*(?:hour|hours|hr|hrs|h\b)', payload.description, re.IGNORECASE)
        if match_days:
            delay_days = float(match_days.group(1))
        elif match_hrs:
            delay_days = round(float(match_hrs.group(1)) / 24.0, 2)
        elif payload.exception_type.strip().lower() in ["shipment delay", "delay"] or "delay" in payload.exception_type.lower():
            delay_days = 1.0

    # If exception_type is 'Shipment delay', estimate cost as (delay_days * 150)
    estimated_cost = 0.0
    if payload.exception_type.strip().lower() in ["shipment delay", "delay"] or "delay" in payload.exception_type.lower():
        estimated_cost = float(delay_days * 150.0)

    # 2. Run our compiled LangGraph pipeline using the input
    initial_state = {
        "shipment_id": payload.shipment_id,
        "exception_type": payload.exception_type,
        "description": payload.description,
        "severity": payload.severity,
        "delay_days": delay_days,
        "estimated_cost": estimated_cost,
        "ai_draft_message": None,
        "ai_recommendations": None,
        "escalation_path": None,
        "error": None
    }
    
    try:
        final_state = await compiled_graph.ainvoke(initial_state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Agent error during processing: {str(e)}")
    
    # 3. Save the results (including agent-generated values) to MongoDB
    now = datetime.utcnow()
    exception_record = {
        "shipment_id": payload.shipment_id,
        "exception_type": payload.exception_type,
        "severity": final_state.get("severity", payload.severity),
        "description": payload.description,
        "delay_days": delay_days,
        "estimated_cost": estimated_cost,
        "escalation_path": final_state.get("escalation_path"),
        "ai_draft_message": final_state.get("ai_draft_message"),
        "ai_recommendations": final_state.get("ai_recommendations"),
        "status": "Open",
        "status_updated_at": now,
        "created_at": now
    }
    
    result = await db.exceptions.insert_one(exception_record)
    
    # Update the parent shipment's status to reflect the new exception
    await db.shipments.update_one(
        {"_id": ship_id},
        {"$set": {"current_status": f"Exception: {payload.exception_type}"}}
    )
    
    saved_record = await db.exceptions.find_one({"_id": result.inserted_id})
    return clean_mongo_doc(saved_record)


@app.get("/api/exceptions", response_model=List[ExceptionResponse])
async def list_exceptions():
    cursor = db.exceptions.find().sort("created_at", -1)
    records = await cursor.to_list(length=100)
    return [clean_mongo_doc(r) for r in records]


# ==========================================
# 4. CARRIER ESCALATION ENDPOINT
# ==========================================

@app.post("/api/exceptions/escalate", response_model=EscalationResponse)
async def escalate_exception(payload: EscalationRequest):
    """Drafts a formal escalation email to a carrier regarding a critical shipment exception."""
    exception_doc = None
    shipment_doc = None
    
    # 1. Fetch exception doc if exception_id is provided
    if payload.exception_id:
        try:
            exc_oid = ObjectId(payload.exception_id)
            exception_doc = await db.exceptions.find_one({"_id": exc_oid})
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid exception ID format")
            
        if not exception_doc:
            raise HTTPException(status_code=404, detail="Exception record not found")
        
        # Look up associated shipment
        if "shipment_id" in exception_doc and exception_doc["shipment_id"]:
            try:
                ship_oid = ObjectId(exception_doc["shipment_id"])
                shipment_doc = await db.shipments.find_one({"_id": ship_oid})
            except Exception:
                pass

    # 2. Fetch shipment doc if shipment_id is provided and not already found
    if not shipment_doc and payload.shipment_id:
        try:
            ship_oid = ObjectId(payload.shipment_id)
            shipment_doc = await db.shipments.find_one({"_id": ship_oid})
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid shipment ID format")
            
        if not shipment_doc:
            raise HTTPException(status_code=404, detail="Shipment record not found")

    if not shipment_doc and not exception_doc:
        raise HTTPException(status_code=400, detail="Must provide a valid exception_id or shipment_id")

    shipment_info = shipment_doc or {}
    exception_info = exception_doc or {
        "exception_type": "Shipment Exception Escalation",
        "severity": payload.urgency or "Critical",
        "description": payload.additional_notes or "Immediate carrier escalation requested.",
        "estimated_cost": 0.0
    }

    # 3. Generate carrier escalation email draft using Gemini agent
    carrier_name = payload.carrier_name or "Carrier Operations & Dispatch Management"
    urgency = payload.urgency or exception_info.get("severity", "Critical")
    
    escalation_result = await draft_carrier_escalation_email(
        shipment_info=shipment_info,
        exception_info=exception_info,
        carrier_name=carrier_name,
        urgency=urgency,
        additional_notes=payload.additional_notes
    )

    # 4. Update the exception record status to 'Escalated' if applicable
    now = datetime.utcnow()
    if exception_doc:
        await db.exceptions.update_one(
            {"_id": exception_doc["_id"]},
            {"$set": {"status": "Escalated", "status_updated_at": now}}
        )

    return EscalationResponse(
        carrier_name=escalation_result["carrier_name"],
        subject=escalation_result["subject"],
        email_draft=escalation_result["email_draft"],
        exception_id=str(exception_doc["_id"]) if exception_doc and "_id" in exception_doc else payload.exception_id,
        shipment_id=str(shipment_doc["_id"]) if shipment_doc and "_id" in shipment_doc else payload.shipment_id,
        severity=escalation_result.get("severity", urgency),
        escalated_at=now
    )

