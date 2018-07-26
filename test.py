from binascii import unhexlify, hexlify
from eclair import EclairNode
from hashlib import sha256
from itertools import product
from lightningd import LightningNode
from lnaddr import lndecode
from lnd import LndNode
from ptarmd import PtarmNode
from concurrent import futures
from utils import BitcoinD, BtcD
from bech32 import bech32_decode

import logging
import os
import pytest
import sys
import tempfile
import time

TEST_DIR = tempfile.mkdtemp(prefix='lightning-')
TEST_DEBUG = os.getenv("TEST_DEBUG", "0") == "1"
impls = [EclairNode, LightningNode, LndNode, PtarmNode]

if TEST_DEBUG:
    logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
logging.info("Tests running in '%s'", TEST_DIR)


class NodeFactory(object):
    """A factory to setup and start `lightningd` daemons.
    """
    def __init__(self, testname, executor, btc, btcd):
        self.testname = testname
        self.next_id = 1
        self.nodes = []
        self.executor = executor
        self.btc = btc
        self.btcd = btcd

    def get_node(self, implementation):
        node_id = self.next_id
        self.next_id += 1

        lightning_dir = os.path.join(
            TEST_DIR, self.testname, "node-{}/".format(node_id))
        port = 16330+node_id

        node = implementation(lightning_dir, port, self.btc,
                              executor=self.executor, node_id=node_id)
        self.nodes.append(node)

        node.btcd = self.btcd
        node.daemon.start()
        return node

    def killall(self):
        for n in self.nodes:
            n.daemon.stop()


@pytest.fixture()
def bitcoind():
    btc = BitcoinD(bitcoin_dir=os.path.join(TEST_DIR, "bitcoind"), rpcport=28332)
    btc.start()
    bch_info = btc.rpc.getblockchaininfo()
    w_info = btc.rpc.getwalletinfo()
    # Make sure we have segwit and some funds
    if bch_info['blocks'] < 120:
        logging.debug("SegWit not active, generating some more blocks")
        btc.rpc.generate(120 - bch_info['blocks'])
    elif w_info['balance'] < 1:
        logging.debug("Insufficient balance, generating 1 block")
        btc.rpc.generate(1)

    yield btc

    try:
        btc.rpc.stop()
    except:
        btc.proc.kill()
    btc.proc.wait()


@pytest.fixture(scope="module")
def btcd():
    btcd = BtcD()
    btcd.start()

    yield btcd

    try:
        btcd.rpc.stop()
    except:
        btcd.proc.kill()
    btcd.proc.wait()


@pytest.fixture
def node_factory(request, bitcoind):
    executor = futures.ThreadPoolExecutor(max_workers=20)
    node_factory = NodeFactory(request._pyfuncitem.name, executor, bitcoind, None)
    yield node_factory
    node_factory.killall()
    executor.shutdown(wait=False)


def wait_for(success, timeout=30, interval=1):
    start_time = time.time()
    while not success() and time.time() < start_time + timeout:
        time.sleep(interval)
    if time.time() > start_time + timeout:
        raise ValueError("Error waiting for {}", success)


def sync_blockheight(btc, nodes):
    info = btc.rpc.getblockchaininfo()
    blocks = info['blocks']

    print("Waiting for %d nodes to blockheight %d" % (len(nodes), blocks))
    for n in nodes:
        wait_for(lambda: n.info()['blockheight'] == blocks, interval=1)


def generate_until(btc, success, blocks=30, interval=1):
    """Generate new blocks until `success` returns true.

    Mainly used to wait for transactions to confirm since they might
    be delayed and we don't want to add a long waiting time to all
    tests just because some are slow.
    """
    for i in range(blocks):
        time.sleep(interval)
        if success():
            return
        btc.rpc.generate(1)
    time.sleep(interval)
    if not success():
        raise ValueError("Generated %d blocks, but still no success", blocks)


def idfn(impls):
    return "_".join([i.displayName for i in impls])


@pytest.mark.parametrize("impl", impls, ids=idfn)
def test_start(bitcoind, node_factory, impl):
    node = node_factory.get_node(implementation=impl)
    assert node.ping()
    sync_blockheight(bitcoind, [node])


@pytest.mark.parametrize("impls", product(impls, repeat=2), ids=idfn)
def test_connect(node_factory, bitcoind, impls):
    node1 = node_factory.get_node(implementation=impls[0])
    node2 = node_factory.get_node(implementation=impls[1])

    # Needed by lnd in order to have at least one block in the last 2 hours
    bitcoind.rpc.generate(1)

    print("Connecting {}@{}:{} -> {}@{}:{}".format(
        node1.id(), 'localhost', node1.daemon.port,
        node2.id(), 'localhost', node2.daemon.port))
    node1.connect('localhost', node2.daemon.port, node2.id())

    wait_for(lambda: node1.peers(), timeout=5)
    wait_for(lambda: node2.peers(), timeout=5)

    # TODO(cdecker) Check that we are connected
    assert node1.id() in node2.peers()
    assert node2.id() in node1.peers()


def confirm_channel(bitcoind, n1, n2):
    print("Waiting for channel {} -> {} to confirm".format(n1.id(), n2.id()))
    assert n1.id() in n2.peers()
    assert n2.id() in n1.peers()
    for i in range(30):
        time.sleep(1)
        if n1.check_channel(n2) and n2.check_channel(n1):
            print("Channel {} -> {} confirmed".format(n1.id(), n2.id()))
            return True
        bhash = bitcoind.rpc.generate(1)[0]
        n1.block_sync(bhash)
        n2.block_sync(bhash)

    # Last ditch attempt
    return n1.check_channel(n2) and n2.check_channel(n1)


@pytest.mark.parametrize("impls", product(impls, repeat=2), ids=idfn)
def test_open_channel(bitcoind, node_factory, impls):
    node1 = node_factory.get_node(implementation=impls[0])
    node2 = node_factory.get_node(implementation=impls[1])

    node1.connect('localhost', node2.daemon.port, node2.id())

    wait_for(lambda: node1.peers(), interval=1)
    wait_for(lambda: node2.peers(), interval=1)

    node1.addfunds(bitcoind, 2 * 10**7)

    node1.openchannel(node2.id(), 'localhost', node2.daemon.port, 10**7)
    time.sleep(1)
    bitcoind.rpc.generate(2)

    assert confirm_channel(bitcoind, node1, node2)

    assert(node1.check_channel(node2))
    assert(node2.check_channel(node1))

    # Generate some more, to reach the announcement depth
    bitcoind.rpc.generate(4)


@pytest.mark.parametrize("impls", product(impls, repeat=2), ids=idfn)
def test_gossip(node_factory, bitcoind, impls):
    """ Create a network of lightningd nodes and connect to it using 2 new nodes
    """
    # These are the nodes we really want to test
    node1 = node_factory.get_node(implementation=impls[0])
    node2 = node_factory.get_node(implementation=impls[1])

    # Using lightningd since it is quickest to start up
    nodes = [node_factory.get_node(implementation=LightningNode) for _ in range(5)]
    for n1, n2 in zip(nodes[:4], nodes[1:]):
        n1.connect('localhost', n2.daemon.port, n2.id())
        n1.addfunds(bitcoind, 2 * 10**7)
        n1.openchannel(n2.id(), 'localhost', n2.daemon.port, 10**7)
        assert confirm_channel(bitcoind, n1, n2)

    time.sleep(5)
    bitcoind.rpc.generate(30)
    time.sleep(5)

    # Wait for gossip to settle
    for n in nodes:
        wait_for(lambda: len(n.getnodes()) == 5, interval=1, timeout=120)
        wait_for(lambda: len(n.getchannels()) == 8, interval=1, timeout=120)

    # Now connect the first node to the line graph and the second one to the first
    node1.connect('localhost', nodes[0].daemon.port, nodes[0].id())
    node2.connect('localhost', n1.daemon.port, n1.id())

    # They should now be syncing as well
    # TODO(cdecker) Uncomment the following line when eclair exposes non-local channels as well (ACINQ/eclair/issues/126)
    #wait_for(lambda: len(node1.getchannels()) == 8)
    wait_for(lambda: len(node1.getnodes()) == 5, interval=1)

    # Node 2 syncs through node 1
    # TODO(cdecker) Uncomment the following line when eclair exposes non-local channels as well (ACINQ/eclair/issues/126)
    #wait_for(lambda: len(node2.getchannels()) == 8)
    wait_for(lambda: len(node2.getnodes()) == 5, interval=1)


@pytest.mark.parametrize("impl", impls, ids=idfn)
def test_invoice_decode(node_factory, impl):
    capacity = 10**7
    node1 = node_factory.get_node(implementation=impl)

    amount = int(capacity / 10)
    payment_request = node1.invoice(amount)
    hrp, data = bech32_decode(payment_request)

    assert hrp and data
    assert hrp.startswith('lnbcrt')


@pytest.mark.parametrize("impls", product(impls, repeat=2), ids=idfn)
def test_direct_payment(bitcoind, node_factory, impls):
    node1 = node_factory.get_node(implementation=impls[0])
    node2 = node_factory.get_node(implementation=impls[1])
    capacity = 10**7

    node1.connect('localhost', node2.daemon.port, node2.id())

    wait_for(lambda: node1.peers(), interval=1)
    wait_for(lambda: node2.peers(), interval=1)

    node1.addfunds(bitcoind, 2*capacity)
    time.sleep(5)
    bitcoind.rpc.generate(10)
    time.sleep(5)

    node1.openchannel(node2.id(), 'localhost', node2.daemon.port, capacity)
    assert confirm_channel(bitcoind, node1, node2)

    sync_blockheight(bitcoind, [node1, node2])

    amount = int(capacity / 10)
    req = node2.invoice(amount)
    dec = lndecode(req)

    print("Decoded payment request", req, dec)
    payment_key = node1.send(req)
    assert(sha256(unhexlify(payment_key)).digest() == dec.paymenthash)


def gossip_is_synced(nodes, num_channels):
    print("Checking %d nodes for gossip sync" % (len(nodes)))
    for i, n in enumerate(nodes):
        node_chans = n.getchannels()
        logging.debug("Node {} knows about the following channels {}".format(i, node_chans))
        if len(node_chans) != num_channels:
            print("Node %d is missing %d channels" % (i, num_channels - len(node_chans)))
            return False
    return True


def check_channels(pairs):
    ok = True
    logging.debug("Checking all channels between {}".format(pairs))
    for node1, node2 in pairs:
        ok &= node1.check_channel(node2)
        ok &= node2.check_channel(node1)
    return ok


def node_has_route(node, channels):
    """Check whether a node knows about a specific route.

    The route is a list of node_id tuples
    """
    return set(channels).issubset(set(node.getchannels()))


@pytest.mark.parametrize("impls", product(impls, repeat=3), ids=idfn)
def test_forwarded_payment(bitcoind, node_factory, impls):
    num_nodes = len(impls)
    nodes = [node_factory.get_node(implementation=impls[i]) for i in range(3)]
    capacity = 10**7

    for i in range(num_nodes-1):
        nodes[i].connect('localhost', nodes[i+1].daemon.port, nodes[i+1].id())
        nodes[i].addfunds(bitcoind, 4 * capacity)

    for i in range(num_nodes-1):
        nodes[i].openchannel(nodes[i+1].id(), 'localhost', nodes[i+1].daemon.port, capacity)
        assert confirm_channel(bitcoind, nodes[i], nodes[i+1])

    bitcoind.rpc.generate(6)
    sync_blockheight(bitcoind, nodes)

    # Make sure we have a path
    ids = [n.info()['id'] for n in nodes]
    route = [(ids[i-1], ids[i]) for i in range(1, len(ids))]
    wait_for(lambda: node_has_route(nodes[0], route), timeout=120)
    sync_blockheight(bitcoind, nodes)

    src = nodes[0]
    dst = nodes[len(nodes)-1]
    amount = int(capacity / 10)
    req = dst.invoice(amount)

    print("Waiting for a route to be found")
    wait_for(lambda: src.check_route(dst.id(), amount), timeout=120)

    payment_key = src.send(req)
    dec = lndecode(req)
    assert(sha256(unhexlify(payment_key)).digest() == dec.paymenthash)


@pytest.mark.parametrize("impls", product(impls, repeat=2), ids=idfn)
def test_reconnect(bitcoind, node_factory, impls):
    node1 = node_factory.get_node(implementation=impls[0])
    node2 = node_factory.get_node(implementation=impls[1])
    capacity = 10**7

    node1.connect('localhost', node2.daemon.port, node2.id())

    wait_for(lambda: node1.peers(), interval=1)
    wait_for(lambda: node2.peers(), interval=1)

    node1.addfunds(bitcoind, 2*capacity)
    time.sleep(5)
    bitcoind.rpc.generate(10)
    time.sleep(5)

    node1.openchannel(node2.id(), 'localhost', node2.daemon.port, capacity)

    for i in range(30):
        node1.bitcoin.rpc.generate(1)
        time.sleep(1)

    wait_for(lambda: node1.check_channel(node2))
    wait_for(lambda: node2.check_channel(node1))
    sync_blockheight(bitcoind, [node1, node2])

    amount = int(capacity / 10)
    req = node2.invoice(amount)
    payment_key = node1.send(req)
    dec = lndecode(req)
    assert(sha256(unhexlify(payment_key)).digest() == dec.paymenthash)

    print("Sleep before restart")
    time.sleep(5)

    print("Restarting")
    node2.restart()

    time.sleep(15)

    wait_for(lambda: node1.check_channel(node2))
    wait_for(lambda: node2.check_channel(node1))
    sync_blockheight(bitcoind, [node1, node2])

    time.sleep(15)

    req = node2.invoice(amount)
    payment_key = node1.send(req)
    dec = lndecode(req)
    assert(sha256(unhexlify(payment_key)).digest() == dec.paymenthash)
