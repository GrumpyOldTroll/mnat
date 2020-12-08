FROM ubuntu:focal
LABEL maintainer="jholland@akamai.com"

RUN apt-get update && \
  apt-get install -y \
    python3 \
    python3-pip \
    git

# There's a patch on jetconf not yet upstreamed.  The patch was made
# against commit 487d7a5f362e04f17dfab141355b240700c1cb85
# --jake 2020-12
RUN git clone https://github.com/CZ-NIC/jetconf.git
COPY server/jetconf.patch /tmp/jetconf.patch
RUN patch -d jetconf -p 1 -i /tmp/jetconf.patch
RUN ( cd jetconf && pip3 install . )

COPY server/docker/jetconf-config.yaml /etc/mnat/
COPY server/docker/server-start /bin/server-start

COPY server/module/ /tmp/mnat_module/
RUN ( cd /tmp/mnat_module && pip3 install . )

COPY server/files/ /etc/mnat/

ENTRYPOINT ["/bin/server-start"]

