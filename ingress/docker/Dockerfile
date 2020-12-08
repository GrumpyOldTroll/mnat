FROM ubuntu:focal
LABEL maintainer="jholland@akamai.com"

RUN apt-get update && \
  apt-get install -y \
    python3 \
    python3-pip \
    libpcap-dev

# note: Cython has to come before python-libpcap (separate calls).
# installing python-libpcap in the same command with Cython
# fails, whereas installing it afterwards is ok.
# See https://github.com/pypa/pip/issues/2381
# --jake 2020-12
RUN pip3 install Cython && \
  pip3 install \
    h2 twisted pyOpenSSL service_identity \
    python-libpcap

RUN mkdir -p /var/run/mnat/ && touch /var/run/mnat/ingress-joined.sgs && mkdir /etc/mnat

COPY common/ /tmp/common/
RUN cd /tmp/common && pip3 install .
COPY common/mnat-translate /bin/mnat-translate
COPY ingress/mnat-ingress /bin/mnat-ingress
COPY ingress/docker/ingress-start /bin/ingress-start

ENTRYPOINT ["/bin/ingress-start"]

