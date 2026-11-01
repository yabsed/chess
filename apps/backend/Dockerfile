FROM docker.io/library/python:3.13-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /bin/uv

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache --no-install-project

COPY app ./app
RUN mkdir /app/data && chown 10001:10001 /app/data
USER 10001:10001

EXPOSE 8000
CMD [".venv/bin/uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--ws-max-size", "4096"]
