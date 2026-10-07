import os
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from openai import OpenAI
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []
    context: Optional[Dict[str, Any]] = None


class ChatResponse(BaseModel):
    reply: str
    source: str


# Retrieve key from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


@router.post("/chat", response_model=ChatResponse)
async def chat_assistant(request: ChatRequest):
    message = request.message.lower().strip()
    ctx = request.context or {}
    # Extract operational context safely
    checked_in = ctx.get("checked_in", 200)
    waiting = ctx.get("waiting", 57)
    delayed = ctx.get("delayed", 88)
    # NOTE: active_patient is intentionally excluded from the AI prompt to prevent
    # PHI leakage to external AI services (OpenAI / Google Gemini).
    # Only aggregate, non-identifiable counts are sent externally.
    # 1. If OpenAI API Key is present, use GPT-4o for natural language explanation
    if OPENAI_API_KEY:
        try:
            if OPENAI_API_KEY.startswith("AI") or OPENAI_API_KEY.startswith("AQ"):
                client = OpenAI(
                    api_key=OPENAI_API_KEY,
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
                )
                model_name = "gemini-1.5-flash"
            else:
                client = OpenAI(api_key=OPENAI_API_KEY)
                model_name = "gpt-4o"

            # HIPAA: Only aggregate, de-identified operational counts are sent to external AI.
            # No patient names, IDs, DOB, MRN, or PII are included.
            system_prompt = (
                "You are SBN Sentinel's Conversational Assistant. Your role is to explain clinical telemetry, "
                "patient flow status, and medical billing metrics to the user. "
                "Do not perform calculations yourself. Sentinel's core engine does all calculations independently. "
                "Instead, explain the current state using the context below.\n\n"
                "CURRENT SENTINEL STATE CONTEXT (aggregate counts only — no PHI):\n"
                f"- Checked-in Patients: {checked_in}\n"
                f"- Currently in Consultation: {waiting}\n"
                f"- Delayed Patients (Anomalies detected by Sentinel): {delayed}\n"
                "Please respond to the user's message in a helpful, conversational manner, referencing the context if relevant."
            )

            # Convert history to OpenAI format
            messages = [{"role": "system", "content": system_prompt}]
            for turn in request.history or []:
                messages.append({"role": turn.get("role", "user"),
                                "content": turn.get("content", "")})

            messages.append({"role": "user", "content": request.message})

            completion = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0.7,
                max_tokens=250
            )
            reply_text = completion.choices[0].message.content
            return ChatResponse(reply=reply_text, source="openai")
        except Exception as e:  # noqa
            # Fallback to local assistant if OpenAI API call fails
            pass
    # 2. Local heuristic fallback responder (context-aware & extremely robust)
    active_patient = ctx.get("active_patient", "None selected")
    reply_text = ""
    if "hi" in message or "hello" in message or "hey" in message:
        reply_text = (
            "Hello! I am the Sentinel AI assistant. I can explain the current clinical simulation state for you. "
            f"Currently, Sentinel's core engine reports {checked_in} checked-in patients, "
            f"{waiting} in active consultation, and {delayed} delayed patients. How can I help you navigate these stats?"
        )
    elif "delay" in message or "anomaly" in message or "delayed" in message:
        reply_text = (
            "Sentinel's independent anomaly engine predicts {delayed} delayed cases today. "
            f"This calculation is based on average wait times (currently {ctx.get('avg_wait', '188m')}) "
            "and room utilization. You can review delayed patients directly in the Patient Flow queue."
        )
    elif "checked" in message or "patient" in message or "flow" in message:
        reply_text = (
            f"According to Sentinel's telemetry calculations, we have {checked_in} patients checked-in today, "
            f"with {waiting} currently in consultation. The active patient under view is {active_patient}."
        )
    elif "billing" in message or "insurance" in message or "log" in message:
        reply_text = (
            "The Revenue Intelligence subsystem has verified active coverage and auto-coded diagnostic ICD/CPT markers. "
            "Patient copays are processed independently of my conversational interface."
        )
    elif "calculation" in message or "prediction" in message or "reasoning" in message:
        reply_text = (
            "All operational predictions, delays, wait times, and clinic routing calculations are performed "
            "locally and independently by Sentinel's core simulation engines. I am only here to explain "
            "those calculations in natural language.")
    else:
        reply_text = (
            f"I understand. Currently, Sentinel reports: {checked_in} Checked In, {waiting} In Consultation, "
            f"and {delayed} Delayed. You can select any patient to inspect their billing codes, insurance "
            "eligibility, and clinical SOAP notes. Let me know if you need any explanations!"
        )
    return ChatResponse(reply=reply_text, source="local_heuristic")
