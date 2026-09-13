FROM python:3.12-slim AS build

WORKDIR /src
COPY labs/38-sigraft-service/pyproject.toml labs/38-sigraft-service/README.md ./
COPY labs/38-sigraft-service/src ./src
COPY labs/38-sigraft-service/config.example.toml ./config.example.toml
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 10001 sigraft
COPY --from=build /wheels /wheels
COPY --from=build /src/config.example.toml /etc/sigraft/config.toml
RUN python -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

USER 10001
EXPOSE 8080
CMD ["python", "-m", "lab_38_sigraft_service.sigraft_service", "--host", "0.0.0.0", "--port", "8080", "--digest", "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "--config", "/etc/sigraft/config.toml", "--environment", "local"]
