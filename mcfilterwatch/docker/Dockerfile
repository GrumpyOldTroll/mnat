FROM ubuntu:focal
LABEL maintainer="jholland@akamai.com"

RUN apt-get update && \
  apt-get install -y \
    python3

RUN mkdir /var/run/mcfilterwatch

COPY mcfilterwatch/mcfilterwatch.py /bin/mcfilterwatch.py

ENTRYPOINT ["/bin/mcfilterwatch.py"]

