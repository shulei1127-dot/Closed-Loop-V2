#!/bin/bash
set -e

echo "Waiting for PostgreSQL..."
until python -c "
import psycopg, os
url = os.environ['DATABASE_URL'].replace('+psycopg', '')
psycopg.connect(url).close()
" 2>/dev/null; do
    sleep 2
done
echo "PostgreSQL is ready."

echo "Running database migrations..."
alembic upgrade head
echo "Migrations complete."

echo "Starting application..."
exec "$@"