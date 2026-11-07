# Creates image that patches Reolink firmware
FROM ubuntu:26.04

COPY --from=ghcr.io/astral-sh/uv:0.9.25 /uv /uvx /usr/local/bin/

RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 fakeroot mtd-utils liblzo2-2 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/local-ding

COPY payload/local-ding payload/manifest.json payload/dropbearmulti payload/dropbear.json ./payload/
COPY payload/licenses ./payload/licenses/
COPY assets ./assets/
COPY tools ./tools/
COPY local-ding-fw README.md LICENSE THIRD-PARTY.md ./

USER 1000:1000

ENTRYPOINT ["/opt/local-ding/local-ding-fw"]

CMD ["--help"]
