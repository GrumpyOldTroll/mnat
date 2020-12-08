#!/usr/bin/env python3

from ipaddress import ip_address, ip_network
from colorlog import info, warning
from datetime import datetime, timedelta

class LocalPool(object):
    def __init__(self):
        self.free_sgs = set()
        self.assigned_sgs = set()

        from_src = ip_address('10.9.1.2')
        for i in range(1,5):
            to_grp = ip_address(f'239.1.1.{i}')
            self.free_sgs.add((from_src, to_grp))

    def borrow_local_sg(self, for_global_sg=None):
        if not self.free_sgs:
            return None

        sg = self.free_sgs.pop()
        self.assigned_sgs.add(sg)
        return sg

    def return_local_sg(self, sg):
        assert(sg not in self.free_sgs)
        self.assigned_sgs.remove(sg)
        adding_new = (len(self.free_sgs) == 0)
        self.free_sgs.add(sg)
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
            local_sg = top_assignments.local_pool.borrow_local_sg(gsg)
            if local_sg:
                assignment = LocalAssignment(gsg, local_sg)
                gsg.assignment = assignment
                info(f'assigned {gsg.assignment.local_sg[0]}->{gsg.assignment.local_sg[1]} for {gsg.assignment.global_sg.sg[0]}->{gsg.assignment.global_sg.sg[1]}')

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
        self.local_pool = LocalPool()

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

