# Test-only renderer; never used by API or workers.
FROM sentinelops-backend:local
USER root
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends libreoffice-writer poppler-utils && rm -rf /var/lib/apt/lists/*
RUN uv pip install --python /app/.venv/bin/python pdf2image
USER app
