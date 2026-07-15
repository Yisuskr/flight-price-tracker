# Flight Price Tracker

A configurable flight price tracker that watches the routes and dates you choose, stores price history in SQLite, and sends email alerts when a flight drops below your threshold.

It can run once on demand or stay running continuously with independent schedules for each flight data source.

## Features

- Track any route supported by IATA airport codes.
- Search one or many origins, one or many destinations, and multiple travel dates.
- Supports one-way and round-trip searches.
- Sends email through SendGrid over HTTPS or Gmail SMTP locally.
- Uses optional flight data sources: Google Flights via SerpAPI, Kiwi, Skyscanner, and Aviasales.
- Stores price history in `data/prices.db`.
- Runs locally with Python or Docker.
- Deployable to Render with `render.yaml`.

## Project Structure

```text
flight-price-tracker/
|-- tracker/
|   |-- main.py              # CLI, scheduler, SQLite history
|   |-- config.py            # Loads .env and config.yaml
|   |-- flight.py            # Shared flight result model and SerpAPI source
|   |-- notifier.py          # SendGrid or SMTP email delivery
|   `-- sources/
|       |-- aggregator.py    # Merges source results
|       |-- aviasales.py
|       |-- kiwi.py
|       `-- skyscanner.py
|-- config.yaml              # Public route and alert configuration
|-- .env.example             # Secret/API key template
|-- Dockerfile
|-- docker-compose.yml
|-- render.yaml
`-- requirements.txt
```

## Requirements

- Python 3.12+ or Docker.
- Email delivery credentials:
  - SendGrid API key for hosted deployments.
  - Gmail app password for local SMTP.
- At least one flight data API key:
  - SerpAPI for Google Flights.
  - Kiwi Tequila API.
  - RapidAPI key for Skyscanner.
  - Aviasales / Travelpayouts token.

## Quick Start

Clone the repository:

```bash
git clone https://github.com/Yisuskr/flight-price-tracker.git
cd flight-price-tracker
```

Create your local secrets file:

```bash
cp .env.example .env
```

Fill `.env`:

```env
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=your_sendgrid_api_key_here

# For local Gmail SMTP instead:
# EMAIL_PROVIDER=smtp
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=465
# SMTP_USER=your_gmail@gmail.com
# SMTP_PASSWORD=your_gmail_app_password_here

SENDER_EMAIL=verified_sender@example.com
SENDER_NAME=Flight Tracker

SERPAPI_KEY=your_serpapi_key_here
KIWI_API_KEY=
RAPIDAPI_KEY=
AVIASALES_TOKEN=
```

For SendGrid, `SENDER_EMAIL` must be a verified sender in SendGrid.
For Gmail SMTP, `SENDER_EMAIL` should usually match `SMTP_USER`.

Useful links:

- SendGrid API keys: https://app.sendgrid.com/settings/api_keys
- SendGrid sender authentication: https://app.sendgrid.com/settings/sender_auth
- Gmail app passwords: https://myaccount.google.com/apppasswords

Edit `config.yaml`:

```yaml
alert_email: "recipient@example.com"
price_threshold: 650
currency: "EUR"

origins:
  - "MAD"
destinations:
  - "JFK"

outbound_dates:
  - "2026-04-27"
return_dates:
  - "2026-05-08"

adults: 1
carry_on_bags: 0
checked_bags: 0

max_alerts_per_day: 3
send_summary_when_no_alert: true
```

The tracker checks every combination:

```text
origins x destinations x outbound_dates x return_dates
```

## Run Once

With Docker:

```bash
docker compose run --rm tracker --check-now
```

Without Docker:

```bash
pip install -r requirements.txt
python -m tracker.main --check-now
```

## Run Continuously

With Docker:

```bash
docker compose up -d
docker compose logs -f
```

Without Docker:

```bash
python -m tracker.main
```

Continuous mode uses these intervals from `config.yaml`:

```yaml
google_interval_hours: 8
kiwi_interval_hours: 60
skyscanner_interval_hours: 72
aviasales_interval_hours: 24
```

Leave an API key blank in `.env` to disable that source.

## Email Providers

Hosted deployment, such as Render:

```env
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=your_sendgrid_api_key_here
SENDER_EMAIL=verified_sender@example.com
SENDER_NAME=Flight Tracker
```

Local Gmail SMTP:

```env
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=your_gmail@gmail.com
SMTP_PASSWORD=your_gmail_app_password_here
SENDER_EMAIL=your_gmail@gmail.com
SENDER_NAME=Flight Tracker
```

Gmail SMTP needs a Google app password, not your normal Gmail password.

## Test Email

```bash
docker compose run --rm tracker --test-email
```

or:

```bash
python -m tracker.main --test-email
```

## Deploy to Render

1. Push this repository to GitHub.
2. Create a new Render service from the GitHub repository.
3. Render will use `render.yaml` and the Dockerfile.
4. Add environment variables in Render:
   - `EMAIL_PROVIDER=sendgrid`
   - `SENDGRID_API_KEY`
   - `SENDER_EMAIL`
   - `SENDER_NAME`
   - Any flight source keys you want to use.
5. Optional: add a persistent disk mounted at `/app/data` if you want SQLite history to survive redeploys.

## Configuration Reference

| Key | Description | Default |
| --- | --- | --- |
| `alert_email` | Recipient email for alerts | required |
| `price_threshold` | Alert when price is less than or equal to this value | required |
| `currency` | Price currency requested from APIs | `EUR` |
| `origins` | List of origin IATA airport codes | required |
| `destinations` | List of destination IATA airport codes | required |
| `outbound_dates` | List of departure dates, `YYYY-MM-DD` | required |
| `return_dates` | List of return dates; empty list means one-way | `[]` |
| `adults` | Number of adult passengers | `1` |
| `carry_on_bags` | Carry-on bags requested where supported | `0` |
| `checked_bags` | Checked bags requested where supported | `0` |
| `max_alerts_per_day` | Maximum emails sent per day | `3` |
| `send_summary_when_no_alert` | Send a summary even if no price is below threshold | `true` |
| `google_interval_hours` | Continuous interval for SerpAPI / Google Flights | `8` |
| `kiwi_interval_hours` | Continuous interval for Kiwi | `60` |
| `skyscanner_interval_hours` | Continuous interval for Skyscanner | `72` |
| `aviasales_interval_hours` | Continuous interval for Aviasales | `24` |
| `initial_sources` | Sources to run immediately at startup | `["google", "aviasales"]` |

## Price History

Every check is stored in SQLite:

```bash
sqlite3 data/prices.db "SELECT checked_at, origin, destination, airline, price, currency, source FROM price_history ORDER BY checked_at DESC LIMIT 10;"
```

## Notes

- API providers have different quotas and response shapes.
- Some sources may return no data for low-traffic routes.
- Use `EMAIL_PROVIDER=sendgrid` for hosted deployments because many free hosts block SMTP ports.
- Use `EMAIL_PROVIDER=smtp` with a Gmail app password for local runs from your own computer.
- `.env` is ignored by Git and should never be committed.

## License

MIT
