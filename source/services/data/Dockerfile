FROM platform-base-postgres AS pgtools
FROM platform-base-python AS wheels
WORKDIR /build
COPY --from=platform-deps python/build /deps/build
COPY environments/build/requirements.lock /locks/build.lock
RUN --network=none python -m pip install --no-index --find-links=/deps/build \
    --require-hashes -r /locks/build.lock
COPY services/data/pyproject.toml ./service/pyproject.toml
COPY services/data/src ./service/src
COPY services/data/client ./client
COPY packages/protocol-contracts ./protocol-contracts
RUN --network=none python -m pip wheel --no-deps --no-index --no-build-isolation \
    --wheel-dir /wheels ./protocol-contracts ./service ./client

FROM platform-base-python
COPY --from=platform-deps system/data /debs
RUN --network=none set -eu; \
    printf '%s\n' 'deb [trusted=yes] file:/debs ./' > /tmp/locked-debs.list; \
    : > /debs/Packages; \
    for archive in /debs/*.deb; do \
        [ -e "$archive" ] || continue; \
        filename=${archive##*/}; \
        dpkg-deb -f "$archive" >> /debs/Packages; \
        printf 'Filename: ./%s\nSize: %s\nSHA256: %s\n\n' \
            "$filename" "$(stat -c %s "$archive")" \
            "$(sha256sum "$archive" | cut -d ' ' -f 1)" >> /debs/Packages; \
    done; \
    if [ -s /debs/Packages ]; then \
        apt-get -qq -o Dir::Etc::sourcelist=/tmp/locked-debs.list \
            -o Dir::Etc::sourceparts=- -o Acquire::Languages=none update; \
        set --; \
        for archive in /debs/*.deb; do \
            package=$(dpkg-deb -f "$archive" Package); \
            version=$(dpkg-deb -f "$archive" Version); \
            set -- "$@" "$package=$version"; \
        done; \
        DEBIAN_FRONTEND=noninteractive apt-get -qq -y --no-install-recommends \
            --allow-downgrades -o Dir::Etc::sourcelist=/tmp/locked-debs.list \
            -o Dir::Etc::sourceparts=- install "$@"; \
        test -z "$(dpkg --audit)"; \
    fi; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb \
        /tmp/locked-debs.list /debs
COPY --from=pgtools /usr/lib/postgresql/16/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pgtools /usr/lib/postgresql/16/bin/pg_restore /usr/local/bin/pg_restore
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_ROOT=/data
WORKDIR /app
RUN useradd --system --uid 10001 platform && mkdir /data /backups && chown platform:platform /data /backups
COPY --from=wheels /wheels /wheels
COPY --from=platform-deps python/common /wheels
RUN --network=none python -m pip install --no-index --find-links=/wheels platform-data-service==3.0.0 platform-data-client==2.0.0 && rm -rf /wheels
COPY services/data/deploy/seccomp.json /app/seccomp.json
USER 10001
EXPOSE 8000
CMD ["uvicorn", "platform_data_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
