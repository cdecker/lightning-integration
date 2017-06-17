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
            'lightningd/lightningd',
            '--bitcoin-datadir={}'.format(bitcoin_dir),
            '--lightning-dir={}'.format(lightning_dir),
            '--port={}'.format(port),
            '--disable-irc',
            '--bitcoind-regtest',
            '--dev-broadcast-interval=1000',
        ]
        self.cmd_line += ["--{}={}".format(k, v) for k, v in LIGHTNINGD_CONFIG.items()]
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
    def __init__(self, lightning_dir, lightning_port, btc, executor=None, node_id=0):
        self.bitcoin = btc
        self.executor = executor
        self.daemon = LightningD(lightning_dir, btc.bitcoin_dir, port=lightning_port)
        socket_path = os.path.join(lightning_dir, "lightning-rpc").format(node_id)
        self.rpc = LightningRpc(socket_path, self.executor)

    def connect(self, remote_node, capacity, async=False):
        # Collect necessary information
        addr = self.rpc.newaddr()['address']
        txid = self.bitcoin.rpc.sendtoaddress(addr, capacity)
        tx = self.bitcoin.rpc.gettransaction(txid)

        def call_connect():
            try:
                self.rpc.connect('127.0.0.1', remote_node.daemon.port, tx['hex'], async=False)
            except:
                pass
        t = threading.Thread(target=call_connect)
        t.daemon = True
        t.start()
        
        def wait_connected():
            # TODO(cdecker) Monitor the mempool to see if its time to generate yet.
            time.sleep(5)
        
            # The sleep should have given bitcoind time to add the tx to its mempool
            self.bitcoin.rpc.generate(1)

            # Now wait for confirmation
            self.daemon.wait_for_log("-> CHANNELD_NORMAL|STATE_NORMAL")
            remote_node.daemon.wait_for_log("-> CHANNELD_NORMAL|STATE_NORMAL")

        if async:
            return self.executor.submit(wait_connected)
        else:
            return wait_connected()

    def openchannel(self, remote_node, capacity):
        addr = self.rpc.newaddr()['address']
        txid = self.bitcoin.rpc.sendtoaddress(addr, capacity / 10**6)
        tx = self.bitcoin.rpc.getrawtransaction(txid)
        self.rpc.addfunds(tx)
        self.rpc.fundchannel(remote_node.info['id'], capacity)
        self.daemon.wait_for_log('sendrawtx exit 0, gave')
        time.sleep(1)
        self.bitcoin.rpc.generate(6)
        self.daemon.wait_for_log('-> CHANNELD_NORMAL|STATE_NORMAL')
