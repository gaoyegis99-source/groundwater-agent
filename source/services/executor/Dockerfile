FROM platform-base-python AS wheels
WORKDIR /build
COPY --from=platform-deps python/build /deps/build
COPY environments/build/requirements.lock /locks/build.lock
RUN --network=none python -m pip install --no-index --find-links=/deps/build \
    --require-hashes -r /locks/build.lock
COPY packages/protocol-contracts ./protocol-contracts
COPY services/data/client ./data-client
COPY services/executor/pyproject.toml ./executor/pyproject.toml
COPY services/executor/src ./executor/src
RUN --network=none python -m pip wheel --no-deps --no-index --no-build-isolation \
    --wheel-dir /wheels ./protocol-contracts ./data-client ./executor

FROM platform-base-python AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 EXECUTOR_STATE_ROOT=/data/state EXECUTOR_OBJECT_ROOT=/data/objects EXECUTOR_WORK_ROOT=/work
WORKDIR /app
RUN useradd --system --uid 10001 platform && mkdir -p /data/state /data/objects /work && chown -R platform:platform /data /work
COPY --from=wheels /wheels /wheels
COPY --from=platform-deps python/common /wheels
RUN --network=none python -m pip install --no-cache-dir --no-index --find-links=/wheels platform-executor-service==7.0.0 && rm -rf /wheels
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/internal/health/live', timeout=3)" || exit 1
CMD ["uvicorn", "platform_executor_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
