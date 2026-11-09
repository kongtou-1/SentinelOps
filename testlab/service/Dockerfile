FROM sentinelops-backend:local
WORKDIR /lab
COPY service.py ./service.py
ARG REVISION
LABEL org.opencontainers.image.title="SentinelOps test business" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.revision=$REVISION
