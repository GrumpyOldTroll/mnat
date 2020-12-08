from colorlog import info, warning, error, debug
from os import urandom
from base64 import b32encode

from yangson.instance import InstanceRoute
from yangson.exceptions import NonexistentInstance

from jetconf.helpers import JsonNodeT, PathFormat
from jetconf.data import BaseDatastore
from .assignments import assigned

class OpHandlersContainer:
    def __init__(self, ds: BaseDatastore):
        self.ds = ds

    def establish_subscription_op(self, input_args: JsonNodeT, username: str) -> JsonNodeT:
        info(f'called establish_subscription: {input_args}')
        return {'id':2}

    def refresh_watcher_id_op(self, input_args: JsonNodeT, username: str) -> JsonNodeT:
        watch_id = input_args.get('watcher-id')
        info(f'called refresh-watcher-id: {watch_id}')
        debug(f'  (from input args: {input_args})')
        assigned.check_timeouts()

        if not watch_id:
            raise ValueError(f'Could not extract watcher-id from {input_args}')
        w = assigned.watchers.get(watch_id)
        if not w:
            raise ValueError(f'Found no watcher-id {watch_id}')
        w.refresh()

    def get_new_watcher_id_op(self, input_args: JsonNodeT, username: str) -> JsonNodeT:
        info(f'called get-new-watcher-id: {input_args}')
        watcher_id = b32encode(urandom(10)).decode('utf-8')
        assigned.create_watcher(watcher_id)
        return {'watcher-id': watcher_id, 'refresh-period': 20}

def register_op_handlers(ds: BaseDatastore):
    op_handlers_obj = OpHandlersContainer(ds)
    ds.handlers.op.register(op_handlers_obj.establish_subscription_op,
            "ietf-subscribed-notifications:establish-subscription")
    ds.handlers.op.register(op_handlers_obj.get_new_watcher_id_op,
            "ietf-mnat:get-new-watcher-id")
    ds.handlers.op.register(op_handlers_obj.refresh_watcher_id_op,
            "ietf-mnat:refresh-watcher-id")

