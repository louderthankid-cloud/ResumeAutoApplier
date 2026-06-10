import uuid
from sqlalchemy import Column, String, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    tg_id = Column(String, nullable=False, index=True)

    name = Column(String, nullable=False)
    target_job = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String)
    resume_path = Column(String)
    base_message = Column(Text)

    applied_companies = Column(JSON, default=list)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
