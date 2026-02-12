# -*- coding: utf-8 -*-
"""
Email Manager (Phase 9.3).
Управляет чтением (IMAP) и отправкой (SMTP) электронных писем.
"""

import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import structlog
from typing import List, Dict, Optional, Any

logger = structlog.get_logger("EmailManager")

class EmailManager:
    def __init__(self, config: Dict[str, str]):
        """
        Инициализация с параметрами из .env или конфига.
        config: {
            "EMAIL_IMAP_SERVER": "imap.gmail.com",
            "EMAIL_SMTP_SERVER": "smtp.gmail.com",
            "EMAIL_USER": "your@email.com",
            "EMAIL_PASS": "app-password"
        }
        """
        self.imap_server = config.get("EMAIL_IMAP_SERVER")
        self.smtp_server = config.get("EMAIL_SMTP_SERVER")
        self.user = config.get("EMAIL_USER")
        self.password = config.get("EMAIL_PASS")
        self.imap_port = int(config.get("EMAIL_IMAP_PORT", 993))
        self.smtp_port = int(config.get("EMAIL_SMTP_PORT", 587))

    def _decode_header(self, header):
        if not header:
            return ""
        decoded, encoding = decode_header(header)[0]
        if isinstance(decoded, bytes):
            return decoded.decode(encoding or "utf-8")
        return decoded

    async def get_latest_emails(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Получает последние N писем из папки INBOX."""
        emails = []
        if not self.user or not self.password or not self.imap_server:
            logger.warning("Email credentials missing")
            return [{"error": "Email configuration missing"}]

        try:
            # imaplib не асинхронный, выполняем в потоке если нужно, 
            # но для внутреннего использования в агенте пока сделаем синхронно 
            # (или обернем в to_thread в хендлере)
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.user, self.password)
            mail.select("inbox")

            status, messages = mail.search(None, "ALL")
            if status != "OK":
                return []

            msg_ids = messages[0].split()
            # Берем последние N
            for msg_id in msg_ids[-limit:]:
                status, data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue

                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)
                
                subject = self._decode_header(msg.get("Subject"))
                from_ = self._decode_header(msg.get("From"))
                date = msg.get("Date")

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode(errors="ignore")
                            break
                else:
                    body = msg.get_payload(decode=True).decode(errors="ignore")

                emails.append({
                    "id": msg_id.decode(),
                    "from": from_,
                    "subject": subject,
                    "date": date,
                    "body": body[:500] + "..." if len(body) > 500 else body
                })

            mail.logout()
            return emails[::-1] # Самые свежие первыми
        except Exception as e:
            logger.error(f"IMAP Error: {e}")
            return [{"error": str(e)}]

    async def send_email(self, to: str, subject: str, content: str) -> bool:
        """Отправляет письмо через SMTP."""
        if not self.user or not self.password or not self.smtp_server:
            logger.warning("Email credentials missing")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = self.user
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(content, "plain"))

            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.user, self.password)
            server.send_message(msg)
            server.quit()
            
            logger.info(f"Email sent to {to}")
            return True
        except Exception as e:
            logger.error(f"SMTP Error: {e}")
            return False
