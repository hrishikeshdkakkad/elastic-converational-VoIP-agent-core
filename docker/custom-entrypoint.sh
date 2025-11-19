#!/bin/bash
set -e

# Run the original entrypoint to create the default database
docker-entrypoint.sh postgres &

# Wait for the database to be ready
until pg_isready -U "$POSTGRES_USER"; do
  sleep 1
done

# Create the voice_ai database
createdb -U "$POSTGRES_USER" voice_ai || true

# Wait for the original entrypoint to finish
wait
