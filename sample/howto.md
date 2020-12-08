# Intro

This is a how-to guide for running [MNAT](https://datatracker.ietf.org/doc/draft-jholland-mboned-mnat/) in an example environment.
These instructions try to capture how I ran it in the [multicast-ingest-platform's](https://github.com/GrumpyOldTroll/multicast-ingest-platform) sample network.
Your own environment is likely different, but hopefully the parallels aren't hard.

# Setup

This test environment uses config files for border-rtr to disable pim and use statically routed groups instead.
On border-rtr, drop the contents of [border-rtr/frr](border-rtr/frr) into /etc/frr/ and do a `docker restart frr`:

~~~
sudo docker stop frr
sudo mv /etc/frr orig.frr
sudo cp -r mnat/sample/border-rtr/frr /etc/frr
sudo docker start frr
~~~

Then install smcroute and run it with the static config file:

~~~
sudo apt install smcroute
~~~

~~~
sudo smcroutectl add brf0 239.0.0.0/8 bdn0
~~~

## Server

I'm running my server on the border-rtr, but it could be done in cloud instead.

You need a cert (H2 requires TLS).
If you're deploying to production (e.g. behind nginx) or something, use the nginx guides for how to set it up.

For testing/local operation, generating a self-signed one looks like this:

~~~
user@border-rtr:~$ openssl req -x509 -newkey rsa:4096 -keyout self_key.pem -out ca.pem -days 365
Generating a RSA private key
...
writing new private key to 'key.pem'
Enter PEM pass phrase:
Verifying - Enter PEM pass phrase:
-----
You are about to be asked to enter information that will be incorporated
into your certificate request.
What you are about to enter is what is called a Distinguished Name or a DN.
There are quite a few fields but you can leave some blank
For some fields there will be a default value,
If you enter '.', the field will be left blank.
-----
Country Name (2 letter code) [AU]:US
State or Province Name (full name) [Some-State]:CA
Locality Name (eg, city) []:Simi
Organization Name (eg, company) [Internet Widgits Pty Ltd]:Jake
Organizational Unit Name (eg, section) []:Jake test
Common Name (e.g. server FQDN or YOUR name) []:mnat.example.com
Email Address []:jholland@akamai.com

user@border-rtr:~$ mnat/mnat-server/local-test/cert_gen/gen_server_cert.sh sample-net border-rtr.hackathon.jakeholland.net

Generating new private key:
Generating RSA private key, 2048 bit long modulus (2 primes)
......................+++++
..................................................................+++++
e is 65537 (0x010001)

1. Generating CSR:

2. Signing CSR with test CA's key:
Signature ok
subject=CN = border-rtr.hackathon.jakeholland.net
Getting CA Private Key
Enter pass phrase for self_key.pem:

Done
~~~

The above 2 commands will create a few files:

 * ca.pem (self-signed public root)
 * self_key.pem
 * server_sample-net.crt
 * server_sample-net.key

Copy the ones except for self_key.pem into `/etc/mnat/`, and also copy `ca.pem` to ingest-rtr and access-rtr for use with mnat-ingress and mnat-egress.

After that, copying the ca.pem file generated during the first command and passing `--cacert ca.pem` to the `mnat-ingress` and `mnat-egress` programs will be able to connect to server name `border-rtr.hackathon.jakeholland.net` where the server is running.

~~~
sudo mkdir /etc/mnat
sudo cp ca.pem server_sample-net.crt server_sample-net.key /etc/mnat/
sudo cp mnat/sample/border-rtr/mnat.conf /etc/mnat/
sudo cp mnat/mnat-server/src/data-mnat.json /etc/mnat/
sudo cp -r mnat/mnat-server/src/doc-root/ /etc/mnat/
sudo cp -r mnat/mnat-server/src/yang-modules/ /etc/mnat/
sudo cp -r mnat/mnat-server/src/jetconf_mnat/ /etc/mnat/
~~~

You also need jetconf installed:

~~~
sudo apt install -y python3-venv
sudo python3 -m venv /etc/mnat/venv
sudo /etc/mnat/venv/bin/python -m pip install --upgrade pip
sudo /etc/mnat/venv/bin/python -m pip install jetconf
~~~

Then running it is:

~~~
sudo bash -c "PYTHONPATH=/etc/mnat /etc/mnat/venv/bin/jetconf -c /etc/mnat/mnat.conf"
~~~

TBD: add a .service file and systemctl instructions.
TBD: probably better: docker container?  Kind of annoying with the cert...

## Ingress

~~~
BASE=$PWD
python -m venv $BASE/venv-mnat
$BASE/bin/python -m pip install --upgrade pip
$BASE/bin/python -m pip install h2 twisted pyOpenSSL service_identity watchdog
# for translate:
sudo apt-get install libpcap-dev
$BASE/bin/python -m pip install Cython python-libpcap
~~~

Set up the native docker network with an interface for the ingress translator:

~~~
sudo bash -e -x <<EOF
/sbin/ip link add dev dum0 type veth peer name dum1
sleep 1
/sbin/ip addr add 10.10.200.254/24 dev dum0
/sbin/ip addr add 10.10.200.1/24 dev dum1

#/sbin/ip addr add 10.100.100.100/32 dev dum0
/sbin/ip link set up dev dum0
/sbin/ip link set up dev dum1
EOF

sudo docker network create --driver macvlan --subnet=10.10.200.0/24 --gateway=10.10.200.1 --opt parent=dum0 mcast-native-ingest
sudo smcroutectl restart
~~~

Running:

~~~
sudo venv-mnat/bin/python mnat/mnat-ingress.py -i dum1 -o irf0 -s border-rtr.hackathon.jakeholland.net -p 8443 -v --cacert ca.pem -f ingest-control.joined-sgs
~~~

~~~
sudo venv-mnat/bin/python mnat/ingest-manager.py -a amt-bridge -n mcast-native-ingest -f ingest-control.joined-sgs -i dum1 -v
~~~

## Egress

~~~
BASE=$PWD
python -m venv $BASE/venv-mnat
$BASE/bin/python -m pip install --upgrade pip
$BASE/bin/python -m pip install h2 twisted pyOpenSSL service_identity watchdog
# for translate:
sudo apt-get install libpcap-dev
$BASE/bin/python -m pip install Cython python-libpcap
~~~

Running:

~~~
sudo venv-mnat/bin/python mnat/mnat-egress.py -i xup0 -o xdn0 -s border-rtr.hackathon.jakeholland.net -p 8443 -v --cacert ca.pem -f mnat-egress-control.joined-sgs
~~~

Running the igmp monitor:

~~~
sudo venv-mnat/bin/python mnat/igmp-monitor.py -i xdn0 -x 10.7.1.1 -v

# polling frr igmp state was unreliable, using the above pcap-based one instead
# sudo venv-mnat/bin/python mnat/frr-poll-igmp.py -i xdn0 -c "/usr/bin/docker exec frr vtysh -e 'show ip igmp source'" -f mnat-egress-control.joined-sgs -v
~~~

## Forwarding (border-rtr)

On the border-rtr it needs to be configured to forward the traffic:

~~~
smcroutectl add brf0 239.0.0.0/8 bdn0
~~~

# Running

## Manually testing components

This section is for when you need to troubleshoot.
It manually does the things that mnat-ingress and mnat-egress do, and checks that things behave as expected.

This all assumes that you've done the setup steps, so now you're in a statically forwarding network.

### Basic Forwarding

I usually test this with [iperf-ssm](https://github.com/GrumpyOldTroll/iperf-ssm).

From ingest-rtr, generate some traffic:

~~~
user@ingest-rtr:~$ iperf-ssm/src/iperf --client 239.1.1.1 --udp --ttl 30 --bandwidth 1K --bind 10.9.1.2 --len 125 --time 900
~~~

From access-rtr, make sure it gets received:

~~~
user@access-rtr:~$ iperf-ssm/src/iperf --server --udp --bind 239.1.1.1 --source 10.9.1.2  --interval 1 --len 1500 --interface xup0
...
[  3] local 239.1.1.1 port 5001 connected with 10.9.1.2 port 5001
[ ID] Interval       Transfer     Bandwidth        Jitter   Lost/Total Datagrams
[  3]  0.0- 1.0 sec   125 Bytes  1.00 Kbits/sec   0.000 ms  110/  111 (99%)
[  3]  1.0- 2.0 sec   125 Bytes  1.00 Kbits/sec   0.000 ms    0/    1 (0%)
[  3]  2.0- 3.0 sec   125 Bytes  1.00 Kbits/sec   0.010 ms    0/    1 (0%)
~~~

If that's not working, on border-rtr you want to check whether the packets are getting forwarded:

Are the packets coming to the border-rtr from ingest-rtr?

~~~
user@border-rtr:~$ sudo tcpdump -i brf0 -n udp
tcpdump: verbose output suppressed, use -v or -vv for full protocol decode
listening on brf0, link-type EN10MB (Ethernet), capture size 262144 bytes
19:30:53.541509 IP 10.9.1.2.5001 > 239.1.1.1.5001: UDP, length 125
19:30:54.541497 IP 10.9.1.2.5001 > 239.1.1.1.5001: UDP, length 125
19:30:55.541519 IP 10.9.1.2.5001 > 239.1.1.1.5001: UDP, length 125
~~~

Are they getting fowarded from border-rtr to access-rtr?

~~~
user@border-rtr:~$ sudo tcpdump -i bdn0 -n udp
tcpdump: verbose output suppressed, use -v or -vv for full protocol decode
listening on brf0, link-type EN10MB (Ethernet), capture size 262144 bytes
19:30:53.541509 IP 10.9.1.2.5001 > 239.1.1.1.5001: UDP, length 125
19:30:54.541497 IP 10.9.1.2.5001 > 239.1.1.1.5001: UDP, length 125
~~~

If not, is the route configured?

~~~
user@border-rtr:~$ sudo smcroutectl show route
ROUTE (S,G)                        INBOUND             PACKETS      BYTES OUTBOUND
(\*, 239.0.0.0)                     brf0             0000000000 0000000000  bdn0
(10.9.1.2, 239.1.1.1)              brf0             0000000005 0000000765  bdn0
~~~

### Manually ingesting and translating

Manually launch an AMT gateway for a known sender:

~~~
user@ingest-rtr:~$ sudo docker run -d --rm --name amtgw --privileged grumpyoldtroll/amtgw:latest $(python3 libmcrx/driad.py 23.212.185.5)
~~~

If you wanted a local setup without installing, keep it in a venv:

~~~
user@ingest-rtr:~$ python3 -m venv venv-mnat
user@ingest-rtr:~$ source ~/venv-mnat/bin/activate
(venv-mnat) user@ingest-rtr:~$ sudo apt install -y build-essential python3-dev && python -m pip install --upgrade pip && python -m pip install scapy h2 twisted pyOpenSSL service_identity
~~~

Then join the group and run the translator:

~~~
(venv-mnat) user@ingest-rtr:~$ sudo smcroutectl join docker0 23.212.185.5 232.1.1.1
(venv-mnat) user@ingest-rtr:~$ sudo bash
root@ingest-rtr:/home/user# source venv-mnat/bin/activate
(venv-mnat) root@ingest-rtr:/home/user# python mnat/mnat-translate.py --iface-in docker0 --src-in 23.212.185.5 --grp-in 232.1.1.1 --iface-out irf0 --src-out 10.9.1.2 --grp-out 239.1.1.1
~~~

Make sure the packets are getting to access-rtr (as above), and run the translator there as well.  Same setup:

~~~
user@access-rtr:~$ python3 -m venv venv-mnat
user@access-rtr:~$ source ~/venv-mnat/bin/activate
(venv-mnat) user@access-rtr:~$ sudo apt install -y build-essential python3-dev && python -m pip install --upgrade pip && python -m pip install scapy h2 twisted pyOpenSSL service_identity
~~~

And run the reverse translate:

~~~
(venv-mnat) user@access-rtr:~$ sudo smcroutectl join xup0 10.9.1.2 239.1.1.1
(venv-mnat) user@access-rtr:~$ sudo bash
root@access-rtr:/home/user# source venv-mnat/bin/activate
(venv-mnat) root@access-rtr:/home/user# python mnat/mnat-translate.py --iface-in xup0 --src-out 23.212.185.5 --grp-out 232.1.1.1 --iface-out xdn0 --src-in 10.9.1.2 --grp-in 239.1.1.1
~~~

