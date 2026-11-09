FROM platform-base-python AS wheels
WORKDIR /build
COPY --from=platform-deps python/build /deps/build
COPY --from=platform-deps python/common /deps/common
COPY environments/build/requirements.lock /locks/build.lock
RUN --network=none python -m pip install --no-index --find-links=/deps/build \
    --require-hashes -r /locks/build.lock
COPY packages/workflow-compiler /workflow-compiler
COPY packages/protocol-contracts /protocol-contracts
COPY services/executor/client /executor-client
RUN --network=none python -m pip wheel --no-deps --no-index --no-build-isolation \
    --wheel-dir /wheels \
    /workflow-compiler /protocol-contracts /executor-client
COPY kernel/pyproject.toml ./
COPY kernel/src ./src
RUN --network=none python -m pip wheel --no-deps --no-index --no-build-isolation \
    --wheel-dir /wheels .

FROM platform-base-python AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KERNEL_DATA_ROOT=/data TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
WORKDIR /app
RUN useradd --system --uid 10001 platform && mkdir /data && chown platform:platform /data
COPY --from=wheels /wheels /wheels
COPY --from=platform-deps python/common /wheels
RUN --network=none python -m pip install --no-cache-dir --no-index --find-links=/wheels platform-kernel-services==7.0.0 && rm -rf /wheels
COPY kernel/vendor/tiktoken/o200k_base.tiktoken /opt/tiktoken-cache/fb374d419588a4632f3f557e76b4b70aebbca790
RUN --network=none python -c "import hashlib,pathlib,tiktoken; p=pathlib.Path('/opt/tiktoken-cache/fb374d419588a4632f3f557e76b4b70aebbca790'); assert hashlib.sha256(p.read_bytes()).hexdigest() == '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'; tiktoken.get_encoding('o200k_base')" && chmod -R a-w /opt/tiktoken-cache
USER 10001
RUN --network=none python -c "import tiktoken; assert tiktoken.get_encoding('o200k_base').encode('domain package')"
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/internal/health', timeout=3)" || exit 1
CMD ["uvicorn", "platform_kernel.main:app", "--host", "0.0.0.0", "--port", "8000"]
