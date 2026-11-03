FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM python:3.15.2-slim-trixie

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY tarka ./tarka

RUN uv sync --frozen --no-dev --no-cache

USER 65532:65532
EXPOSE 8000

CMD ["uvicorn", "tarka.proxy.app:app", "--host", "0.0.0.0", "--port", "8000"]