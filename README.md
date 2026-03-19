# ✈ Flight Price Tracker — Miami → Tenerife

A 24/7 flight price monitoring bot that watches **Miami (MIA) → Tenerife (TFS + TFN)** on Google Flights and sends you an email the moment the price drops below your threshold. Monitors both Tenerife airports simultaneously and shows prices in EUR.

Built with Python, SerpAPI, Gmail SMTP, and Docker. Deployable for free on Railway.

---

## Features

- Checks Google Flights on a configurable interval (default: every 6 hours)
- Searches **both Tenerife airports simultaneously**: TFS (Sur / Reina Sofia) and TFN (Norte / Los Rodeos)
- Supports one-way and round-trip searches
- Prices in **EUR** by default (configurable)
- Sends a beautifully formatted HTML email when the price drops below your limit, showing which airport is cheaper
- Throttles alerts (configurable max emails per day) to avoid inbox spam
- Stores full price history in a local SQLite database
- Runs in Docker — one command to start, runs forever

---

## Project Structure

```
flight-price-tracker/
├── tracker/
│   ├── __init__.py
│   ├── main.py        # Scheduler loop, CLI entry point, SQLite history
│   ├── flight.py      # Google Flights data fetching via SerpAPI
│   ├── notifier.py    # Gmail SMTP email alerts
│   └── config.py      # Loads .env + config.yaml
├── config.yaml        # Your travel dates, price threshold, intervals
├── .env.example       # Credentials template (copy to .env)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Prerequisites

| Service | What you need | Free tier |
|---------|--------------|-----------|
| [SerpAPI](https://serpapi.com/) | API key | 100 searches/month |
| Gmail | Account + App Password | Free |
| [Railway](https://railway.app/) | Account | $5 free credit/month |

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/flight-price-tracker.git
cd flight-price-tracker
```

### 2. Set up credentials

```bash
cp .env.example .env
```

Edit `.env` with your real values:

```env
SERPAPI_KEY=your_serpapi_key_here
SMTP_USER=your.gmail@gmail.com
SMTP_PASSWORD=your_16_char_app_password
```

> **Gmail App Password:** Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), create a new app password, and paste the 16-character code (no spaces) as `SMTP_PASSWORD`. You must have 2-Step Verification enabled.

### 3. Configure your alert

Edit `config.yaml`:

```yaml
alert_email: "your@email.com"       # Where alerts are sent
price_threshold_usd: 600             # Alert when price <= this
outbound_date: "2026-06-15"          # Your travel date
return_date: "2026-06-30"            # null for one-way
check_interval_hours: 6              # How often to check
max_alerts_per_day: 3                # Max emails per day
```

### 4. Run locally with Docker

```bash
# Build and start (runs 24/7 in background)
docker compose up -d

# View live logs
docker compose logs -f

# Stop
docker compose down
```

### 5. Test your setup

```bash
# Send a test email to verify SMTP is working
docker compose run --rm tracker --test-email

# Run a single price check right now
docker compose run --rm tracker --check-now
```

---

## Deploy to Railway (free, 24/7)

Railway gives you a free hobby plan that keeps your container running continuously.

### Steps

1. Push your code to GitHub (make sure `.env` is in `.gitignore` — it already is).

2. Go to [railway.app](https://railway.app/) and create a new project:
   - **New Project → Deploy from GitHub repo**
   - Select your `flight-price-tracker` repository

3. Add environment variables in Railway's dashboard:
   - Go to your service → **Variables** tab
   - Add `SERPAPI_KEY`, `SMTP_USER`, `SMTP_PASSWORD`

4. Railway auto-detects the `Dockerfile` and deploys it. Your tracker starts immediately.

5. To persist the SQLite database across redeploys, add a **Volume** in Railway:
   - Service → **Volumes** → Add volume → mount path `/app/data`

That's it. Your tracker is now live 24/7 at zero cost.

---

## Running Without Docker

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill credentials
cp .env.example .env

# Send test email
python -m tracker.main --test-email

# Run single check
python -m tracker.main --check-now

# Run continuously
python -m tracker.main
```

---

## Price History

Every check is recorded in `data/prices.db` (SQLite). You can query it directly:

```bash
sqlite3 data/prices.db "SELECT checked_at, airline, price_usd, stops FROM price_history ORDER BY price_usd ASC LIMIT 10;"
```

---

## Configuration Reference

| Key | Description | Default |
|-----|-------------|---------|
| `alert_email` | Recipient email for alerts | required |
| `price_threshold_usd` | Alert when price ≤ this | required |
| `outbound_date` | Departure date (YYYY-MM-DD) | required |
| `return_date` | Return date or `null` for one-way | `null` |
| `origin` | IATA origin code | `MIA` |
| `destination` | IATA destination code | `TFS` |
| `adults` | Number of passengers | `1` |
| `currency` | Price currency | `USD` |
| `check_interval_hours` | Hours between checks | `6` |
| `max_alerts_per_day` | Max alert emails per day | `3` |
| `smtp_host` | SMTP server | `smtp.gmail.com` |
| `smtp_port` | SMTP port | `587` |

---

## SerpAPI Free Tier Usage

The free tier includes **100 searches/month**. At the default 6-hour interval that's ~120 searches/month — slightly over the free limit. Adjust the interval to stay within quota:

| Interval | Searches/month |
|----------|---------------|
| 6 hours | ~120 (slightly over free tier) |
| 8 hours | ~90 |
| 12 hours | ~60 |
| 24 hours | ~30 |

---

## License

MIT
