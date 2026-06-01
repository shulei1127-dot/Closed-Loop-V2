FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY apps/ apps/
COPY core/ core/
COPY models/ models/
COPY repositories/ repositories/
COPY scheduler/ scheduler/
COPY schemas/ schemas/
COPY services/ services/

RUN pip install --no-cache-dir .

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

COPY apps/ apps/
COPY core/ core/
COPY models/ models/
COPY repositories/ repositories/
COPY scheduler/ scheduler/
COPY schemas/ schemas/
COPY services/ services/
COPY static/ static/
COPY templates/ templates/
COPY migrations/ migrations/
COPY alembic.ini alembic.ini
COPY tests/fixtures/ tests/fixtures/

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONUNBUFFERED=1
EXPOSE 8200

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8200"]