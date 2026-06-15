from schemas.application import ApplicationStatus


class PipelineError(Exception):
    """база: любая типизированная ошибка пайплайна, маппится в статус заявки"""

    status: ApplicationStatus = ApplicationStatus.FORM_FILL_FAILED

    def __init__(self, detail: str = "", url: str | None = None):
        self.detail = detail
        self.url = url
        super().__init__(detail)


class SiteUnavailableError(PipelineError):
    """сайт не отвечает: таймаут, DNS, connection refused и тд"""

    status = ApplicationStatus.SITE_DOWN


class DdosProtectionError(PipelineError):
    """анти-бот заглушка"""

    status = ApplicationStatus.DDOS_PROTECTION
