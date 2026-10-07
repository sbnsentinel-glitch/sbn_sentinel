import asyncio
import random
from datetime import datetime, timezone
from app.schemas.signal import SignalEvent

# SES-002: All simulation data flows through the event pipeline
from app.services.processing_orchestrator import processing_orchestrator


class SimulationEngine:
    def __init__(self):
        self.running = False
        self.clients = []

        self.patients = ["John Doe", "Jane Smith", "Michael Johnson", "Emily Davis", "Robert Brown"]
        self.events = [
            {"source": "Practice Fusion", "type": "EHR", "msg_template": "Patient {patient} appointment marked as No-Show."},
            {"source": "Practice Fusion", "type": "EHR", "msg_template": "New appointment booked by {patient}."},
            {"source": "Twilio", "type": "Phone", "msg_template": "Missed call from {patient} (+1-555-0198)."},
            {"source": "Outlook", "type": "Email", "msg_template": "Email received from Lab Corp regarding {patient} results."},
            {"source": "Practice Fusion", "type": "EHR", "msg_template": "Patient {patient} wait time has reached 45 minutes."}
        ]

    async def generate_realistic_data(self):
        while self.running:
            await asyncio.sleep(random.uniform(3, 8))  # Generate an event every 3 to 8 seconds

            patient = random.choice(self.patients)
            event_template = random.choice(self.events)
            message_str = event_template["msg_template"].format(patient=patient)

            # ─────────────────────────────────────────────────────────────
            # SES-002 COMPLIANT: All data flows through the 8-layer pipeline
            # No direct engine calls — pipeline orchestrator handles all layers
            # ─────────────────────────────────────────────────────────────
            try:
                event_model = processing_orchestrator.create_event(
                    event_type=event_template["type"],
                    source=event_template["source"],
                    raw_payload={
                        "patient_name": patient,
                        "detail": message_str,
                        "content": message_str,
                    },
                    priority=random.choice(["Normal", "Normal", "High", "Low", "Critical"]),
                    initiated_by="system@sentinel.local",
                )

                # Execute pipeline synchronously for simulation feed
                processing_orchestrator.process_event_background(event_model.id)

                from app.db.database import SessionLocal
                from app.models.intelligence import OperationalIntelligenceModel, RevenueIntelligenceModel, DecisionContextModel

                db = SessionLocal()
                try:
                    intel = db.query(OperationalIntelligenceModel).filter(
                        OperationalIntelligenceModel.event_id == event_model.id).first()
                    revenue = db.query(RevenueIntelligenceModel).filter(
                        RevenueIntelligenceModel.event_id == event_model.id).first()
                    context = db.query(DecisionContextModel).filter(
                        DecisionContextModel.event_id == event_model.id).first()

                    event = SignalEvent(
                        id=event_model.id[:8],
                        source=event_template["source"],
                        type=event_template["type"],
                        message=message_str,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        metadata={"patient_name": patient, "pipeline_event_id": event_model.id},
                        risk_level=intel.priority if intel else "Information",
                        problem="",
                        reason="",
                        business_impact=intel.operational_impact if intel else "",
                        recommended_action=intel.recommendation if intel else "",
                        expected_outcome="",
                        primary_context=context.primary_context if context else "",
                        secondary_context=context.secondary_context if context else "",
                        context_confidence=context.confidence if context else "",
                        context_reason=context.reason if context else "",
                        revenue_risk_category=revenue.opportunity_category if revenue else "UNAVAILABLE",
                        estimated_financial_exposure=revenue.estimated_exposure if revenue else "UNAVAILABLE",
                        revenue_confidence=revenue.financial_priority if revenue else "UNAVAILABLE",
                        operational_dependency=""
                    )
                    await self.broadcast(event)
                finally:
                    db.close()

            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"[SimEngine] Pipeline error: {e}")

    async def generate_dynamic_encounters(self):
        from app.db.database import SessionLocal
        from app.models.encounter import EncounterModel
        while self.running:
            await asyncio.sleep(10)  # Every 10 seconds, move someone or create someone
            try:
                db = SessionLocal()
                # 1. Move "Waiting" -> "In Room"
                waiting = db.query(EncounterModel).filter(EncounterModel.status == "Waiting").all()
                if waiting and random.random() > 0.3:
                    p = random.choice(waiting)
                    p.status = "In Room"
                    p.wait_time = f"{random.randint(5, 45)} mins"

                # 2. Move "In Room" -> "Completed"
                in_room = db.query(EncounterModel).filter(EncounterModel.status == "In Room").all()
                if in_room and random.random() > 0.5:
                    p = random.choice(in_room)
                    p.status = "Completed"
                    p.billing_status = "Pending"

                # 3. Move "Completed" -> "Billed" -> "Paid"
                completed = db.query(EncounterModel).filter(
                    EncounterModel.status == "Completed").all()
                for c in completed:
                    if c.billing_status == "Pending" and random.random() > 0.7:
                        c.billing_status = "Billed"
                    elif c.billing_status == "Billed" and random.random() > 0.7:
                        c.billing_status = "Paid"

                # 4. Chance to spawn new patient
                if random.random() > 0.6:
                    new_id = f"enc_{random.randint(1000, 99999)}"
                    new_enc = EncounterModel(
                        id=new_id,
                        patient_id=random.choice(["Alex M.", "Brian K.", "Chloe T.", "Daniel S.", "Eva R.", "Fiona W.", "Liam H.", "Zoe P."]),
                        provider_id=random.choice(["Dr. Smith", "Dr. Patel", "Dr. Chen", "Dr. Sarah Jenkins"]),
                        date=datetime.now().strftime("%Y-%m-%d"),
                        diagnosis=random.choice(["Routine checkup", "Mild fever", "Back pain", "Hypertension followup", "Allergy evaluation"]),
                        type=random.choice(["Checkup", "Consultation", "Urgent Care"]),
                        status="Waiting",
                        billing_status="Pending",
                        copay=float(random.choice([20, 30, 45, 50, 100])),
                        priority=random.choice(["Normal", "Urgent"]),
                        wait_time="0 mins",
                        department=random.choice(["General Practice", "Cardiology", "Radiology"]),
                        avatar=f"https://i.pravatar.cc/150?img={random.randint(1, 70)}"
                    )
                    db.add(new_enc)

                db.commit()
                db.close()
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"[Encounter Sim] Error: {e}")

    async def broadcast(self, event: SignalEvent):
        # Push to all connected WebSocket clients
        for q in self.clients:
            await q.put(event.model_dump_json())

    def start(self):
        if not self.running:
            self.running = True
            asyncio.create_task(self.generate_realistic_data())
            asyncio.create_task(self.generate_dynamic_encounters())

    def stop(self):
        self.running = False

    def add_client(self, queue: asyncio.Queue):
        self.clients.append(queue)

    def remove_client(self, queue: asyncio.Queue):
        if queue in self.clients:
            self.clients.remove(queue)


simulation_engine = SimulationEngine()
