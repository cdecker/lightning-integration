from utils import TailableProc

import json
import logging
import os
import time
import subprocess
import re
import socket


class PtarmD(TailableProc):

    def __init__(self, lightning_dir, bitcoin_dir, port=9735):
        TailableProc.__init__(self, lightning_dir, 'ptarmd({}).format(port)')
        self.lightning_dir = lightning_dir
        self.port = port
        self.cmd_line = [
            'bin/ptarmd',
            '-d', lightning_dir,
            '-p', str(port),
            '-a' '127.0.0.1',
            '-c', '{}/bitcoin.conf'.format(bitcoin_dir),
            '--rpcport', str(port+1234),
        ]
        self.prefix = 'ptarmd'

        if not os.path.exists(lightning_dir):
            os.makedirs(lightning_dir)

    def start(self):
        TailableProc.start(self)
        self.wait_for_log("start bitcoin testnet/regtest", offset=100)
        time.sleep(3)
        logging.info("PtarmD started")

    def stop(self):
        TailableProc.stop(self)
        logging.info("PtarmD stopped")


class PtarmNode(object):

    displayName = 'ptarmigan'

    def __init__(self, lightning_dir, lightning_port, btc, executor=None,
                 node_id=0):
        self.bitcoin = btc
        self.executor = executor
        self.daemon = PtarmD(
            lightning_dir,
            btc.bitcoin_dir,
            port=lightning_port
        )
        self.rpc = PtarmRpc('127.0.0.1', lightning_port+1234)
        self.myid = None
        self.node_id = node_id
        self.bitcoind = None
        self.txid = None
        self.vout = None
        self.peer_host = None
        self.peer_port = None
        self.peer_node_id = None
        self.push_sat = 0
        self.feerate_per_kw = 12*1000

    def peers(self):
        r = self.rpc.getinfo()
        return [p['node_id'] for p in r['peers']]

    def getinfo(self):
        raise NotImplementedError()

    def id(self):
        if not self.myid:
            self.myid = self.rpc.getinfo()['node_id']
        return self.myid

    def openchannel(self, node_id, host, port, satoshis):
        # Make sure we have a connection already
        if node_id not in self.peers():
            raise ValueError("Must connect to node before opening a channel")
        return self.rpc.fundchannel(
            node_id,
            self.peer_host,
            self.peer_port,
            self.txid,
            self.vout,
            satoshis,
            self.push_sat,
            self.feerate_per_kw
        )

    def getaddress(self):
        raise NotImplementedError()

    def addfunds(self, bitcoind, satoshis):
        # ptarmd uses bitcoind's wallet.
        self.bitcoind = bitcoind
        addr = bitcoind.rpc.getnewaddress('', 'p2sh-segwit')
        self.txid = bitcoind.rpc.sendtoaddress(addr, float(satoshis) / 10**8)
        listunspent = bitcoind.rpc.listunspent(0, 1, [addr])
        self.vout = listunspent[0]['vout']

        # Lock vout to not be used for other transactions.
        assert bitcoind.rpc.lockunspent(
            False,
            [{"txid": self.txid, "vout":  self.vout}]
        )

        time.sleep(1)
        bitcoind.rpc.generate(1)

    def ping(self):
        """ Simple liveness test to see if the node is up and running

        Returns true if the node is reachable via RPC, false otherwise.
        """
        try:
            self.rpc.getinfo()
            return True
        except Exception:
            return False

    def check_channel(self, remote):
        """ Make sure that we have an active channel with remote
        """
        remote_id = remote.id()
        self_id = self.id()
        for p in self.rpc.getinfo()['peers']:
            if 'node_id' not in p:
                continue
            if remote.id() == p['node_id']:
                state = p['status']
                self.logger.debug("Channel {} -> {} state: {}".format(
                    self_id,
                    remote_id, state
                ))
                return state == 'established'

        self.logger.warning("Channel {} -> {} not found".format(
            self_id,
            remote_id
        ))
        return False

    def getchannels(self):
        proc = subprocess.run(
            ['{}/bin/showdb'.format(os.getcwd()), '-c'],
            stdout=subprocess.PIPE,
            cwd=self.daemon.lightning_dir
        )
        decoder = json.JSONDecoder()
        objs, _ = decoder.raw_decode(proc.stdout.decode("UTF-8"))
        result = []
        if 'channel_announcement_list' in objs:
            for c in objs['channel_announcement_list']:
                if c['type'] != 'channel_announcement':
                    continue
                result.append((c['node1'], c['node2']))
                result.append((c['node2'], c['node1']))
        return set(result)

    def getnodes(self):
        """ Get nodes on channels
        """
        nodes = set()

        # Get a list of node ids from `node_announcement`s. but it
        # always includes my node id even if my node has no relevant
        # channels.
        proc = subprocess.run(
            ['{}/bin/showdb'.format(os.getcwd()), '-n'],
            stdout=subprocess.PIPE,
            cwd=self.daemon.lightning_dir
        )
        objs, _ = json.JSONDecoder().raw_decode(proc.stdout.decode("UTF-8"))
        if 'node_announcement_list' not in objs:
            return set()
        nodes = set([n['node'] for n in objs['node_announcement_list']])

        # Get a list of `channel_announcement`s,
        # and discard my node id from `nodes` if it has no relevant channels.
        proc = subprocess.run(
            ['{}/bin/showdb'.format(os.getcwd()), '-c'],
            stdout=subprocess.PIPE,
            cwd=self.daemon.lightning_dir
        )
        objs, _ = json.JSONDecoder().raw_decode(proc.stdout.decode("UTF-8"))
        if 'channel_announcement_list' not in objs:
            return set()
        for c in objs['channel_announcement_list']:
            if c['type'] != 'channel_announcement':
                continue
            if c['node1'] == self.id():
                # found
                break
            if c['node2'] == self.id():
                # found
                break
        else:
            # not found
            nodes.discard(self.id())

        return nodes

    def invoice(self, amount):
        r = self.rpc.invoice(amount)
        return r['bolt11']

    def send(self, req):
        if self.rpc.pay(req) != 'start payment':
            return ''
        line = self.daemon.wait_for_log("p_payment_preimage:", offset=100)
        pp = re.search('[0-9a-f]{64}', line)
        if pp:
            return pp.group()
        return ''

    def connect(self, host, port, node_id):
        self.peer_host = host
        self.peer_port = port
        self.peer_node_id = node_id
        return self.rpc.connect(node_id, host, port)

    def info(self):
        r = self.rpc.getinfo()
        return {
            'id': r['node_id'],
            'blockheight': r['block_count'],
        }

    def block_sync(self, blockhash):
        time.sleep(1)

    def restart(self):
        self.daemon.stop()
        time.sleep(5)
        self.daemon.start()
        time.sleep(1)

    def check_route(self, node_id, amount):
        proc = subprocess.run([
            '{}/bin/routing'.format(os.getcwd()),
            '-s',
            self.id(),
            '-r',
            node_id,
            '-a',
            str(amount)
        ], stdout=subprocess.PIPE, cwd=self.daemon.lightning_dir)
        return proc.returncode == 0


class TcpSocketRpc(object):
    # The code of this class was copied a lot from `lightning.py`
    # - https://github.com/ElementsProject/lightning/blob/master/contrib/pylightning/lightning/lightning.py

    def __init__(self, host, port, executor=None, logger=logging):
        self.host = host
        self.port = port
        self.decoder = json.JSONDecoder()
        self.executor = executor
        self.logger = logger

    @staticmethod
    def _writeobj(sock, obj):
        s = json.dumps(obj)
        sock.sendall(bytearray(s, 'UTF-8'))

    def _readobj(self, sock):
        buff = b''
        while True:
            try:
                b = sock.recv(1024)
                buff += b
                if len(b) == 0:
                    return {'error': 'Connection to RPC server lost.'}
                # Convert late to UTF-8 so glyphs split across recvs do not
                # impact us
                objs, _ = self.decoder.raw_decode(buff.decode("UTF-8"))
                return objs
            except ValueError:
                # Probably didn't read enough
                pass

    def __getattr__(self, name):
        """Intercept any call that is not explicitly defined and call @call

        We might still want to define the actual methods in the subclasses for
        documentation purposes.
        """
        name = name.replace('_', '-')

        def wrapper(**kwargs):
            return self.call(name, payload=kwargs)
        return wrapper

    def call(self, method, payload=None):
        self.logger.debug("Calling %s with payload %r", method, payload)

        if payload is None:
            payload = {}
        # Filter out arguments that are None
        payload = [v for v in payload if v is not None]

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((self.host, self.port))
        self._writeobj(sock, {
            "method": method,
            "params": payload,
            "id": 0
        })
        resp = self._readobj(sock)
        sock.close()

        self.logger.debug("Received response for %s call: %r", method, resp)
        if "error" in resp:
            raise ValueError(
                "RPC call failed: {}, method: {}, payload: {}".format(
                    resp["error"],
                    method,
                    payload
                ))
        elif "result" not in resp:
            raise ValueError("Malformed response, \"result\" missing.")
        return resp["result"]


class PtarmRpc(TcpSocketRpc):

    def invoice(self, msatoshi):
        payload = [msatoshi]
        return self.call("invoice", payload)

    def getinfo(self):
        return self.call("getinfo")

    def pay(self, bolt11, msatoshi=None, description=None, riskfactor=None):
        payload = [bolt11, 0]
        return self.call("routepay", payload)

    def connect(self, peer_id, host=None, port=None):
        payload = [peer_id, '127.0.0.1', port]
        return self.call("connect", payload)

    def fundchannel(self, peer_id, peer_host, peer_port, txid, txindex,
                    funding_sat, push_sat, feerate_per_kw):
        payload = [
            peer_id,
            peer_host,
            peer_port,
            txid,
            txindex,
            funding_sat,
            push_sat,
            feerate_per_kw
        ]
        return self.call("fund", payload)
