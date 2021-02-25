FROM ubuntu:focal

RUN apt-get update && apt-get install -y \
    build-essential \
    autoconf libtool-bin make \
    git

RUN git -c advice.detachedHead=false clone -b 0.0.2 --depth 1 https://github.com/GrumpyOldTroll/libmcrx /tmp/libmcrx
RUN cd /tmp/libmcrx && ./autogen.sh && ./configure && make

RUN DEBIAN_FRONTEND="noninteractive" apt-get install -y cmake
RUN git clone https://github.com/GrumpyOldTroll/mcproxy /tmp/mcproxy && \
    cd /tmp/mcproxy && cmake . && make

FROM ubuntu:focal
LABEL maintainer="jholland@akamai.com"

RUN apt-get update && \
  apt-get install -y \
    python3 \
    python3-pip \
    tcpdump \
    iproute2

RUN apt-get install -y \
    libpcap-dev

# note: Cython has to come before python-libpcap (separate calls).
# installing python-libpcap in the same command with Cython
# fails, whereas installing it afterwards is ok.
# See https://github.com/pypa/pip/issues/2381
# --jake 2020-12
RUN pip3 install Cython && \
  pip3 install \
    h2 twisted pyOpenSSL service_identity watchdog \
    python-libpcap

COPY --from=0 /tmp/libmcrx/mcrx-check /usr/bin/mcrx-check
COPY --from=0 /tmp/mcproxy/mcproxy-bin /usr/bin/mcproxy

RUN mkdir -p /var/run/mnat/ && touch /var/run/mnat/igmp-monitor.sgs && mkdir /etc/mnat

COPY igmpwatch/igmp-monitor.py /bin/igmp-monitor.py
COPY igmpwatch/docker/igmpwatch-start.py /bin/igmpwatch-start.py

ENTRYPOINT ["/bin/igmpwatch-start.py"]

