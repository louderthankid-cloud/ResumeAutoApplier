from pydantic import BaseModel
from typing import Optional


class Vacancy(BaseModel):
    id: str  # "hh_12345678"
    title: str
    company_name: str
    vacancy_url: str
    description: str

    company_site: Optional[str] = None
    hr_email: Optional[str] = None
