#!/bin/bash
set -e

echo "Running database migrations..."

# Wait for database to be ready
until PGPASSWORD=$POSTGRES_PASSWORD psql -h $DB_HOST -U $POSTGRES_USER -d $POSTGRES_DB -c '\q' 2>/dev/null; do
  echo "Waiting for PostgreSQL to be ready..."
  sleep 2
done

# Create voice_ai database if it doesn't exist
PGPASSWORD=$POSTGRES_PASSWORD psql -h $DB_HOST -U $POSTGRES_USER -d $POSTGRES_DB <<-EOSQL
    SELECT 'CREATE DATABASE voice_ai'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'voice_ai')\gexec
EOSQL

echo "Database voice_ai ready"

# Prepare Alembic connection URL (falls back to DATABASE_URL converted to sync driver)
if [ -z "$ALEMBIC_DATABASE_URL" ]; then
  if [[ "$DATABASE_URL" == postgresql+asyncpg://* ]]; then
    export ALEMBIC_DATABASE_URL="${DATABASE_URL/+asyncpg/}"
  else
    export ALEMBIC_DATABASE_URL="${DATABASE_URL:-postgresql://temporal:temporal@postgresql:5432/voice_ai}"
  fi
fi

# Run Alembic migrations with uv
echo "Applying database migrations..."
ALEMBIC_DATABASE_URL="$ALEMBIC_DATABASE_URL" uv run alembic upgrade head

echo "Migrations complete"

# Start the application
exec "$@"
