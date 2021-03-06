FROM ubuntu:focal

RUN apt-get update && apt-get install -y \
    build-essential \
    autoconf libtool-bin make \
    git

RUN git -c advice.detachedHead=false clone -b 0.0.2 --depth 1 https://github.com/GrumpyOldTroll/libmcrx /tmp/libmcrx
RUN cd /tmp/libmcrx && ./autogen.sh && ./configure && make

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
    h2 twisted pyOpenSSL service_identity watchdog \
    python-libpcap

COPY --from=0 /tmp/libmcrx/mcrx-check /usr/bin/mcrx-check

RUN mkdir /etc/mnat

COPY common/ /tmp/common/
RUN cd /tmp/common && pip3 install .
COPY common/mnat-translate.py /bin/mnat-translate.py
COPY egress/mnat-egress.py /bin/mnat-egress.py
COPY egress/docker/egress-start.py /bin/egress-start.py

ENTRYPOINT ["/bin/egress-start.py"]

