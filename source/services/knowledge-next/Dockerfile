FROM platform-base-python AS wheels
WORKDIR /build
COPY --from=platform-deps python/build /deps/build
COPY environments/build/requirements.lock /locks/build.lock
RUN --network=none python -m pip install --no-index --find-links=/deps/build \
    --require-hashes -r /locks/build.lock
COPY packages/embedding-client ./embedding-client
COPY packages/protocol-contracts ./protocol-contracts
COPY services/data/client ./data-client
COPY services/knowledge-next/pyproject.toml ./service/pyproject.toml
COPY services/knowledge-next/src ./service/src
COPY services/knowledge-next/client ./client
RUN --network=none python -m pip wheel --no-deps --no-index --no-build-isolation \
    --wheel-dir /wheels ./embedding-client ./protocol-contracts ./data-client ./service ./client

FROM platform-base-python
WORKDIR /app
RUN useradd --uid 10001 --create-home knowledge
COPY --from=wheels /wheels /wheels
COPY --from=platform-deps python/common /wheels
RUN --network=none python -m pip install --no-cache-dir --no-index --find-links=/wheels \
      platform-knowledge-next==2.0.0 platform-knowledge-client==2.0.0 \
    && rm -rf /wheels
USER knowledge
EXPOSE 8000
CMD ["platform-knowledge", "serve"]
