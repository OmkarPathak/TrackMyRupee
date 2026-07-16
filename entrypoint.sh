#!/bin/sh

# Exit immediately if a command exits with a non-zero status
set -e

# Apply database migrations
echo "Applying database migrations..."
python manage.py migrate

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Compile translation messages
echo "Compiling translation messages..."
python manage.py compilemessages

# Execute the passed command (e.g., runserver)
echo "Starting application..."
exec "$@"
