from yangson.instance import InstanceRoute
from jetconf.helpers import JsonNodeT
from jetconf.handler_base import StateDataListHandler, StateDataContainerHandler
from jetconf.data import BaseDatastore
from colorlog import info, warning

from .assignments import assigned

def generate_watcher_assignments(w):
    mapped_sgs = []
    monitored = set()
    for gsg in w.subscribed_gsgs.values():
        monitored.update(gsg.sg)
        source = gsg.sg[0]
        group = gsg.sg[1]
        sg_id = gsg.sg_id
        sg_dat = {
            'id': sg_id,
            'global-subscription': {
                'source': str(source),
                'group': str(group)
            }
          }
        if gsg.assignment:
            sg_dat['state'] = 'assigned-local-multicast'
            # tbd: support asm-group:
            sg_dat['local-mapping'] = {
                    'source': str(gsg.assignment.local_sg[0]),
                    'group': str(gsg.assignment.local_sg[1]),
                }
        else:
            sg_dat['state'] = 'unassigned'
        mapped_sgs.append(sg_dat)

    for mon in w.monitors.values():
        for gsg in assigned.subscribed_sgs.values():
            if gsg.sg not in monitored:
                if mon.includes(gsg):
                    monitored.update(gsg.sg)
                    source = gsg.sg[0]
                    group = gsg.sg[1]
                    sg_id = gsg.sg_id
                    sg_dat = {
                        'id': sg_id,
                        'global-subscription': {
                            'source': str(source),
                            'group': str(group)
                        }
                      }
                    if gsg.assignment:
                        sg_dat['state'] = 'assigned-local-multicast'
                        # tbd: support asm-group:
                        sg_dat['local-mapping'] = {
                                'source': str(gsg.assignment.local_sg[0]),
                                'group': str(gsg.assignment.local_sg[1]),
                            }
                    else:
                        sg_dat['state'] = 'unassigned'
                    mapped_sgs.append(sg_dat)


    return {
        'id': w.watcher_id,
        'mapped-sg': mapped_sgs
    }

def generate_watchers_list():
    watchers_list = []
    for w in assigned.watchers.values():
        watchers_list.append(generate_watcher_assignments(w))
    return watchers_list

class AssignedWatcherHandler(StateDataListHandler):
    def generate_list(self, node_ii: InstanceRoute, username: str, staging: bool) -> JsonNodeT:
        # This method has to generate entire list
        info(f'MappedSG List {node_ii}')
        assigned.check_timeouts()
        return generate_watchers_list()

    def generate_item(self, node_ii: InstanceRoute, username: str, staging: bool) -> JsonNodeT:
        # This method has to generate a specific node
        watcher_id = node_ii[-1].keys.get(('id', None))
        assigned.check_timeouts()

        w = assigned.watchers.get(watcher_id)
        if not w:
            # these all fail with an uncomfortable unhandled exception and closing of the connection instead of an error code:
            '''
            return {
                'ietf-restconf:errors' : {
                    'error' : [
                      {
                        'error-type' : 'application',
                        'error-tag' : 'unknown-element',
                        'error-app-tag' : 'unknown-watcher-id',
                        'error-path': '/ietf-restconf:restconf/ietf-restconf:state/ietf-mnat:assigned-channels/watcher',
                        'error-message' : f'No such watcher id "{watcher_id}"',
                      }
                    ]
                  }
                }
            '''
            # return None
            # raise ValueError(f'Found no watcher-id {watcher_id}')
            warning(f'no such watcher-id: {watcher_id} in assigned-channels/watcher generate_item')
            info(f'live watcher ids: {list(assigned.watchers.keys())}')
            return {}

        return generate_watcher_assignments(w)

class AssignedChannelsHandler(StateDataContainerHandler):
    def generate_node(self, node_ii: InstanceRoute, username: str, staging: bool) -> JsonNodeT:
        info("assigned-channels handler, ii = {}".format(node_ii))
        assigned.check_timeouts()
        watcher_list = generate_watchers_list()
        resp = {
            'watcher': watcher_list
        }
        return resp

# Instantiate state data handlers
def register_state_handlers(ds: BaseDatastore):
    msg = AssignedWatcherHandler(ds, "/ietf-mnat:assigned-channels/watcher")
    ds.handlers.state.register(msg)
    msg = AssignedChannelsHandler(ds, "/ietf-mnat:assigned-channels")
    ds.handlers.state.register(msg)

# check here for some moreexamples:
# https://gitlab.nic.cz/jetconf/jetconf-jukebox/-/blob/master/jetconf_jukebox/usr_state_data_handlers.py
