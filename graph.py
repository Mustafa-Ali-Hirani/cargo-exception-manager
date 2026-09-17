import os
from typing import TypedDict, List, Optional
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from google import genai
from dotenv import load_dotenv

# Load variables
load_dotenv()

# Initialize the modern stable Gemini Client (automatically reads GEMINI_API_KEY from .env)
client = genai.Client()

# ==========================================
# 1. LANGGRAPH STATE DEFINITION
# ==========================================
class AgentState(TypedDict):
    shipment_id: str
    exception_type: str
    description: str
    severity: str
    delay_days: Optional[float]
    estimated_cost: Optional[float]
    ai_draft_message: Optional[str]
    ai_recommendations: Optional[List[str]]
    escalation_path: Optional[str]
    error: Optional[str]

# ==========================================
# 2. AGENT NODES
# ==========================================

async def classify_exception_node(state: AgentState) -> AgentState:
    """Node 1: Classifies severity using stable gemini-3.5-flash-lite"""
    prompt = f"""
    You are an expert logistics coordinator. Analyze the following shipment exception and classify its severity level.
    Only output one of these values: "Low", "Medium", "High", or "Critical". Do not write any other text.
    
    Exception Type: {state['exception_type']}
    Description: {state['description']}
    """
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
        )
        severity = response.text.strip()
        if severity not in ["Low", "Medium", "High", "Critical"]:
            severity = "Medium"
        state['severity'] = severity
    except Exception as e:
        state['error'] = f"Classification error: {str(e)}"
        state['severity'] = "Medium"
    return state


async def draft_message_node(state: AgentState) -> AgentState:
    """Node 2: Drafts professional notification using stable gemini-3.5-flash-lite"""
    prompt = f"""
    You are an AI Cargo Exception Coordinator at Powerhouse Pvt. Ltd. 
    Write a highly professional, polite update email to the importer client regarding an exception.
    Keep the tone reassuring but transparent. Explain the situation and state that we are actively handling it.
    
    Exception Type: {state['exception_type']}
    Severity: {state['severity']}
    Description: {state['description']}
    
    Include subject line placeholder: [Subject: Urgent Shipment Update - {state['exception_type']}]
    """
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
        )
        state['ai_draft_message'] = response.text.strip()
    except Exception as e:
        state['ai_draft_message'] = f"Failed to draft message automatically. Error: {str(e)}"
    return state


async def recommend_actions_node(state: AgentState) -> AgentState:
    """Node 3: Recommends next steps using stable gemini-3.5-flash-lite with SOP Compliance and Critical Escalation Path"""
    prompt = f"""
    You are a senior supply chain and logistics compliance consultant. Recommend actionable operational next-steps for the internal logistics team to resolve this issue.
    Provide the output strictly as a bulleted list of 3-4 items. Do not write any introduction or conclusion.
    
    Exception Type: {state['exception_type']}
    Severity: {state['severity']}
    Description: {state['description']}
    
    SOP Compliance Instructions:
    - If the exception is a Customs Hold or Delay exceeding 48 hours, include a note in the recommendations about compliance risks.
    - If the severity is "Critical", include an escalation path (e.g., immediate escalation to executive management, port authority liaison, and senior carrier dispatch leadership) in the recommendations.
    """
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
        )
        raw_lines = response.text.strip().split("\n")
        recommendations = [line.lstrip("*- ").strip() for line in raw_lines if line.strip()]
        
        # If severity is Critical, ensure an explicit escalation path is registered in state
        if state.get('severity') == "Critical":
            escalation_msg = "Critical Escalation Path: Immediately notify Senior Operations Management, Carrier Executive Dispatch, and Legal/Compliance Officers."
            state['escalation_path'] = escalation_msg
            if not any("escalat" in r.lower() for r in recommendations):
                recommendations.insert(0, escalation_msg)
        else:
            state['escalation_path'] = None
            
        state['ai_recommendations'] = recommendations
    except Exception as e:
        fallback_recs = ["Assess carrier options manually", "Contact destination terminal", "Alert customs broker"]
        if state.get('severity') == "Critical":
            fallback_recs.insert(0, "Critical Escalation Path: Immediately escalate to Senior Operations Management and Carrier Executive Dispatch.")
            state['escalation_path'] = "Critical Escalation Path: Immediately escalate to Senior Operations Management and Carrier Executive Dispatch."
        state['ai_recommendations'] = fallback_recs
    return state

# ==========================================
# 3. CARRIER ESCALATION AGENT
# ==========================================
async def draft_carrier_escalation_email(
    shipment_info: dict,
    exception_info: dict,
    carrier_name: str = "Carrier Operations & Dispatch Management",
    urgency: str = "Critical",
    additional_notes: Optional[str] = None
) -> dict:
    """Drafts a formal, urgent carrier escalation email using Gemini."""
    prompt = f"""
    You are a Senior Logistics Escalations Director at Powerhouse Pvt. Ltd.
    Draft a formal, urgent, and professional Carrier Escalation Letter / Email to {carrier_name}.
    
    Shipment Information:
    - B/L Number: {shipment_info.get('bl_number', 'N/A')}
    - Container ID: {shipment_info.get('container_id', 'N/A')}
    - Route: {shipment_info.get('origin', 'N/A')} to {shipment_info.get('destination', 'N/A')}
    - Current Status: {shipment_info.get('current_status', 'N/A')}
    
    Exception Details:
    - Exception Type: {exception_info.get('exception_type', 'N/A')}
    - Severity Level: {exception_info.get('severity', urgency)}
    - Issue Description: {exception_info.get('description', 'N/A')}
    - Estimated Financial Cost: ${float(exception_info.get('estimated_cost', 0.0)):,.2f}
    - Additional Notes: {additional_notes or 'Immediate intervention and resolution plan required.'}
    
    Format and Requirements:
    1. First line MUST be the Subject line in exact format: [Subject: FORMAL CARRIER ESCALATION: B/L {shipment_info.get('bl_number', 'N/A')} - {exception_info.get('exception_type', 'Shipment Exception')}]
    2. Tone: Highly professional, firm, and urgent. Reference SOP breach and financial penalties if delay persists.
    3. Demand: Request a written root-cause analysis, corrective action plan, and revised delivery schedule within 4 hours.
    4. Sign off formally from: Senior Operations Escalations Team, Powerhouse Pvt. Ltd.
    """
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
        )
        content = response.text.strip()
    except Exception as e:
        content = f"[Subject: FORMAL CARRIER ESCALATION: B/L {shipment_info.get('bl_number', 'N/A')} - URGENT]\n\nDear {carrier_name},\n\nThis is a formal escalation regarding B/L {shipment_info.get('bl_number', 'N/A')} (Container: {shipment_info.get('container_id', 'N/A')}).\n\nException: {exception_info.get('exception_type')}\nDescription: {exception_info.get('description')}\nEstimated Financial Impact: ${float(exception_info.get('estimated_cost', 0.0)):,.2f}\n\nPlease provide an immediate status update and corrective action plan within 4 hours.\n\nSincerely,\nPowerhouse Logistics Escalations Team"
    
    lines = content.splitlines()
    subject = f"FORMAL CARRIER ESCALATION: B/L {shipment_info.get('bl_number', 'N/A')} - {exception_info.get('exception_type', 'Shipment Exception')}"
    if lines and "[Subject:" in lines[0]:
        subj_part = lines[0].replace("[Subject:", "").rstrip("]").strip()
        if subj_part:
            subject = subj_part

    return {
        "carrier_name": carrier_name,
        "subject": subject,
        "email_draft": content,
        "severity": exception_info.get('severity', urgency)
    }

# ==========================================
# 4. WORKFLOW ASSEMBLY
# ==========================================
workflow = StateGraph(AgentState)
workflow.add_node("classify", classify_exception_node)
workflow.add_node("drafter", draft_message_node)
workflow.add_node("recommend", recommend_actions_node)

workflow.set_entry_point("classify")
workflow.add_edge("classify", "drafter")
workflow.add_edge("drafter", "recommend")
workflow.add_edge("recommend", END)

compiled_graph = workflow.compile()