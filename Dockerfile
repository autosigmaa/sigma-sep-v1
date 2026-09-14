FROM python:3.12-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    SIGMA_DB_PATH=/data/checkpoints.sqlite
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache --python /usr/local/bin/python
COPY sigma ./sigma
RUN useradd --uid 10001 --user-group --create-home sigma \
    && mkdir /data && chown sigma:sigma /data
USER sigma
EXPOSE 8000
CMD ["uvicorn", "sigma.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
