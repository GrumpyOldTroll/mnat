FROM ubuntu:focal
LABEL maintainer="jholland@akamai.com"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
  apt-get install -y \
    python3 \
    python3-pip \
    smcroute \
    docker.io \
    dnsutils

RUN pip3 install watchdog

RUN mkdir -p /var/run/ingest/ && touch /var/run/ingest/joined.sgs

COPY driad-ingest/docker/driad-start /bin/driad-ingest-start
COPY driad-ingest/driad-ingest-mgr /bin/driad-ingest-mgr

ENTRYPOINT ["/bin/driad-start"]

