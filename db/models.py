import uuid
from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime,
    Integer,
    LargeBinary,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    tg_id = Column(String, nullable=False, index=True)

    target_job = Column(String, nullable=False)
    resume_text = Column(Text, nullable=False)  # извлечённый текст из pdf/docx

    # файл резюме, хранится в бд
    # перед прогоном материализуется во временный файл
    resume_blob = Column(LargeBinary)  # содержимое файла
    resume_filename = Column(String)  # оригинальное имя (для аттача к письму)
    resume_mime = Column(String)

    # временный путь к материализованному файлу на время прогона (не хранилище)
    resume_path = Column(String)

    # опционально: ллм берёт всё из resume_text, эти поля нужныв для отображения в меню
    name = Column(String)
    email = Column(String)
    phone = Column(String)
    base_message = Column(Text)

    applications = relationship("Application", back_populates="candidate")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Application(Base):

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "company_name_clean", name="uq_app_candidate_company"
        ),
        Index("ix_app_candidate_status", "candidate_id", "status"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id = Column(
        String, ForeignKey("candidates.id"), nullable=False, index=True
    )

    company_name = Column(String, nullable=False)  # как есть, для отображения
    company_name_clean = Column(String, nullable=False)  # нормализованное, для дедупа

    site_url = Column(String)
    source_url = Column(String)  # откуда узнали о вакансии, нужно для ручной проверки
    vacancy_url = Column(String)
    target_url = Column(String)  # куда в итоге пошли заполнять (форма/каталог)
    hr_email = Column(String)

    channel = Column(String)  # "form" | "email" | "both" | None
    status = Column(String, nullable=False)  # итог: значения ApplicationStatus

    form_status = Column(String)  # значения FormFillStatus | None
    email_status = Column(String)  # значения EmailStatus | None
    form_scope = Column(String)  # "vacancy" | "general" | None

    error_detail = Column(Text)  # подробности: текст ошибки, незаполненные поля
    reason = Column(Text)  # причина от ллм

    attempts = Column(Integer, nullable=False, default=1)

    candidate = relationship("Candidate", back_populates="applications")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
