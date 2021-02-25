#!/usr/bin/env python3

from ipaddress import ip_address, ip_network
from colorlog import debug, info, warning, error
from datetime import datetime, timedelta
from os.path import isfile
from os import getenv
from random import randrange
import json
from collections import OrderedDict
from itertools import islice
import traceback

strict = True

def get_nth_address(ip_net, idx):
    '''
The hosts() iter construction seems to be instantiating each
address or something, because it's taking a terribly long time:

    $ time python -c "from ipaddress import ip_network; from itertools import islice; print(next(islice(ip_network('232.0.0.0/8'), 8, None)))"
    232.0.0.8

    real	0m0.038s
    user	0m0.024s
    sys	0m0.011s

    $ time python -c "from ipaddress import ip_network; from itertools import islice; print(next(islice(ip_network('232.0.0.0/8'), 8388600, None)))"
    232.127.255.248

    real	0m4.732s
    user	0m4.416s
    sys	0m0.042s

    $ time python -c "from ipaddress import ip_network; from itertools import islice; print(next(islice(ip_network('232.0.0.0/8'), 8388600, None, 8388600)))"
    232.127.255.248

    real	0m4.205s
    user	0m4.116s
    sys	0m0.036s

    $ time python -c "from ipaddress import ip_network; from itertools import islice; print(next(islice(ip_network('232.0.0.0/8'), 16777208, None, 16777208)))"
    232.255.255.248

    real	0m8.219s
    user	0m8.129s
    sys	0m0.036s

TBD: check the idx is inside ip_net.num_addresses, pull out an
integer representation from ip_net.network_address.packed (including
with ipv6), add idx, conver back into an address and return it, instead
of this very sad slice-based function that iterates on the existing
hosts() function for ip_network.
Better still, upstream this functionality to the hosts() iterator and
make it so the islice's step param makes it a properly constant-time op.

NB: bug in python's ip_network, I think.  It says hosts() returns an
iterator, but for a /32 it returns a list with 1 entry:
$ python3 -c "from ipaddress import ip_network; print(next(ip_network('10.1.1.0/31').hosts()))"
10.1.1.0
$ python3 -c "from ipaddress import ip_network; print(next(ip_network('10.1.1.0/32').hosts()))"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
TypeError: 'list' object is not an iterator

NB: and another bug? cannot iterate to all the addresses:
$ python3 -c "from ipaddress import ip_network; from itertools import islice; print(ip_network('10.1.1.0/29').num_addresses)"
8
$ python3 -c "from ipaddress import ip_network; from itertools import islice; print(next(islice(iter(ip_network('10.1.1.0/29').hosts()), 6, None,6)))"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
StopIteration
$ python3 -c "from ipaddress import ip_network; from itertools import islice; print(list(ip_network('10.1.1.0/29').hosts()))"
[IPv4Address('10.1.1.1'), IPv4Address('10.1.1.2'), IPv4Address('10.1.1.3'), IPv4Address('10.1.1.4'), IPv4Address('10.1.1.5'), IPv4Address('10.1.1.6')]
    
--jake 2021-02-20
    '''
    # from the "consume" recipe in https://docs.python.org/3.7/library/itertools.html#itertools-recipes
    info(f'pulling addr {idx}/{ip_net.num_addresses} from {ip_net}')
    if idx == 0:
        return ip_net.network_address
    elif idx == (ip_net.num_addresses - 1):
        return ip_net.broadcast_address
    else:
        if idx == 1:
            return next(ip_net.hosts())
        else:
            return next(islice(ip_net.hosts(), idx-1, None, idx-1))


class PoolRange(object):
    def __init__(self, pool_fname, idx, range_val, default_source):
        group_range_str = range_val.get('group-range')
        exclude_vals = range_val.get('exclude', [])
        source_range_str = range_val.get('source-range', default_source)

        self.idx = idx
        self.base_group_range = ip_network(group_range_str)
        if source_range_str in set(['asm', 'keep']):
            self.source_range = source_range_str
            self.source_count = 1
        else:
            self.source_range = ip_network(source_range_str)
            self.source_count = self.source_range.num_addresses
        self.usable_ranges = []
        self.group_count = 0
        self.in_use = []

        exclude = []

        if not self.base_group_range.is_multicast:
            if strict:
                raise ValueError(f'failed parse of {pool_fname}: non-multicast base range {self.base_group_range}')
            warning(f'strange parse of {pool_fname}: non-multicast base range {self.base_group_range}, using non-multicast destinations in pool')

        for name,val in range_val.items():
            if name not in set(['group-range','source-range','exclude','note']):
                if strict:
                    raise ValueError(f'failed parse of {pool_fname}: unknown field {name} in {range_val}')
                warning(f'ignoring group-pool item "{name}" in {pool_fname}')
                continue

        for exclude_val in exclude_vals:
            for name,val in exclude_val.items():
                if name not in set(['groupex-range', 'note']):
                    if strict:
                        raise ValueError(f'failed parse of {pool_fname}: unknown field {name} in exclude of {range_val}')
                    warning(f'ignoring group-range/exclude item "{name}" in {pool_fname}')
                    continue

            groupex_val = exclude_val.get('groupex-range')
            if not groupex_val:
                warning(f'exclude list for {group_range_str} has no groupex-range entry in {pool_fname}')
                if strict:
                    raise ValueError(f'failed parse of {pool_fname}: no groupex-range in exclude of {range_val}')
                continue

            groupex = ip_network(groupex_val)
            if not groupex.subnet_of(self.base_group_range):
                if strict:
                    raise ValueError(f'failed parse of {pool_fname}: no groupex-range in exclude of {range_val}')
                warning(f'ignoring groupex-range="{groupex}": not a subnet o {self.base_group_range}')
                continue

            skip = False
            for prior_exclude in exclude:
                if prior_exclude.overlaps(groupex):
                    if strict:
                        raise ValueError(f'failed parse of {pool_fname}: groupex-range {groupex} overlaps with {prior_exclude}')
                    warning(f'ignoring groupex-range="{groupex}": overlaps with previous groupex-range="{prior_exclude}"')
                    skip = True
                    break
            if skip:
                continue

            exclude.append(groupex)

        valid_ranges = [self.base_group_range]
        remaining_excludes = []
        for ex in exclude:
            new_valid = []
            for rg in valid_ranges:
                if ex.subnet_of(rg):
                    new_valid.extend(rg.address_exclude(ex))
                else:
                    new_valid.add(rg)
            valid_ranges = new_valid
        
        self.usable_ranges = valid_ranges
        self.group_count = sum(map(lambda x: x.num_addresses, self.usable_ranges))

class LocalPool(object):
    def __init__(self, pool_fname, pool_json):
        self.ranges = []
        self.assigned_sgs = dict()
        self.assigned_idxs = OrderedDict()

        group_pool = pool_json.get('group-pool')
        if not group_pool:
            warning(f'no group-pool entry at top level of {pool_fname}')
            group_pool = {'ranges':[]}
        self.default_source_range = group_pool.get('default-source-range', 'keep')
        for name,val in pool_json.items():
            if name not in set(['group-pool','note']):
                warning(f'ignoring top-level pool item "{name}" in {pool_fname}')
                continue

        for name,val in group_pool.items():
            if name not in set(['ranges','default-source-range','note']):
                warning(f'ignoring group-pool item "{name}" in {pool_fname}')
                continue

        idx = 0
        sg_count = 0
        for range_val in group_pool.get('ranges'):
            pool_range = PoolRange(pool_fname, idx, range_val, self.default_source_range)
            self.ranges.append(pool_range)
            cur_count = pool_range.group_count * pool_range.source_count
            sg_count += cur_count
            idx += 1
        self.sg_count = sg_count
        # TBD: sanity checks: do they overlap?  complain somehow.

    def borrow_local_sg(self, for_global_gsg):
        for_global_sg = for_global_gsg.sg
        info(f'borrowing sg from pool for {for_global_sg}')
        if len(self.assigned_idxs) >= self.sg_count:
            # all available sgs are assigned
            return None

        idx = randrange(self.sg_count - len(self.assigned_idxs))
        for lower_idxs in self.assigned_idxs.keys():
            if lower_idxs >= idx:
                break
            idx += 1

        next_idx = idx
        orig_idx = idx
        unexpected_tryfails = []
        while True:
            if len(unexpected_tryfails) != 0 and next_idx == orig_idx:
                warning(f'assignment fail-safe: wrapped the available idxs ({len(unexpected_tryfails)} unexpected failure indexes in pool assignment from {orig_idx})')
                return None

            if len(unexpected_tryfails) > 50:
                warning(f'assignment loop failsafe: hit unexpected failures on idxs: {unexpected_tryfails}')
                return None

            idx = next_idx
            next_idx = (idx + 1) % self.sg_count

            if idx in self.assigned_idxs:
                warning(f'generated idx {idx} hit existing assigned_idx with {assigned_idxs[idx]} and looped idx {assigned_sgs.get(assigned_idxs[idx])}')
                unexpected_tryfails.add(idx)
                continue

            range_idx = idx
            found = False
            for rng in self.ranges:
                rng_sg_count = rng.source_count * rng.group_count
                if range_idx < rng_sg_count:
                    found = True
                    break
                range_idx -= rng_sg_count

            if not found:
                warning(f'range fail-safe: orig_idx {orig_idx} reached end of ranges with {range_idx} from {idx} overall')
                return None

            src_idx = range_idx // rng.group_count
            grp_idx = range_idx % rng.group_count

            source_ip = None

            if rng.source_range in set(['keep','asm']):
                if src_idx != 0:
                    warning(f'range source idx fail-safe: src_idx = {src_idx} on src,grp counts {rng.source_count},{rng.group_count}, range_idx={range_idx}')
                    return None
                if rng.source_range == 'asm':
                    source_ip = None
                else:
                    source_ip = for_global_sg[0]
            else:
                source_ip = get_nth_address(rng.source_range, src_idx)

            info(f'picked source ip {source_ip} from idx {idx} (src_idx={src_idx}/{rng.source_count})')

            net_idx = grp_idx
            found=False
            for grp_net in rng.usable_ranges:
                if net_idx < grp_net.num_addresses:
                    found = True
                    break
                net_idx -= grp_net.num_addresses

            if not found:
                warning(f'group net fail-safe: orig_idx {orig_idx} found source {source_ip} on {src_idx} but missed group on {grp_idx}')
                return None

            group_ip = get_nth_address(grp_net, net_idx)

            info(f'picked group ip {group_ip} from idx {idx} (grp_idx={grp_idx}->net_idx={net_idx} from {grp_net})')
            sg = (source_ip,group_ip)
            if sg in self.assigned_sgs:
                warning(f'generated assigned_sg {sg} on idx {idx} but hit existing assigned_sg with {assigned_sgs[sg]}')
                unexpected_tryfails.append(idx)
                continue

            if idx in self.assigned_idxs:
                warning(f'internal error: after checking assigned idx hit assigned idx already present for {idx} finding {sg} vs. {self.assigned_idxs[sg]}')
                unexpected_tryfails.append(idx)
                continue

            self.assigned_idxs[idx] = sg
            self.assigned_sgs[sg] = idx
            return sg

        return None

    def return_local_sg(self, sg):
        if sg not in self.assigned_sgs:
            warning(f'local sg {sg} returned but was not assigned')
            return False

        idx = self.assigned_sgs[sg]
        del(self.assigned_sgs[sg])

        if idx not in self.assigned_idxs:
            warning(f'internal error: idx {idx} from return of local sg {sg} not in assigned_idxs')
            return False

        adding_new = (len(self.assigned_idxs) == self.sg_count)
        del(self.assigned_idxs[idx])

        return adding_new

class Watcher(object):
    def __init__(self, watcher_id):
        self.watcher_id = watcher_id
        self.subscribed_gsgs = {}  # { GlobalSG.sg: GlobalSG }
        self.last_refresh = datetime.now()
        self.monitors = {} # { monitor_id: Monitor)

    def refresh(self):
        self.last_refresh = datetime.now()

    def unsubscribe(self, top_assignments, sg):
        if sg not in self.subscribed_gsgs:
            warning(f'tried to remove {sg} from {self.watcher_id} when not present')
            return
        gsg = self.subscribed_gsgs[sg]
        del(self.subscribed_gsgs[sg])
        del(gsg.subscribed_watchers[self.watcher_id])
        if len(gsg.subscribed_watchers) == 0:
            info(f'all subscribers of {sg[0]}->{sg[1]} left')
            del(top_assignments.subscribed_sgs[gsg.sg])
            if gsg.assignment:
                newly_freed = top_assignments.local_pool.return_local_sg(gsg.assignment.local_sg)
                info(f'unassigned {gsg.assignment.local_sg[0]}->{gsg.assignment.local_sg[1]}, new space={newly_freed}')
                gsg.assignment = None
                if newly_freed:
                    for sg,gsg in top_assignments.subscribed_sgs.items():
                        if not gsg.assignment:
                            local_sg = top_assignments.local_pool.borrow_local_sg(gsg)
                            if local_sg:
                                assignment = LocalAssignment(gsg, local_sg)
                                gsg.assignment = assignment
                                info(f'assigned {gsg.assignment.local_sg[0]}->{gsg.assignment.local_sg[1]} for {gsg.assignment.global_sg.sg[0]}->{gsg.assignment.global_sg.sg[1]}')
                                break

    def subscribe(self, top_assignments, sg):
        gsg = top_assignments.subscribed_sgs.get(sg)
        if not gsg:
            gsg = GlobalSG(sg, top_assignments.new_sg_id())
            top_assignments.subscribed_sgs[sg] = gsg
            try:
                local_sg = top_assignments.local_pool.borrow_local_sg(gsg)
            except Exception as e:
                error(str(e))
                error(traceback.format_exc())
                raise

            if local_sg:
                assignment = LocalAssignment(gsg, local_sg)
                gsg.assignment = assignment
                info(f'assigned {gsg.assignment.local_sg[0]}->{gsg.assignment.local_sg[1]} for {gsg.assignment.global_sg.sg[0]}->{gsg.assignment.global_sg.sg[1]}')
            else:
                info(f'no local assignment returned for {gsg.assignment.global_sg.sg[0]}->{gsg.assignment.global_sg.sg[1]}')

        if self.watcher_id not in gsg.subscribed_watchers:
            gsg.subscribed_watchers[self.watcher_id] = self
        if gsg.sg not in self.subscribed_gsgs:
            self.subscribed_gsgs[gsg.sg] = gsg

class LocalAssignment(object):
    def __init__(self, global_sg, local_sg):
        self.global_sg = global_sg
        self.local_sg = local_sg

class GlobalSG(object):
    def __init__(self, sg, sg_id):
        self.sg = sg
        self.subscribed_watchers = {} # { Watcher.watcher_id: Watcher }
        self.assignment = None
        self.sg_id = sg_id

class BaseMonitor(object):
    def __init__(self, monitor_id):
        self.monitor_id = monitor_id

    def includes(self, gsg):
        return False

class SourcePrefixMonitor(BaseMonitor):
    def __init__(self, monitor_id, src_pre):
        super().__init__(monitor_id)
        self.src_pre = src_pre

    def includes(self, gsg):
        if gsg.sg[0] in self.src_pre:
            return True
        return False

class Assignments(object):
    def __init__(self):
        self.watchers = {} # { Watcher.watcher_id : Watcher }
        self.subscribed_sgs = {} # { GlobalSG.sg : GlobalSG }
        self.timeout_duration = timedelta(seconds=60)
        self.recheck_delay = timedelta(seconds=15)
        self.next_sg_id = 1
        self.last_check = datetime.now()

        pool_fname = getenv('MNAT_POOL')
        if pool_fname:
            info(f'Loading {pool_fname} (set by MNAT_POOL environment)')
        else:
            pool_fname = '/etc/mnat/pool.json'
            info(f'Loading {pool_fname} (default location)')

        if isfile(pool_fname):
            with open(pool_fname) as f:
                pool_fd = f.read()
            pool_json = json.loads(pool_fd)
        else:
            warning(f'no file at {pool_fname}, using default')
            pool_fname = '(internal-default)'
            pool_json = {'group-pool':{'ranges':[
                    { 'group-range': '239.1.1.0/29',
                      'source-range':'10.9.1.2/32' } ] } }

        self.local_pool = LocalPool(pool_fname, pool_json)

    def new_sg_id(self):
        ret = self.next_sg_id
        self.next_sg_id += 1
        return ret

    def create_watcher(self, watcher_id):
        if watcher_id in self.watchers:
            raise ValueError(f'watcher-id {watcher_id} already taken')
        w = Watcher(watcher_id)
        self.watchers[watcher_id] = w
        return w

    def check_timeouts(self):
        now = datetime.now()
        if now - self.last_check < self.recheck_delay:
            return

        self.check_invariants()
        self.last_check = now
        removes = []
        for w in self.watchers.values():
            if now - w.last_refresh > self.timeout_duration:
                removes.append(w)
        for w in removes:
            del(self.watchers[w.watcher_id])
            gsg_removes = []
            while len(w.subscribed_gsgs):
                gsg = next(iter(w.subscribed_gsgs.values()))
                w.unsubscribe(self, gsg.sg)
        self.check_invariants()

    def set_monitors(self, watcher_id, monitors):
        info(f'setting monitors {watcher_id}: {monitors}')
        self.check_invariants()
        w = self.watchers.get(watcher_id)
        if not w:
            w = self.create_watcher(watcher_id)
        set_ids = set()
        for monitor in monitors:
            mid = monitor['id']
            set_ids.update(mid)
            src_pre_str = monitor.get('global-source-prefix')
            if src_pre_str:
                src_pre = ip_network(src_pre_str)
                mon = SourcePrefixMonitor(mid, src_pre)
                w.monitors[mon.monitor_id] = mon

        removes = set(w.monitors.keys()) - set_ids
        for mid in removes:
            del(w.monitors[mid])

    def set_subscribed_sgs(self, watcher_id, sgs):
        info(f'setting sgs {watcher_id}: {sgs}')
        self.check_invariants()
        w = self.watchers.get(watcher_id)
        if not w:
            w = self.create_watcher(watcher_id)
        if w.subscribed_gsgs:
            checks = set(sgs)
            removes = list(filter(lambda x: x not in checks, w.subscribed_gsgs.keys()))
            for sg in removes:
                w.unsubscribe(self, sg)
        for sg in sgs:
            w.subscribe(self, sg)
        self.check_invariants()

    def check_invariants(self):
        for wid, w in self.watchers.items():
            try:
                assert(wid == w.watcher_id)
            except:
                print(f'invariant fail wid: {wid}')
                raise
            for sg, gsg in w.subscribed_gsgs.items():
                try:
                    assert(sg == gsg.sg)
                    assert(sg in self.subscribed_sgs)
                    assert(wid in gsg.subscribed_watchers)
                    assert(gsg.subscribed_watchers[wid] is w)
                except:
                    print(f'invariant fail wid-backref: {wid}: {sg}')
                    raise

        for sg, gsg in self.subscribed_sgs.items():
            try:
                assert(sg == gsg.sg)
            except:
                print(f'invariant fail sg: {sg}')
                raise
            for wid, w in gsg.subscribed_watchers.items():
                try:
                    assert(wid == w.watcher_id)
                    assert(wid in self.watchers)
                    assert(sg in w.subscribed_gsgs)
                    assert(w.subscribed_gsgs[sg] is gsg)
                except:
                    print(f'invariant fail sg-backref: {wid}: {sg}')
                    raise

assigned = Assignments()

