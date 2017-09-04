from binascii import unhexlify
from eclair import EclairNode
from itertools import product
from lightningd import LightningNode
from lnd import LndNode
from concurrent import futures
from pprint import pprint
from utils import BitcoinD, BtcD

import logging
import os
import pytest
import sys
import tempfile
import time
import unittest

TEST_DIR = tempfile.mkdtemp(prefix='lightning-')
TEST_DEBUG = os.getenv("TEST_DEBUG", "0") == "1"
impls = [EclairNode, LightningNode, LndNode]


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
            TEST_DIR, self.testname, "lightning-{}/".format(node_id))
        port = 16330+node_id

        node = implementation(lightning_dir, port, self.btc,
                              executor=self.executor, node_id=node_id)
        self.nodes.append(node)

        node.daemon.start()
        return node

    def killall(self):
        for n in self.nodes:
            n.daemon.stop()


@pytest.fixture(scope="module")
def bitcoind():
    btc = BitcoinD(rpcport=28332)
    btc.start()
    info = btc.rpc.getinfo()
    # Make sure we have segwit and some funds
    if info['blocks'] < 432:
        logging.debug("SegWit not active, generating some more blocks")
        btc.rpc.generate(432 - info['blocks'])
    elif info['balance'] < 1:
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
def node_factory(request, bitcoind, btcd):
    executor = futures.ThreadPoolExecutor(max_workers=20)
    node_factory = NodeFactory(request._pyfuncitem.name, executor, bitcoind, btcd)
    yield node_factory
    node_factory.killall()
    executor.shutdown(wait=False)


def wait_for(success, timeout=30, interval=0.1):
    start_time = time.time()
    while not success() and time.time() < start_time + timeout:
        time.sleep(interval)
    if time.time() > start_time + timeout:
        raise ValueError("Error waiting for {}", success)


def idfn(impls):
    return "_".join([i.__name__ for i in impls])


@pytest.mark.parametrize("impl", impls, ids=idfn)
def testStart(node_factory, impl):
    node = node_factory.get_node(implementation=impl)
    assert node.ping()


@pytest.mark.parametrize("impls", product(impls, repeat=2), ids=idfn)
def testConnect(node_factory, bitcoind, impls):
    node1 = node_factory.get_node(implementation=impls[0])
    node2 = node_factory.get_node(implementation=impls[1])

    # Needed by lnd in order to have at least one block in the last 2 hours
    bitcoind.rpc.generate(1)

    print("Connecting {}@{}:{} -> {}@{}:{}".format(
        node1.id(), 'localhost', node1.daemon.port,
        node2.id(), 'localhost', node2.daemon.port))
    node1.rpc.connect('localhost', node2.daemon.port, node2.id())

    wait_for(lambda: node1.peers(), timeout=5)
    wait_for(lambda: node2.peers(), timeout=5)

    # TODO(cdecker) Check that we are connected
    assert node1.id() in node2.peers()
    assert node2.id() in node1.peers()


@pytest.mark.parametrize("impls", product(impls, repeat=2), ids=idfn)
def testOpenchannel(bitcoind, node_factory, impls):
    node1 = node_factory.get_node(implementation=impls[0])
    node2 = node_factory.get_node(implementation=impls[1])

    node1.rpc.connect('localhost', node2.daemon.port, node2.id())

    wait_for(lambda: node1.peers(), interval=1)
    wait_for(lambda: node2.peers(), interval=1)

    node1.addfunds(bitcoind, 2 * 10**7)
    time.sleep(1)
    bitcoind.rpc.generate(1)
    time.sleep(1)

    # LndNode disagrees on open_channel
    assert impls[1] != LndNode

    node1.openchannel(node2.id(), 'localhost', node2.daemon.port, 10**7)
    for _ in range(6):
        time.sleep(1)
        bitcoind.rpc.generate(1)

    time.sleep(10)
    wait_for(lambda: node1.check_channel(node2), interval=1, timeout=10)
    wait_for(lambda: node2.check_channel(node1), interval=1, timeout=10)

    # The nodes should know at least about this one channel
    nodeids = set([node1.id(), node2.id()])
    wait_for(lambda: nodeids.issubset(node1.getnodes()), interval=1)
    wait_for(lambda: nodeids.issubset(node2.getnodes()), interval=1)

@pytest.mark.parametrize("impls", product(impls, repeat=2), ids=idfn)
def testgossip(node_factory, bitcoind, impls):
    """ Create a network of lightningd nodes and connect to it using 2 new nodes
    """
    # These are the nodes we really want to test
    node1 = node_factory.get_node(implementation=impls[0])
    node2 = node_factory.get_node(implementation=impls[1])

    # Using lightningd since it is quickest to start up
    nodes = [node_factory.get_node(implementation=LightningNode) for _ in range(5)]
    for n1, n2 in zip(nodes[:4], nodes[1:]):
        n1.rpc.connect('localhost', n2.daemon.port, n2.id())
        n1.addfunds(bitcoind, 2 * 10**7)
        n1.openchannel(n2.id(), 'localhost', n2.daemon.port, 10**7)
    time.sleep(1)
    bitcoind.rpc.generate(6)

    # Wait for gossip to settle
    for n in nodes:
        wait_for(lambda: len(n.getnodes()) == 5)
        wait_for(lambda: len(n.getchannels()) == 8)

    # Now connect the first node to the line graph and the second one to the first
    node1.rpc.connect('localhost', nodes[0].daemon.port, nodes[0].id())
    node2.rpc.connect('localhost', n1.daemon.port, n1.id())

    # They should now be syncing as well
    # TODO(cdecker) Uncomment the following line when eclair exposes non-local channels as well (ACINQ/eclair/issues/126)
    #wait_for(lambda: len(node1.getchannels()) == 8)
    wait_for(lambda: len(node1.getnodes()) == 5, interval=1)

    # Node 2 syncs through node 1
    # TODO(cdecker) Uncomment the following line when eclair exposes non-local channels as well (ACINQ/eclair/issues/126)
    #wait_for(lambda: len(node2.getchannels()) == 8)
    wait_for(lambda: len(node2.getnodes()) == 5, interval=1)

@pytest.mark.parametrize("impls", product(impls, repeat=2), ids=idfn)
def testPayment(bitcoind, node_factory, impls):
    node1 = node_factory.get_node(implementation=impls[0])
    node2 = node_factory.get_node(implementation=impls[1])

    node1.rpc.connect('localhost', node2.daemon.port, node2.id())

    wait_for(lambda: node1.peers(), interval=1)
    wait_for(lambda: node2.peers(), interval=1)

    node1.addfunds(bitcoind, 2 * 10**7)
    time.sleep(1)
    bitcoind.rpc.generate(1)
    time.sleep(1)

    # LndNode disagrees on open_channel
    assert LndNode != impls[1]

    node1.openchannel(node2.id(), 'localhost', node2.daemon.port, 10**7)

    for _ in range(10):
        time.sleep(1)
        bitcoind.rpc.generate(1)

    wait_for(lambda: node1.check_channel(node2), interval=1, timeout=10)
    wait_for(lambda: node2.check_channel(node1), interval=1, timeout=10)

    bitcoind.rpc.generate(6)
    
    time.sleep(10)

    amount = 10**7
    rhash = node2.invoice(amount)
    node1.send(node2, rhash, amount)
