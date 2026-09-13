FROM python:3.12-slim AS build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /workspace
COPY pyproject.toml README.md ./
RUN python -m venv /opt/relay-venv && \
    /opt/relay-venv/bin/pip install --upgrade pip setuptools wheel
COPY src ./src
RUN /opt/relay-venv/bin/pip install --no-deps .

FROM python:3.12-slim AS runtime
ENV PATH="/opt/relay-venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system relay && useradd --system --gid relay --uid 10001 --home /app relay
WORKDIR /app
COPY --from=build /opt/relay-venv /opt/relay-venv
USER relay:relay
EXPOSE 8080
CMD ["python", "-m", "lab_32_container_deploy.relay_api"]
