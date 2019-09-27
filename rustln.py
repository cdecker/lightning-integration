from utils import TailableProc, BITCOIND_CONFIG

import logging
import os
import time

class RustLightningBitcoinrpc(TailableProc):

    def __init__(self, lightning_dir, bitcoind, port):
        TailableProc.__init__(self, lightning_dir)
        self.lightning_dir = lightning_dir
        self.bitcoind = bitcoind
        self.port = port
        self.cmd_line = [
            'bin/rust-lightning-bitcoinrpc',
            '-chain=regtest',
            '-p2pport={}'.format(port),
            '-datadir={}'.format(lightning_dir),
            '-rpcauth={}:{}'.format(BITCOIND_CONFIG['rpcuser'], BITCOIND_CONFIG['rpcpassword']),
            '-rpc_host=127.0.0.1:{}'.format(self.bitcoind.rpcport),
        ]
        self.prefix = 'rust-lightning-bitcoinrpc'

        if not os.path.exists(lightning_dir):
            os.makedirs(lightning_dir)

    def start(self):
        TailableProc.start(self)
        # self.wait_for_log("Started interactive shell! Commands:")
        time.sleep(5)
        logging.info("Success! Starting up...")

    def stop(self):
        TailableProc.stop(self)
        logging.info("RustLightningBitcoinrpc stopped")


class RustLightningBitcoinrpcNode(object):

    displayName = 'rust-lightning-bitcoinrpc'

    def __init__(self, lightning_dir, lightning_port, btc, executor=None, node_id=0):
        self.bitcoin = btc
        self.executor = executor
        self.daemon = RustLightningBitcoinrpc(lightning_dir, self.bitcoin, port=lightning_port)

        # TODO rust-lightning-bitcoinrpc doesn't havean rpc interface, it just takes commands directly on the command line when running

    def id(self):
        pass

    def ping(self):
        pass

    def peers(self):
        pass

    def check_channel(self, remote):
        pass

    def addfunds(self, bitcoind, satoshis):
        pass

    def openchannel(self, node_id, host, port, satoshis):
        pass

    def getchannels(self):
        pass

    def getnodes(self):
        pass

    def invoice(self, amount):
        pass

    def send(self, bolt11):
        pass

    def connect(self, host, port, node_id):
        pass

    def info(self):
        pass

    def block_sync(self, blockhash):
        print("Waiting for node to learn about", blockhash)
        self.daemon.wait_for_log('NTFN: New block: height=([0-9]+), sha={}'.format(blockhash))

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
        pass
