from eclair import EclairNode
from lightningd import LightningNode
from concurrent import futures
from utils import BitcoinD

import itertools
import logging
import os
import pytest
import sys
import tempfile
import unittest

bitcoind = None
TEST_DIR = tempfile.mkdtemp(prefix='lightning-')
TEST_DEBUG = os.getenv("TEST_DEBUG", "0") == "1"

if TEST_DEBUG:
    logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
logging.info("Tests running in '%s'", TEST_DIR)

def setupBitcoind():
    global bitcoind
    bitcoind = BitcoinD(rpcport=28332)
    bitcoind.start()
    info = bitcoind.rpc.getinfo()
    # Make sure we have segwit and some funds
    if info['blocks'] < 432:
        logging.debug("SegWit not active, generating some more blocks")
        bitcoind.rpc.generate(432 - info['blocks'])
    elif info['balance'] < 1:
        logging.debug("Insufficient balance, generating 1 block")
        bitcoind.rpc.generate(1)

def tearDownBitcoind():
    global bitcoind
    try:
        bitcoind.rpc.stop()
    except:
        bitcoind.proc.kill()
    bitcoind.proc.wait()


def setUpModule():
    setupBitcoind()


def tearDownModule():
    tearDownBitcoind()


class NodeFactory(object):
    """A factory to setup and start `lightningd` daemons.
    """
    def __init__(self, func, executor):
        self.func = func
        self.next_id = 1
        self.nodes = []
        self.executor = executor

    def get_node(self, implementation):
        node_id = self.next_id
        self.next_id += 1

        lightning_dir = os.path.join(
            TEST_DIR, self.func._testMethodName, "lightning-{}/".format(node_id))
        port = 16330+node_id

        node = implementation(lightning_dir, port, bitcoind, executor=self.executor, node_id=node_id)
        self.nodes.append(node)

        node.daemon.start()
        # Cache `getinfo`, we'll be using it a lot
        #node.info = node.rpc.getinfo()
        return node

    def killall(self):
        for n in self.nodes:
            n.daemon.stop()

clients = [LightningNode, 1, 2]


class SimpleTests(unittest.TestCase):

    def setUp(self):
        self.executor = futures.ThreadPoolExecutor(max_workers=20)
        self.node_factory = NodeFactory(self, self.executor)

    def tearDown(self):
        self.node_factory.killall()
        self.executor.shutdown(wait=False)

    def testStart(self):
        eclNode = self.node_factory.get_node(implementation=EclairNode)
        lightNode = self.node_factory.get_node(implementation=LightningNode)
        print(eclNode.rpc.help())
        print(lightNode.rpc.help())
        
