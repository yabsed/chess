FROM docker.io/library/python:3.14-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /bin/uv

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache --no-install-project

COPY app ./app
ADD --chmod=644 https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem /etc/ssl/certs/rds.pem
USER 10001:10001

EXPOSE 8000
CMD [".venv/bin/uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--ws-max-size", "4096"]
