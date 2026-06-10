import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from core.config import settings

# SMTP настройки — берём из .env
# Яндекс: SMTP_HOST=smtp.yandex.ru  SMTP_PORT=465
# Gmail:  SMTP_HOST=smtp.gmail.com  SMTP_PORT=465

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.yandex.ru")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")  # пароль приложения


def send_email(
    to: str,
    subject: str,
    body: str,
    resume_path: str | None = None,
) -> bool:
    """
    Отправляет письмо через SMTP. Опционально прикрепляет резюме.
    Возвращает True если успешно, False если ошибка.

    В режиме DRY_RUN письмо реально отправляется, но НЕ адресату, а себе —
    на EMAIL_ADDRESS из .env. В тему добавляется пометка с исходным получателем.
    """
    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD:
        print(
            "[EmailService] Ошибка: EMAIL_ADDRESS или EMAIL_APP_PASSWORD не заданы в .env"
        )
        return False

    # ── DRY_RUN: перенаправляем письмо себе ──────────────────────────
    if settings.DRY_RUN:
        real_recipient = to
        to = EMAIL_ADDRESS
        subject = f"[DRY_RUN → {real_recipient}] {subject}"
        body = (
            f"=== DRY RUN ===\n"
            f"Это письмо НЕ ушло реальному адресату.\n"
            f"Настоящий получатель: {real_recipient}\n"
            f"================\n\n"
            f"{body}"
        )
        print(f"[EmailService] [DRY RUN] Перенаправляю письмо: {real_recipient} → {to}")

    print(f"[EmailService] Отправляю письмо на {to}...")

    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Прикрепляем резюме если есть
    if resume_path and os.path.exists(resume_path):
        with open(resume_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            filename = os.path.basename(resume_path)
            part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            msg.attach(part)
        print(f"[EmailService] Прикреплено резюме: {resume_path}")
    elif resume_path:
        print(f"[EmailService] Резюме не найдено по пути: {resume_path}")

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to, msg.as_string())

        print(f"[EmailService] Письмо успешно отправлено на {to}")
        return True

    except smtplib.SMTPAuthenticationError:
        print(
            "[EmailService] Ошибка авторизации — проверь EMAIL_ADDRESS и EMAIL_APP_PASSWORD"
        )
        return False
    except Exception as e:
        print(f"[EmailService] Ошибка отправки: {e}")
        return False
