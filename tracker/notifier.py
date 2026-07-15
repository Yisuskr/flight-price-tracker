import html
import json
import logging
import smtplib
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests

from tracker.flight import FlightResult

logger = logging.getLogger(__name__)

_CURRENCY_SYMBOL = {"EUR": "EUR ", "USD": "$", "GBP": "GBP "}


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _currency_symbol(currency: str) -> str:
    return _CURRENCY_SYMBOL.get(currency, f"{currency} ")


def _book_button(flight: FlightResult) -> str:
    url = flight.booking_url()
    if not url:
        return '<span style="color:#aaa;font-size:12px;">-</span>'
    return (
        '<a href="{url}" style="background:#1a73e8;color:#fff;text-decoration:none;'
        'padding:5px 10px;border-radius:4px;font-size:12px;white-space:nowrap;">Book</a>'
    ).format(url=_esc(url))


def _render_row(flight: FlightResult, sym: str, is_alert: bool) -> str:
    bg = "#e8f5e9" if is_alert else "#ffffff"
    border = "#c8e6c9" if is_alert else "#eeeeee"
    weight = "700" if is_alert else "400"
    color = "#2e7d32" if is_alert else "#444"
    return f"""\
<tr style="background:{bg};">
  <td style="padding:9px 8px;border-bottom:1px solid {border};font-weight:{weight};color:{color};">{_esc(flight.origin)} -> {_esc(flight.destination)}</td>
  <td style="padding:9px 8px;border-bottom:1px solid {border};color:{color};">{_esc(flight.outbound_date)}</td>
  <td style="padding:9px 8px;border-bottom:1px solid {border};color:{color};">{_esc(flight.return_date or "-")}</td>
  <td style="padding:9px 8px;border-bottom:1px solid {border};color:{color};">{_esc(flight.airline)}</td>
  <td style="padding:9px 8px;border-bottom:1px solid {border};color:{color};">{_esc(flight.duration)}</td>
  <td style="padding:9px 8px;border-bottom:1px solid {border};color:{color};">{_esc(flight.layovers_str())}</td>
  <td style="padding:9px 8px;border-bottom:1px solid {border};font-weight:{weight};color:{color};">{sym}{flight.price:.0f}</td>
  <td style="padding:9px 8px;border-bottom:1px solid {border};font-size:12px;color:#777;">{_esc(getattr(flight, "source", "-"))}</td>
  <td style="padding:9px 8px;border-bottom:1px solid {border};">{_book_button(flight)}</td>
</tr>"""


def _google_flights_url(best: FlightResult, currency: str) -> str:
    return (
        "https://www.google.com/travel/flights"
        f"?q=Flights%20from%20{best.origin}%20to%20{best.destination}%20on%20{best.outbound_date}"
        f"&curr={currency}"
    )


def _build_batch_content(
    all_results: list[FlightResult],
    alerts: list[FlightResult],
    threshold: float,
    currency: str,
) -> tuple[str, str, str]:
    sym = _currency_symbol(currency)
    alert_set = {id(f) for f in alerts}
    best = all_results[0]
    rows_html = "\n".join(_render_row(f, sym, id(f) in alert_set) for f in all_results)
    checked_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    status = "Price alert" if alerts else "Flight price summary"

    subject = (
        f"[{status}] {best.origin}->{best.destination} {sym}{best.price:.0f} "
        f"| {best.outbound_date} -> {best.return_date or '-'}"
    )

    html_body = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body {{ font-family: Arial, sans-serif; background:#f4f6f8; margin:0; padding:20px; }}
    .card {{ background:#fff; border-radius:8px; padding:28px; max-width:900px; margin:0 auto;
             box-shadow:0 2px 10px rgba(0,0,0,0.08); }}
    h1 {{ color:#1a73e8; margin:0 0 4px; font-size:24px; }}
    .subtitle {{ color:#555; font-size:14px; margin-bottom:20px; }}
    .best-price {{ background:#e8f5e9; border:2px solid #43a047; border-radius:8px;
                   text-align:center; padding:16px; margin:20px 0; }}
    .best-price .label {{ font-size:12px; color:#555; text-transform:uppercase; letter-spacing:1px; }}
    .best-price .amount {{ font-size:40px; font-weight:bold; color:#2e7d32; }}
    .best-price .sub {{ font-size:13px; color:#666; margin-top:4px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; margin-top:16px; }}
    th {{ background:#1a73e8; color:#fff; padding:10px 8px; text-align:left; font-size:12px;
          text-transform:uppercase; letter-spacing:.5px; }}
    .cta {{ display:block; text-align:center; background:#1a73e8; color:#fff !important;
            text-decoration:none; padding:13px 28px; border-radius:6px; font-size:15px;
            font-weight:bold; margin:24px auto; width:fit-content; }}
    .footer {{ text-align:center; font-size:12px; color:#888; margin-top:24px; line-height:1.5; }}
  </style>
</head>
<body>
<div class="card">
  <h1>{_esc(status)}</h1>
  <div class="subtitle">{_esc(best.origin)} -> {_esc(best.destination)} flight tracker</div>

  <div class="best-price">
    <div class="label">Best price found</div>
    <div class="amount">{sym}{best.price:.0f} {_esc(currency)}</div>
    <div class="sub">{_esc(best.airline)} | {_esc(best.origin)} -> {_esc(best.destination)} | Out {best.outbound_date} / Return {best.return_date or "-"}</div>
    <div class="sub">Stops: {_esc(best.layovers_str())}</div>
    <div class="sub" style="color:#888;margin-top:6px;">Threshold: {sym}{threshold:.0f} {_esc(currency)}</div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Route</th>
        <th>Outbound</th>
        <th>Return</th>
        <th>Airline</th>
        <th>Duration</th>
        <th>Stops</th>
        <th>Price</th>
        <th>Source</th>
        <th>Book</th>
      </tr>
    </thead>
    <tbody>
{rows_html}
    </tbody>
  </table>

  <a class="cta" href="{_esc(_google_flights_url(best, currency))}">Search on Google Flights</a>

  <div class="footer">
    Sent by Flight Price Tracker<br>
    Checked: {checked_at} | {len(all_results)} result(s) compared
  </div>
</div>
</body>
</html>"""

    plain_lines = [
        status,
        f"Threshold: {sym}{threshold:.0f} {currency}",
        f"Checked: {checked_at}",
        "",
    ]
    for f in all_results:
        marker = " ALERT" if id(f) in alert_set else ""
        plain_lines.append(
            f"{f.origin}->{f.destination} | {f.outbound_date} -> {f.return_date or '-'} | "
            f"{sym}{f.price:.0f} | {f.airline} | {f.duration} | {f.layovers_str()} | "
            f"{getattr(f, 'source', '-')}{marker}"
        )

    return subject, html_body, "\n".join(plain_lines)


class Notifier:
    def __init__(self, cfg: dict):
        self.provider: str = cfg.get("email_provider", "sendgrid")
        self.sendgrid_key: str = cfg.get("sendgrid_api_key", "")
        self.smtp_host: str = cfg.get("smtp_host", "smtp.gmail.com")
        self.smtp_port: int = int(cfg.get("smtp_port", 465))
        self.smtp_user: str = cfg.get("smtp_user", "")
        self.smtp_password: str = cfg.get("smtp_password", "")
        self.sender: str = cfg["sender_email"]
        self.sender_name: str = cfg.get("sender_name", "Flight Tracker")
        self.recipient: str = cfg["alert_email"]
        self.currency: str = cfg["currency"]
        self.max_alerts_per_day: int = cfg["max_alerts_per_day"]
        self._alert_date: Optional[date] = None
        self._alerts_today: int = 0

    def _can_send(self) -> bool:
        today = date.today()
        if self._alert_date != today:
            self._alert_date = today
            self._alerts_today = 0
        if self._alerts_today >= self.max_alerts_per_day:
            logger.info("Daily email limit reached (%d).", self.max_alerts_per_day)
            return False
        return True

    def _send(self, subject: str, html_body: str, plain_body: str) -> bool:
        if self.provider in {"smtp", "gmail"}:
            return self._send_smtp(subject, html_body, plain_body)
        return self._send_sendgrid(subject, html_body, plain_body)

    def _send_sendgrid(self, subject: str, html_body: str, plain_body: str) -> bool:
        if not self.sendgrid_key:
            logger.error("SENDGRID_API_KEY is not configured; cannot send email.")
            return False
        payload = {
            "personalizations": [{"to": [{"email": self.recipient}]}],
            "from": {"email": self.sender, "name": self.sender_name},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": plain_body},
                {"type": "text/html", "value": html_body},
            ],
        }
        try:
            response = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {self.sendgrid_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload),
                timeout=15,
            )
            if response.status_code in (200, 202):
                self._alerts_today += 1
                logger.info(
                    "Email sent to %s via SendGrid (%d/%d today).",
                    self.recipient,
                    self._alerts_today,
                    self.max_alerts_per_day,
                )
                return True
            logger.error("SendGrid error %d: %s", response.status_code, response.text)
            return False
        except requests.RequestException as exc:
            logger.error("Error sending email via SendGrid: %s", exc)
            return False

    def _send_smtp(self, subject: str, html_body: str, plain_body: str) -> bool:
        if not self.smtp_user or not self.smtp_password:
            logger.error("SMTP_USER and SMTP_PASSWORD are required for EMAIL_PROVIDER=smtp.")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self.sender_name} <{self.sender}>"
        msg["To"] = self.recipient
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=20) as smtp:
                smtp.login(self.smtp_user, self.smtp_password)
                smtp.sendmail(self.sender, [self.recipient], msg.as_string())
            self._alerts_today += 1
            logger.info(
                "Email sent to %s via SMTP (%d/%d today).",
                self.recipient,
                self._alerts_today,
                self.max_alerts_per_day,
            )
            return True
        except (OSError, smtplib.SMTPException) as exc:
            logger.error("Error sending email via SMTP: %s", exc)
            return False

    def send_alert_batch(
        self,
        all_results: list[FlightResult],
        alerts: list[FlightResult],
        threshold: float,
    ) -> bool:
        if not self._can_send():
            return False
        subject, html_body, plain_body = _build_batch_content(
            all_results=all_results,
            alerts=alerts,
            threshold=threshold,
            currency=self.currency,
        )
        return self._send(subject, html_body, plain_body)

    def send_test_email(self) -> bool:
        if not self._can_send():
            return False
        subject = "[Flight Tracker] Test email"
        plain = "Flight Price Tracker email delivery is configured correctly."
        html_body = """\
<html><body style="font-family:Arial,sans-serif;padding:20px;">
  <h2 style="color:#1a73e8;">Flight Price Tracker is ready</h2>
  <p>Email delivery is configured correctly.</p>
  <p>Add your routes in <code>config.yaml</code>, then run a one-time check or leave the tracker running.</p>
</body></html>"""
        return self._send(subject, html_body, plain)
