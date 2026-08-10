# Self-Hosting with Docker

Run your own private instance of TrackMyRupee on any Linux server in about 10 minutes.

!!! note "Who this is for"
    Self-hosting gives you full data control. Your transactions never leave your server. This guide covers the Docker Compose path, which is the recommended approach. For a bare-metal Python setup without Docker, see the [Manual Python Setup](#5-manual-python-setup) section below.

---

## 1. Clone the Repository

```bash
git clone <repository-url>
cd django-finance-tracker
```

---

## 2. Create Your Environment File

Create a file named `.env` in the repository root. At minimum you need the following settings:

```env
# Required
SECRET_KEY='replace-with-a-long-random-string'
DEBUG=False

# Database (leave blank to use SQLite)
# DATABASE_URL='postgres://user:password@localhost:5432/dbname'

# Email: choose one option below

# Option A: Brevo API (recommended for self-hosting)
# BREVO_API_KEY=''

# Option B: SMTP
# EMAIL_HOST='smtp.gmail.com'
# EMAIL_PORT=587
# EMAIL_USE_TLS=True
# EMAIL_HOST_USER=''
# EMAIL_HOST_PASSWORD=''

# Optional features
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

!!! warning "Never commit .env to git"
    Your `.env` file contains your `SECRET_KEY` and any API credentials. Confirm that `.env` is listed in your `.gitignore` file before pushing any code.

---

## 3. Start the Containers

```bash
docker-compose up --build
```

On first start, the container automatically runs database migrations and sets up a demo user with sample data. This may take 2 to 3 minutes on first build while it downloads the base image.

---

## 4. Open the App

Navigate to `http://localhost:8000` in your browser.

!!! tip "Sign up vs. demo"
    The demo user (`demo` / `demo`) is pre-populated with sample data and is read-only. Click **Create Account** to register your real account.

---

## 5. Manual Python Setup

If you prefer to run without Docker, follow these steps.

**Requirements**: Python 3.8 or later, pip

```bash
# 1. Clone
git clone <repository-url>
cd django-finance-tracker

# 2. Create a virtual environment
python3 -m venv env
source env/bin/activate   # On Windows: env\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env (use the same variables from Step 2 above)

# 5. Run migrations
python manage.py migrate

# 6. Optional: load demo data
python manage.py setup_demo_user

# 7. Start the server
python manage.py runserver
```

---

## 6. Production Hardening Checklist

Before exposing your instance to the internet, complete the following steps:

- [ ] Set `DEBUG=False` in `.env`
- [ ] Set `SECRET_KEY` to a long random string (generate one with `python -c "import secrets; print(secrets.token_hex(50))"`)
- [ ] Put a reverse proxy such as Nginx or Caddy in front of the app for HTTPS
- [ ] Set `ALLOWED_HOSTS` in `settings.py` or via an environment variable to your domain
- [ ] Use PostgreSQL instead of SQLite for production by setting the `DATABASE_URL` variable
- [ ] Enable email for password reset and notifications
- [ ] Set up regular database backups

!!! example "Real-world use case"
    Rohan runs his own instance on a Rs. 600 per month Hetzner VPS. He sets DEBUG=False, puts Caddy in front for automatic HTTPS, and points his domain `finance.rohan.me` at the server. His family uses the shared instance with each member having their own account, and the data never leaves their own server.

---

## 7. Localization

TrackMyRupee ships with English, Hindi, and Marathi translations. To add or update translations:

```bash
# Requires gettext
# macOS: brew install gettext
# Ubuntu: sudo apt install gettext

# Extract translatable strings
python manage.py makemessages -l mr -l hi

# Apply common financial term translations
python update_translations.py

# Compile
python manage.py compilemessages
```

---

## Related Links
- [Getting Started](../01-getting-started/index.md)
- [Mobile App](../20-mobile-app/index.md)
- [FAQ](../22-faq/index.md)
