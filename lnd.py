from utils import TailableProc
import rpc_pb2_grpc as lnrpc_grpc
import rpc_pb2 as lnrpc


import grpc
import logging
import os
import time


class LndD(TailableProc):
    def __init__(self, lightning_dir, bitcoin_dir, port):
        TailableProc.__init__(self, lightning_dir)
        self.lightning_dir = lightning_dir
        self.bitcoin_dir = bitcoin_dir
        self.port = port
        self.rpc_port = str(10000 + port)
        self.prefix = 'lnd'

        self.cmd_line = [
            'lnd/lnd',
            '--peerport={}'.format(self.port),
            '--rpcport={}'.format(self.rpc_port),
            '--bitcoin.active',
            '--datadir={}'.format(lightning_dir),
            '--debuglevel=trace',
            '--bitcoin.rpcuser=rpcuser',
            '--bitcoin.rpcpass=rpcpass',
            '--configfile={}'.format(os.path.join(lightning_dir, 'lnd.conf')),
            '--bitcoin.regtest',
        ]

        if not os.path.exists(lightning_dir):
            os.makedirs(lightning_dir)
        with open(os.path.join(lightning_dir, "lnd.conf"), "w") as f:
            f.write("""[Application Options]\n""")

    def start(self):
        TailableProc.start(self)
        self.wait_for_log("Opened wallet")
        time.sleep(30)
        logging.info("LND started (pid: {})".format(self.proc.pid))


class LndNode(object):

    def __init__(self, lightning_dir, lightning_port, btc, executor=None,
                 node_id=0):
        self.bitcoin = btc
        self.executor = executor
        self.daemon = LndD(lightning_dir, btc.bitcoin_dir, port=lightning_port)
        self.rpc = LndRpc(lightning_port)

    @property
    def id(self):
        while True:
            try:
                print(self.rpc.stub.GetInfo(lnrpc.GetInfoRequest()))
            except:
                time.sleep(1)
                pass

class LndRpc(object):
    def __init__(self, rpc_port):
        print('localhost:{}'.format(rpc_port))
        channel = grpc.insecure_channel('localhost:{}'.format(rpc_port))
        self.stub = lnrpc_grpc.LightningStub(channel)

    def connect(self, host, port, node_id):
        logging.debug(self.stub.GetInfo(lnrpc.GetInfoRequest()))


