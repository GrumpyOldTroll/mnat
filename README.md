# Intro

This is a prototype for [MNAT](https://datatracker.ietf.org/doc/draft-jholland-mboned-mnat/).  There are several distinct docker images:

 * mnat-ingress: web client that talks to mnat-server from the data ingress side (near the sender or the ingest point) and translates from the upstream global (Sg,Gg) addresses to a local network\'s (Sl,Gl) addresses downstream of the ingress, for transport within the network.
 * mnat-egress: web client that talks to mnat-server from the data egress side (near the receiver), and translates from the upstream network\'s (Sl,Gl) addresses to the global (Sg,Gg) addresses downstream of the egress, for delivering the global traffic to the receiver.
 * mnat-server: web service providing the mapping info between the local (Sl,Gl) addresses and the global (Sg,Gg) addresses.
 * driad-ingest: a launcher of AMT gateway instances, much like the ingest-rtr from [multicast-ingest-platform](https://github.com/GrumpyOldTroll/multicast-ingest-platform), but firing off updates from edits to a joinfile (which mnat-ingress can do) rather than PIM packets.

mnat-ingress can produce either an upstream join on a target interface, or can produce a "joinfile" that can be consumed by an ingest manager that launches AMT gateways based on the [multicast-ingest-platform](https://github.com/GrumpyOldTroll/multicast-ingest-platform).

mnat-egress will listen for downstream IGMP and translate to an upstream join.
(This would be extensible to support PIM with a separation on joinfiles the same way mnat-ingress does, so pleaset let me know if you're interested in that use case, but for now it's combined into one container.)

# Running

## Server

To run the server in a docker container without a web server in front, you'll need to generate keys.
The [server/README.md](server/README.md) has a section with commands and some helper scripts to do that.
The ca.pem file will need to be copied to the ingress and egress nodes unless the server cert is signed by a trusted root in their environment's default certificate authority (for instance, in `/etc/ssl/certs` in most linuxes).

~~~
PORT=8443
SERVERCERT=/home/user/server_sample-net.crt
SERVERKEY=/home/user/server_sample-net.key
CLIENTCA=/home/user/ca.pem
POOL=/home/user/pool.json
MNATV=0.0.4

sudo docker run \
    --name mnat-server \
    -d --restart=unless-stopped \
    --log-opt max-size=2m --log-opt max-file=5 \
    -p $PORT:8443/tcp \
    -v $SERVERKEY:/etc/mnat/server.key \
    -v $SERVERCERT:/etc/mnat/server.crt \
    -v $CLIENTCA:/etc/mnat/clientca.pem \
    -v $POOL:/etc/mnat/pool.json \
    grumpyoldtroll/mnat-server:${MNATV}
~~~

This is a docker container that provides an H2 interface.
In addition to being able to run directly on a listening port, it can be fronted by web servers that can use an H2 backend, for example [apache](https://httpd.apache.org/docs/trunk/mod/mod_proxy_http2.html) and [nginx](https://www.nginx.com/blog/http2-module-nginx/).

However, note that at the time of this writing, mnat-ingress and mnat-egress require an H2 server, and apache only has backend support, and proxies as Http 1.1.
nginx does provide h2 for both the backend and proxying.

The above command runs the container with the H2 listening port exposed to receive inbound connections into a [jetconf](https://pypi.org/project/jetconf/) instance running inside the container.
If you want to front it with nginx, please refer to the nginx setup guides for configuration instructions.

### Assignment Pool

The `pool.json` file used above is the input file to control the local pool of available addresses.
The spec does not provide a standardized input here, as there may be many business constraints in different environments.
However, this implementation tries to provide some flexibility.
There's an example in [server/files/pool.json](server/files/pool.json) that might be OK for some networks.

The file is json, and is structured like this:

~~~
{
  "group-pool": {
    "ranges": [
      {
        "group-range": "239.192.0.0/14",
        "exclude": [
          {
            "group-range": "239.195.255.0/24"
          }
        ],
        "source-range": "keep",
      }
    ],
    "default-source-range": "10.10.10.10/32"
  }
}
~~~

The "group-pool" field is at the top level.

It contains a "ranges" list that define range objects and a "default-source-range" field.
If not provided, the default-source-range default value is "keep".

The range objects have a "group-range" and an optional "source-range", plus an optional "exclude" list.

The permitted entries from the available (S,G)s in the group-pool are chosen by a uniform random selection from the possible choices.
(So a group-range IPv4 with a /24 would have 256 options, and if there is a source-range with a /30 there would be 256\*4 options from the extra 4 sources.)

The "exclude" field excluding a range inside the given group-range.
It must be wholly inside the parent group-range if provided, or the top-level group-range will produce a warning at the beginning of the log and be ignored.

If the "source-range" is not provided, the "default-source-range" from the top "group-pool" object is used.
It can either be a unicast IP address and prefix with the same address family as the group range, or it can have one of 2 special values: "keep" or "asm".
"keep" means to use SSM with the original global (S,G)'s source, and "asm" means to use a (\*,G) ASM join for the local assignment.

Each object also can have an optional "note" field that's unstructured text, ignored by the server.
Other unknown values produce a warning and are ignored.

## Ingress

In MNAT, an ingress node will translate traffic from globally addressed (S,G)s to locally addressed (S,G)s in accordance with the MNAT spec, based on the information it receives from the MNAT server.

It also is responsible for signaling upstream group membership using the global addresses.

Prerequisites to use this implementation of the mnat-ingress node:

 - docker  (e.g. sudo apt install docker.io)

These setup instructions only work on a linux-based OS.

### Deployment models

This mnat-ingress implementation allows for upstream advertisement of its membership state for global (S,G)s with 2 different modes:

 * via IGMPv3/MLDv2, sending membership reports on a given interface; or
 * via a "joinfile", advertising current join state that can be acted on by the driad-ingest container described below, to ingest traffic over AMT from outside the network.

#### Running with the driad-ingest service

When running with the driad-ingest service, the mnat-ingress container will update the `/var/run/mnat/ingress-joined.sgs` file within its container so that it contains a line with `source-ip,group-ip` for each upstream joined (S,G).

Those updates happen in response to changes published by the mnat-server it's connected to.

The driad-ingest container can watch this file and launch new AMT gateways as needed according to [DRIAD](https://tools.ietf.org/html/rfc8777)-based discovery.

However, the ingress container needs an upstream interface to receie traffic on regardless of whether it's producing upstream joins.
This upstream interface should see native multicast traffic arriving in response to the ingress's join actions.

##### Configuring a virtual interface

When running with an ingest container instead of with an upstream that joins, I recommend connecting a macvlan docker network to one interface of a veth pair, as follows:

~~~
sudo bash -e -x << EOF
/sbin/ip link add dev veth0 type veth peer name veth1
sleep 1
/sbin/ip addr add 10.10.200.254/24 dev veth0
/sbin/ip addr add 10.10.200.1/24 dev veth1

/sbin/ip link set up dev veth0
/sbin/ip link set up dev veth1
EOF

sudo docker network create \
    --driver macvlan \
    --subnet=10.10.200.0/24 --gateway=10.10.200.1 \
    --opt parent=veth0 \
    mcast-native-ingest
~~~

With the commands above, after reboot the veth pair will generally disappear, which generally means the docker network needs to be destroyed and re-created each reboot.

To make it persistent, it's [possible](https://askubuntu.com/a/1058278) to use [netplan](https://manpages.ubuntu.com/manpages/cosmic/man5/netplan.5.html) with [systemd.netdev](https://manpages.ubuntu.com/manpages/cosmic/man5/systemd.netdev.5.html) config files, for example like these:

~~~
#/etc/systemd/network/25-veth0.netdev
[NetDev]
Name=veth0
Kind=veth

[Peer]
Name=veth1
~~~

~~~
# /etc/netplan/10-ingest-rtr-init.yaml
network:
  version: 2
  ethernets:
    veth0:
      addresses: [10.10.200.254/24]
    veth1:
      addresses: [10.10.200.1/24]
    irf0:
      dhcp4: false
      gateway4: 10.9.1.1
      optional: true
      addresses: [10.9.1.2/24]
      nameservers:
        addresses: [10.9.1.1]
~~~

##### Running the mnat-ingress instance

The ingress will run with the host's networking, and there are several things that need to be passed in:

 * **upstream-interface**:\
   the interface that will receive globally addressed traffic
 * **downstream-interface**:\
   the interface on which locally addressed traffic should be produced
 * **server** (and optionally **port**):\
   the mnat-server instance to talk to, to discover global-local address mappings

Additionally, 3 files may need to be mounted into the container:

 * **/etc/mnat/ca.pem** (optional):\
   This is needed if using a self-signed cert in the server, see the instructions for mnat-server, above.
 * **/etc/mnat/client.pem** (optional):\
   This is needed if the server requires client authentication.  This is a [PEM](https://tools.ietf.org/html/rfc7468) file containing a private key.
 * **/var/run/mnat/ingress-joined.sgs** (recommended):\
   This is a [joinfile](https://github.com/GrumpyOldTroll/multicast-ingest-platform#joinfiles) that tracks the set of currently joined global (S,G)s.  The external mounting of this file can be monitored by [driad-ingest](https://github.com/GrumpyOldTroll/multicast-ingest-platform#driad-ingest) or [cbacc](https://github.com/GrumpyOldTroll/multicast-ingest-platform#cbacc), or even by an upstream mnat-egress instance for a network segment with different constraints.  The next section describes passing it to a driad-ingest instance.

~~~
IFACE=irf0
SERVER=border-rtr.hackathon.jakeholland.net
PORT=8443
SERVERCERT=/home/user/ca.pem
JOINFILE=/home/user/ingress/ingress.sgs
UPSTREAM=veth0
INGEST=veth1 # this interface has the GATEWAY ip for the docker network
SUBNET=10.10.200.0/24
GATEWAY=10.10.200.1
MNATV=0.0.4

echo "" > $JOINFILE

sudo docker network create --driver bridge amt-bridge
sudo docker network create --driver macvlan \
    --subnet=$SUBNET --gateway=$GATEWAY \
    --opt parent=${INGEST} mnat-native-ingest

sudo docker run \
    --name mnat-ingress \
    -d --restart=unless-stopped \
    --privileged --network host \
    --log-opt max-size=2m --log-opt max-file=5 \
    -v $JOINFILE:/var/run/mnat/ingress-joined.sgs \
    -v $SERVERCERT:/etc/mnat/ca.pem \
    grumpyoldtroll/mnat-ingress:$MNATV \
      --upstream-interface ${UPSTREAM} --downstream-interface ${IFACE} \
      --server $SERVER --port $PORT -v
~~~

TBD: show example for using upstream interface without joinfile for non-driad-ingest upstream.

##### Running driad-ingest

The driad-ingest container comes from the [multicast-ingest-platform](https://github.com/GrumpyOldTroll/multicast-ingest-platform#driad-ingest) project.

The `/var/run/mnat/ingress-joined.sgs` provides a direct integration with the [joinfile](https://github.com/GrumpyOldTroll/multicast-ingest-platform#joinfiles) input for a [cbacc](https://github.com/GrumpyOldTroll/multicast-ingest-platform#cbacc) or [driad-ingest](https://github.com/GrumpyOldTroll/multicast-ingest-platform#driad-ingest) container (instead of using the joinfile from pimwatch, as that project outlines).

TBD: when population count is added as output and accepted by cbacc (and ignored by driad-ingest), update the description with the ref to [how it's used](https://datatracker.ietf.org/doc/html/draft-ietf-mboned-cbacc-02#section-2.3.2).

The network that the AMT gateways use to make an AMT tunnel to a relay is more flexible, and a bridge network is the recommended way for docker containers:

~~~
sudo docker network create --driver bridge amt-bridge
~~~

Several things need to be passed to the driad-ingest instance:

 * **amt**\
  The docker network that AMT gateways will use for AMT traffic.
 * **native**\
  The docker network that AMT gateways will send native multicast to, after receiving it from an AMT tunnel
 * **interface**\
  The interface used to produce the IGMP/MLD membership messages indicating join and leave to the AMT gateways.
 * **joinfile**\
  The location within the container of the joinfile to monitor.

Several things also need to be mounted in the container:

 * **/var/run/docker.sock**\
  The docker socket to use for issuing docker commands to spawn and destroy the AMT gateways
 * **/var/run/ingest/**\
  The directory containing the joinfile that's passed in has to be mounted as a directory.  This is because internally, the file is watched with [inotify](https://man7.org/linux/man-pages/man7/inotify.7.html), which wants to monitor the directory for changes.

So the driad-ingest instance is run like this (feeding in the same JOINFILE that mnat-ingress is updating):

~~~
JOINFILE=/home/user/ingress-joined.sgs

sudo docker run \
    --name driad-ingest \
    -d --restart=unless-stopped \
    --privileged \
    --log-opt max-size=2m --log-opt max-file=5 \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v $(dirname $JOINFILE):/var/run/ingest/ \
    grumpyoldtroll/driad-ingest:0.0.6 \
      --amt amt-bridge \
      --native mnat-native-ingest \
      --joinfile /var/run/ingest/$(basename $JOINFILE) -v
~~~

## Egress

In MNAT, an egress node will translate traffic from locally addressed (S,G)s back to globally addressed (S,G)s for forwarding out of the restricted part of the network.

The mnat-egress container listens on a downstream interface for IGMPv3/MLDv2 membership reports and responds by advertising its downstream interest in the global (S,G) space to the mnat-server.

The mnat-server then notifies any mnat-egress and mnat-ingress nodes that are interested in that global (S,G) about changes to the mapping between the local and global (S,G) space.
This will cause mnat-ingress nodes to join the global (S,G) upstream and forward it on the local (S,G) assigned by the mnat-server into its downstream network.
That traffic will reach the mnat-egress, which will translate it back to the global (S,G) addressing and forward it to its downstream.

The mnat-egress also maintains upstream group membership by managing its membership in the local (S,G) space with [mcrx-check](https://github.com/GrumpyOldTroll/libmcrx/blob/master/HOWTO.md#running-an-amt-gateway).

mnat-egress needs several things passed in:

 * **upstream-interface**:\
   the interface that will join and receive locally addressed traffic from the ISP
 * **downstream-interface**:\
   the interface on which globally addressed traffic should be produced in response to downstream joins
 * **server** (and optionally **port**):\
   the mnat-server instance to talk to, to discover global-local address mappings
 * **input-file**:\
   the input [joinfile](https://github.com/GrumpyOldTroll/multicast-ingest-platform#joinfiles) to monitor to determine the set of currently-joined (S,G)s.  This is generally updated by a downstream monitor such as igmpwatch or mcfilterwatch.

Additionally, 2 files may need to be mounted into the container:

 * **/etc/mnat/ca.pem** (optional):\
   This is needed if using a self-signed cert in the server, see the instructions for mnat-server, above.
 * **/etc/mnat/client.pem** (optional):\
   This is needed if the server requires client authentication.  This is a [PEM](https://tools.ietf.org/html/rfc7468) file containing a private key.

There are a few different reasonable ways to run the mnat-egress, covered in the sections below.

### Egress in the next-hop router

If you're using the [sample-network](https://github.com/GrumpyOldTroll/multicast-ingest-platform/blob/master/sample-network/README.md) from multicast-ingest-platform, it's a fine choice to run mnat-egress in the access-rtr node.  It likewise would make good sense to run it in a home gateway.  This corresponds to a "bump-in-the-wire" deployment as described in the [MNAT spec](https://datatracker.ietf.org/doc/draft-ietf-mboned-mnat/).

An external process is expected to maintain an input [joinfile](https://github.com/GrumpyOldTroll/multicast-ingest-platform#joinfiles).  The examples below show how to run it with igmpwatch in the sample-network.

The 2 container together are launched like this:

~~~
INPUT=xup0
OUTPUT=xdn0
SERVER=border-rtr.hackathon.jakeholland.net
PORT=8443
SERVERCERT=${HOME}/ca.pem
# if you want client auth, add -v $CLIENTCERT:/etc/mnat/client.pem
#CLIENTCERT=${HOME}client.pem
EGJOINFILE=${HOME}/igmp-sgs/egress.sgs
mkdir -p $(dirname ${EGJOINFILE}) && echo "" > ${EGJOINFILE}
MNATV=0.0.4

sudo docker run \
    --name mnat-egress \
    -d --restart=unless-stopped \
    --privileged --network host \
    --log-opt max-size=2m --log-opt max-file=5 \
    -v $SERVERCERT:/etc/mnat/ca.pem \
    -v $(dirname $EGJOINFILE):/var/run/egress-sgs/ \
    grumpyoldtroll/mnat-egress:${MNATV} \
      --input-file /var/run/egress-sgs/$(basename ${EGJOINFILE}) \
      --upstream-interface $INPUT --downstream-interface $OUTPUT \
      --server $SERVER --port $PORT -v

sudo docker run \
    --name igmpwatch \
    -d --restart=unless-stopped \
    --privileged --network host \
    --log-opt max-size=2m --log-opt max-file=5 \
    -v $EGJOINFILE:/var/run/mnat/$(basename ${EGJOINFILE}) \
    grumpyoldtroll/igmpwatch:${MNATV} \
      --output-file /var/run/mnat/$(basename ${EGJOINFILE}) \
      --interface ${OUTPUT} \
       -v
~~~

### Egress in the receiver device

If you don't want to deploy the mnat-egress in the next-hop router, it's also possible to run in the receiving device instead, emulating a sort of OS-integrated bump in the wire deployment.

This deployment would use a different external process from igmpwatch to update the joinfile, since it proved not simple to get the kernel to respond to an IGMP query from the same device to the interface where the join is active.
So instead, it uses mcfilterwatch to monitor the `/proc/net/mcfilter` file maintained by the linux kernel.

The 2 container together are launched like this:

~~~
INPUT=ens1
OUTPUT=veth0
JOININT=veth1
SERVER=border-rtr.hackathon.jakeholland.net
PORT=8443
SERVERCERT=${HOME}/ca.pem
MNATV=0.0.4
# if you want client auth, add -v $CLIENTCERT:/etc/mnat/client.pem
#CLIENTCERT=${HOME}client.pem
EGJOINFILE=${HOME}/egress-sgs/egress.sgs
mkdir -p $(dirname ${EGJOINFILE}) && echo "" > ${EGJOINFILE}

sudo docker run \
    --name mnat-egress \
    -d --restart=unless-stopped \
    --privileged --network host \
    --log-opt max-size=2m --log-opt max-file=5 \
    -v $SERVERCERT:/etc/mnat/ca.pem \
    -v $(dirname $EGJOINFILE):/var/run/egress-sgs/ \
    grumpyoldtroll/mnat-egress:$MNATV \
      --input-file /var/run/egress-sgs/$(basename ${EGJOINFILE}) \
      --upstream-interface $INPUT --downstream-interface $OUTPUT \
      --server $SERVER --port $PORT -v

sudo docker run \
    --name mcfilter \
    -d --restart=unless-stopped \
    --network host \
    --log-opt max-size=2m --log-opt max-file=5 \
    --mount type=bind,source=$(dirname ${EGJOINFILE}),target=/etc/mcjoins/ \
    grumpyoldtroll/mcfilter:$MNATV \
      -v \
      --joinfile /etc/mcjoins/$(basename ${EGJOINFILE}) \
      --interface ${JOININT}
~~~

In this deployment, it's necessary to create a veth pair just like in the mnat-ingress, but optionally with different IPs just for telling them apart easier.

It's also necessary to add a route for the sources you want to join to the interface connected to the mnat-egress instance, so that the join will be directed there.  (Also it may be necessary to add such a route for the group, for the case of something like vlc that uses the default socket behavior rather than using the libmcrx method of doing the join on the interface toward the source.)

An example of how to set that up is here:

~~~
# add a file like this:
cat | sudo tee /etc/netplan/01-mnat-egress-netcfg.yaml > /dev/null <<EOF
# /etc/netplan/01-mnat-egress-netcfg.yaml
network:
  version: 2
  ethernets:
    veth0:
      renderer: networkd
      addresses: [10.10.201.2/30]
    veth1:
      renderer: networkd
      addresses: [10.10.201.1/30]
EOF

# and another file like this:
cat | sudo tee /etc/systemd/network/25-veth-b0.netdev > /dev/null <<EOF
# /etc/systemd/network/25-veth-b0.netdev
[NetDev]
Name=veth0
Kind=veth

[Peer]
Name=veth1
EOF

# and append a line to rc.local for startup setting of the route:
cat | sudo tee -a /etc/rc.local > /dev/null <<EOF
#!/bin/sh
# <akamai added="$(date)" why="multicast trials">
# source route for akamai senders for mnat-egress on-receiver:
ip route add 23.212.185.0/24 dev veth1
# </akamai>
EOF
sudo chmod +x /etc/rc.local
~~~

