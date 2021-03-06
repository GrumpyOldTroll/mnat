# #!/usr/bin/env python3

import sys
import argparse
from ipaddress import ip_address
import logging
import struct
import signal
import subprocess
from datetime import datetime, timedelta
from pylibpcap.pcap import sniff
from pylibpcap import send_packet
import socket
import os
import random

pkts = 0
sent = 0
drops = 0
last_msg = datetime.now()

def carry_around_add(a, b):
    c = a + b
    return (c & 0xffff) + (c >> 16)

def internal_checksum(msg):
    s = 0
    for i in range(0, len(msg)-1, 2):
        w = (msg[i]) + ((msg[i+1]) << 8)
        s = carry_around_add(s, w)
    if len(msg) % 2 == 1:
        s = carry_around_add(s, msg[-1])
    return s

def invert_cksum(s):
    return ~s & 0xffff

# IP and UDP checksums are: invert_cksum(internal_cksum(pkt_data))

def get_callback(args):
    iface = args.iface_out
    out_src = ip_address(args.src_out)
    out_grp = ip_address(args.grp_out)
    in_src = ip_address(args.src_in)
    in_grp = ip_address(args.grp_in)
    if in_grp.version != in_src.version:
        raise ValueError(f'in grp and src must match version: {in_grp} vs. {in_src}')

    '''
    if in_grp.version == 4:
        in_layer = IP
    elif in_grp.version == 6:
        in_layer = IPv6

    if out_grp.version != out_src.version:
        raise ValueError(f'out grp and src must match version: {grp} vs. {src}')
    if out_grp.version == 4:
        out_layer = IP
    elif out_grp.version == 6:
        out_layer = IPv6

    # scapy wants to convert it from string each packet, hmm...
    macaddr = get_if_hwaddr(iface)
    src=str(out_src)
    grp=str(out_grp)
    base=Ether(src=macaddr)/out_layer(dst=grp, src=src)
    '''
    # very helpful example here:
    # https://www.binarytides.com/raw-socket-programming-in-python-linux/

    assert(in_grp.version == in_src.version)
    assert(out_grp.version == out_src.version)

    assert(in_grp.version == 4 or in_grp.version == 6)
    assert(out_grp.version == 4 or out_grp.version == 6)

    # UDP checksum
    # https://en.wikipedia.org/wiki/User_Datagram_Protocol#Checksum_computation
    # https://tools.ietf.org/html/rfc768
    # https://tools.ietf.org/html/rfc2460#section-8.1

    # 4-to-4 or 6-to-6: just change addresses and update checksum in
    #     udp header (and in ipv4 header if 4)
    # 4-to-6: ttl to hop limit, length to length (with adjustment),
    #     dscp+ecn to traffic class, 0 flow label, next header=udp
    # 6-to-4: hop limit to ttl, length to length (with adjustment),
    #     traffic class to dscp+ecn, protocol=udp
    # TBD: support fragmentation for 4-to-6 and 6-to-4.  For now,
    # drop if there's a fragment header in 6 or MF or fragment offset
    # in 4?
    # for now, I'm only supporting input traffic that's ip 4 or 6 over
    # ethernet with udp as the next protocol for input packets.
    # long-term, this script should be replaced by a tc or iptables
    # module and copy whatever masquerade is doing, probably, i'm pretty
    # sure there's rules for nat that i'm not quite following properly.
    # --jake 2020-12

    if out_grp.version == 4:
        # ip4 header to produce:
        # https://tools.ietf.org/html/rfc791#section-3.1
        '''
            0                   1                   2                   3
        0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |Version|  IHL  |Type of Service|          Total Length         |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |         Identification        |Flags|      Fragment Offset    |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |  Time to Live |    Protocol   |         Header Checksum       |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |                       Source Address                          |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |                    Destination Address                        |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |                    Options                    |    Padding    |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       '''

        s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        #s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, (iface+"\0").encode('utf-8'))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, bytes(iface, 'utf-8'))
        s.connect((str(out_grp), 0))
        # s.connect((iface, socket.IPPROTO_IP, socket.PACKET_MULTICAST))
        if in_grp.version == 4:
            # convert 4-to-4
            cksum_adjust = carry_around_add(invert_cksum(internal_checksum(in_src.packed+in_grp.packed)), internal_checksum(out_src.packed+out_grp.packed))
            addresses = out_src.packed + out_grp.packed
            def change_pkt(in_pkt):
                # print(' '.join(['%02x'%x for x in in_pkt[:64]]))
                if len(in_pkt) < 28:
                    return None
                hlen = (in_pkt[0]&0xf)*4
                if hlen + 8 > len(in_pkt):
                    return None
                if hlen < 20:
                    return None
                plen = (in_pkt[2]*256 + in_pkt[3])
                if plen > len(in_pkt):
                    return None
                prot = in_pkt[9]
                if prot != 17:
                    return None
                df = ((in_pkt[6]&0x40)>>7)
                mf = ((in_pkt[6]&0x20)>>6)
                frag_off = ((in_pkt[6]&0x1f)*256)+(in_pkt[7])
                if mf or frag_off:
                    return None

                udp_off = hlen

                debugging = False
                in_ip_cksum = in_pkt[11]*256+in_pkt[10]
                in_udp_cksum = in_pkt[udp_off+7]*256+in_pkt[udp_off+6]

                if debugging:
                    pshdr = in_src.packed + in_grp.packed + b'\x00\x11' + in_pkt[udp_off+4:udp_off+6]
                    in_u_cksum = invert_cksum(internal_checksum(pshdr + in_pkt[udp_off:]))
                    in_i_cksum = invert_cksum(internal_checksum(in_pkt[:udp_off]))
                    print(f'udp in check: {in_u_cksum} from {in_pkt[udp_off+7]:02x}{in_pkt[udp_off+6]:02x} (ip {in_i_cksum} on {in_pkt[11]:02x}{in_pkt[10]:02x})')
                    mk_udp_cksum = invert_cksum(internal_checksum(pshdr + in_pkt[udp_off:udp_off+6] + in_pkt[udp_off+8:]))
                    print(f'udp calc: {mk_udp_cksum:04x}, {mk_udp_cksum//256:02x}{mk_udp_cksum%256:02x}, inv {invert_cksum(mk_udp_cksum):04x}')
                    mk_changed_cksum = invert_cksum(internal_checksum(addresses + pshdr[8:] + in_pkt[udp_off:udp_off+6] + in_pkt[udp_off+8:]))
                    print(f'new udp : {mk_changed_cksum:04x}, {mk_changed_cksum//256:02x}{mk_changed_cksum%256:02x}, inv {invert_cksum(mk_changed_cksum):04x}')
                    mk_old_addrs = internal_checksum(in_src.packed + in_grp.packed)
                    mk_old_inv = invert_cksum(mk_old_addrs)
                    print(f'old  : {mk_old_addrs:04x}, inv: {mk_old_inv:04x}')
                    mk_addrs = internal_checksum(addresses)
                    mk_inv = invert_cksum(mk_addrs)
                    print(f'addrs: {mk_addrs:04x}, inv: {mk_inv:04x}')
                    mk_adj = carry_around_add(carry_around_add(invert_cksum(mk_udp_cksum), mk_old_inv), mk_addrs)
                    mk_adj2 = carry_around_add(carry_around_add(mk_addrs, mk_old_inv), invert_cksum(mk_udp_cksum))
                    print(f'adj  : {mk_adj:04x} adj2: {mk_adj2:04x}')
                    orig_ck = in_pkt[udp_off+7]*256 + in_pkt[udp_off+6]
                    updated = invert_cksum(carry_around_add(cksum_adjust, invert_cksum(orig_ck)))
                    print(f'try  : {orig_ck:04x} -> {updated:04x}')

                out_ip_cksum = invert_cksum(carry_around_add(cksum_adjust, invert_cksum(in_ip_cksum)))
                if in_udp_cksum:
                    out_udp_cksum = invert_cksum(carry_around_add(cksum_adjust, invert_cksum(in_udp_cksum)))
                    if out_udp_cksum == 0:
                        out_udp_cksum = 0xffff
                else:
                    out_udp_cksum = 0
                out_ip = bytes([out_ip_cksum%256, out_ip_cksum//256])
                out_udp = bytes([out_udp_cksum%256, out_udp_cksum//256])
                out_pkt = in_pkt[:10]+out_ip+addresses+in_pkt[20:udp_off+6]+out_udp+in_pkt[udp_off+8:]

                if debugging:
                    pshdr = out_src.packed + out_grp.packed + b'\x00\x11' + out_pkt[udp_off+4:udp_off+6]
                    out_udp_cksum = invert_cksum(internal_checksum(pshdr + out_pkt[udp_off:]))
                    out_ip_cksum = invert_cksum(internal_checksum(out_pkt[:udp_off]))
                    print(f'udp out check: {out_udp_cksum} from {out_pkt[udp_off+7]:02x}{out_pkt[udp_off+6]:02x} (adjust={cksum_adjust}) (ip {out_ip_cksum} on {out_pkt[11]:02x}{out_pkt[10]:02x})')
                return out_pkt
        else:
            # convert 6-to-4
            cksum_adjust = carry_around_add(invert_cksum(internal_checksum(in_src.packed+in_grp.packed)), internal_checksum(out_src.packed+out_grp.packed))
            addresses = out_src.packed + out_grp.packed
            def change_pkt(in_pkt):
                hoff = 0
                if len(in_pkt) < hoff + 48:
                    return None
                traffic_class = ((in_pkt[hoff]&0xf)<<4)|((in_pkt[hoff+1]&0xf0)>>4)
                hoplim = in_pkt[hoff+7]

                paylen = (in_pkt[hoff+4]*256 + in_pkt[hoff+5])
                if paylen > len(in_pkt)-40-hoff:
                    return None
                prot = in_pkt[hoff+6]
                # TBD: support fragmentation and/or any extension headers?
                # should be ok to loop to below on hoff += 40 while prot
                # is an extension.
                if prot != 17:
                    return None

                udp_off = hoff+40

                debugging = False
                in_udp_cksum = in_pkt[udp_off+7]*256+in_pkt[udp_off+6]
                if in_udp_cksum:
                    out_udp_cksum = invert_cksum(carry_around_add(cksum_adjust, invert_cksum(in_udp_cksum)))
                    if out_udp_cksum == 0:
                        out_udp_cksum = 0xffff
                else:
                    out_udp_cksum = 0

                out_udp = bytes([out_udp_cksum%256, out_udp_cksum//256])

                hlen=20
                ihl = (((hlen-1)//4)+1)
                totlen = hlen+paylen
                ipid = random.randint(0, 0xffff)
                prot = 17 # UDP
                front_hdr = bytes([0x40|ihl, traffic_class, totlen//256, totlen%256,
                    ipid//256, ipid%256, 0, 0,
                    hoplim, prot])
                ip_cksum = invert_cksum(carry_around_add(internal_checksum(front_hdr), internal_checksum(addresses)))
                out_pkt = front_hdr + bytes([ip_cksum%256, ip_cksum//256]) +\
                        addresses + in_pkt[udp_off:udp_off+6] + out_udp + \
                        in_pkt[udp_off+8:]

                return out_pkt
    else:
        s = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_RAW)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, bytes(iface, 'utf-8'))
        s.connect((str(out_grp), 0))

        # ip6 header to produce:
        # https://tools.ietf.org/html/rfc8200#section-3
        '''
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |Version| Traffic Class |           Flow Label                  |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |         Payload Length        |  Next Header  |   Hop Limit   |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |                                                               |
       +                                                               +
       |                                                               |
       +                         Source Address                        +
       |                                                               |
       +                                                               +
       |                                                               |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       |                                                               |
       +                                                               +
       |                                                               |
       +                      Destination Address                      +
       |                                                               |
       +                                                               +
       |                                                               |
       +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
       '''

        if in_grp.version == 4:
            # convert 4-to-6
            cksum_adjust = carry_around_add(invert_cksum(internal_checksum(in_src.packed+in_grp.packed)), internal_checksum(out_src.packed+out_grp.packed))
            addresses = out_src.packed + out_grp.packed
            def change_pkt(in_pkt):
                # print(' '.join(['%02x'%x for x in in_pkt[:64]]))
                if len(in_pkt) < 28:
                    return None
                hlen = (in_pkt[0]&0xf)*4
                if hlen + 8 > len(in_pkt):
                    return None
                if hlen < 20:
                    return None
                plen = (in_pkt[2]*256 + in_pkt[3])
                if plen > len(in_pkt):
                    return None
                prot = in_pkt[9]
                if prot != 17:
                    return None
                df = ((in_pkt[6]&0x40)>>7)
                mf = ((in_pkt[6]&0x20)>>6)
                frag_off = ((in_pkt[6]&0x1f)*256)+(in_pkt[7])
                if mf or frag_off:
                    return None

                udp_off = hlen

                # in_ip_cksum = in_pkt[11]*256+in_pkt[10]
                in_udp_cksum = in_pkt[udp_off+7]*256+in_pkt[udp_off+6]

                if in_udp_cksum:
                    out_udp_cksum = invert_cksum(carry_around_add(cksum_adjust, invert_cksum(in_udp_cksum)))
                    if out_udp_cksum == 0:
                        out_udp_cksum = 0xffff
                else:
                    out_udp_cksum = 0

                out_udp = bytes([out_udp_cksum%256, out_udp_cksum//256])

                in_tos = in_pkt[1]
                in_ttl = in_pkt[8]
                flow = 0
                out0 = 0x60 | ((in_tos&0xf0)>>4)
                out1 = ((in_tos&0xf)<<4) | ((flow&0xf0000)>>16)
                out2 = (flow&0xff00)>>8
                out3 = (flow&0xff)
                out_row1 = bytes([out0, out1, out2, out3])

                paylen = plen - hlen
                hdr = 17
                out_row2 = bytes([paylen // 256, paylen % 256, hdr, in_ttl])

                out_pkt = out_row1 + out_row2 + addresses + \
                    in_pkt[udp_off:(udp_off+6)] + out_udp + \
                    in_pkt[udp_off+8:]

                return out_pkt
        else:
            # convert 6-to-6
            def change_pkt(in_pkt):
                # is it useful to have a header offset here?
                hoff = 0
                if len(in_pkt) < hoff + 48:
                    return None
                traffic_class = ((in_pkt[0]&0xf)<<4)|((in_pkt[1]&0xf0)>>4)
                hoplim = in_pkt[7]
                prot = in_pkt[6]

                paylen = (in_pkt[4]*256 + in_pkt[5])
                if paylen > len(in_pkt)-40-hoff:
                    return None
                # TBD: support fragmentation and/or any extension headers?
                # should be ok to loop to below on hoff += 40 while prot
                # is an extension.
                if prot != 17:
                    return None

                udp_off = hoff+40

                debugging = False
                in_udp_cksum = in_pkt[udp_off+7]*256+in_pkt[udp_off+6]
                if in_udp_cksum:
                    out_udp_cksum = invert_cksum(carry_around_add(cksum_adjust, invert_cksum(in_udp_cksum)))
                    if out_udp_cksum == 0:
                        out_udp_cksum = 0xffff
                else:
                    out_udp_cksum = 0

                out_udp = bytes([out_udp_cksum%256, out_udp_cksum//256])

                # maybe :hoff+8?  not sure if I'm supporting extension
                # headers what all has to change...
                out_pkt = in_pkt[:8] + addresses + \
                        in_pkt[udp_off:udp_off+6] + out_udp + \
                        in_pkt[udp_off+8:]

                return out_pkt

    def sg_monitor_callback(pkt):
        global pkts, last_msg, sent, drops
        pkts += 1
        now = datetime.now()

        out_p = change_pkt(pkt)
        if out_p:
            s.send(out_p)
            sent += 1
        else:
            drops += 1

        if now - last_msg >= timedelta(seconds=3):
            print(f'{now} ({in_src}->{in_grp})=>({out_src}->{out_grp}): {pkts} pkts, {drops} dropped {sent} sent')
            last_msg = now

        #send_packet(iface, pkt)
        #u = pkt[UDP]
        #sendp(base/UDP(sport=u.sport, dport=u.dport)/u.payload, iface=iface, verbose=False)
        return None
        #return pkt.summary() # sprintf("%IP.src% %IP.dst%")
    return sg_monitor_callback

stopping=False
last_refreshed = datetime.now()
def stop_handler(signum, frame):
    global stopping
    print(f'{datetime.now()}: stopping mnat-translate ({os.getpid()})')
    stopping = True

def refresh_handler(signum, frame):
    global last_refreshed
    last_refreshed = datetime.now()

def main(args_in):
    global stopping, last_refreshed
    global last_msg
    parser = argparse.ArgumentParser(
            description='''
UDP packet IPs are converted for from_src->from_dst seen on from_interface to to_src->to_dst written out on to_interface''', prog=args_in[0])

    #logging.basicConfig(level=logging.WARNING)
    logging.getLogger("scapy").setLevel(logging.WARNING)
    #logging.getLogger("scapy").setLevel(logging.INFO)

    parser.add_argument('--iface-in', required=True)
    parser.add_argument('--src-in', type=ip_address, required=True)
    parser.add_argument('--grp-in', type=ip_address, required=True)
    parser.add_argument('--iface-out', required=True)
    parser.add_argument('--src-out', type=ip_address, default=None)
    parser.add_argument('--grp-out', type=ip_address, default=None)
    parser.add_argument('--timeout', type=int, default=0, help='seconds to run without a SIGUSR1 signal')
    parser.add_argument('--no-join', action='store_true', default=False, help='use if the upstream join will be handled another way.')
    parser.add_argument('-v', '--verbose', action='count', default=0)

    args = parser.parse_args(args_in[1:])
    filter_str = f'udp and src {args.src_in} and dst {args.grp_in}'

    os.environ["PYTHONUNBUFFERED"] = "1"
    print(f'starting mnat-translate ({os.getpid()})')

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGHUP, stop_handler)
    signal.signal(signal.SIGUSR1, refresh_handler)

    dead_delay = None
    if args.timeout > 0:
        dead_delay = timedelta(seconds=args.timeout)

    prn = get_callback(args)

    if not args.no_join:
        joined = do_join(args.iface_in, ip_address(args.src_in), ip_address(args.grp_in))

    '''
    while not stopping:
        sniff(prn=prn, filter=filter_str, iface=args.iface_in, store=0, monitor=True, timeout=2)

        if dead_delay and datetime.now() - last_refreshed > dead_delay:
            print('shutting down by timeout (no SIGUSR1 received in {dead_delay}')
            stopping = True

        now = datetime.now()
        if now - last_msg >= timedelta(seconds=3):
            print(f'{now}: {pkts} pkts')
            last_msg = now
    '''

    for plen, t, buf in sniff(args.iface_in, filters=filter_str, count=-1, promisc=1):
        prn(buf[14:])
        #print("[+]: Payload len=", plen)
        #print("[+]: Time", t)
        #print("[+]: Payload", buf)
        if dead_delay and datetime.now() - last_refreshed > dead_delay:
            print('shutting down by timeout (no SIGUSR1 received in {dead_delay}')
            break


    if not args.no_join:
        joined.leave()

    now = datetime.now()
    global pkts, drops, sent
    print(f'{now}: {pkts} pkts, {drops} dropped {sent} sent')

    return 0

class StayJoined(object):
    def __init__(self, iface, src, grp):
        self.iface = iface
        self.src = src
        self.grp = grp
        self.p = None

    def leave(self):
        self.p.signal(signal.SIGINT)
        print(f'leaving {self.src}->{self.grp}')
        try:
          ret = self.p.wait(timeout=3)
          print(f'left {self.src}->{self.grp}')
        except subprocess.TimeoutExpired:
          print(f'hard kill for {self.src}->{self.grp}')
          self.p.kill()

          self.p = None

def do_join(iface, src, grp):
    sj = StayJoined(iface, src, grp)
    # TBD: maybe add a "join only" mode for mcrx-check that doesn't try
    # to receive, only does the join? --jake 2021-02-06
    # for now i join and listen, but just count packets.  hopefully not
    # harmful to receive and ignore if someone is sending on this port.
    cmd = ['/usr/bin/mcrx-check',
            '-i', iface,
            '-s', str(src),
            '-g', str(grp),
            '-p', '1783',  # 'Decomissioned [sic]'
            '-d', '0'
            '-c', '0']
    sj.p = subprocess.Popen(cmd)
    print(f'started {cmd}: {sj.p.pid}')
    return sj
    
if __name__=="__main__":
    ret = main(sys.argv)
    exit(ret)


'''
joined_sock = None
def do_join(iface, src, grp):
    # we aren't actually going to receive with the socket, but we still
    # need to create one so we can issue a join.  (The packets actually
    # will be received by scapy's pcap/bpf, not by the socket.)
    # --jake 2020-11

    # in 2020-11, python doesn't have IP_ADD_SOURCE_MEMBERSHIP or
    # IPV6_ADD_SOURCE_MEMBERSHIP still.  Also not present is
    # MCAST_JOIN_SOURCE_GROUP, tho that one's harder to use from
    # python since sockaddr_storage is only loosely defined in
    # RFC 3493 and in fact differs somewhat between OSs.
    # Probably the right thing to do is to pull the join code apart from
    # the receive code in libmcrx and integrate it into a pypi module to
    # allow this, but for now I hack it horribly.  I think this number is
    # linux-only.
    # https://elixir.bootlin.com/linux/latest/source/tools/include/uapi/linux/in.h#L150
    # in BSD it looks like 70 is the magic number instead.  Neither seems
    # to have the IPV6_ADD_SOURCE_MEMBERSHIP, which I guess since it isn't
    # even in RFC 3678 seems maybe not surprising.
    # http://fxr.watson.org/fxr/source/netinet/in.h?v=FREEBSD-12-STABLE#L478
    # --jake 2020-11

    if grp.version == 4:
        if not hasattr(socket, 'IP_ADD_SOURCE_MEMBERSHIP'):
            socket.IP_ADD_SOURCE_MEMBERSHIP=39
            socket.IP_DROP_SOURCE_MEMBERSHIP=40

        sock = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
        #sock.bind((src.packed, 10000))

        inf_ip = ip_address('172.17.0.1')
        mreq = struct.pack('4s4s4s', grp.packed, inf_ip.packed, src.packed)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_SOURCE_MEMBERSHIP, mreq)
    elif grp.version == 6:
        if not hasattr(socket, 'IPV6_ADD_SOURCE_MEMBERSHIP'):
            raise Exception('IPv6 ssm join not readily available from python')

        sock = socket.socket(family=socket.AF_INET6, type=socket.SOCK_DGRAM)
        inf_ip = ip_address('::')
        mreq = struct.pack('16s16s16s', grp.packed, inf.packed, src.packed)
        sock.sockopt(socket.IPPROTO_IP, socket.IPV6_ADD_SOURCE_MEMBERSHIP, mreq)

    # keep the socket alive, too
    joined_sock = sock
'''

