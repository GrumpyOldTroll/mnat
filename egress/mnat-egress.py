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
from mnat.common_client import get_logger, RequestBuf, H2Protocol, TRANSLATE_TO_GLOBAL
from os.path import abspath, dirname
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

logger = None

class EgressProtocol(H2Protocol):
    def setupWatcher(self):
        data = json.dumps({
            'ietf-mnat:watcher': {
              'id': self.watcher_id,
              'joined-sg': []
            }
          }).encode('utf-8')
        req = RequestBuf(
            path='/data/ietf-mnat:egress-global-joined',
            method='POST',
            data=data)
        self.sendRequest(req)

        control_file = f'{self.watch_dir}/{self.control_file}'
        if os.path.isfile(control_file):
            self.refresh_joins_from_file(control_file)

    def join_update(self, sgs):
        logger.info('join update:\n  ' + '\n  '.join([f'{s}->{g}' for s,g in sgs]))
        send_sgs = [{'source':str(s), 'group':str(g), 'id':str(idx)} for (s,g),idx in zip(sgs, range(len(sgs)))]
        data = json.dumps({
            'ietf-mnat:watcher': {
              'id': self.watcher_id,
              'joined-sg': send_sgs
            }
          }).encode('utf-8')
        req = RequestBuf(
            path=f'/data/ietf-mnat:egress-global-joined/watcher={self.watcher_id}',
            method='PUT',
            data=data)
        self.sendRequest(req)

    def refresh_joins_from_file(self, in_file):
        global logger
        logger.info(f'refreshing joins from {in_file}')

        if not self.watcher_id:
            logger.info(f'  no watcher_id set, deferring')
            return

        sgs = []
        with open(in_file) as f:
            line_num = 0
            for in_line in f:
                line = in_line.strip()
                line_num += 1
                if not line:
                    continue
                if line.startswith('#'):
                    continue
                sg = tuple(v.strip() for v in line.split(','))
                try:
                    assert(len(sg) == 2)
                    src = ip_address(sg[0])
                    grp = ip_address(sg[1])
                    assert(grp.is_multicast)
                except Exception as e:
                    logger.warning(f'{in_file}:{line_num}: expected comma-separated ips: {line} ({str(e)}')
                    continue
                sgs.append((src, grp))
        self.join_update(sgs)

def on_created_handler(protocol, control_file):
    def on_created(event):
        global logger
        logger.debug(f'on_created({event})')
        if event.src_path.endswith(control_file):
            protocol.refresh_joins_from_file(event.src_path)
    return on_created

def on_moved_handler(protocol, control_file):
    def on_moved(event):
        global logger
        logger.debug(f'on_moved({event})')
        if event.dest_path.endswith(control_file):
            protocol.refresh_joins_from_file(event.dest_path)
    return on_moved

def on_modified_handler(protocol, control_file):
    def on_modified(event):
        global logger
        logger.debug(f'on_modified({event})')
        if event.src_path.endswith(control_file):
            protocol.refresh_joins_from_file(event.src_path)
    return on_modified

def main(args_in):
    global logger

    parser = argparse.ArgumentParser(
            description='''This is an implementation of an egress node in
draft-jholland-mboned-mnat.
''')

    parser.add_argument('-v', '--verbose', action='count', default=0)
    parser.add_argument('-f', '--control-file', required=True,
            help='this file is monitored for the (S,G)s that are joined.  Each line is "sourceip,groupip" (no quotes), the file can change on the fly')
    parser.add_argument('-s', '--server', required=True, help='hostname of server')
    parser.add_argument('-p', '--port', help='port for h2 on server', default=443, type=int)
    parser.add_argument('--cacert', help='filename of cert to verify server with (must be a pem if provided)')
    parser.add_argument('-c', '--cert', help='filename of cert to authenticate this client to the server (must be a pem with private key included)')
    parser.add_argument('-i', '--interface-in', help='receive interface for local network NATted traffic')
    parser.add_argument('-o', '--interface-out', help='transmit interface for de-NATted global traffic')

    args = parser.parse_args(args_in[1:])

    logger = get_logger('mnat', args.verbose)

    protocol = EgressProtocol(args.server, args.port, logger, args.cert, args.cacert)
    protocol.verbose = args.verbose

    CONTROL = args.control_file
    watch_dir = dirname(abspath(CONTROL))

    protocol.watch_dir = watch_dir
    protocol.control_file = CONTROL

    protocol.setTranslations(TRANSLATE_TO_GLOBAL, args.interface_in, args.interface_out)

    protocol.start()

    event_handler = PatternMatchingEventHandler(
            patterns=['*/'+CONTROL],
            ignore_patterns=None,
            ignore_directories=True,
            case_sensitive=True)

    event_handler.on_created = on_created_handler(protocol, CONTROL)
    event_handler.on_moved = on_moved_handler(protocol, CONTROL)
    event_handler.on_modified = on_modified_handler(protocol, CONTROL)
    #event_handler.on_deleted = on_deleted

    logger.info(f'watching {watch_dir}/{CONTROL}')
    observer = Observer()
    observer.schedule(event_handler, watch_dir, recursive=False)
    observer.start()

    try:
        protocol.runLoop()
    finally:
        observer.stop()
        observer.join()

    return 0

if __name__=="__main__":
    ret=main(sys.argv)
    sys.exit(ret)

