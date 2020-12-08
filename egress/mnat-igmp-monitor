#!/usr/bin/env python3

import re
import sys
import logging
import subprocess
import argparse
from ipaddress import ip_address
from collections import namedtuple
from datetime import datetime, timedelta
from threading import Thread, Lock
from queue import Empty, Queue

IGMPNotice = namedtuple('IGMPNotice', ('from_ip',
    'joined_sgs','joined_gs',
    'left_sgs', 'left_gs', 'timestamp'))

class IGMPMonitor(object):
    '''
    Usage:
    m = IGMPMonitor('xdn0', '10.7.1.1')
    m.start()

    # now m.notice_queue is a queue.Queue object producing a stream
    # of IGMPNotice objects.
    notice = m.notice_queue.get(timeout=4) 

    m.stop() when you're done
    '''
    def __init__(self, ifname, exclude_from_ip=None):
        self.notice_queue = None
        self.self_ip = exclude_from_ip
        self.ifname = ifname
        self._thread = None
        self._p = None

    def start(self):
        self.notice_queue = Queue()
        self._thread = Thread(target=self._monitor)
        self._thread.start()

    def stop(self):
        if self._p:
            self._p.kill()
            self._p = None

        if self._thread:
            self._thread.join()
            self._thread = None

    def _monitor(self):
        '''
        watching igmp is kind of a hack, ideally you could pull this info
        from the routing table or the 'vtysh -e "show ip igmp sources"' on
        frr.  However, this doesn't always seem to work well, for unclear
        reasons.

        So this launches a tcpdump and watches the output, yielding a
        tuple of (src and grp are ip_addresses):
          joined (S,G)s: set([(src,grp)])
          joined (*,G)s: set([grp])
          left (S,G)s: set([src,grp])
          left (*,G)s: set([grp])
         * note: left (*,G)s is often used when the last (S,G) has just left,
                 so even when handling only SSM it's important to handle
                 the (*,G)s left.

        this is a blocking call, and you run it with:
        for join_sgs, join_gs, left_sgs, left_gs in igmpmonitor(ifname, local_ip):
            handle_updates()

        The relevant IGMP output from 'tcpdump -i xdn0 -n -vvv igmp' looks
        like one of a few cases:

    1. adding a new subscribed (S,G):
    19:16:45.075608 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 44, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 1 group record(s) [gaddr 232.1.1.1 allow { 23.212.185.4 }]

    1.a. Multiple (S,G)s with the same group, different sources:
    19:22:59.085712 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 56, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 23.212.185.5 }] [gaddr 224.0.0.251 is_ex { }]
    * note: this breaks mac.

    2. removing a subscribed (S,G):
    19:16:58.587517 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 44, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 1 group record(s) [gaddr 232.1.1.1 block { 23.212.185.5 }]

    3. Another way of removing a subscribed (S,G) that was previously the
    only (S,G) for that G:
    03:56:33.384477 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 40, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 1 group record(s) [gaddr 232.1.1.1 to_in { }]

        '''

        logger = logging.getLogger('igmpmon')

        to_report_addr = ip_address('224.0.0.22')

        cmd = ['/usr/bin/stdbuf', '-oL', '-eL', '/usr/sbin/tcpdump', '-i', self.ifname, '-vvv', '-n', 'igmp']
        if self.self_ip:
            cmd.extend(['and', 'not', 'src', str(self.self_ip)])

        logger.info(f'launching {" ".join(cmd)}')
        popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True)
        self._p = popen

        igmp_line_re = re.compile(r'\s*(?P<first_ip>[0-9.]+)\s*(?P<dir>[<>])\s+(?P<second_ip>[0-9.]+)\s*: igmp v3 report, (?P<ngrps>[0-9]+) group record\(s\) ')
        gaddr_re = re.compile(r'\s*\[\s*gaddr\s+(?P<grp>[0-9.]+)\s+(?P<op>[a-zA-Z_]+)\s+\{\s*(?P<srcs>[0-9. ]*)\s*\}\s*\]\s*')
        for stdout_line in iter(popen.stdout.readline, ""):
            m = igmp_line_re.match(stdout_line)
            if not m:
                logger.debug(f'skipping line: {stdout_line.rstrip()}')
                continue
            ngrps = int(m.group('ngrps'))
            if m.group('dir') == '<':
                from_ip = ip_address(m.group('second_ip'))
                to_ip = ip_address(m.group('first_ip'))
            else:
                from_ip = ip_address(m.group('first_ip'))
                to_ip = ip_address(m.group('second_ip'))

            if to_ip != to_report_addr:
                logger.warning(f'saw an IGMPv3 report sent to {to_ip} instead of {to_report_addr}')

            grp_sets = stdout_line[m.end():]
            found_grps = 0
            add_sgs = set()
            add_gs = set()
            rm_sgs = set()
            rm_gs = set()
            while True:
                m = gaddr_re.match(grp_sets)
                if not m:
                    break
                grp_sets = grp_sets[m.end():]

                op = m.group('op')

                if op not in set(['allow','block','to_in','is_in','is_ex']):
                    logger.warning(f'ignoring unknown igmp operator: "{op}" (in: "{stdout_line}")')
                    continue

                grp = ip_address(m.group('grp'))
                srcs = [ip_address(src) for src in m.group('srcs').strip().split()]
                # group record types:
                # https://tools.ietf.org/html/rfc3376#section-4.2.12
                # 1=MODE_IS_INCLUDE
                # 2=MODE_IS_EXCLUDE
                # 3=CHANGE_TO_INCLUDE_MODE
                # 4=CHANGE_TO_EXCLUDE_MODE
                # 5=ALLOW_NEW_SOURCES
                # 6=BLOCK_OLD_SOURCES
                # bizarrely, from mac I get a "is_in <grp> { }" when
                # iperf-ssm closes, so I think this is treated as a

                if op == 'to_ex' or op == 'is_ex':
                    if len(srcs) == 0:
                        rm_gs.add(grp)
                    else:
                        for src in srcs:
                            rm_sgs.add((src,grp))
                elif op == 'to_in' or op == 'is_in':
                    if len(srcs) == 0:
                        # jake 2020-12: you would think this would be
                        #   an ASM joined state, but I'm only doing SSM,
                        #   and mac seems to have a bug where killing a
                        #   process that was SSM joined will often produce
                        #   to_in with an empty source list for some
                        #   reason.  Thus, I treat this as a remove instead
                        #   of an add, but this is not a very friendly
                        #   workaround and makes ASM a little flakier.
                        # add_gs.add(grp)
                        if op == 'to_in':
                            rm_gs.add(grp)
                        else:
                            add_gs.add(grp)
                    else:
                        for src in srcs:
                            add_sgs.add((src,grp))
                elif op == 'allow':
                    for src in srcs:
                        add_sgs.add((src,grp))
                elif op == 'block':
                    for src in srcs:
                        rm_sgs.add((src,grp))

            notice = IGMPNotice(from_ip, add_sgs, add_gs, rm_sgs, rm_gs, datetime.now())
            logger.debug(f'parsed igmp packet: {notice}')
            self.notice_queue.put(notice)
            logger.debug(f'put packet on queue')

        popen.stdout.close()
        return_code = popen.wait()
        if return_code:
            logger.warning(f'return code {return_code} from tcpdump')
        self.notice_queue.put(None)

        '''
        Staying joined had a period of 2m+ up to maybe 20-30 seconds  or so (linux router, mac receiver):

    21:59:13.238839 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 }] [gaddr 224.0.0.251 is_ex { }]
    22:01:21.245901 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 }] [gaddr 224.0.0.251 is_ex { }]
    22:03:25.248133 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 }] [gaddr 224.0.0.251 is_ex { }]
    22:05:27.243280 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 }] [gaddr 224.0.0.251 is_ex { }]
    22:07:38.258460 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 }] [gaddr 224.0.0.251 is_ex { }]
    22:09:43.261378 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 }] [gaddr 224.0.0.251 is_ex { }]
    22:11:50.264847 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 }] [gaddr 224.0.0.251 is_ex { }]
    22:13:54.267027 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 }] [gaddr 224.0.0.251 is_ex { }]
    22:15:56.263379 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 }] [gaddr 224.0.0.251 is_ex { }]
    22:17:56.255248 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))
        10.7.1.50 > 224.0.0.22: igmp v3 report, 2 group record(s) [gaddr 232.1.1.1 is_in { 23.212.185.4 }] [gaddr 224.0.0.251 is_ex { }]
    22:20:10.273450 IP (tos 0xc0, ttl 1, id 0, offset 0, flags [DF], proto IGMP (2), length 52, options (RA))

        '''
        return

def setup_logger(name, verbosity=0):
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

class SGStatus(object):
    def __init__(self, sg):
        self.sg = sg
        self.start_time = datetime.now()
        self.last_update = self.start_time
        self.expire_time = self.start_time + timedelta(seconds=160)

    def update(self):
        self.last_update = datetime.now()
        self.expire_time = datetime.now() + timedelta(seconds=160)

def dump_file(joined_sgs, outfname):
    dump = '\n'.join([f'{src},{grp}' for src,grp in joined_sgs.keys()])
    with open(outfname, 'w') as f:
        print(dump, file=f)

def main(args_in):
    parser = argparse.ArgumentParser(
            description='''This operates in conjunction with mnat-egress.
It's intended to monitor IGMP packets received on an interface
and update the mnat-egress control file to maintain the current
set of joined (S,G)s.
''')

    parser.add_argument('-v', '--verbose', action='count', default=0)
    parser.add_argument('-i', '--interface', help='only watch the named interface', required=True)
    parser.add_argument('-x', '--address-exclude', help='exclude this IP (provide this interface\'s IP, optionally', type=ip_address)
    #parser.add_argument('-i', '--interface', help='only watch the named interface', action='append', default=[])
    parser.add_argument('-f', '--control-file',
            default='ingest-control.joined-sgs',
            help='provide the full path here, the (S,G)s that are joined are dumped into this file according to polled changes in the output of cmd.  Each line is "sourceip,groupip" (no quotes)')

    args = parser.parse_args(args_in[1:])

    logger = setup_logger('igmpmon', args.verbose)
    outfname = args.control_file

    m = IGMPMonitor(args.interface, args.address_exclude)
    m.start()

    joined_sgs = {}  # {(src,grp): SGStatus}
    dump_file(joined_sgs, outfname)
    next_expire = datetime.now()+timedelta(seconds=300)
    while True:
        expire_dur = next_expire - datetime.now()
        if expire_dur.seconds > 0:
            timeout = expire_dur.seconds
        else:
            timeout = 0.01
        try:
            notice = None
            next_notice = m.notice_queue.get(timeout=timeout)
            if next_notice is None:
                break
            notice = next_notice
        except Empty:
            pass

        if not notice:
            now = datetime.now()
            removing = []
            next_expire = now + timedelta(seconds=300)
            for sg, status in joined_sgs.items():
                if status.expire_time <= now:
                    removing.append(sg)
                else:
                    if status.expire_time < next_expire:
                        next_expire = status.expire_time

            if removing:
                for sg in removing:
                    del(joined_sgs[sg])
                dump_file(joined_sgs, outfname)
            continue

        removing = set()
        added = set()
        if notice.left_gs:
            for rm_grp in notice.left_gs:
                for src,grp in joined_sgs.keys():
                    if rm_grp == grp:
                        removing.add((src,grp))
        if notice.left_sgs:
            for sg in notice.left_sgs:
                if sg in joined_sgs:
                    removing.add(sg)
        if notice.joined_gs:
            # ignore this, I'm ssm-only. --jake 2020-12
            pass
        if notice.joined_sgs:
            now = datetime.now()
            for sg in notice.joined_sgs:
                if sg not in joined_sgs:
                    added.add(sg)
                    newStatus = SGStatus(sg)
                    joined_sgs[sg] = newStatus
                    if newStatus.expire_time < next_expire:
                        next_expire = newStatus.expire_time
                else:
                    joined_sgs[sg].expire_time = now + timedelta(seconds=160)

        if added or removing:
            for sg in removing:
                del(joined_sgs[sg])
            dump_file(joined_sgs, outfname)

    m.stop()

    return 0

if __name__=="__main__":
    ret = main(sys.argv)
    sys.exit(ret)

