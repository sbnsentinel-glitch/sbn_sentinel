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

def _normalize_date(date_str: str) -> str:
    try:
        # Handle 'Z' which Python 3.11 supports natively
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
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
        normalized = _normalize_date(checkpoint)
        with SessionLocal() as db:
            cursor = db.query(CursorModel).filter(
                CursorModel.connector_id == connector_id,
                CursorModel.resource_type == resource_type
            ).with_for_update().first()
            if cursor:
                if normalized > cursor.checkpoint:
                    cursor.checkpoint = normalized
            else:
                cursor = CursorModel(connector_id=connector_id, resource_type=resource_type, checkpoint=normalized)
                db.add(cursor)
            db.commit()


cursor_store = CursorStore()
