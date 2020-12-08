# Intro

This is a prototype for [MNAT](https://datatracker.ietf.org/doc/draft-jholland-mboned-mnat/).  There are several distinct docker images:

 * mnat-ingress: web client that talks to mnat-server from the data ingress side (near the sender or the ingest point) and translates from the upstream global (Sg,Gg) addresses to a local network\'s (Sl,Gl) addresses downstream of the ingress, for transport within the network.
 * mnat-egress: web client that talks to mnat-server from the data egress side (near the receiver), and translates from the upstream network\'s (Sl,Gl) addresses to the global (Sg,Gg) addresses downstream of the egress, for delivering the global traffic to the receiver.
 * mnat-server: web service providing the mapping info between the local (Sl,Gl) addresses and the global (Sg,Gg) addresses.

mnat-ingress can produce either an upstream join on a target interface, or can produce a "joinfile" that can be consumed by an ingest manager that launches AMT gateways based on the [multicast-ingest-platform](https://github.com/GrumpyOldTroll/multicast-ingest-platform).

mnat-egress will listen for downstream IGMP and translate to an upstream join.
(This would be extensible to support PIM with a separation on joinfiles the same way mnat-ingress does, so pleaset let me know if you're interested in that use case, but for now it's combined into one container.)

# Running

## Server

To run the server in a docker container without a web server in front, you'll need to generate keys.
The [server/README.md](server/README.md) has a section with commands and some helper scripts to do that.
The ca.pem file will need to be copied to the ingress and egress nodes.

~~~
PORT=8443
SERVERCERT=/home/user/server_sample-net.crt
SERVERKEY=/home/user/server_sample-net.key
CLIENTCA=/home/user/ca.pem

sudo docker run \
    --name mnat-server \
    -d --restart=unless-stopped \
    -p $PORT:8443/tcp \
    -v $SERVERKEY:/etc/mnat/server.key \
    -v $SERVERCERT:/etc/mnat/server.crt \
    -v $CLIENTCA:/etc/mnat/clientca.pem \
    grumpyoldtroll/mnat-server:0.0.1
~~~

This is a docker container that provides an H2 interface.
In addition to being able to run directly on a listening port, it can be fronted by web servers that can use an H2 backend, for example [apache](https://httpd.apache.org/docs/trunk/mod/mod_proxy_http2.html) and [nginx](https://www.nginx.com/blog/http2-module-nginx/).

However, note that at the time of this writing, mnat-ingress and mnat-egress require an H2 server, and apache only has backend support, and proxies as Http 1.1.
nginx does provide h2 for both the backend and proxying.

The above command runs the container with the H2 listening port exposed to receive inbound connections into a [jetconf](https://pypi.org/project/jetconf/) instance running inside the container.
If you want to front it with nginx, please refer to the nginx setup guides for configuration instructions.

## Ingress

In MNAT, an ingress node will translate traffic from globally addressed (S,G)s to locally addressed (S,G)s in accordance with the MNAT spec, based on the information it receives from the MNAT server.

It also is responsible for signaling upstream group membership using the global addresses.

Prerequisites to use this implementation of the mnat-ingress node:

 - docker  (e.g. sudo apt install docker.io)
 - smcroutectl  (e.g. sudo apt install smcroutectl)

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
/sbin/ip link add dev dum0 type veth peer name dum1
sleep 1
/sbin/ip addr add 10.10.200.254/24 dev dum0
/sbin/ip addr add 10.10.200.1/24 dev dum1

/sbin/ip link set up dev dum0
/sbin/ip link set up dev dum1
EOF

sudo docker network create \
    --driver macvlan \
    --subnet=10.10.200.0/24 --gateway=10.10.200.1 \
    --opt parent=dum0 \
    mcast-native-ingest
~~~

After reboot the veth pair will generally disappear, which generally means the docker network needs to be destroyed and re-created.
(TBD: initialization scripts to automate this properly, in conjunction with an auto-restarting mnat-ingress instance.)

##### Running the mnat-ingress instance

The ingress will run with the host's networking, and there are several things that need to be passed in:

 * **upstream-interface**:\
   the interface that will receive globally addressed traffic
 * **downstream-interface**:\
   the interface on which locally addressed traffic should be produced
 * **server** (and optionally **port**):\
   the mnat-server instance to talk to, to discover global-local address mappings

Additionally, 2 files may need to be mounted into the container:

 * **/etc/mnat/ca.pem** (optional):\
   This is needed if using a self-signed cert in the server, see the instructions for mnat-server, above.
 * **/var/run/mnat/ingress-joined.sgs** (optional):\

~~~
INPUT=dum1
OUTPUT=irf0
SERVER=border-rtr.hackathon.jakeholland.net
PORT=8443
SERVERCERT=/home/user/ca.pem
JOINFILE=/home/user/ingress-joined.sgs
echo "" > $JOINFILE

sudo docker run \
    --name mnat-ingress \
    --privileged --network host \
    -v $JOINFILE:/var/run/mnat/ingress-joined.sgs \
    -v $SERVERCERT:/etc/mnat/ca.pem \
    -d --restart=unless-stopped \
    grumpyoldtroll/mnat-ingress:0.0.1 \
      --upstream-interface $INPUT --downstream-interface $OUTPUT \
      --server $SERVER --port $PORT
~~~

##### Running driad-ingest

The driad-ingest container monitors a joinfile (such as the one produced by the mnat-ingress instance) for changes.
It responds to changes in the file by launching and destroying AMT gateway instances, and passing IGMP/MLD membership reports into them.

The AMT gateway instances are separate docker containers that open an AMT tunnel, send membership reports to the AMT relay, and produce native multicast traffic by decapsulating the AMT data packets received from the relays.

So the driad-ingest container needs to be able to launch and destroy other docker containers, and to discover the location of AMT relays using DNS.

The docker containers launched this way need to be able to send and receive AMT traffic (UDP port 2268) to and from AMT relays on the internet.

So the driad-ingest container will run using host networking, and it will launch containers named "ingest-gw-\<sourceip\>" to connect to each different source IP needed according to its joinfile input.

The network that receives the native multicast from the AMT gateways will be the one attached to the upstream interface of the mnat-ingress instance, which we created above, named `mcast-native-ingest`.

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
 * **/var/run/smcroute.sock**\
  The smcroutectl socket to use for doing joins and leaves in the upstream native network.
 * **/var/run/ingest/**\
  The directory containing the joinfile that's passed in has to be mounted as a directory.  This is because internally, the file is watched with [inotify](https://man7.org/linux/man-pages/man7/inotify.7.html), which wants to monitor the directory for changes.

So the driad-ingress instance is run like this:

~~~
INPUT=dum1
JOINFILE=/home/user/ingress-joined.sgs

sudo docker run \
    --name driad-mgr \
    --privileged --network host \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /var/run/smcroute.sock:/var/run/smcroute.sock \
    -v $(dirname $JOINFILE):/var/run/ingest/ \
    -d --restart=unless-stopped \
    grumpyoldtroll/driad-ingest:0.0.1 \
      --amt amt-bridge \
      --native mcast-native-ingest \
      --interface $INPUT \
      --joinfile /var/run/ingest/$(basename $JOINFILE)
~~~

## Egress

In MNAT, an egress node will translate traffic from locally addressed (S,G)s back to globally addressed (S,G)s for forwarding out of the restricted part of the network.

The mnat-egress container listens on a downstream interface for IGMPv3/MLDv2 membership reports and responds by advertising its downstream interest in the global (S,G) space to the mnat-server.

The mnat-server then notifies any mnat-egress and mnat-ingress nodes that are interested in that global (S,G) about changes to the mapping between the local and global (S,G) space.
This will cause mnat-ingress nodes to join the global (S,G) upstream and forward it on the local (S,G) assigned by the mnat-server into its downstream network.
That traffic will reach the mnat-egress, which will translate it back to the global (S,G) addressing and forward it to its downstream.

The mnat-egress also maintains upstream group membership by managing its membership in the local (S,G) space with smcroutectl.

mnat-egress needs several things passed in:

 * **upstream-interface**:\
   the interface that will join and receive locally addressed traffic from the ISP
 * **downstream-interface**:\
   the interface on which globally addressed traffic should be produced in response to downstream joins
 * **server** (and optionally **port**):\
   the mnat-server instance to talk to, to discover global-local address mappings

Additionally, 2 need to be mounted into the container:

 * **/etc/mnat/ca.pem** (optional):\
   This is needed if using a self-signed cert in the server, see the instructions for mnat-server, above.
 * **/var/run/smcroute.sock**:\
   This is needed in order to let smcroutectl join the local (S,G) on the upstream interface

~~~
INPUT=xup0
OUTPUT=xdn0
SERVER=border-rtr.hackathon.jakeholland.net
PORT=8443
SERVERCERT=/home/user/ca.pem
# if you want client auth, add -v $CLIENTCERT:/etc/mnat/client.pem
#CLIENTCERT=client.pem
sudo docker run \
    --name mnat-egress \
    --privileged --network host \
    -v $SERVERCERT:/etc/mnat/ca.pem \
    -v /var/run/smcroute.sock:/var/run/smcroute.sock \
    -d --restart=unless-stopped \
    grumpyoldtroll/mnat-egress:0.0.1 \
      --upstream-interface $INPUT --downstream-interface $OUTPUT \
      --server $SERVER --port $PORT
~~~

