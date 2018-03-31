from binascii import hexlify, unhexlify
from utils import TailableProc, BITCOIND_CONFIG
import rpc_pb2_grpc as lnrpc_grpc
import rpc_pb2 as lnrpc


import grpc
import logging
import os
import time
import codecs


# Needed for grpc to negotiate a valid cipher suite
os.environ["GRPC_SSL_CIPHER_SUITES"] = "ECDHE-ECDSA-AES256-GCM-SHA384"


class LndD(TailableProc):

    CONF_NAME = 'lnd.conf'

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
            '--datadir={}'.format(lightning_dir),
            '--debuglevel=trace',
            '--rpclisten=127.0.0.1:{}'.format(self.rpc_port),
            '--restlisten=127.0.0.1:{}'.format(self.rest_port),
            '--listen=127.0.0.1:{}'.format(self.port),
            '--tlscertpath=tls.cert',
            '--tlskeypath=tls.key',
            '--bitcoin.node=bitcoind',
            '--bitcoind.rpchost=127.0.0.1:{}'.format(BITCOIND_CONFIG.get('rpcport', 18332)),
            '--bitcoind.rpcuser=rpcuser',
            '--bitcoind.rpcpass=rpcpass',
            '--bitcoind.zmqpath=tcp://127.0.0.1:29000',
            '--configfile={}'.format(os.path.join(lightning_dir, self.CONF_NAME)),
            '--no-macaroons',
            '--nobootstrap',
            '--noencryptwallet',
        ]

        if not os.path.exists(lightning_dir):
            os.makedirs(lightning_dir)
        with open(os.path.join(lightning_dir, self.CONF_NAME), "w") as f:
            f.write("""[Application Options]\n""")

    def start(self):
        super().start()
        self.wait_for_log('RPC server listening on')
        self.wait_for_log('Done catching up block hashes')
        time.sleep(5)

        logging.info('LND started (pid: {})'.format(self.proc.pid))


class LndNode(object):

    displayName = 'lnd'

    def __init__(self, lightning_dir, lightning_port, btc, executor=None, node_id=0):
        self.bitcoin = btc
        self.executor = executor
        self.daemon = LndD(lightning_dir, btc.bitcoin_dir, port=lightning_port)
        self.rpc = LndRpc(lightning_port+10000)
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
        self_id = self.id()
        remote_id = remote.id()
        channels = self.rpc.stub.ListChannels(lnrpc.ListChannelsRequest()).channels
        channel_by_remote = {c.remote_pubkey: c for c in channels}
        if remote_id not in channel_by_remote:
            self.logger.warning("Channel {} -> {} not found".format(self_id, remote_id))
            return False

        channel = channel_by_remote[remote_id]
        self.logger.debug("Channel {} -> {} state: {}".format(self_id, remote_id, channel))
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
        assert(self.rpc.stub.WalletBalance(lnrpc.WalletBalanceRequest()).total_balance == satoshis)

    def openchannel(self, node_id, host, port, satoshis):
        peers = self.rpc.stub.ListPeers (lnrpc.ListPeersRequest()).peers
        peers_by_pubkey = {p.pub_key: p for p in peers}
        if not node_id in peers_by_pubkey:
            raise ValueError("Could not find peer {} in peers {}".format(node_id, peers))
        peer = peers_by_pubkey[node_id]
        self.rpc.stub.OpenChannel(lnrpc.OpenChannelRequest(
            node_pubkey=codecs.decode(peer.pub_key, 'hex_codec'),
            local_funding_amount=satoshis,
            push_sat=0
        ))

        # Somehow broadcasting a tx is slow from time to time
        time.sleep(5)

    def getchannels(self):
        req = lnrpc.ChannelGraphRequest()
        rep = self.rpc.stub.DescribeGraph(req)
        channels = []

        for e in rep.edges:
            channels.append((e.node1_pub, e.node2_pub))
            channels.append((e.node2_pub, e.node1_pub))
        return channels

    def getnodes(self):
        req = lnrpc.ChannelGraphRequest()
        rep = self.rpc.stub.DescribeGraph(req)
        nodes = set([n.pub_key for n in rep.nodes]) - set([self.id()])
        return nodes

    def invoice(self, amount):
        req = lnrpc.Invoice(value=int(amount/1000))
        rep = self.rpc.stub.AddInvoice(req)
        return rep.payment_request

    def send(self, req):
        req = lnrpc.SendRequest(payment_request=req)
        res = self.rpc.stub.SendPaymentSync(req)
        return hexlify(res.payment_preimage)

    def connect(self, host, port, node_id):
        addr = lnrpc.LightningAddress(pubkey=node_id, host="{}:{}".format(host, port))
        req = lnrpc.ConnectPeerRequest(addr=addr, perm=True)
        logging.debug(self.rpc.stub.ConnectPeer(req))

    def info(self):
        r = self.rpc.stub.GetInfo(lnrpc.GetInfoRequest())
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
        self.rpc = LndRpc(self.daemon.rpc_port)


class LndRpc(object):

    def __init__(self, rpc_port):
        self.port = rpc_port
        cred = grpc.ssl_channel_credentials(open('tls.cert').read())
        channel = grpc.secure_channel('localhost:{}'.format(rpc_port), cred)
        self.stub = lnrpc_grpc.LightningStub(channel)
