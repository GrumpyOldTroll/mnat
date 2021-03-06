#!/usr/bin/env python3

# pip install h2
# pip install twisted pyOpenSSL service_identity watchdog

# H2Protocol code adapted from:
# https://python-hyper.org/projects/hyper-h2/en/stable/twisted-post-example.html
#AUTHORITY = u'nghttp2.org'
#PATH = '/httpbin/post'


import os
import sys
import json
import argparse
from ipaddress import ip_address
from mnat.common_client import get_logger, RequestBuf, H2Protocol, TRANSLATE_TO_LOCAL

in_interface = None
out_interface = None

class IngressProtocol(H2Protocol):
    def setupWatcher(self):
        data = json.dumps({
            'ietf-mnat:watcher': {
              'id': self.watcher_id,
                'monitor': [
                    {
                        'id': '0',
                        'global-source-prefix': '0.0.0.0/0'
                    },
                    {
                        'id': '1',
                        'global-source-prefix': '::/0'
                    }
                ]
            }
          }).encode('utf-8')
        req = RequestBuf(
            #path=f'/data/ietf-mnat:egress-global-joined/watcher={self.watcher_id}',
            #method='PUT',
            path='/data/ietf-mnat:ingress-watching',
            method='POST',
            data=data)
        self.sendRequest(req)

    outfile = None
    def polledLatestMappings(self, mappings):
        cur_translates = set(self.current_mappings.keys())
        super().polledLatestMappings(mappings)
        after_translates = set(self.current_mappings.keys())

        # Just always write.  The ingest-mgr uses a refresh of this
        # file to refresh the "still joined" status, and will expire
        # things if it runs too long.
        #if cur_translates != after_translates and self.outfile:
        if self.outfile:
            dump = '\n'.join([f'{src},{grp}' for src,grp in after_translates])
            with open(self.outfile, 'w') as f:
                print(dump, file=f)

def main(args_in):
    parser = argparse.ArgumentParser(
            description='''This is an implementation of an inress node in
draft-jholland-mboned-mnat.
''')

    parser.add_argument('-v', '--verbose', action='count', default=0)
    parser.add_argument('-s', '--server', required=True, help='hostname of server')
    parser.add_argument('-p', '--port', help='port for h2 on server', default=443, type=int)
    parser.add_argument('--cacert', help='filename of cert to verify server with (must be a pem if provided)')
    parser.add_argument('-c', '--cert', help='filename of cert to authenticate this client to the server (must be a pem with private key included)')
    parser.add_argument('-i', '--interface-in', help='receive interface for global traffic (does not perform translation if not provided)')
    parser.add_argument('-o', '--interface-out', help='transmit interface for NATted traffic using local transport (does not perform translation if not provided)')
    parser.add_argument('-f', '--control-file', help='provide the full path here, the (S,G)s that are joined are dumped into this file according to polled changes in the output of cmd.  Each line is "sourceip,groupip" (no quotes)')

    args = parser.parse_args(args_in[1:])
    logger = get_logger('mnat', args.verbose)

    protocol = IngressProtocol(args.server, args.port, logger, args.cert, args.cacert)
    protocol.verbose = args.verbose

    protocol.setTranslations(TRANSLATE_TO_LOCAL, args.interface_in, args.interface_out)
    protocol.outfile = args.control_file
    #protocol.no_join = True

    protocol.start()

    protocol.runLoop()

    return 0

if __name__=="__main__":
    ret=main(sys.argv)
    sys.exit(ret)

