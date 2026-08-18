FROM python:3.12-slim AS build

WORKDIR /src
COPY labs/30-release-pipeline/pyproject.toml labs/30-release-pipeline/README.md ./
COPY labs/30-release-pipeline/src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 10001 relay
COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

USER 10001
EXPOSE 8080
CMD ["python", "-m", "lab_30_release_pipeline.relay_api", "--host", "0.0.0.0", "--port", "8080"]
