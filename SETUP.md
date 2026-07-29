
## Manual Setup

-   Python 3.8+
-   pip (Python package manager)

1.  **Clone the repository**:
    ```bash
    git clone <repository-url>
    cd django-finance-tracker
    ```

2.  **Create a virtual environment** (optional but recommended):
    ```bash
    python3 -m venv env
    source env/bin/activate  # On Windows use `env\Scripts\activate`
    ```

3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Create `.env` file**:
    Create a `.env` file in the root directory. At a minimum, for local development you need:
    ```env
    SECRET_KEY='your-secret-key-here'
    DEBUG=True
    USE_SQLITE=True
    ```

5.  **Apply database migrations**:
    ```bash
    python manage.py migrate
    ```

6.  **Setup Demo User** (Optional but recommended):
    ```bash
    python manage.py setup_demo_user
    ```

    **Note**: This command populates the database with sample data for the demo mode.


## Self-Hosted Expense Tracker (Docker)

1.  **Clone the repository**:
    ```bash
    git clone <repository-url>
    cd django-finance-tracker
    ```

2.  **Create `.env` file**:
    Create a file named `.env` in the root directory. Below are all the environment variables supported by the application. Fill in the ones you need based on the features you want to enable:
    ```env
    # Required Core Settings
    SECRET_KEY='your-secret-key-here'
    DEBUG=True

    # Database Settings (Leave blank or remove to use SQLite by default)
    # USE_SQLITE=False
    # DATABASE_URL='postgres://user:password@localhost:5432/dbname'

    # Email Settings (Choose Brevo API or SMTP)
    BREVO_API_KEY=''
    # OR
    EMAIL_HOST='smtp.gmail.com'
    EMAIL_PORT=587
    EMAIL_USE_TLS=True
    EMAIL_HOST_USER=''
    EMAIL_HOST_PASSWORD=''

    # Authentication / Google OAuth
    GOOGLE_CLIENT_ID=''
    GOOGLE_CLIENT_SECRET=''

    # Payments (Razorpay)
    RAZORPAY_KEY_ID=''
    RAZORPAY_KEY_SECRET=''
    RAZORPAY_WEBHOOK_SECRET=''

    # Push Notifications (Web Push)
    VAPID_PUBLIC_KEY=''
    VAPID_PRIVATE_KEY=''
    VAPID_ADMIN_EMAIL='support@example.com'

    # AI Insights
    GEMINI_API_KEY=''

    # Analytics & Tracking
    GOOGLE_ANALYTICS_ID=''
    SENTRY_DSN=''
    
    # reCAPTCHA
    RECAPTCHA_PUBLIC_KEY=''
    RECAPTCHA_SECRET_KEY=''
    ```
    **Note**: The application will not run correctly without at least the Core Settings in this file.

3.  **Run with Docker Compose**:
    ```bash
    docker-compose up --build
    ```
    **Note**: The container defaults to running migrations and setting up the demo user automatically on startup.

4.  **Access the application**:
    Open your browser and navigate to `http://localhost:8000`.


### Getting Started

1.  **Sign Up**: Create a new account.
2.  **Add Expenses**: Manually add expenses or use the "Upload More" button.
3.  **View Dashboard**: authenticating will take you to the dashboard where you can filter and analyze your spending.

### Bulk Upload Format

To upload expenses via Excel, ensure your file follows this format:

-   **File Type**: `.xlsx`
-   **Sheets**: You can have multiple sheets (e.g., "Jan", "Feb").
-   **Columns**: The following columns are strictly required (headers are case-insensitive):
    -   `Date`: Supported formats: `DD MMM YYYY` (e.g., 01 Jan 2025), `YYYY-MM-DD`.
    -   `Amount`: Numeric value.
    -   `Description`: Text description.
    -   `Category`: Expense category (e.g., Food, Travel).

**Note**: When uploading, you will be asked to select a "Target Year". This year will override the year in the Excel dates to ensure data consistency.


## Localization / Internationalization

TrackMyRupee supports multi-language interfaces (currently English, Hindi, and Marathi).

### Development setup
To work with translations locally, you must have `gettext` installed:
- **macOS**: `brew install gettext`
- **Ubuntu/Debian**: `sudo apt-get install gettext`

### Translation Workflow
1. **Update strings**: Add `{% trans "String" %}` in templates or `gettext` in Python files.
2. **Extract messages**:
   ```bash
   python manage.py makemessages -l mr -l hi
   ```
3. **Apply translations**:
   Use our utility script to automatically apply translations for common financial terms:
   ```bash
   python update_translations.py
   ```
4. **Compile messages**:
   ```bash
   python manage.py compilemessages
   ```

