FROM ubuntu@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

LABEL org.opencontainers.image.title="Groundwater ADV-02 MF6 spike runtime" \
      org.opencontainers.image.version="6.8.1" \
      org.opencontainers.image.licenses="CC0-1.0" \
      org.opencontainers.image.vendor="U.S. Geological Survey" \
      io.groundwater.spike.scope="INDEPENDENT_ADV_02_SPIKE_ONLY" \
      io.groundwater.mf6.archive.sha256="439a29f3e60f8e88b0ac083db10c92e3474b203d18ca6583863d8464b811ba90" \
      io.groundwater.mf6.binary.sha256="004e333a73605c6b6b048127ce8099e2130c0125e2b161812a49691a8fcdc8f3"

COPY --chmod=0444 code.json /usr/share/doc/modflow6/code.json
COPY --chmod=0444 LICENSE.md /usr/share/licenses/modflow6/LICENSE.md
COPY --chmod=0444 DISCLAIMER.md /usr/share/licenses/modflow6/DISCLAIMER.md
COPY --chmod=0555 mf6 /usr/local/bin/mf6

USER 65532:65532
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/mf6"]
