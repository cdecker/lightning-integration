from lightning import LightningRpc
from utils import TailableProc

import logging
import os
import time

LIGHTNINGD_CONFIG = {
    "bitcoind-poll": "1s",
    "log-level": "debug",
    "deadline-blocks": 5,
    "min-htlc-expiry": 6,
    "locktime-blocks": 6,
}


class LightningD(TailableProc):
    def __init__(self, lightning_dir, bitcoin_dir, port=9735):
        TailableProc.__init__(self, lightning_dir)
        self.lightning_dir = lightning_dir
        self.port = port
        self.cmd_line = [
            'bin/lightningd',
            '--bitcoin-datadir={}'.format(bitcoin_dir),
            '--lightning-dir={}'.format(lightning_dir),
            '--port={}'.format(port),
            '--disable-irc',
            '--network=regtest',
            '--dev-broadcast-interval=1000',
        ]
        self.cmd_line += [
            "--{}={}".format(k, v) for k, v in LIGHTNINGD_CONFIG.items()
        ]
        self.prefix = 'lightningd'

        if not os.path.exists(lightning_dir):
            os.makedirs(lightning_dir)

    def start(self):
        TailableProc.start(self)
        self.wait_for_log("Creating IPv6 listener on port")
        logging.info("LightningD started")

    def stop(self):
        TailableProc.stop(self)
        logging.info("LightningD stopped")

class LightningNode(object):
    def __init__(self, lightning_dir, lightning_port, btc, executor=None,
                 node_id=0):
        self.bitcoin = btc
        self.executor = executor
        self.daemon = LightningD(lightning_dir, btc.bitcoin_dir,
                                 port=lightning_port)
        socket_path = os.path.join(lightning_dir, "lightning-rpc").format(
            node_id)
        self.rpc = LightningRpc(socket_path, self.executor)

    def peers(self):
        return [p['peerid'] for p in self.rpc.getpeers()['peers']]

    def id(self):
        return self.rpc.getinfo()['id']

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
        tx = bitcoind.rpc.getrawtransaction(txid)
        self.rpc.addfunds(tx)

    def ping(self):
        """ Simple liveness test to see if the node is up and running

        Returns true if the node is reachable via RPC, false otherwise.
        """
        try:
            self.rpc.help()
            return True
        except:
            return False

    def check_channel(self, remote):
        """ Make sure that we have an active channel with remote
        """
        for p in self.rpc.getpeers()['peers']:
            if remote.id() == p['peerid']:
                return p['state'] == 'CHANNELD_NORMAL'

        return False

    def getchannels(self):
        result = []
        for c in self.rpc.getchannels()['channels']:
            result.append((c['source'], c['destination']))
        print("Numchannels", len(result))
        return set(result)

    def getnodes(self):
        return set([n['nodeid'] for n in self.rpc.getnodes()['nodes']])
