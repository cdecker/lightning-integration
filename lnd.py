from utils import TailableProc
import rpc_pb2_grpc as lnrpc_grpc
import rpc_pb2 as lnrpc


import grpc
import logging
import os
import time


# Needed for grpc to negotiate a valid cipher suite
os.environ["GRPC_SSL_CIPHER_SUITES"] = "ECDHE-ECDSA-AES256-GCM-SHA384"

class LndD(TailableProc):
    def __init__(self, lightning_dir, bitcoin_dir, port):
        TailableProc.__init__(self, lightning_dir)
        self.lightning_dir = lightning_dir
        self.bitcoin_dir = bitcoin_dir
        self.port = port
        self.rpc_port = str(10000 + port)
        self.prefix = 'lnd'

        self.cmd_line = [
            'bin/lnd',
            '--peerport={}'.format(self.port),
            '--rpcport={}'.format(self.rpc_port),
            '--bitcoin.active',
            '--datadir={}'.format(lightning_dir),
            '--debuglevel=debug',
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
        self.wait_for_log("gRPC proxy started at localhost:8080")
        self.wait_for_log("Done catching up block hashes")

        # FLAKE: Seems lnd isn't always ready to accept incoming rpc calls, so wait a bit longer
        time.sleep(5)

        logging.info("LND started (pid: {})".format(self.proc.pid))


class LndNode(object):

    def __init__(self, lightning_dir, lightning_port, btc, executor=None,
                 node_id=0):
        self.bitcoin = btc
        self.executor = executor
        self.daemon = LndD(lightning_dir, btc.bitcoin_dir, port=lightning_port)
        self.rpc = LndRpc(lightning_port+10000)

    def id(self):
        return self.info().identity_pubkey

    def info(self):
        return self.rpc.stub.GetInfo(lnrpc.GetInfoRequest())

    def ping(self):
        """ Simple liveness test to see if the node is up and running

        Returns true if the node is reachable via RPC, false otherwise.
        """
        try:
            self.rpc.stub.GetInfo(lnrpc.GetInfoRequest())
            return True
        except Exception as e:
            print(e)
            return False

    def peers(self):
        peers = self.rpc.stub.ListPeers(lnrpc.ListPeersRequest()).peers
        return [p.pub_key for p in peers]

    def check_channel(self, remote):
        """ Make sure that we have an active channel with remote
        """
        channels = self.rpc.stub.ListChannels(lnrpc.ListChannelsRequest()).channels
        channel_by_remote = {c.remote_pubkey: c for c in channels}
        if remote.id() not in channel_by_remote:
            return False

        channel = channel_by_remote[remote.id()]
        return channel.active

    def addfunds(self, bitcoind, satoshis):
        req = lnrpc.NewAddressRequest(type=1)
        addr = self.rpc.stub.NewAddress(req).address
        txid = bitcoind.rpc.sendtoaddress(addr, float(satoshis) / 10**8)
        self.daemon.wait_for_log("Inserting unconfirmed transaction")
        bitcoind.rpc.generate(1)
        self.daemon.wait_for_log("Marking unconfirmed transaction")

        # The above still doesn't mean the wallet balance is updated,
        # so let it settle a bit
        time.sleep(1)
        assert(self.rpc.stub.WalletBalance(lnrpc.WalletBalanceRequest()).balance == satoshis)

    def openchannel(self, node_id, host, port, satoshis):
        peers = self.rpc.stub.ListPeers (lnrpc.ListPeersRequest()).peers
        peers_by_pubkey = {p.pub_key: p for p in peers}
        if not node_id in peers_by_pubkey:
            raise ValueError("Could not find peer {} in peers {}".format(node_id), peers)
        peer = peers_by_pubkey[node_id]
        self.rpc.stub.OpenChannel(lnrpc.OpenChannelRequest(
            target_peer_id=peer.peer_id,
            local_funding_amount=satoshis,
            push_sat=0
        ))

        # Somehow broadcasting a tx is slow from time to time
        time.sleep(5)


class LndRpc(object):
    def __init__(self, rpc_port):
        self.port = rpc_port
        cred = grpc.ssl_channel_credentials(open('tls.cert').read())
        channel = grpc.secure_channel('localhost:{}'.format(rpc_port), cred)
        self.stub = lnrpc_grpc.LightningStub(channel)

    def connect(self, host, port, node_id):
        addr = lnrpc.LightningAddress(pubkey=node_id, host="{}:{}".format(host, port))
        req = lnrpc.ConnectPeerRequest(addr=addr, perm=True)
        logging.debug(self.stub.ConnectPeer(req))
