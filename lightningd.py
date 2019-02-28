from lightning import LightningRpc
from utils import TailableProc

import json
import logging
import os
import time


LIGHTNINGD_CONFIG = {
    "log-level": "debug",
    "watchtime-blocks": 6,
    "network": "regtest",
}


class LightningD(TailableProc):

    def __init__(self, lightning_dir, bitcoind, port=9735):
        TailableProc.__init__(self, lightning_dir)
        self.lightning_dir = lightning_dir
        self.port = port
        self.bitcoind = bitcoind
        self.cmd_line = [
            'bin/lightningd',
            '--bitcoin-datadir={}'.format(self.bitcoind.bitcoin_dir),
            '--lightning-dir={}'.format(lightning_dir),
            '--addr=127.0.0.1:{}'.format(port),
            '--dev-broadcast-interval=500',
            '--dev-bitcoind-poll=1',
            '--plugin-dir=src/lightning/plugins',

            # The following are temporary workarounds
            '--bitcoin-rpcport={}'.format(self.bitcoind.rpcport),
            '--bitcoin-rpcuser=rpcuser',
            '--bitcoin-rpcpassword=rpcpass',
        ]
        self.cmd_line += [
            "--{}={}".format(k, v) for k, v in LIGHTNINGD_CONFIG.items()
        ]
        self.prefix = 'lightningd'

        if not os.path.exists(lightning_dir):
            os.makedirs(lightning_dir)

    def start(self):
        TailableProc.start(self)
        self.wait_for_log("Server started with public key")
        time.sleep(5)
        logging.info("LightningD started")

    def stop(self):
        TailableProc.stop(self)
        logging.info("LightningD stopped")


class LightningNode(object):

    displayName = 'lightning'

    def __init__(self, lightning_dir, lightning_port, btc, executor=None, node_id=0):
        self.bitcoin = btc
        self.executor = executor
        self.daemon = LightningD(lightning_dir, self.bitcoin,
                                 port=lightning_port)
        socket_path = os.path.join(lightning_dir, "lightning-rpc").format(
            node_id)
        self.invoice_count = 0
        self.logger = logging.getLogger('lightning-node({})'.format(lightning_port))

        self.rpc = LightningRpc(socket_path, self.executor)

        orig_call = self.rpc._call

        def rpc_call(method, args):
            self.logger.debug("Calling {} with arguments {}".format(method, json.dumps(args, indent=4, sort_keys=True)))
            r = orig_call(method, args)
            self.logger.debug("Call returned {}".format(json.dumps(r, indent=4, sort_keys=True)))
            return r

        self.rpc._call = rpc_call
        self.myid = None

    def peers(self):
        return [p['id'] for p in self.rpc.listpeers()['peers']]

    def getinfo(self):
        if not self.info:
            self.info = self.rpc.getinfo()
        return self.info

    def id(self):
        if not self.myid:
            self.myid = self.rpc.getinfo()['id']
        return self.myid

    def openchannel(self, node_id, host, port, satoshis):
        # Make sure we have a connection already
        if node_id not in self.peers():
            raise ValueError("Must connect to node before opening a channel")
        return self.rpc.fundchannel(node_id, satoshis)

    def getaddress(self):
        return self.rpc.newaddr()['address']

    def addfunds(self, bitcoind, satoshis):
        addr = self.getaddress()
        txid = bitcoind.rpc.sendtoaddress(addr, float(satoshis) / 10**8)
        bitcoind.rpc.getrawtransaction(txid)
        while len(self.rpc.listfunds()['outputs']) == 0:
            time.sleep(1)
            bitcoind.rpc.generate(1)

    def ping(self):
        """ Simple liveness test to see if the node is up and running

        Returns true if the node is reachable via RPC, false otherwise.
        """
        try:
            self.rpc.help()
            return True
        except:
            return False

    def check_channel(self, remote, require_both=False):
        """Make sure that we have an active channel with remote

        `require_both` must be False unless the other side supports
        sending a `channel_announcement` and `channel_update` on
        `funding_locked`. This doesn't work for eclair for example.

        """
        remote_id = remote.id()
        self_id = self.id()
        peer = None
        for p in self.rpc.listpeers()['peers']:
            if remote.id() == p['id']:
                peer = p
                break
        if not peer:
            self.logger.debug('Peer {} not found in listpeers'.format(remote))
            return False

        if len(peer['channels']) < 1:
            self.logger.debug('Peer {} has no channel open with us'.format(remote))
            return False

        state = p['channels'][0]['state']
        self.logger.debug("Channel {} -> {} state: {}".format(self_id, remote_id, state))

        if state != 'CHANNELD_NORMAL' or not p['connected']:
            self.logger.debug('Channel with peer {} is not in state normal ({}) or peer is not connected ({})'.format(
                remote_id, state, p['connected']))
            return False

        # Make sure that gossipd sees a local channel_update for routing
        scid = p['channels'][0]['short_channel_id']

        channels = self.rpc.listchannels(scid)['channels']

        if not require_both and len(channels) >= 1:
            return channels[0]['active']

        if len(channels) != 2:
            self.logger.debug('Waiting for both channel directions to be announced: 2 != {}'.format(len(channels)))
            return False

        return channels[0]['active'] and channels[1]['active']

    def getchannels(self):
        result = []
        for c in self.rpc.listchannels()['channels']:
            result.append((c['source'], c['destination']))
        return set(result)

    def getnodes(self):
        return set([n['nodeid'] for n in self.rpc.listnodes()['nodes']])

    def invoice(self, amount):
        invoice = self.rpc.invoice(amount, "invoice%d" % (self.invoice_count), "description")
        self.invoice_count += 1
        return invoice['bolt11']

    def send(self, req):
        result = self.rpc.pay(req)
        return result['payment_preimage']

    def connect(self, host, port, node_id):
        return self.rpc.connect(node_id, host, port)

    def info(self):
        r = self.rpc.getinfo()
        return {
            'id': r['id'],
            'blockheight': r['blockheight'],
        }

    def block_sync(self, blockhash):
        time.sleep(1)

    def restart(self):
        self.daemon.stop()
        time.sleep(5)
        self.daemon.start()
        time.sleep(1)

    def stop(self):
        self.daemon.stop()

    def start(self):
        self.daemon.start()

    def check_route(self, node_id, amount):
        try:
            r = self.rpc.getroute(node_id, amount, 1.0)
        except ValueError as e:
            if (str(e).find("Could not find a route") > 0):
                return False
            raise
        return True
