#!/usr/bin/env python3

import argparse
import subprocess
from os.path import isfile
import os
import sys
import time
import signal
from datetime import datetime

stopping=False
def stop_handler(signum, frame):
    global stopping
    print(f'{datetime.now()}: stopping mnat-ingress wrapper ({os.getpid()})')
    stopping = True

def main(args_in):
    global stopping
    parser = argparse.ArgumentParser(
            description='''
This runs mnat-ingress.py in the expected docker container layout.

mnat-ingress monitors the mnat server for active mappings (which should
come from joins reported by egresses).  This launches:
 - mnat-translate to translate from the upstream global to the downstream
   local addresses.
   - this launches smcroutectl to join the global (S,G) on the upstream
     interface.

In addition to launching the join upstream, mnat-ingress exports the
active joins in the control file.  ingress-start specifies the control
file within the docker container as /var/run/mnat/ingress-joined.sgs,
so that's the file that should be monitored for changes.

Since this is designed as the docker entry point, it will use docker-
specific paths by default if they are present.  This happens with:
    - /etc/mnat/ca.pem (containing a public root cert to validate the server)
    - /etc/mnat/client.pem (containing a private cert to prove identity of this client)
    - /var/run/mnat/ingress-joined.sgs (containing the upstream joined (S,G)s, which can be useful to export to ingest-mgr or cbacc-mgr)
''')

    parser.add_argument('-v', '--verbose', action='count', default=0)
    parser.add_argument('-s', '--server', required=True, help='hostname of server')
    parser.add_argument('-p', '--port', help='port for h2 on server', default=443, type=int)
    parser.add_argument('-u', '--upstream-interface', help='receive interface for local network NATted traffic', required=True)
    parser.add_argument('-d', '--downstream-interface', help='transmit interface for de-NATted global traffic', required=True)

    args = parser.parse_args(args_in[1:])
    verbosity = None
    if args.verbose:
        verbosity = '-'+'v'*args.verbose

    control='/var/run/mnat/ingress-joined.sgs'
    cacert ='/etc/mnat/ca.pem'
    clientcert ='/etc/mnat/client.pem'

    ingress_cmd = [
            '/usr/bin/stdbuf', '-oL', '-eL', 
            sys.executable, '/bin/mnat-ingress.py',
            '-i', args.upstream_interface,
            '-o', args.downstream_interface,
            '-s', args.server,
            '-p', str(args.port),
            '-f', control,
        ]

    if verbosity:
        ingress_cmd.append(verbosity)

    if isfile(cacert):
        ingress_cmd.extend([
            '--cacert', cacert,
        ])

    if isfile(clientcert):
        ingress_cmd.extend([
            '--cert', clientcert,
        ])

    os.environ["PYTHONUNBUFFERED"] = "1"
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGHUP, stop_handler)

    ingress_p = subprocess.Popen(ingress_cmd)

    ingress_ret = None
    while ingress_ret is None and not stopping:
        ingress_ret = ingress_p.poll()
        time.sleep(1)

    if ingress_ret is None:
        ingress_p.send_signal(signal.SIGTERM)
        ingress_p.wait(1)

    return ingress_ret

if __name__=="__main__":
    ret = main(sys.argv)
    sys.exit(ret)

