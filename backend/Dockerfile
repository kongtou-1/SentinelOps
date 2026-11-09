FROM ghcr.io/astral-sh/uv:0.12.21-python3.12-trixie-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-wqy-zenhei && rm -rf /var/lib/apt/lists/*
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::Retries=2 update && apt-get install -y --no-install-recommends git subversion && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
COPY vendor/auto-ontology/pyproject.toml ./vendor/auto-ontology/pyproject.toml
COPY vendor/auto-ontology/auto_ontology ./vendor/auto-ontology/auto_ontology
COPY vendor/auto-ontology/LICENSE vendor/auto-ontology/THIRD_PARTY_NOTICES.md vendor/auto-ontology/OPEN_SOURCE_NOTICE ./vendor/auto-ontology/
RUN uv sync --frozen --no-dev --no-install-project --no-editable
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY collector-compose.yaml ./
COPY scripts/prepare_volumes.py ./prepare_volumes.py
COPY scripts/seed_knowledge.py ./scripts/seed_knowledge.py
# Runtime code/dependencies are read-only; writable data lives in prepared volumes or /tmp.
# Avoid copying the entire Python environment into another layer on every source change.
RUN useradd --uid 1000 --user-group --create-home app && chown app:app /app
USER app
CMD ["/app/.venv/bin/python", "-m", "app.cli.runtime"]
