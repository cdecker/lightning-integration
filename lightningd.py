from lightning import LightningRpc
from utils import TailableProc

import logging
import os
import time

LIGHTNINGD_CONFIG = {
    "bitcoind-poll": "1s",
    "log-level": "debug",
    "locktime-blocks": 6,
    "network": "regtest",
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
            '--network=regtest',
            '--dev-broadcast-interval=1000',
            '--override-fee-rates={0}/{0}/{0}'.format(12*1000), # Fix fee in sat/kw
        ]
        self.cmd_line += [
            "--{}={}".format(k, v) for k, v in LIGHTNINGD_CONFIG.items()
        ]
        self.prefix = 'lightningd'

        if not os.path.exists(lightning_dir):
            os.makedirs(lightning_dir)

    def start(self):
        TailableProc.start(self)
        self.wait_for_log("Hello world")
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
        self.invoice_count = 0
        self.logger = logging.getLogger('lightning-node({})'.format(lightning_port))

        self.rpc = LightningRpc(socket_path, self.executor)

        orig_call = self.rpc._call
        def rpc_call(method, args):
            self.logger.debug("Calling {} with arguments {}".format(method, args))
            r = orig_call(method, args)
            self.logger.debug("Call returned {}".format(r))
            return r

        self.rpc._call = rpc_call
        self.myid = None

    def peers(self):
        return [p['peerid'] for p in self.rpc.getpeers()['peers']]

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
        remote_id = remote.id()
        self_id = self.id()
        for p in self.rpc.getpeers()['peers']:
            if remote.id() == p['peerid']:
                self.logger.debug("Channel {} -> {} state: {}".format(self_id, remote_id, p['state']))
                return p['state'] == 'CHANNELD_NORMAL' and p['connected']

        self.logger.warning("Channel {} -> {} not found".format(self_id, remote_id))
        return False

    def getchannels(self):
        result = []
        for c in self.rpc.getchannels()['channels']:
            result.append((c['source'], c['destination']))
        return set(result)

    def getnodes(self):
        return set([n['nodeid'] for n in self.rpc.getnodes()['nodes']])

    def invoice(self, amount):
        invoice = self.rpc.invoice(amount, "invoice%d" % (self.invoice_count), "description")
        self.invoice_count += 1
        print(invoice)
        return invoice['bolt11']

    def send(self, req):
        result = self.rpc.pay(req)
        return result['preimage']

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

LightningNode.displayName = 'lightning'
