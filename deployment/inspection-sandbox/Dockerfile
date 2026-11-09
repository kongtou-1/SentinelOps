FROM python:3.12-slim-bookworm
WORKDIR /runner
COPY server.py /runner/server.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "-I", "/runner/server.py"]
