"""
notifier.py - Sends price alert emails via SendGrid HTTP API.
(SMTP is blocked on Render free tier — SendGrid uses HTTPS, always works)
"""

import logging
import json
import requests
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from tracker.flight import FlightResult

logger = logging.getLogger(__name__)

_CURRENCY_SYMBOL = {"EUR": "€", "USD": "$", "GBP": "£"}

# ---------------------------------------------------------------------------
# HTML row templates
# ---------------------------------------------------------------------------

_ROW_ALERT = """\
<tr style="background:#e8f5e9;">
  <td style="padding:10px 8px;border-bottom:1px solid #c8e6c9;font-weight:700;color:#2e7d32;">{origin} &#8594; {destination}</td>
  <td style="padding:10px 8px;border-bottom:1px solid #c8e6c9;font-weight:700;color:#2e7d32;">{outbound_date}</td>
  <td style="padding:10px 8px;border-bottom:1px solid #c8e6c9;font-weight:700;color:#2e7d32;">{return_date}</td>
  <td style="padding:10px 8px;border-bottom:1px solid #c8e6c9;font-weight:700;color:#2e7d32;">{airline}</td>
  <td style="padding:10px 8px;border-bottom:1px solid #c8e6c9;font-weight:700;color:#2e7d32;">{duration}</td>
  <td style="padding:10px 8px;border-bottom:1px solid #c8e6c9;font-weight:700;color:#2e7d32;">{layovers}</td>
  <td style="padding:10px 8px;border-bottom:1px solid #c8e6c9;font-weight:700;color:#2e7d32;font-size:16px;">{sym}{price:.0f} &#9989;</td>
  <td style="padding:10px 8px;border-bottom:1px solid #c8e6c9;font-size:11px;color:#1a73e8;">{source}</td>
  <td style="padding:10px 8px;border-bottom:1px solid #c8e6c9;">{book_btn}</td>
</tr>"""

_ROW_NORMAL = """\
<tr>
  <td style="padding:9px 8px;border-bottom:1px solid #eeeeee;color:#555;">{origin} &#8594; {destination}</td>
  <td style="padding:9px 8px;border-bottom:1px solid #eeeeee;color:#555;">{outbound_date}</td>
  <td style="padding:9px 8px;border-bottom:1px solid #eeeeee;color:#555;">{return_date}</td>
  <td style="padding:9px 8px;border-bottom:1px solid #eeeeee;color:#555;">{airline}</td>
  <td style="padding:9px 8px;border-bottom:1px solid #eeeeee;color:#555;">{duration}</td>
  <td style="padding:9px 8px;border-bottom:1px solid #eeeeee;color:#555;">{layovers}</td>
  <td style="padding:9px 8px;border-bottom:1px solid #eeeeee;color:#555;">{sym}{price:.0f}</td>
  <td style="padding:9px 8px;border-bottom:1px solid #eeeeee;font-size:11px;color:#888;">{source}</td>
  <td style="padding:9px 8px;border-bottom:1px solid #eeeeee;">{book_btn}</td>
</tr>"""

_BOOK_BTN = '<a href="{url}" style="background:#1a73e8;color:#fff;text-decoration:none;padding:4px 10px;border-radius:4px;font-size:11px;white-space:nowrap;">Reservar</a>'
_BOOK_BTN_NONE = '<span style="color:#ccc;font-size:11px;">—</span>'

_HTML_BATCH = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body {{ font-family: Arial, sans-serif; background:#f4f6f8; margin:0; padding:20px; }}
    .card {{ background:#fff; border-radius:12px; padding:30px; max-width:800px; margin:0 auto;
             box-shadow:0 2px 10px rgba(0,0,0,0.08); }}
    h1 {{ color:#1a73e8; margin:0 0 4px; font-size:24px; }}
    .subtitle {{ color:#555; font-size:14px; margin-bottom:20px; }}
    .best-price {{ background:#e8f5e9; border:2px solid #43a047; border-radius:8px;
                   text-align:center; padding:16px; margin:20px 0; }}
    .best-price .label {{ font-size:12px; color:#555; text-transform:uppercase; letter-spacing:1px; }}
    .best-price .amount {{ font-size:40px; font-weight:bold; color:#2e7d32; }}
    .best-price .sub {{ font-size:13px; color:#777; margin-top:4px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; margin-top:16px; }}
    th {{ background:#1a73e8; color:#fff; padding:10px 8px; text-align:left; font-size:12px;
          text-transform:uppercase; letter-spacing:.5px; }}
    .cta {{ display:block; text-align:center; background:#1a73e8; color:#fff !important;
            text-decoration:none; padding:13px 28px; border-radius:8px; font-size:15px;
            font-weight:bold; margin:24px auto; width:fit-content; }}
    .footer {{ text-align:center; font-size:12px; color:#aaa; margin-top:24px; }}
  </style>
</head>
<body>
<div class="card">
  <h1>&#9992; Alerta de Precio de Vuelo</h1>
  <div class="subtitle">Tenerife (TFS/TFN) &#8594; Miami (MIA) &bull; Sin equipaje &bull; Ida y vuelta</div>

  <div class="best-price">
    <div class="label">Mejor precio encontrado</div>
    <div class="amount">{sym}{best_price:.0f} {currency}</div>
    <div class="sub">{best_airline} &bull; {best_origin} &#8594; MIA &bull; Sal. {best_outbound} / Vuel. {best_return}</div>
    <div class="sub">Escala(s): {best_layovers}</div>
    <div class="sub" style="color:#888;margin-top:6px;">Tu umbral: {sym}{threshold:.0f} {currency}</div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Ruta</th>
        <th>Salida</th>
        <th>Vuelta</th>
        <th>Aerolinea</th>
        <th>Duración</th>
        <th>Escalas</th>
        <th>Precio</th>
        <th>Fuente</th>
        <th>Reservar</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>

  <a class="cta" href="https://www.google.com/flights?hl=es#flt={best_origin}.MIA.{best_outbound};c:{currency};e:1;s:0*1;sd:1;t:f">
    Buscar en Google Flights
  </a>

  <div class="footer">
    Enviado por tu Flight Price Tracker &bull; TFS/TFN &#8594; MIA monitor<br>
    Revisado: {checked_at} UTC &bull; {n_combos} combinaciones analizadas<br>
    Para ajustar el umbral, edita <code>price_threshold_usd</code> en config.yaml.
  </div>
</div>
</body>
</html>"""


def _render_row(flight: FlightResult, sym: str, is_alert: bool) -> str:
    template = _ROW_ALERT if is_alert else _ROW_NORMAL
    url = flight.booking_url()
    book_btn = _BOOK_BTN.format(url=url) if url else _BOOK_BTN_NONE
    return template.format(
        origin=flight.origin,
        destination=flight.destination,
        outbound_date=flight.outbound_date,
        return_date=flight.return_date or "—",
        airline=flight.airline,
        duration=flight.duration,
        layovers=flight.layovers_str(),
        sym=sym,
        price=flight.price,
        source=getattr(flight, "source", "—"),
        book_btn=book_btn,
    )


def _build_batch_message(
    all_results: list[FlightResult],
    alerts: list[FlightResult],
    threshold: float,
    currency: str,
    recipient: str,
    sender: str,
) -> MIMEMultipart:
    sym = _CURRENCY_SYMBOL.get(currency, currency)
    alert_set = {id(f) for f in alerts}
    best = all_results[0]  # already sorted by price ascending

    rows_html = "\n".join(
        _render_row(f, sym, id(f) in alert_set) for f in all_results
    )

    html = _HTML_BATCH.format(
        sym=sym,
        currency=currency,
        threshold=threshold,
        best_price=best.price,
        best_airline=best.airline,
        best_origin=best.origin,
        best_outbound=best.outbound_date,
        best_return=best.return_date or "—",
        best_layovers=best.layovers_str(),
        rows=rows_html,
        checked_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        n_combos=len(all_results),
    )

    # Plain text fallback
    lines = [
        "ALERTA DE PRECIO — Tenerife -> Miami",
        f"Umbral: {sym}{threshold:.0f} {currency}",
        f"Revisado: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"{'Ruta':<12} {'Salida':<12} {'Vuelta':<12} {'Precio':>8}  {'Aerolinea':<16} {'Duración':<10} Escalas",
        "-" * 95,
    ]
    for f in all_results:
        mark = " <-- ALERTA" if id(f) in alert_set else ""
        lines.append(
            f"{f.origin}->{f.destination:<6} {f.outbound_date:<12} {f.return_date or '—':<12} "
            f"{sym}{f.price:>6.0f}  {f.airline:<16} {f.duration:<10} {f.layovers_str()}{mark}"
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"[Vuelo Alerta] TFS/TFN->MIA {sym}{best.price:.0f} {currency} "
        f"| {best.outbound_date} -> {best.return_date or '—'}"
    )
    msg["From"] = f"Flight Tracker <{sender}>"
    msg["To"] = recipient
    msg.attach(MIMEText("\n".join(lines), "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


class Notifier:
    def __init__(self, cfg: dict):
        self.sendgrid_key: str = cfg.get("sendgrid_api_key", "")
        self.sender: str = cfg["smtp_user"]       # reuse as sender address
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
            logger.info("Límite diario (%d emails) alcanzado.", self.max_alerts_per_day)
            return False
        return True

    def _send(self, subject: str, html: str, plain: str) -> bool:
        if not self.sendgrid_key:
            logger.error("SENDGRID_API_KEY no configurado — no se puede enviar email.")
            return False
        payload = {
            "personalizations": [{"to": [{"email": self.recipient}]}],
            "from": {"email": self.sender, "name": "Flight Tracker"},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": plain},
                {"type": "text/html",  "value": html},
            ],
        }
        try:
            resp = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {self.sendgrid_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload),
                timeout=15,
            )
            if resp.status_code in (200, 202):
                self._alerts_today += 1
                logger.info(
                    "Email enviado a %s via SendGrid (hoy: %d/%d)",
                    self.recipient, self._alerts_today, self.max_alerts_per_day,
                )
                return True
            else:
                logger.error("SendGrid error %d: %s", resp.status_code, resp.text)
                return False
        except requests.RequestException as exc:
            logger.error("Error enviando email via SendGrid: %s", exc)
            return False

    def send_alert_batch(
        self,
        all_results: list[FlightResult],
        alerts: list[FlightResult],
        threshold: float,
    ) -> bool:
        if not self._can_send():
            return False
        msg = _build_batch_message(
            all_results=all_results,
            alerts=alerts,
            threshold=threshold,
            currency=self.currency,
            recipient=self.recipient,
            sender=self.sender,
        )
        # Extract subject, html and plain from MIMEMultipart
        subject = msg["Subject"]
        plain = ""
        html = ""
        for part in msg.walk():
            ct = part.get_content_type()
            raw = part.get_payload(decode=False)
            if not isinstance(raw, (str, bytes)):
                continue
            decoded = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            if ct == "text/plain":
                plain = decoded
            elif ct == "text/html":
                html = decoded
        return self._send(subject, html, plain)

    def send_test_email(self) -> bool:
        subject = "[Flight Tracker] Email de prueba — configuración correcta"
        plain = "Flight Tracker activo. Monitorizando TFS+TFN -> MIA con múltiples combos de fechas."
        html = """\
        <html><body style="font-family:Arial,sans-serif;padding:20px;">
          <h2 style="color:#1a73e8;">&#9992; Flight Tracker activo</h2>
          <p>El email funciona correctamente.</p>
          <p>Monitorizando <strong>TFS y TFN &#8594; MIA</strong> con
             <strong>4 combinaciones de fechas</strong> por ciclo.</p>
          <p>Recibirás un email con tabla comparativa cuando alguna opción baje de tu umbral.</p>
        </body></html>"""
        return self._send(subject, html, plain)
