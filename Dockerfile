FROM python:3.12-slim

WORKDIR /app

# Build deps for any remaining source wheels; psycopg2-binary itself ships libpq,
# but keeping these is cheap and guards against future dep swaps.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --shell /bin/bash --uid 1000 sentinel \
    && chown -R sentinel:sentinel /app
USER sentinel

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Entrypoint runs a local preflight first; on failure the container exits
# immediately instead of entering a crash-loop hiding a config bug.
# Override via `command:` or `CMD` — see compose examples.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["bot"]
