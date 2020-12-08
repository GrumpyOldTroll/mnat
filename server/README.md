# Intro

This is mnat-server.
It's an implementation of the server part of [MNAT](https://datatracker.ietf.org/doc/draft-jholland-mboned-mnat/).
See also the [overall project description](https://github.com/GrumpyOldTroll/mnat).

# Key generation

You need a cert (H2 requires TLS).
If you're deploying to production behind a web server, you'll want to look at the server's docs for hooking up certs (e.g. [nginx](https://nginx.org/en/docs/http/configuring_https_servers.html) supports H2).

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

That's the full dump from running 2 commands:

~~~
openssl req -x509 -newkey rsa:4096 -keyout self_key.pem -out ca.pem -days 365
mnat-server/local-test/cert_gen/gen_server_cert.sh sample-net border-rtr.hackathon.jakeholland.net
~~~

The above 2 commands will create a few files:

 * ca.pem (self-signed public root)
 * self_key.pem
 * server_sample-net.crt
 * server_sample-net.key

These files are referenced with these names in the mnat setup instructions for running the various docker containers.

ca.pem contains the public key, and is used as the trust root so that the clients (mnat-ingress and mnat-egress) know whether to trust the server.

server_sample-net.crt contains a different more specific public key further down the chain from ca.pem, and is the cert presented by the server to the clients that connect.

server_sample-net.key contains the private key that's needed by the server during its operation, to prove ownership of the .crt.

self_key.pem contains the private key that starts the chain of trust from the ca.pem.

If you're using a real hostname signed by a certificate authority, you'd set up the web server with that, and NOT pass the ca.pem into mnat-ingress and mnat-egress, but for lab testing the ability to use self-signed certs can be helpful.

