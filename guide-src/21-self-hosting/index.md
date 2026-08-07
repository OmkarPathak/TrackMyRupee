# Self-Hosting with Docker

Run your own private instance of TrackMyRupee on any Linux server in about 10 minutes.

!!! note "Who this is for"
    Self-hosting gives you full data sovereignty — your transactions never leave your server. This guide covers the Docker Compose path (recommended). For bare-metal Python setup, see [Manual Setup](#manual-python-setup) below.

## Docker Compose setup (recommended)

### Step 1 — Clone the repository

```bash
git clone <repository-url>
cd django-finance-tracker
```

### Step 2 — Create your `.env` file

Create a file named `.env` in the repo root. At minimum you need the core settings:

```env
# ── Required ──────────────────────────────────────────────────────────────
SECRET_KEY='replace-with-a-long-random-string'
DEBUG=False

# ── Database (leave blank to use SQLite) ─────────────────────────────────
# DATABASE_URL='postgres://user:password@localhost:5432/dbname'

# ── Email (choose one) ───────────────────────────────────────────────────
# Option A — Brevo API (recommended for self-hosting)
# BREVO_API_KEY=''
# Option B — SMTP
# EMAIL_HOST='smtp.gmail.com'
# EMAIL_PORT=587
# EMAIL_USE_TLS=True
# EMAIL_HOST_USER=''
# EMAIL_HOST_PASSWORD=''

# ── Optional features ────────────────────────────────────────────────────
# GOOGLE_CLIENT_ID=''          # Google OAuth login
# GOOGLE_CLIENT_SECRET=''
# RAZORPAY_KEY_ID=''           # Payments
# RAZORPAY_KEY_SECRET=''
# RAZORPAY_WEBHOOK_SECRET=''
# VAPID_PUBLIC_KEY=''          # Web Push notifications
# VAPID_PRIVATE_KEY=''
# VAPID_ADMIN_EMAIL='you@example.com'
# GEMINI_API_KEY=''            # AI category insights
# GOOGLE_ANALYTICS_ID=''
# SENTRY_DSN=''
# RECAPTCHA_PUBLIC_KEY=''
# RECAPTCHA_SECRET_KEY=''
```

!!! warning "Never commit `.env` to git"
    `.env` contains your `SECRET_KEY` and any API credentials. Confirm `.env` is in `.gitignore` before pushing.

### Step 3 — Start the containers

```bash
docker-compose up --build
```

On first start the container automatically:

1. Runs database migrations (`python manage.py migrate`)
2. Sets up a demo user with sample data (`python manage.py setup_demo_user`)

This may take 2–3 minutes on first build (downloading the base image).

### Step 4 — Open the app

Navigate to `http://localhost:8000` in your browser.

<!-- TODO: screenshot (desktop, 1280x800) of the self-hosted dashboard on first launch -->
![Self-hosted dashboard on desktop](../screenshots/21-self-hosting/self-hosted-dashboard-desktop.png)

!!! tip "Sign up vs. demo"
    The demo user (`demo` / `demo`) is pre-populated with sample data and is read-only. Click **Create Account** to register your real account.

---

## Manual Python setup

If you prefer to run without Docker:

**Requirements:** Python 3.8+, pip

```bash
# 1. Clone
git clone <repository-url>
cd django-finance-tracker

# 2. Virtual environment
python3 -m venv env
source env/bin/activate          # Windows: env\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env (see Step 2 above — same variables)

# 5. Migrate
python manage.py migrate

# 6. (Optional) Demo data
python manage.py setup_demo_user

# 7. Run
python manage.py runserver
```

---

## Production hardening checklist

Before exposing your instance to the internet:

- [ ] Set `DEBUG=False` in `.env`
- [ ] Set `SECRET_KEY` to a long random string (use `python -c "import secrets; print(secrets.token_hex(50))"`)
- [ ] Put a reverse proxy (Nginx or Caddy) in front of the app for HTTPS
- [ ] Configure `ALLOWED_HOSTS` in `settings.py` or via an env var to your domain
- [ ] Use PostgreSQL instead of SQLite for production (`DATABASE_URL` env var)
- [ ] Enable email (for password reset and notifications)
- [ ] Set up regular database backups

!!! example "Real-world use case"
    Rohan runs his own instance on a ₹600/month Hetzner VPS. He sets `DEBUG=False`, puts Caddy in front for automatic HTTPS, and points his domain `finance.rohan.me` at the server. His family uses the shared instance — each member has their own account — and the data never leaves their own server.

---

## Localization

TrackMyRupee ships with English, Hindi, and Marathi translations. To add or update translations:

```bash
# Requires gettext: brew install gettext (macOS) | sudo apt install gettext (Ubuntu)

# Extract translatable strings
python manage.py makemessages -l mr -l hi

# Apply common financial term translations (utility script)
python update_translations.py

# Compile
python manage.py compilemessages
```

## Related links

- [Getting Started](../01-getting-started/index.md)
- [Mobile App](../20-mobile-app/index.md)
- [FAQ](../22-faq/index.md)
