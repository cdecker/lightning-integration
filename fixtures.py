from btcproxy import ProxiedBitcoinD
from ephemeral_port_reserve import reserve
from concurrent import futures

import os
import pytest
import tempfile
import logging
import shutil


TEST_DIR = tempfile.mkdtemp(prefix='lightning-')
TEST_DEBUG = os.getenv("TEST_DEBUG", "0") == "1"


# A dict in which we count how often a particular test has run so far. Used to
# give each attempt its own numbered directory, and avoid clashes.
__attempts = {}


class NodeFactory(object):
    """A factory to setup and start `lightningd` daemons.
    """
    def __init__(self, testname, executor, bitcoind, btcd):
        self.testname = testname
        self.next_id = 1
        self.nodes = []
        self.executor = executor
        self.bitcoind = bitcoind
        self.btcd = btcd

    def get_node(self, implementation):
        node_id = self.next_id
        self.next_id += 1

        lightning_dir = os.path.join(
            TEST_DIR, self.testname, "node-{}/".format(node_id))
        port = reserve()

        node = implementation(lightning_dir, port, self.bitcoind,
                              executor=self.executor, node_id=node_id)
        self.nodes.append(node)

        node.btcd = self.btcd
        node.daemon.start()
        return node

    def killall(self):
        for n in self.nodes:
            n.daemon.stop()

            
@pytest.fixture
def directory(request, test_base_dir, test_name):
    """Return a per-test specific directory.

    This makes a unique test-directory even if a test is rerun multiple times.

    """
    global __attempts
    # Auto set value if it isn't in the dict yet
    __attempts[test_name] = __attempts.get(test_name, 0) + 1
    directory = os.path.join(test_base_dir, "{}_{}".format(test_name, __attempts[test_name]))
    request.node.has_errors = False

    yield directory

    # This uses the status set in conftest.pytest_runtest_makereport to
    # determine whether we succeeded or failed.
    if not request.node.has_errors and request.node.rep_call.outcome == 'passed':
        shutil.rmtree(directory)
    else:
        logging.debug("Test execution failed, leaving the test directory {} intact.".format(directory))

        
@pytest.fixture(scope="session")
def test_base_dir():
    directory = tempfile.mkdtemp(prefix='ltests-')
    print("Running tests in {}".format(directory))

    yield directory

    if os.listdir(directory) == []:
        shutil.rmtree(directory)

        
@pytest.fixture
def test_name(request):
    yield request.function.__name__

    
@pytest.fixture()
def bitcoind(directory):
    proxyport = reserve()
    btc = ProxiedBitcoinD(bitcoin_dir=os.path.join(directory, "bitcoind"), proxyport=proxyport)
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

    # Mock `estimatesmartfee` to make c-lightning happy
    def mock_estimatesmartfee(r):
        return {"id": r['id'], "error": None, "result": {"feerate": 0.00100001, "blocks": r['params'][0]}}

    btc.mock_rpc('estimatesmartfee', mock_estimatesmartfee)

    yield btc

    try:
        btc.rpc.stop()
    except Exception:
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


