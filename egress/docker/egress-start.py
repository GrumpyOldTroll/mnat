#!/usr/bin/env python3

import argparse
import subprocess
from os.path import isfile, abspath
import os
import sys
import time
import signal

stopping = False

def stop_handler(signum, frame):
    global stopping
    print(f'{datetime.now()}: stopping mnat-egress.py')
    stopping = True

def main(args_in):
    global stopping
    parser = argparse.ArgumentParser(
            description='''
This runs mnat-egress, monitoring the input file for changes.

mnat-egress notifies the server about joins it learns of from its joinfile.
Based on the (S,G) entries (one per line), it runs:
 - mnat-translate to translate the from upstream local to downstream global
   - this launches mcrx-check (from libmcrx) to join the local (S,G) on
     the upstream interface.

Since this is designed as the docker entry point, it will use docker-
specific paths by default if they are present.  This happens with:
    - /etc/mnat/ca.pem (containing a public root cert to validate the server)
    - /etc/mnat/client.pem (containing a private cert to prove identity of this client)
''')

    parser.add_argument('-v', '--verbose', action='count', default=0)
    parser.add_argument('-s', '--server', required=True, help='hostname of server')
    parser.add_argument('-p', '--port', help='port for h2 on server', default=443, type=int)
    parser.add_argument('-u', '--upstream-interface', help='receive interface for local network NATted traffic', required=True)
    parser.add_argument('-d', '--downstream-interface', help='transmit interface for de-NATted global traffic', required=True)
    parser.add_argument('-f', '--input-file', required=True,
            help='input joinfile for downstream joined (S,G)s')

    args = parser.parse_args(args_in[1:])
    verbosity = None
    if args.verbose:
        verbosity = '-'+'v'*args.verbose

    control=abspath(args.input_file)
    cacert ='/etc/mnat/ca.pem'
    clientcert ='/etc/mnat/client.pem'

    egress_cmd = [
            '/usr/bin/stdbuf', '-oL', '-eL', 
            sys.executable, '/bin/mnat-egress.py',
            '-i', args.upstream_interface,
            '-o', args.downstream_interface,
            '-s', args.server,
            '-p', str(args.port),
            '-f', control,
        ]

    if verbosity:
        egress_cmd.append(verbosity)

    if isfile(cacert):
        egress_cmd.extend([
            '--cacert', cacert,
        ])

    if isfile(clientcert):
        egress_cmd.extend([
            '--cert', clientcert,
        ])

    os.environ["PYTHONUNBUFFERED"] = "1"
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGHUP, stop_handler)

    egress_p = subprocess.Popen(egress_cmd)

    egress_ret = None
    while egress_ret is None and not stopping:
        egress_ret = egress_p.poll()
        time.sleep(1)

    if egress_ret is None:
        egress_p.send_signal(signal.SIGTERM)
        egress_p.wait(1)

    return 0

if __name__=="__main__":
    ret = main(sys.argv)
    sys.exit(ret)

