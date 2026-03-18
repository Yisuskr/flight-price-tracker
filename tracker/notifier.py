"""
notifier.py - Sends price alert emails via Gmail SMTP.
"""

import logging
import smtplib
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from tracker.flight import FlightResult

logger = logging.getLogger(__name__)

# HTML email template for the alert
_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body {{ font-family: Arial, sans-serif; background-color: #f4f6f8; margin: 0; padding: 20px; }}
    .card {{
      background: #ffffff;
      border-radius: 12px;
      padding: 30px;
      max-width: 580px;
      margin: 0 auto;
      box-shadow: 0 2px 10px rgba(0,0,0,0.08);
    }}
    .header {{ text-align: center; margin-bottom: 24px; }}
    .header h1 {{ color: #1a73e8; margin: 0; font-size: 26px; }}
    .route {{ font-size: 18px; font-weight: bold; color: #333; text-align: center; margin: 16px 0; }}
    .price-box {{
      background: #e8f5e9;
      border: 2px solid #43a047;
      border-radius: 8px;
      text-align: center;
      padding: 20px;
      margin: 20px 0;
    }}
    .price-box .label {{ font-size: 13px; color: #555; text-transform: uppercase; letter-spacing: 1px; }}
    .price-box .amount {{ font-size: 42px; font-weight: bold; color: #2e7d32; }}
    .price-box .threshold {{ font-size: 13px; color: #888; margin-top: 6px; }}
    .details-table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
    .details-table td {{ padding: 10px 8px; border-bottom: 1px solid #eeeeee; font-size: 14px; }}
    .details-table td:first-child {{ color: #777; width: 40%; }}
    .details-table td:last-child {{ font-weight: 600; color: #333; }}
    .cta {{
      display: block;
      text-align: center;
      background: #1a73e8;
      color: #fff !important;
      text-decoration: none;
      padding: 14px 28px;
      border-radius: 8px;
      font-size: 15px;
      font-weight: bold;
      margin: 24px auto;
      width: fit-content;
    }}
    .footer {{ text-align: center; font-size: 12px; color: #aaa; margin-top: 24px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>✈ Flight Price Alert</h1>
      <p style="color:#555;margin:4px 0;">A price below your threshold was found!</p>
    </div>

    <div class="route">{origin_name} ({origin}) &rarr; {destination_name} ({destination})</div>

    <div class="price-box">
      <div class="label">Best price found</div>
      <div class="amount">${price:.0f} {currency}</div>
      <div class="threshold">Your alert threshold: ${threshold:.0f} {currency}</div>
    </div>

    <table class="details-table">
      <tr><td>Airline</td><td>{airline}</td></tr>
      <tr><td>Departure date</td><td>{outbound_date}</td></tr>
      <tr><td>Return date</td><td>{return_date}</td></tr>
      <tr><td>Trip type</td><td>{trip_type}</td></tr>
      <tr><td>Duration</td><td>{duration}</td></tr>
      <tr><td>Stops</td><td>{stops}</td></tr>
      <tr><td>Checked at</td><td>{checked_at}</td></tr>
    </table>

    <a class="cta" href="https://www.google.com/flights?hl=en#flt={origin}.{destination}.{outbound_date};c:{currency};e:1;s:0*1;sd:1;t:f">
      Search on Google Flights
    </a>

    <div class="footer">
      Sent by your Flight Price Tracker &bull; MIA &rarr; TFS monitor<br>
      To stop receiving alerts, update <code>price_threshold_usd</code> in your config.yaml.
    </div>
  </div>
</body>
</html>
"""


def _build_message(
    flight: FlightResult,
    threshold: float,
    currency: str,
    recipient: str,
    sender: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"[Flight Alert] MIA→TFS ${flight.price_usd:.0f} {currency} "
        f"({flight.outbound_date})"
    )
    msg["From"] = f"Flight Tracker <{sender}>"
    msg["To"] = recipient

    # Plain text fallback
    plain = (
        f"Price Alert: Miami (MIA) -> Tenerife (TFS)\n\n"
        f"Price found: ${flight.price_usd:.2f} {currency}\n"
        f"Your threshold: ${threshold:.2f} {currency}\n\n"
        f"Airline:        {flight.airline}\n"
        f"Departure:      {flight.outbound_date}\n"
        f"Return:         {flight.return_date or 'N/A (one-way)'}\n"
        f"Duration:       {flight.duration}\n"
        f"Stops:          {'Direct' if flight.stops == 0 else flight.stops}\n"
        f"Checked at:     {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"Search: https://www.google.com/flights\n"
    )

    html = _HTML_TEMPLATE.format(
        origin=flight.origin,
        destination=flight.destination,
        origin_name="Miami",
        destination_name="Tenerife",
        price=flight.price_usd,
        threshold=threshold,
        currency=currency,
        airline=flight.airline,
        outbound_date=flight.outbound_date,
        return_date=flight.return_date or "N/A (one-way)",
        trip_type="Round-trip" if flight.return_date else "One-way",
        duration=flight.duration,
        stops="Direct" if flight.stops == 0 else str(flight.stops),
        checked_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


class Notifier:
    def __init__(self, cfg: dict):
        self.smtp_host: str = cfg["smtp_host"]
        self.smtp_port: int = cfg["smtp_port"]
        self.smtp_user: str = cfg["smtp_user"]
        self.smtp_password: str = cfg["smtp_password"]
        self.recipient: str = cfg["alert_email"]
        self.currency: str = cfg["currency"]
        self.max_alerts_per_day: int = cfg["max_alerts_per_day"]

        # Throttle: track how many emails sent today
        self._alert_date: Optional[date] = None
        self._alerts_today: int = 0

    def _can_send(self) -> bool:
        today = date.today()
        if self._alert_date != today:
            self._alert_date = today
            self._alerts_today = 0
        if self._alerts_today >= self.max_alerts_per_day:
            logger.info(
                "Daily alert limit (%d) reached. Skipping email.", self.max_alerts_per_day
            )
            return False
        return True

    def send_alert(self, flight: FlightResult, threshold: float) -> bool:
        """
        Send a price alert email. Returns True on success.
        Respects the max_alerts_per_day cap to avoid inbox flooding.
        """
        if not self._can_send():
            return False

        msg = _build_message(
            flight=flight,
            threshold=threshold,
            currency=self.currency,
            recipient=self.recipient,
            sender=self.smtp_user,
        )

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.smtp_user, self.recipient, msg.as_string())
            self._alerts_today += 1
            logger.info(
                "Alert email sent to %s (alerts today: %d/%d)",
                self.recipient,
                self._alerts_today,
                self.max_alerts_per_day,
            )
            return True
        except smtplib.SMTPException as exc:
            logger.error("Failed to send alert email: %s", exc)
            return False

    def send_test_email(self) -> bool:
        """Send a test email to verify SMTP credentials are working."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "[Flight Tracker] Test email - setup successful!"
        msg["From"] = f"Flight Tracker <{self.smtp_user}>"
        msg["To"] = self.recipient

        html = """\
        <html><body style="font-family:Arial,sans-serif;padding:20px;">
          <h2 style="color:#1a73e8;">✈ Flight Tracker is running!</h2>
          <p>Your email alerts are configured correctly.</p>
          <p>You will receive a notification whenever a Miami &rarr; Tenerife
             flight drops below your configured price threshold.</p>
        </body></html>"""

        msg.attach(MIMEText("Flight Tracker is running! Email alerts are configured correctly.", "plain"))
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.smtp_user, self.recipient, msg.as_string())
            logger.info("Test email sent to %s", self.recipient)
            return True
        except smtplib.SMTPException as exc:
            logger.error("Failed to send test email: %s", exc)
            return False
