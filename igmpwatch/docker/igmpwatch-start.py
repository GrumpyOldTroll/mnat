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
This runs igmp-monitor, producing an output joinfile, and mcproxy,
providing IGMP querying.

igmp-monitor looks at the igmp traffic to determine what's joined downstream
and notifies an upstream by updating its control file, which something
like mnat-egress or cbacc or driad-ingest can monitor.

Since this is designed as the docker entry point, it will use docker-
specific paths by default if they are present.  This happens with:
    - /var/run/mnat/igmp-monitor.sgs (shouldn't need export, but provides the set of joined (S,G)s, one per line)
''')

    parser.add_argument('-v', '--verbose', action='count', default=0)
    parser.add_argument('-i', '--interface', help='transmit interface for de-NATted global traffic', required=True)
    #parser.add_argument('-q', '--querier-upstream',
    #        help='probably a dummy interface to provide as the mcproxy upstream', default=None)
    parser.add_argument('-f', '--output-file', required=True,
            help='output joinfile for upstream joined (S,G)s')

    args = parser.parse_args(args_in[1:])
    verbosity = None
    if args.verbose:
        verbosity = '-'+'v'*args.verbose

    control='/var/run/mnat/igmp-monitor.sgs'

    igmp_cmd = [
            '/usr/bin/stdbuf', '-oL', '-eL', 
            sys.executable, '/bin/igmp-monitor.py',
            '-i', args.interface,
            '-f', args.output_file,
        ]

    if verbosity:
        igmp_cmd.append(verbosity)

    os.environ["PYTHONUNBUFFERED"] = "1"
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGHUP, stop_handler)

    mcproxy_p = None
    if False: # args.querier_upstream:
        with open('/tmp/mcproxy4.conf','w') as f:
            print(f'protocol IGMPv3;\npinstance myinst: {args.querier_upstream} ==> {args.interface};', file=f)
        mcproxy_cmd = [
            '/usr/bin/stdbuf', '-oL', '-eL',
            '/usr/bin/mcproxy', '-r', '-f', '/tmp/mcproxy4.conf']
        if verbosity:
            mcproxy_cmd.append('-d')
        mcproxy_p = subprocess.Popen(mcproxy_cmd)

    igmp_p = subprocess.Popen(igmp_cmd)

    igmp_ret, mcproxy_ret =  None, None
    while igmp_ret is None and mcproxy_ret is None and not stopping:
        igmp_ret = igmp_p.poll()
        if mcproxy_p:
            mcproxy_ret = mcproxy_p.poll()
        time.sleep(1)

    if igmp_ret is None:
        igmp_p.send_signal(signal.SIGTERM)

    if mcproxy_ret is None and mcproxy_p is not None:
        mcproxy_p.send_signal(signal.SIGTERM)
        mcproxy_p.wait(1)

    igmp_p.wait(1)

    return 0

if __name__=="__main__":
    ret = main(sys.argv)
    sys.exit(ret)

