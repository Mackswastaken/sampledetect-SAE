from sqlalchemy import Column, String, DateTime, Integer, Float
from sqlalchemy.sql import func
from db import Base


class AudioAsset(Base):
    __tablename__ = "audio_assets"

    id = Column(String, primary_key=True, index=True)
    original_filename = Column(String, nullable=False)
    stored_path = Column(String, nullable=False)
    file_size_bytes = Column(Integer, nullable=False)

    fingerprint_status = Column(String, nullable=False, default="none")  # none|pending|done|error
    fingerprint_path = Column(String, nullable=True)
    fingerprint_error = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LibraryTrack(Base):
    __tablename__ = "library_tracks"

    id = Column(String, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    stored_path = Column(String, nullable=False, unique=True)

    fingerprint_path = Column(String, nullable=True)
    fingerprint_value = Column(String, nullable=True)  # the "fingerprint" string from fpcalc
    duration = Column(Float, nullable=True)

    status = Column(String, nullable=False, default="none")  # none|done|error
    error = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

from sqlalchemy import Column, String, DateTime, Integer, Text
from datetime import datetime

class MonitorIncident(Base):
    __tablename__ = "monitor_incidents"

    id = Column(String, primary_key=True, index=True)  # uuid
    created_at = Column(DateTime, default=datetime.utcnow)

    inbox_filename = Column(String, nullable=False)
    inbox_path = Column(Text, nullable=False)

    mode = Column(String, nullable=False)  # "vr" or "normal"

    match_filename = Column(String, nullable=True)
    match_path = Column(Text, nullable=True)
    common_hashes = Column(Integer, nullable=True)
    rank = Column(Integer, nullable=True)
    offset_sec = Column(String, nullable=True)

    email_sent = Column(String, nullable=True)
    email_reason = Column(Text, nullable=True)
