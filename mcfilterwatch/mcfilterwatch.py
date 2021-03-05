#!/bin/env python3

import subprocess
import re
import argparse
import sys
import time
import logging
import signal
from datetime import datetime
import os
from os.path import abspath, isfile
from ipaddress import ip_address
import struct
import stat

logger = None

stopping=False
def stop_handler(signum, frame):
    global stopping
    logging.info(f'{datetime.now()}: stopping ({os.getpid()})')
    stopping = True

def get_logger(name, verbosity=0):
    log_level = logging.WARNING
    if verbosity > 1:
        log_level = logging.DEBUG
    elif verbosity > 0:
        log_level = logging.INFO

    # python logging wtf: logger.setLevel doesn't work the obvious way:
    # https://stackoverflow.com/a/59705351/3427357 (-jake 2020-07)
    handler = logging.StreamHandler()
    #formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    formatter = logging.Formatter('%(asctime)s[%(levelname)s]: %(message)s')
    handler.setFormatter(formatter)
    _logger = logging.getLogger(name)
    _logger.addHandler(handler)
    _logger.setLevel(log_level)
    return _logger

def main(args_in):
    global stopping, logger

    parser = argparse.ArgumentParser(
            description='''
This monitors /proc/net/mcfilter to notice when a join or leave happens
on a given interface, and updates the output joinfile accordingly.
This assumes the first 4 columns of the current formatting in 2021-02,
for instance here is the output with 23.212.185.5->232.1.1.1 joined on
interface veth1:
$ cat /proc/net/mcfilter
Idx Device        MCA        SRC    INC    EXC
  4  veth1 0xe8010101 0x17d4b905      1      0''')

    parser.add_argument('-v', '--verbose', action='count', default=0)
    parser.add_argument('-i', '--interface', required=True, action='append')
    parser.add_argument('-m', '--mcfilter', default='/proc/net/mcfilter',
            help='location of the file kernel updates (default /proc/net/mcfilter')
    parser.add_argument('-j', '--joinfile', required=True,
            help='path to output joinfile')

    args = parser.parse_args(args_in[1:])

    ifns = set(args.interface)
    mcfilt_re = re.compile(r'\s*(?P<idx>\S+)\s+(?P<ifname>\S+)\s+(?P<grp>\S+)\s+(?P<src>\S+)\s*(?P<remaining>.*)')

    logger = get_logger('mcfilterwatch', args.verbose)
    # fname = '/home/user/tmp/mcfilter'
    orig_in_fname = args.mcfilter
    orig_out_fname = args.joinfile

    in_fname = abspath(orig_in_fname)
    out_fname = abspath(orig_out_fname)

    logger.info(f'watching {in_fname}, mapping to {out_fname}, watching groups in {ifns}')

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGHUP, stop_handler)

    # The default umask is 0o22 which turns off write permission of group and others
    os.umask(0)
    last_sgs = ''

    subprocess.Popen(['ls', '-l', out_fname]).wait()
    subprocess.Popen(['ls', '-ld', os.path.dirname(out_fname)]).wait()

    # default docker running has some odd troubles with permissions, so
    # I'm trying to force things to work or error with the mode.  Not sure
    # why this is happening, it's strange and annoying --jake 2021-03-03
    outmode = stat.S_IROTH|stat.S_IWOTH|stat.S_IRGRP|stat.S_IWGRP|stat.S_IRUSR|stat.S_IWUSR|stat.S_IREAD|stat.S_IWRITE

    if isfile(out_fname):
        os.chmod(out_fname, outmode)
        with open(out_fname) as f:
            f.seek(0)
            last_sgs = f.read()
    logger.info(f'sgs start as:\n{last_sgs}')

    while not stopping:
        with open(in_fname) as f:
            fd = f.read()
        lines = fd.split('\n')
        sgs = []
        for line in lines[1:]:
            if not line:
                continue
            m = mcfilt_re.match(line)
            if not m:
                logger.warning(f'ignoring unmatched line: "{line}"')
                continue
            #logger.debug(f'saw idx={m.group("idx")}, ifn={m.group("ifname")}, src={m.group("src")}, grp={m.group("grp")}')
            if m.group('ifname') not in ifns:
                continue
            srcint = int(m.group('src'), base=16)
            src = ip_address(struct.pack('!L', srcint))
            grpint = int(m.group('grp'), base=16)
            grp = ip_address(struct.pack('!L', grpint))
            sgs.append((src, grp))

        cur_sgs = '\n'.join([f'{src},{grp}' for src,grp in sgs])
        if cur_sgs != last_sgs:
            logger.info(f'sg list ({out_fname}) changed:\n{cur_sgs}')
            last_sgs = cur_sgs
            with open(os.open(out_fname, os.O_CREAT | os.O_RDWR | os.O_TRUNC, outmode), 'w') as f:
                f.seek(0)
                print(cur_sgs, file=f)
                f.close()

        time.sleep(0.5)

if __name__=="__main__":
    ret = main(sys.argv)
    sys.exit(ret)

