from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from typing import List
import random
from app.db.database import get_db
from app.models.encounter import EncounterModel
from app.schemas.encounter import EncounterCreate, EncounterUpdate, EncounterResponse

router = APIRouter()


def seed_default_encounters(db: Session):
    default_encounters = [{"id": "enc_01",
                           "patient_name": "Michael R.",
                           "provider_name": "Dr. Smith",
                           "date": "2026-07-01",
                           "diagnosis": "Unspecified essential hypertension",
                           "type": "Urgent Care",
                           "status": "Waiting",
                           "billing_status": "Pending",
                           "copay": 20.0,
                           "priority": "High",
                           "wait_time": "14 mins",
                           "department": "Cardiology",
                           "avatar": "https://i.pravatar.cc/150?img=11",
                           "clinical_notes": "Subjective: Patient reports mild headaches and occasional palpitations. Objective: BP 145/95 mmHg, HR 82 bpm, regular rhythm. Assessment: Unspecified essential hypertension. Plan: Advised low sodium diet. Initiated Lisinopril 10mg daily. Follow-up in 2 weeks.",
                           "medications": "Lisinopril 10mg daily"},
                          {"id": "enc_02",
                           "patient_name": "Sarah J.",
                           "provider_name": "Dr. Patel",
                           "date": "2026-07-01",
                           "diagnosis": "Acute upper respiratory infection",
                           "type": "Consultation",
                           "status": "In Room",
                           "billing_status": "Billed",
                           "copay": 45.0,
                           "priority": "Normal",
                           "wait_time": "5 mins",
                           "department": "General Practice",
                           "avatar": "https://i.pravatar.cc/150?img=5",
                           "clinical_notes": "Subjective: Patient reports sore throat, congestion, and dry cough for 3 days. Objective: Temp 99.1 F, pharynx erythematous without exudate. Lungs clear to auscultation. Assessment: Acute upper respiratory infection (viral). Plan: Rest, hydration, OTC decongestants. Follow-up if symptoms worsen.",
                           "medications": "OTC Decongestants, Saline Nasal Spray"},
                          {"id": "enc_03",
                           "patient_name": "David L.",
                           "provider_name": "Dr. Smith",
                           "date": "2026-07-01",
                           "diagnosis": "Type 2 diabetes mellitus without complications",
                           "type": "Checkup",
                           "status": "Completed",
                           "billing_status": "Paid",
                           "copay": 20.0,
                           "priority": "Normal",
                           "wait_time": "25 mins",
                           "department": "Cardiology",
                           "avatar": "https://i.pravatar.cc/150?img=12",
                           "clinical_notes": "Subjective: Patient presents for routine diabetic checkup. Reports compliance with medication. Objective: HbA1c 6.8%, BP 122/78 mmHg. Foot exam normal. Assessment: Controlled Type 2 diabetes mellitus. Plan: Continue Metformin 500mg BID. Recommended annual eye exam.",
                           "medications": "Metformin 500mg BID"},
                          {"id": "enc_04",
                           "patient_name": "Emily D.",
                           "provider_name": "Dr. Chen",
                           "date": "2026-07-01",
                           "diagnosis": "Pain in right knee",
                           "type": "X-Ray Scan",
                           "status": "Waiting",
                           "billing_status": "Claim Denied",
                           "copay": 50.0,
                           "priority": "Normal",
                           "wait_time": "18 mins",
                           "department": "Radiology",
                           "avatar": "https://i.pravatar.cc/150?img=23",
                           "clinical_notes": "Subjective: Patient reports sharp pain in the right knee after a minor twist yesterday. Objective: Swelling noted over the lateral patellar region. Limited range of motion. Assessment: Suspected ligamentous strain. Plan: X-ray completed to rule out fracture. Rest, Ice, Compression, Elevation.",
                           "medications": "Ibuprofen 400mg q6h PRN"},
                          {"id": "enc_05",
                           "patient_name": "James K.",
                           "provider_name": "Dr. Patel",
                           "date": "2026-07-01",
                           "diagnosis": "Gastro-esophageal reflux disease",
                           "type": "Consultation",
                           "status": "Delayed",
                           "billing_status": "Pending",
                           "copay": 45.0,
                           "priority": "Urgent",
                           "wait_time": "40 mins",
                           "department": "General Practice",
                           "avatar": "https://i.pravatar.cc/150?img=15",
                           "clinical_notes": "Subjective: Patient reports chronic burning sensation in chest, worse after meals and when lying down. Objective: Abdomen soft, non-tender, no organomegaly. Assessment: GERD. Plan: Avoid trigger foods. Initiated Omeprazole 20mg daily 30 mins before breakfast.",
                           "medications": "Omeprazole 20mg daily"},
                          {"id": "enc_06",
                           "patient_name": "Robert B.",
                           "provider_name": "Dr. Sarah Jenkins",
                           "date": "2026-07-01",
                           "diagnosis": "Lumbago with sciatica, right side",
                           "type": "Therapy",
                           "status": "Completed",
                           "billing_status": "Paid",
                           "copay": 25.0,
                           "priority": "Normal",
                           "wait_time": "12 mins",
                           "department": "Physical Therapy",
                           "avatar": "https://i.pravatar.cc/150?img=3",
                           "clinical_notes": "Subjective: Patient reports lower back pain radiating down the right leg. Objective: Straight leg raise positive on the right at 45 degrees. Palpable lumbar spasm. Assessment: Sciatica secondary to lumbar strain. Plan: Initiated physical therapy program, heat packs, and gentle stretching.",
                           "medications": "Cyclobenzaprine 5mg daily"},
                          {"id": "enc_07",
                           "patient_name": "Jessica M.",
                           "provider_name": "Dr. Sarah Jenkins",
                           "date": "2026-07-01",
                           "diagnosis": "Generalized anxiety disorder",
                           "type": "Consultation",
                           "status": "In Room",
                           "billing_status": "Billed",
                           "copay": 30.0,
                           "priority": "Normal",
                           "wait_time": "8 mins",
                           "department": "Psychiatry",
                           "avatar": "https://i.pravatar.cc/150?img=9",
                           "clinical_notes": "Subjective: Patient reports persistent worry, insomnia, and muscle tension for over 6 months. Objective: Patient appears anxious, hyperactive fidgeting, speech normal. Assessment: Generalized anxiety disorder. Plan: Recommended Cognitive Behavioral Therapy (CBT). Initiated Sertraline 25mg daily.",
                           "medications": "Sertraline 25mg daily"}]
    for enc_data in default_encounters:
        enc = EncounterModel(**enc_data)
        db.add(enc)
    db.commit()


@router.get("/", response_model=List[EncounterResponse])
def get_all_encounters(db: Session = Depends(get_db)):
    """
    Get all clinical patient encounters from SQLite database.
    """
    encounters = db.query(EncounterModel).all()
    return encounters


@router.post("/", response_model=EncounterResponse, status_code=status.HTTP_201_CREATED)
def create_encounter(encounter_in: EncounterCreate, db: Session = Depends(get_db)):
    """
    Register a new patient encounter in the clinic flow.
    """
    existing = db.query(EncounterModel).filter(EncounterModel.id == encounter_in.id).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Encounter with ID '{encounter_in.id}' already exists."
        )
    new_enc = EncounterModel(
        id=encounter_in.id,
        patient_name=encounter_in.patient_name,
        provider_name=encounter_in.provider_name,
        date=encounter_in.date,
        diagnosis=encounter_in.diagnosis,
        type=encounter_in.type,
        status=encounter_in.status or "Waiting",
        billing_status=encounter_in.billing_status or "Pending",
        copay=encounter_in.copay or 245.0,
        priority=encounter_in.priority or "Normal",
        wait_time=encounter_in.wait_time or "0 mins",
        department=encounter_in.department,
        avatar=encounter_in.avatar or f"https://i.pravatar.cc/150?img={random.randint(1, 70)}",
        clinical_notes=encounter_in.clinical_notes,
        medications=encounter_in.medications
    )
    db.add(new_enc)
    db.commit()
    db.refresh(new_enc)
    return new_enc


@router.put("/{encounter_id}", response_model=EncounterResponse)
def update_encounter(
        encounter_id: str,
        encounter_in: EncounterUpdate,
        db: Session = Depends(get_db)):
    """
    Update status, billing_status, wait time, or diagnosis of an existing clinical encounter.
    """
    enc = db.query(EncounterModel).filter(EncounterModel.id == encounter_id).first()
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter record not found")

    if encounter_in.status is not None:
        enc.status = encounter_in.status
    if encounter_in.billing_status is not None:
        enc.billing_status = encounter_in.billing_status
    if encounter_in.diagnosis is not None:
        enc.diagnosis = encounter_in.diagnosis
    if encounter_in.wait_time is not None:
        enc.wait_time = encounter_in.wait_time
    if encounter_in.priority is not None:
        enc.priority = encounter_in.priority
    if encounter_in.clinical_notes is not None:
        enc.clinical_notes = encounter_in.clinical_notes
    if encounter_in.medications is not None:
        enc.medications = encounter_in.medications

    db.commit()
    db.refresh(enc)
    return enc


@router.delete("/{encounter_id}", status_code=status.HTTP_200_OK)
def delete_encounter(encounter_id: str, db: Session = Depends(get_db)):
    """
    Remove/Checkout an encounter record from the active flows.
    """
    enc = db.query(EncounterModel).filter(EncounterModel.id == encounter_id).first()
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter record not found")

    db.delete(enc)
    db.commit()
    return {"message": "Encounter checked out successfully"}
