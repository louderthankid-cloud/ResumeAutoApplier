from pydantic import BaseModel, Field

MIN_RESUME_TEXT = 50


class CandidateCheck(BaseModel):
    ok: bool
    missing: list[str] = Field(default_factory=list)  # блокирует запуск
    warnings: list[str] = Field(default_factory=list)  # не блокирует


def validate_candidate(candidate) -> CandidateCheck:
    """candidate — ORM-объект Candidate (или любой с теми же атрибутами)"""
    missing: list[str] = []
    warnings: list[str] = []

    resume_text = (candidate.resume_text or "").strip()
    if len(resume_text) < MIN_RESUME_TEXT:
        missing.append("текст резюме (resume_text)")

    if not (candidate.target_job or "").strip():
        missing.append("целевая позиция (target_job)")

    if not candidate.resume_blob:
        missing.append("файл резюме для аттача (resume_blob)")

    if not candidate.email:
        warnings.append("email не распознан")
    if not candidate.phone:
        warnings.append("телефон не распознан")
    if not candidate.name:
        warnings.append("имя не распознано")

    return CandidateCheck(ok=not missing, missing=missing, warnings=warnings)
