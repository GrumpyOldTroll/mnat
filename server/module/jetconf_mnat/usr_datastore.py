from jetconf.data import JsonDatastore
from jetconf.journal import RpcInfo
from typing import Any, Tuple
from yangson.instance import InstanceNode, InstanceRoute
from colorlog import info
from .assignments import assigned
from ipaddress import ip_address

class UserDatastore(JsonDatastore):
    def create_node_rpc(self, root: InstanceNode, rpc: RpcInfo, value: Any) -> Tuple[InstanceNode, bool]:
        info(f'create_node_rpc called: path={rpc.path}, value={value}')
        ret = super().create_node_rpc(root, rpc, value)
        info(f'create_node_rpc finished: ret={ret[0].path},ret[1]')
        if rpc.path == '/ietf-mnat:egress-global-joined' and \
                isinstance(value, dict) and 'ietf-mnat:watcher' in value:
            watcher_id = value['ietf-mnat:watcher']['id']
            info('created egress joined {watcher_id}')
            sgs = []
            for sgd in value['ietf-mnat:watcher']['joined-sg']:
                sg = (ip_address(sgd['source']), ip_address(sgd['group']))
                sgs.append(sg)
            assigned.set_subscribed_sgs(watcher_id, sgs)
        elif rpc.path == '/ietf-mnat:ingress-watching' and \
                isinstance(value, dict) and 'ietf-mnat:watcher' in value:
            watcher_id = value['ietf-mnat:watcher']['id']
            info('created ingress watching {watcher_id}')
            monitors = value['ietf-mnat:watcher']['monitor']
            assigned.set_monitors(watcher_id, monitors)

        return ret

    def update_node_rpc(self, root: InstanceNode, rpc: RpcInfo, value: Any) -> Tuple[InstanceNode, bool]:
        info(f'update_node_rpc called: path={rpc.path}, value={value}')
        ret = super().update_node_rpc(root, rpc, value)
        info(f'update_node_rpc finished: ret={ret[0].path},ret[1]')
        if rpc.path.startswith('/ietf-mnat:egress-global-joined/watcher=') and \
                isinstance(value, dict) and 'ietf-mnat:watcher' in value:
            watcher_id = self.get_dm().parse_resource_id(rpc.path)[-1].keys[('id',None)]
            info('updated egress joined {watcher_id}')
            sgs = []
            for sgd in value['ietf-mnat:watcher']['joined-sg']:
                sg = (ip_address(sgd['source']), ip_address(sgd['group']))
                sgs.append(sg)
            assigned.set_subscribed_sgs(watcher_id, sgs)
        elif rpc.path.startswith('/ietf-mnat:ingress-watching/watcher=') and \
                isinstance(value, dict) and 'ietf-mnat:watcher' in value:
            watcher_id = self.get_dm().parse_resource_id(rpc.path)[-1].keys[('id',None)]
            info('updated ingress watching {watcher_id}')
            monitors = value['ietf-mnat:watcher']['monitor']
            assigned.set_monitors(watcher_id, monitors)

        return ret

    '''
    def delete_node_rpc(self, root: InstanceNode, rpc: RpcInfo) -> Tuple[InstanceNode, bool]:
        info(f'delete_node_rpc called: root={rpc.path}')
        ret = super().delete_node_rpc(root, rpc)
        info(f'delete_node_rpc finished: ret={ret[0].path}')
        return ret
    '''

    # Save and Load methods can be customized here

'''
https://gitlab.nic.cz/jetconf/jetconf-jukebox/-/blob/master/jetconf_jukebox/usr_state_data_handlers.py

        artist_list_ii = self.ds.parse_ii("/example-jukebox:jukebox/library/artist", PathFormat.URL)
        jb_artists = self.ds.get_data_root().goto(artist_list_ii).value
        album_count = 0

        for artist in jb_artists:
            album_list = artist.get("album", [])
            album_count += len(album_list)

        return album_count
'''
