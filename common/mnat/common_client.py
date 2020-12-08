#!/usr/bin/env python3

# pip install h2
# pip install twisted pyOpenSSL service_identity watchdog

# H2Protocol code adapted from:
# https://python-hyper.org/projects/hyper-h2/en/stable/twisted-post-example.html
#AUTHORITY = u'nghttp2.org'
#PATH = '/httpbin/post'


import mimetypes
import os
import sys
import json
import logging
import time
import argparse
from ipaddress import ip_address
from datetime import datetime, timedelta
from functools import total_ordering
import subprocess
import signal

from twisted.internet import reactor, defer, task
from twisted.internet.endpoints import connectProtocol, SSL4ClientEndpoint
from twisted.internet.protocol import Protocol
from twisted.internet.ssl import optionsForClientTLS
from twisted.internet.ssl import Certificate
from h2.connection import H2Connection
from h2.events import (
    ResponseReceived, DataReceived, StreamEnded, StreamReset, WindowUpdated,
    SettingsAcknowledged,
)

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

class RequestBuf(object):
    def __init__(self, path, method='GET', data=None, content_type=None, content_encoding=None, callback=None):
        self.path = path
        self.method = method
        self.data = data
        if content_type:
            self.content_type = content_type
        else:
            self.content_type='application/yang-data+json'
        self.content_encoding = content_encoding
        self.callback = callback
        self.built_headers = []
        self.response_headers = []
        self.response_data = None

@total_ordering
class LocalAssignment(object):
    '''This is an object mostly to support future extensions for more
       kinds of local assignments than just (S,G)s.'''
    def __init__(self, state, local_mapping):
        if not local_mapping:
            assert(state.find('unassigned') != -1)
            self.source = None
            self.group = None
            return
        if 'asm-group' in local_mapping:
            self.source = None
            self.group = ip_address(local_mapping['asm-group'])
        else:
            self.source = ip_address(local_mapping['source'])
            self.group = ip_address(local_mapping['group'])

    def __repr__(self):
        if not self.group:
            return '(unassigned)'
        if not self.source:
            return '*->{self.group}'
        return f'{self.source}->{self.group}'

    def __lt__(self, other):
        return (self.source,self.group) < (other.source,other.group)

    def __eq__(self, other):
        return (self.source,self.group) == (other.source,other.group)

class Mapping(object):
    def __init__(self, global_source, global_group, local_assignment):
        self.source = ip_address(global_source)
        self.group = ip_address(global_group)
        self.local = local_assignment

    def __repr__(self):
        return f'{self.source}->{self.group}: {self.local}'

TRANSLATE_TO_LOCAL=1
TRANSLATE_TO_GLOBAL=2
class TranslateManager(object):
    def __init__(self, mapping, direction, in_int, out_int, logger, no_join=False):
        if direction not in set([TRANSLATE_TO_LOCAL,TRANSLATE_TO_GLOBAL]):
            raise ValueError('TranslateManager direction ({direction}) must be either TRANSLATE_TO_LOCAL={TRANSLATE_TO_LOCAL} or TRANSLATE_TO_GLOBAL={TRANSLATE_TO_GLOBAL}')

        self.direction = direction
        self.mapping = mapping
        self.in_int = in_int
        self.out_int = out_int
        self.logger = logger
        self.p = None
        self.no_join = no_join

    def start(self):
        if self.p:
            self.logger.warning(f'internal error: tried to start already-started translator: {p}')
            return

        if not self.in_int or not self.out_int:
            self.logger.warning(f'tried to start translator for {self.mapping} without in or out interface')
            return

        if not self.mapping.local:
            self.logger.info(f'not starting translator without an assignment: {self.mapping}')
            return

        if self.direction == TRANSLATE_TO_LOCAL:
            src_in, grp_in = self.mapping.source, self.mapping.group
            src_out = self.mapping.local.source
            grp_out = self.mapping.local.group
        elif self.direction == TRANSLATE_TO_GLOBAL:
            src_out, grp_out = self.mapping.source, self.mapping.group
            src_in = self.mapping.local.source
            grp_in = self.mapping.local.group

        self.logger.info(f'starting translator for {self.mapping}')
        cmd = [sys.executable, '/bin/mnat-translate',
                '--iface-in', self.in_int,
                '--iface-out', self.out_int,
                '--src-in', str(src_in),
                '--grp-in', str(grp_in),
                '--src-out', str(src_out),
                '--grp-out', str(grp_out),
                '--timeout', '100']
        if self.no_join:
            cmd.append('--no-join')
        self.logger.info('launching translator: "%s"' % ' '.join(cmd))
        self.p = subprocess.Popen(cmd)

    def stop(self):
        if not self.p:
            self.logger.info(f'stopping translator without a process: {self.mapping}')
            return

        self.logger.info(f'stopping translator for {self.mapping}')
        self.p.send_signal(signal.SIGHUP)
        try:
            ret = self.p.wait(timeout=6)
        except TimeoutExpired:
            self.logger.warning(f'hard-stopping translator for {self.mapping}')
            self.p.kill()
        self.p = None

    def check_for_update(self, mapping):
        if self.mapping.source != mapping.source or self.mapping.group != mapping.group:
            self.logger.error(f'internal error: checking for translator update on inconsistent (S,G): {self.mapping.source}->{self.mapping.group} != {mapping.source}->{mapping.group}')
            return
        if not self.p:
            self.logger.info(f'refreshing translator without a process: {self.mapping}')
            return

        if self.mapping.local == mapping.local:
            self.logger.debug(f'mapping stayed stable: {mapping}, refreshing')
            self.p.send_signal(signal.SIGUSR1)
            return

        self.logger.info(f'changing translator for {mapping.source}->{mapping.group} from {self.mapping.local} to {mapping.local}')
        self.stop()
        self.mapping.local = mapping.local
        self.start()

class H2Protocol(Protocol):
    def __init__(self, server, port, logger, certfile, cacert):
        client_cert = None
        if certfile:
            with open(certfile) as f:
                cert_dat=f.read()
            client_cert=Certificate.loadPEM(cert_dat)

        server_cert = None
        if cacert:
            with open(cacert) as f:
                cert_dat=f.read()
            server_cert=Certificate.loadPEM(cert_dat)

        #AUTHORITY='localhost'
        options = optionsForClientTLS(
            hostname=server,
            acceptableProtocols=[b'h2'],
            trustRoot=server_cert, # use None for system default on real certs.
            clientCertificate=client_cert
        )

        if not logger:
            logger = get_logger('mnat-client', 0)
        self.logger = logger
        self.conn = H2Connection()
        self.authority = server
        self.port = port
        self.options = options
        self.known_proto = None
        self.flow_control_deferred = None
        self.settings_acked = False
        self.buffered_requests = []
        self.shutting_down = False
        self.request_table = {}
        self.watcher_id = None
        self.root = '/mnat-ds'
        now = datetime.now()
        self.last_refresh_time = now
        self.last_assign_check_time = now
        self.last_restart_time = now
        self.connect_start_time = now
        self.dead_threshold = timedelta(seconds=20)
        self.restart_check = task.LoopingCall(self.restartIfDead)
        self.restart_check.start(3, now=False)
        self.restarting_deferred = False
        self.refreshing_task = None
        self.polling_task = None
        self.direction = None
        self.in_interface = None
        self.out_interface = None
        self.current_mappings = dict()
        self.no_join = False

    def start(self):
        now = datetime.now()
        if self.watcher_id and (now - self.last_refresh_time > 3*self.dead_threshold):
            self.logger.info(f'(discarding watcher_id={self.watcher_id} during start)')
            self.watcher_id = None

        connectProtocol(
            SSL4ClientEndpoint(reactor, self.authority,
                self.port, self.options),
            self
        )
        self.connect_start_time = now


    def check_start(self):
        if self.restarting_deferred:
            self.logger.info(f'deferred restart fired, re-establishing')
            self.restarting_deferred = False
            self.conn = H2Connection()
            self.start()

    def restartIfDead(self):
        if self.shutting_down:
            return

        now = datetime.now()
        dead = False
        reason = []
        if now - self.connect_start_time > self.dead_threshold:
            if now - self.last_refresh_time > self.dead_threshold:
                dead = True
                reason.append(f'last watchid response {(now-self.last_refresh_time).seconds}s ago')
            if now - self.last_assign_check_time > self.dead_threshold:
                dead = True
                reason.append(f'last assigned check response {(now-self.last_assign_check_time).seconds}s ago')

        if not dead:
            return

        if now - self.last_restart_time < self.dead_threshold:
            self.logger.info(f' (waiting on prior restart)')
            return
        self.last_restart_time = now

        self.logger.info(f'disconnecting and firing restart ({reason})')
        if self.watcher_id and (now - self.last_refresh_time > 3*self.dead_threshold):
            self.logger.info(f'(discarding watcher_id={self.watcher_id} as not working')
            self.watcher_id = None

        self.restarting_deferred = True
        reactor.callLater(20, self.check_start)
        if self.conn and self.transport:
            self.conn.close_connection()
            self.transport.write(self.conn.data_to_send())
            self.transport.loseConnection()
        else:
            self.logger.info('(transport is already down)')

    def endStream(self, stream_id):
        """
        We call this when the stream is cleanly ended by the remote peer. That
        means that the response is complete.

        """
        if stream_id not in self.request_table:
            self.logger.info(f'endStream for {stream_id} called again?')
        else:
            self.logger.info(f'cleanly ending stream {stream_id}')
            req = self.request_table[stream_id]
            del(self.request_table[stream_id])
            if req.callback:
                self.logger.debug(f'fired callback {req.callback}')
                req.callback(req)

        if self.shutting_down:
            self.conn.close_connection()
            self.transport.write(self.conn.data_to_send())
            self.transport.loseConnection()
            self.settings_acked = False

    def connectionLost(self, reason=None):
        """
        Called by Twisted when the connection is gone.
        """
        self.logger.error('connection to server lost')
        self.conn = None
        self.connected = 0
        self.transport = None
        if not self.shutting_down:
            self.restarting_deferred = True
            reactor.callLater(5, self.check_start)
        else:
            if reactor.running:
                reactor.stop()

    def connectionMade(self):
        """
        Called by Twisted when the TCP connection is established. We can start
        sending some data now: we should open with the connection preamble.
        """
        self.logger.info('connection made')
        self.conn.initiate_connection()
        self.transport.write(self.conn.data_to_send())

    def dataReceived(self, data):
        """
        Called by Twisted when data is received on the connection.

        We need to check a few things here. Firstly, we want to validate that
        we actually negotiated HTTP/2: if we didn't, we shouldn't proceed!

        Then, we want to pass the data to the protocol stack and check what
        events occurred.
        """
        if not self.known_proto:
            self.known_proto = self.transport.negotiatedProtocol
            assert self.known_proto == b'h2'

        events = self.conn.receive_data(data)

        for event in events:
            if isinstance(event, ResponseReceived):
                self.handleResponse(event.stream_id, event.headers)
            elif isinstance(event, DataReceived):
                # supposedly acknowledge_received_data is preferred, but
                # when the stream is closed this raises an exception
                # (even though the connection window should be handled
                # in addition to the stream, and even though the ended
                # event isn't processed yet) --jake 2020-11
                # https://python-hyper.org/projects/hyper-h2/en/stable/advanced-usage.html#auto-flow-control
                # also supposedly from the section above it, you're
                # supposed to use data.flow_control_length, instead of
                # len(data), but that seems not available from here.  But
                # if we do nothing, this fails after 64k bytes received.
                # (as it stands, it instead fails after 2b bytes)
                if len(event.data) > 0:
                    self.conn.increment_flow_control_window(len(event.data))
                #self.conn.acknowledge_received_data(event.stream_id,
                #        len(event.data))
                self.handleData(event.stream_id, event.data,
                        event.stream_ended)
            elif isinstance(event, StreamEnded):
                self.endStream(event.stream_id)
            elif isinstance(event, SettingsAcknowledged):
                self.settingsAcked(event)
            elif isinstance(event, StreamReset):
                reactor.stop()
                raise RuntimeError("Stream reset: %d" % event.error_code)
            elif isinstance(event, WindowUpdated):
                self.windowUpdated(event)

        data = self.conn.data_to_send()
        if data:
            self.transport.write(data)

    def settingsAcked(self, event):
        """
        Called when the remote party ACKs our settings. We send a SETTINGS
        frame as part of the preamble, so if we want to be very polite we can
        wait until the ACK for that frame comes before we start sending our
        request.
        """
        self.logger.info(f'settings acked: {event}')
        self.settings_acked = True
        if self.buffered_requests:
            buffered_reqs = self.buffered_requests
            self.buffered_requests = []
            for req in buffered_reqs:
                self.sendRequest(req)

        if not self.watcher_id:
            self.getNewWatcherId()

    def handleResponse(self, stream_id, response_headers):
        """
        Handle the response by printing the response headers.
        """
        status = next((val.decode('utf-8') for name,val in response_headers if name == b':status'), None)
        self.logger.info(f'got response id={stream_id} ({status}: {len(response_headers)} hdrs):')
        for name, value in response_headers:
            self.logger.debug("   %s: %s" % (name.decode('utf-8'), value.decode('utf-8')))
        if stream_id not in self.request_table:
            self.logger.warning(f'response for {stream_id} has no request in request table')
        else:
            req = self.request_table[stream_id]
            req.response_headers = response_headers

    def handleData(self, stream_id, data, stream_ended):
        """
        We handle data that's received by just printing it.
        """
        dat = data.decode('utf-8')
        self.logger.debug(f'handleData(id={stream_id}, len={len(data)}) got:\n{dat}')

        if stream_id not in self.request_table:
            self.logger.warning(f'data for {stream_id} has no request in request table')
        else:
            self.logger.info(f'data for {stream_id}: buffered {len(data)} bytes')
            req = self.request_table[stream_id]
            if req.response_data:
                req.response_data += data
            else:
                req.response_data = data

        # stream_ended seems to be both passed with the got data and
        # also invoked as a separate event, so don't fire it twice.
        #if stream_ended:
        #    self.endStream(stream_ended.stream_id)

    def windowUpdated(self, event):
        """
        We call this when the flow control window for the connection or the
        stream has been widened. If there's a flow control deferred present
        (that is, if we're blocked behind the flow control), we fire it.
        Otherwise, we do nothing.
        """
        if self.flow_control_deferred is None:
            return

        # Make sure we remove the flow control deferred to avoid firing it
        # more than once.
        flow_control_deferred = self.flow_control_deferred
        self.flow_control_deferred = None
        flow_control_deferred.callback(None)

    def nextStreamId(self):
        return self.conn.get_next_available_stream_id()

    def sendRequest(self, req):
        """
        Send the POST request.

        A POST request is made up of one headers frame, and then 0+ data
        frames. This method begins by sending the headers, and then starts a
        series of calls to send data.
        """
        if not self.settings_acked or not self.conn or not self.transport:
            self.logger.debug('dropping request (connection down or settings not yet acked)')
            return

        path = f'{self.root}{req.path}'

        # Now we can build a header block.
        request_headers = [
            (':method', req.method),
            (':authority', self.authority),
            (':scheme', 'https'),
            (':path', path),
            ('user-agent', 'hyper-h2/1.0.0'),
        ]

        if req.data:
            request_headers.append(('content-length', str(len(req.data))))

        # We want to guess a content-type and content-encoding?
        #content_type, content_encoding = mimetypes.guess_type(path)

        if req.content_type is not None:
            request_headers.append(('content-type', req.content_type))

            if req.content_encoding is not None:
                request_headers.append(('content-encoding', req.content_encoding))

        stream_id = self.nextStreamId()
        self.logger.info(f'req id={stream_id}: {req.method} {path}')
        req.built_headers = request_headers
        self.request_table[stream_id] = req
        self.conn.send_headers(stream_id, request_headers)

        if req.data:
            self.sendData(stream_id, req.data)
        else:
            self.logger.info(f'end stream {stream_id} (req without data)')
            self.conn.end_stream(stream_id=stream_id)
            self.transport.write(self.conn.data_to_send())

    def sendData(self, stream_id, data):
        """
        Send some data on the connection.
        """
        # Firstly, check what the flow control window is for stream 1.
        window_size = self.conn.local_flow_control_window(stream_id=stream_id)

        # Next, check what the maximum frame size is.
        max_frame_size = self.conn.max_outbound_frame_size

        # We will send no more than the window size or the remaining file size
        # of data in this call, whichever is smaller.
        bytes_to_send = min(window_size, len(data))
        self.logger.info(f'{bytes_to_send}/{len(data)} bytes for {window_size} window')

        strdat = data[:bytes_to_send].decode('utf-8')
        self.logger.info(f'sending data id={stream_id} ({bytes_to_send} of {len(data)} bytes):\n{strdat}')

        # We now need to send a number of data frames.
        offset = 0
        while bytes_to_send > 0:
            chunk_size = min(bytes_to_send, max_frame_size)
            data_chunk = data[offset:offset+chunk_size]
            self.conn.send_data(stream_id=stream_id, data=data_chunk)

            offset += chunk_size
            bytes_to_send -= chunk_size

        # We've prepared a whole chunk of data to send. If the data is fully
        # sent, we also want to end the stream: we're done here.
        if offset >= len(data):
            self.logger.info(f'end stream {stream_id} (req finished data)')
            self.conn.end_stream(stream_id=stream_id)
        else:
            # We've still got data left to send but the window is closed. Save
            # a Deferred that will call us when the window gets opened.
            def getContinuation(cont_data):
                def continueData():
                    self.logger.info(f'deferred continue of {bytes_to_send} bytes for stream {stream_id}')
                    self.sendData(stream_id, cont_data)
                return continueData
            cont_data = data[-bytes_to_send:]
            self.flow_control_deferred = defer.Deferred()
            self.flow_control_deferred.addCallback(getContinuation(cont_data))

        self.transport.write(self.conn.data_to_send())

    def getNewWatcherId(self):
        req = RequestBuf(
            path='/operations/ietf-mnat:get-new-watcher-id',
            method='POST',
            callback=self.gotWatcherId)
        self.sendRequest(req)

    def gotWatcherId(self, req):
        rcv_text = req.response_data.decode('utf-8').strip()
        resp_j = json.loads(rcv_text)
        self.watcher_id = resp_j['watcher-id']
        self.refresh_period = resp_j.get('refresh-period', 10)
        self.logger.info(f'got Watcher Id: {self.watcher_id} (refresh={self.refresh_period})')
        if self.refresh_period < 1:
            self.refresh_period = 1

        if self.refreshing_task:
            self.refreshing_task.stop()

        self.refreshing_task = task.LoopingCall(self.sendRefreshWatcherId)
        self.refreshing_task.start(self.refresh_period, now=False)
        self.last_refresh_time = datetime.now()

        self.setupWatcher()

        if self.polling_task:
            self.polling_task.stop()
        self.polling_task = task.LoopingCall(self.sendCheckAssigned)
        self.polling_task.start(10, now=True)

        '''
        TBD: it would be wonderful to be getting push notifications from the server with subscribed-notifications --jake 2020-11
        establish_input = {
            'ietf-subscribed-notifications:input': {
                'stream-filter-name': 'ietf-mnat:assignment-updates'
            }
        }
        data=json.dumps(establish_input).encode('utf-8')
        req = RequestBuf(
            path='/operations/ietf-subscribed-notifications:establish-subscription',
            method='POST',
            data=data)
        protocol.sendRequest(req)
        '''

    def sendCheckAssigned(self):
        if self.restarting_deferred or self.shutting_down:
            self.logger.info(f'(skipping assigned-channels pull while down)')
            return
        req = RequestBuf(
            path=f'/data/ietf-mnat:assigned-channels/watcher={self.watcher_id}',
            method='GET',
            callback=self.gotAssigned)
        self.sendRequest(req)

    def gotAssigned(self, req):
        self.last_assign_check_time = datetime.now()
        rcv_text = req.response_data.decode('utf-8').strip()
        try:
            resp_j = json.loads(rcv_text)['ietf-mnat:watcher'][0]
            watcher_id = resp_j['id'] ; assert(watcher_id == self.watcher_id)
            mapped_sgs = resp_j['mapped-sg']
        except Exception as e:
            self.logger.warning(f'failed check of watcher id {self.watcher_id}: {e}, getting new id')
            self.getNewWatcherId()
            return

        # check changes since last time, launch and kill translators
        mappings = []
        for mapped_sg in mapped_sgs:
            assignment_id = mapped_sg['id']
            state = mapped_sg['state']
            global_sub = mapped_sg['global-subscription']
            source = global_sub['source']
            group = global_sub['group']
            local_map = mapped_sg.get('local-mapping')
            mappings.append(Mapping(source, group, LocalAssignment(state, local_map)))
        self.logger.debug(f'gotAssigned: {mapped_sgs}')
        self.polledLatestMappings(mappings)

    def polledLatestMappings(self, mappings):
        cur_translates = set(self.current_mappings.keys())
        mapping_dict = dict([((m.source, m.group), m) for m in mappings])
        updated_translates = set(mapping_dict.keys())
        added_sgs = updated_translates - cur_translates
        removed_sgs = cur_translates - updated_translates
        kept_sgs = cur_translates.intersection(updated_translates)

        for sg in removed_sgs:
            tm = self.current_mappings.get(sg)
            if not tm:
                self.logger.error(f'internal error: removing not-present TranslateManager for {sg[0]}->{sg[1]}')
                continue
            tm.stop()
            del(self.current_mappings[sg])

        for sg in kept_sgs:
            tm = self.current_mappings.get(sg)
            m = mapping_dict.get(sg)
            if not tm:
                self.logger.error(f'internal error: updating not-present translateManager for {sg[0]}->{sg[1]}')
                added_sgs.add(sg)
                continue
            if not m:
                self.logger.error(f'internal error: updating TranslateManager without mapping for {sg[0]}->{sg[1]}')
                continue
            tm.check_for_update(m)

        for sg in added_sgs:
            m = mapping_dict[sg]
            self.current_mappings[sg] = TranslateManager(m, self.direction, self.in_interface, self.out_interface, self.logger, self.no_join)
            self.current_mappings[sg].start()

    def setTranslations(self, direction, in_interface, out_interface):
        if direction != TRANSLATE_TO_LOCAL and direction != TRANSLATE_TO_GLOBAL:
            self.logger.error(f'unknown direction: {direction}, should be TRANSLATE_TO_LOCAL={TRANSLATE_TO_LOCAL} or TRANSLATE_TO_GLOBAL={TRANSLATE_TO_GLOBAL}')
            return

        self.direction = direction
        self.in_interface = in_interface
        self.out_interface = out_interface

    def sendRefreshWatcherId(self):
        if self.restarting_deferred or self.shutting_down:
            self.logger.info(f'(skipping refresh-watcher-id while down)')
            return
        refresh_input = {
            'ietf-mnat:input': {
                'ietf-mnat:watcher-id': self.watcher_id
            }
        }
        data=json.dumps(refresh_input).encode('utf-8')
        req = RequestBuf(
            path=f'/operations/ietf-mnat:refresh-watcher-id',
            method='POST',
            data=data,
            callback=self.refreshDone)
        self.sendRequest(req)

    def refreshDone(self, req):
        self.last_refresh_time = datetime.now()

    def runLoop(self):
        reactor.run()

    def setupWatcher(self):
        # override in derived class for ingress/egress
        self.logger.warning(f'fired un-implemented setupWatcher in H2Protocol client base class')
        pass

