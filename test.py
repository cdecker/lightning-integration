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
def testConnect(node_factory, impls):
    node1 = node_factory.get_node(implementation=impls[0])
    node2 = node_factory.get_node(implementation=impls[1])

    node1.rpc.connect('localhost', node2.daemon.port, node2.id())

    wait_for(lambda: node1.peers())
    wait_for(lambda: node2.peers())

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
    node1.openchannel(node2.id(), 'localhost', node2.daemon.port, 10**7)
