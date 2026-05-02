#!/usr/bin/env bash
docker compose up -d
docker compose exec -T postgres pg_isready -U upwork -d upwork
echo "Postgres ready at localhost:5432"
