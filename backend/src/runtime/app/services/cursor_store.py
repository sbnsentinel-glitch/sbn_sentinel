from sqlalchemy import Column, String, Integer, UniqueConstraint
from app.db.database import Base, SessionLocal
from datetime import datetime, timezone


class CursorModel(Base):
    __tablename__ = "sync_cursors"
    __table_args__ = (
        UniqueConstraint("connector_id", "resource_type", name="uq_cursor_connector_resource"),
    )
    id = Column(Integer, primary_key=True, index=True)
    connector_id = Column(String, nullable=False, index=True)
    resource_type = Column(String, nullable=False)
    checkpoint = Column(String, nullable=False)


def parse_fhir_instant(value: str) -> datetime:
    """
    Parses a FHIR instant/dateTime string into a timezone-aware UTC datetime.
    Correctly handles 'Z' and timezone offsets like +02:00, -05:00.
    Ensures that e.g. 10:00:00+02:00 is evaluated as 08:00:00Z.
    """
    if not value:
        raise ValueError("Empty timestamp string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_date(date_str: str) -> str:
    try:
        return parse_fhir_instant(date_str).isoformat()
    except Exception:
        return date_str


class CursorStore:
    def get(self, connector_id: str, resource_type: str) -> str:
        with SessionLocal() as db:
            cursor = db.query(CursorModel).filter(
                CursorModel.connector_id == connector_id,
                CursorModel.resource_type == resource_type
            ).first()
            return cursor.checkpoint if cursor else None

    def commit(self, connector_id: str, resource_type: str, checkpoint: str):
        if not checkpoint:
            return
        try:
            new_dt = parse_fhir_instant(checkpoint)
            normalized = new_dt.isoformat()
        except Exception:
            normalized = checkpoint
            new_dt = None

        with SessionLocal() as db:
            cursor = db.query(CursorModel).filter(
                CursorModel.connector_id == connector_id,
                CursorModel.resource_type == resource_type
            ).with_for_update().first()
            if cursor:
                should_update = False
                if new_dt:
                    try:
                        cur_dt = parse_fhir_instant(cursor.checkpoint)
                        should_update = new_dt > cur_dt
                    except Exception:
                        should_update = normalized > cursor.checkpoint
                else:
                    should_update = normalized > cursor.checkpoint

                if should_update:
                    cursor.checkpoint = normalized
            else:
                cursor = CursorModel(
                    connector_id=connector_id,
                    resource_type=resource_type,
                    checkpoint=normalized
                )
                db.add(cursor)
            db.commit()


cursor_store = CursorStore()
