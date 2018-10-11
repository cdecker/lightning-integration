from binascii import hexlify
from lnaddr import lndecode
from utils import TailableProc, BITCOIND_CONFIG
import rpc_pb2_grpc as lnrpc_grpc
import rpc_pb2 as lnrpc


import grpc
import logging
import os
import time
import codecs


# Needed for grpc to negotiate a valid cipher suite
os.environ["GRPC_SSL_CIPHER_SUITES"] = "HIGH+ECDSA"


class LndD(TailableProc):

    def __init__(self, lightning_dir, bitcoin_dir, port):
        super().__init__(lightning_dir, 'lnd({})'.format(port))
        self.lightning_dir = lightning_dir
        self.bitcoin_dir = bitcoin_dir
        self.port = port
        self.rpc_port = str(10000 + port)
        self.rest_port = str(20000 + port)
        self.prefix = 'lnd'

        self.cmd_line = [
            'bin/lnd',
            '--bitcoin.active',
            '--bitcoin.regtest',
            '--lnddir={}'.format(lightning_dir),
            '--debuglevel=trace',
            '--rpclisten=127.0.0.1:{}'.format(self.rpc_port),
            '--restlisten=127.0.0.1:{}'.format(self.rest_port),
            '--listen=127.0.0.1:{}'.format(self.port),
            '--bitcoin.node=bitcoind',
            '--bitcoind.rpchost=127.0.0.1:{}'.format(BITCOIND_CONFIG.get('rpcport', 18332)),
            '--bitcoind.rpcuser=rpcuser',
            '--bitcoind.rpcpass=rpcpass',
            '--bitcoind.zmqpubrawblock=tcp://127.0.0.1:29001',
            '--bitcoind.zmqpubrawtx=tcp://127.0.0.1:29002',
            '--no-macaroons',
            '--nobootstrap',
        ]

    def make_channel(self):
        with open(self.lightning_dir + '/tls.cert', 'rb') as f:
            cred = grpc.ssl_channel_credentials(f.read())
        return grpc.secure_channel('localhost:{}'.format(self.rpc_port), cred)

    def start(self):
        super().start()
        self.wait_for_log('RPC server listening on')
        self.unlocker_stub = lnrpc_grpc.WalletUnlockerStub(self.make_channel())
        seed = self.unlocker_stub.GenSeed(lnrpc.GenSeedRequest())
        self.unlocker_stub.InitWallet(lnrpc.InitWalletRequest(wallet_password=b"password", recovery_window=0, cipher_seed_mnemonic=seed.cipher_seed_mnemonic))
        self.wait_for_log('Done catching up block hashes')
        time.sleep(5)
        # need to remake the channel, otherwise the Lightning gRPC service might not be there yet
        self.stub = lnrpc_grpc.LightningStub(self.make_channel())

        logging.info('LND started (pid: {})'.format(self.proc.pid))

    def stop(self):
        self.proc.terminate()
        time.sleep(3)
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        super().save_log()


class LndNode(object):

    displayName = 'lnd'

    def __init__(self, lightning_dir, lightning_port, btc, executor=None, node_id=0):
        self.bitcoin = btc
        self.executor = executor
        self.daemon = LndD(lightning_dir, btc.bitcoin_dir, port=lightning_port)
        self.logger = logging.getLogger('lnd-node({})'.format(lightning_port))
        self.myid = None
        self.node_id = node_id

    def id(self):
        if not self.myid:
            self.myid = self.info()['id']
        return self.myid

    def ping(self):
        """ Simple liveness test to see if the node is up and running

        Returns true if the node is reachable via RPC, false otherwise.
        """
        try:
            self.daemon.stub.GetInfo(lnrpc.GetInfoRequest())
            return True
        except Exception as e:
            print(e)
            return False

    def peers(self):
        peers = self.daemon.stub.ListPeers(lnrpc.ListPeersRequest()).peers
        return [p.pub_key for p in peers]

    def check_channel(self, remote):
        """ Make sure that we have an active channel with remote
        """
        self_id = self.id()
        remote_id = remote.id()
        channels = self.daemon.stub.ListChannels(lnrpc.ListChannelsRequest()).channels
        channel_by_remote = {c.remote_pubkey: c for c in channels}
        if remote_id not in channel_by_remote:
            self.logger.warning("Channel {} -> {} not found".format(self_id, remote_id))
            return False

        channel = channel_by_remote[remote_id]
        self.logger.debug("Channel {} -> {} state: {}".format(self_id, remote_id, channel))
        return channel.active

    def addfunds(self, bitcoind, satoshis):
        req = lnrpc.NewAddressRequest(type=1)
        addr = self.daemon.stub.NewAddress(req).address
        bitcoind.rpc.sendtoaddress(addr, float(satoshis) / 10**8)
        self.daemon.wait_for_log("Inserting unconfirmed transaction")
        bitcoind.rpc.generate(1)
        self.daemon.wait_for_log("Marking unconfirmed transaction")

        # The above still doesn't mean the wallet balance is updated,
        # so let it settle a bit
        i = 0
        while self.daemon.stub.WalletBalance(lnrpc.WalletBalanceRequest()).total_balance == satoshis and i < 30:
            time.sleep(1)
            i += 1
        assert(self.daemon.stub.WalletBalance(lnrpc.WalletBalanceRequest()).total_balance == satoshis)

    def openchannel(self, node_id, host, port, satoshis):
        peers = self.daemon.stub.ListPeers(lnrpc.ListPeersRequest()).peers
        peers_by_pubkey = {p.pub_key: p for p in peers}
        if node_id not in peers_by_pubkey:
            raise ValueError("Could not find peer {} in peers {}".format(node_id, peers))
        peer = peers_by_pubkey[node_id]
        self.daemon.stub.OpenChannel(lnrpc.OpenChannelRequest(
            node_pubkey=codecs.decode(peer.pub_key, 'hex_codec'),
            local_funding_amount=satoshis,
            push_sat=0
        ))

        # Somehow broadcasting a tx is slow from time to time
        time.sleep(5)

    def getchannels(self):
        req = lnrpc.ChannelGraphRequest()
        rep = self.daemon.stub.DescribeGraph(req)
        channels = []

        for e in rep.edges:
            channels.append((e.node1_pub, e.node2_pub))
            channels.append((e.node2_pub, e.node1_pub))
        return channels

    def getnodes(self):
        req = lnrpc.ChannelGraphRequest()
        rep = self.daemon.stub.DescribeGraph(req)
        nodes = set([n.pub_key for n in rep.nodes]) - set([self.id()])
        return nodes

    def invoice(self, amount):
        req = lnrpc.Invoice(value=int(amount/1000))
        rep = self.daemon.stub.AddInvoice(req)
        return rep.payment_request

    def send(self, bolt11):
        req = lnrpc.SendRequest(payment_request=bolt11)
        res = self.daemon.stub.SendPaymentSync(req)
        if res.payment_error:
            raise ValueError(res.payment_error)
        return hexlify(res.payment_preimage)

    def connect(self, host, port, node_id):
        addr = lnrpc.LightningAddress(pubkey=node_id, host="{}:{}".format(host, port))
        req = lnrpc.ConnectPeerRequest(addr=addr, perm=True)
        logging.debug(self.daemon.stub.ConnectPeer(req))

    def info(self):
        r = self.daemon.stub.GetInfo(lnrpc.GetInfoRequest())
        return {
            'id': r.identity_pubkey,
            'blockheight': r.block_height,
        }

    def block_sync(self, blockhash):
        print("Waiting for node to learn about", blockhash)
        self.daemon.wait_for_log('NTFN: New block: height=([0-9]+), sha={}'.format(blockhash))

    def restart(self):
        self.daemon.stop()
        time.sleep(5)
        self.daemon.start()

    def check_route(self, node_id, amount):
        try:
            req = lnrpc.QueryRoutesRequest(pub_key=node_id, amt=int(amount/1000), num_routes=1)
            r = self.daemon.stub.QueryRoutes(req)
        except grpc._channel._Rendezvous as e:
            if (str(e).find("unable to find a path to destination") > 0):
                return False
            raise
        return True
