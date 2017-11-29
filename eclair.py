from binascii import hexlify
from lnaddr import lndecode
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from utils import TailableProc


import json
import logging
import os
import psutil
import re
import requests
import time




def requests_retry_session(
    retries=3,
    backoff_factor=0.3,
    status_forcelist=(500, 502, 504),
    session=None,
):
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


class EclairD(TailableProc):
    def __init__(self, lightning_dir, bitcoin_dir, port):
        TailableProc.__init__(self, lightning_dir, "eclair({})".format(port))
        self.lightning_dir = lightning_dir
        self.bitcoin_dir = bitcoin_dir
        self.port = port
        self.rpc_port = str(10000 + port)
        self.prefix = 'eclair'

        self.cmd_line = [
            '/usr/lib/jvm/java-8-openjdk-amd64/bin/java',
            '-Declair.datadir={}'.format(lightning_dir),
            '-Declair.printToConsole=true',
            '-jar',
            'bin/eclair.jar'
        ]

        if not os.path.exists(lightning_dir):
            os.makedirs(lightning_dir)

        # Adapt the config and store it
        config = open('src/eclair/eclair-core/src/main/resources/reference.conf').read()
        replacements = [
            ('9735', str(port)),
            ('18332', str(28332)),
            ('8080', str(self.rpc_port)),
            ('"test"', '"regtest"'),
            ('"foo"', '"rpcuser"'),
            ('"bar"', '"rpcpass"'),
        ]

        for old, new in replacements:
            config = config.replace(old, new)

        with open(os.path.join(lightning_dir, "eclair.conf"), "w") as f:
            f.write(config)

    def start(self):
        TailableProc.start(self)
        self.wait_for_log("connected to tcp://127.0.0.1:29000")

        # And let's also remember the address
        exp = 'initial wallet address=([a-zA-Z0-9]+)'
        addr_line = self.wait_for_log(exp)
        self.addr = re.search(exp, addr_line).group(1)

        self.logger.info("Eclair started (pid: {})".format(self.proc.pid))

    def stop(self):
        # Java forks internally and detaches its children, use psutil to hunt
        # them down and kill them
        proc = psutil.Process(self.proc.pid)
        processes = [proc] + proc.children(recursive=True)

        # Be nice to begin with
        for p in processes:
            p.terminate()
        _, alive = psutil.wait_procs(processes, timeout=3)

        # But if they aren't, we can be more persuasive
        for p in alive:
            p.kill()
        psutil.wait_procs(alive, timeout=3)


class EclairNode(object):

    def __init__(self, lightning_dir, lightning_port, btc, executor=None,
                 node_id=0):
        self.bitcoin = btc
        self.executor = executor
        self.daemon = EclairD(lightning_dir, btc.bitcoin_dir,
                              port=lightning_port)
        self.rpc = EclairRpc(
            'http://localhost:{}'.format(self.daemon.rpc_port))
        self.logger = logging.getLogger('eclair-node({})'.format(lightning_port))

    def peers(self):
        return self.rpc.peers()

    def id(self):
        info = self.rpc._call("getinfo", [])
        return info['nodeId']

    def openchannel(self, node_id, host, port, satoshis):
        r = self.rpc._call('open', [node_id, host, port, satoshis, 0])
        time.sleep(5)
        self.bitcoin.rpc.generate(6)
        time.sleep(5)
        return r

    def getaddress(self):
        return self.daemon.addr

    def addfunds(self, bitcoind, satoshis):
        addr = self.getaddress()
        bitcoind.rpc.sendtoaddress(addr, float(satoshis) / 10**8)
        time.sleep(5)
        bitcoind.rpc.generate(6)

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
        self_id = self.id()
        remote_id = remote.id()
        for c in self.rpc.channels():
            channel = self.rpc.channel(c)
            if channel['nodeid'] == remote_id:
                self.logger.debug("Channel {} -> {} state: {}".format(self_id, remote_id, channel['state']))
                return channel['state'] == 'NORMAL'
        self.logger.warning("Channel {} -> {} not found".format(self_id, remote_id))
        return False

    def getchannels(self):
        channels = []
        for c in self.rpc._call('allchannels', []):
            channels.append((c['nodeId1'], c['nodeId2']))
            channels.append((c['nodeId2'], c['nodeId1']))
        return channels

    def getnodes(self):
        return set(self.rpc.allnodes())

    def invoice(self, amount):
        req = self.rpc._call("receive", [amount, "invoice1"])
        print(req)
        return req

    def send(self, req):
        result = self.rpc._call("send", [req])
        if 'failures' in result:
            raise ValueError("Failed to send payment: {}".format(result))
        else:
            return result['paymentPreimage']

    def connect(self, host, port, node_id):
        return self.rpc._call('connect', [host, port, node_id])

    def info(self):
        r = self.rpc._call('getinfo', [])
        return {
            'id': r['nodeId'],
            'blockheight': r['blockHeight'],
        }

    def restart(self):
        self.daemon.stop()
        time.sleep(5)
        self.daemon.start()
        time.sleep(1)

EclairNode.displayName = 'eclair'

class EclairRpc(object):

    def __init__(self, url):
        self.url = url
        self.session = requests_retry_session(retries=10, session=requests.Session())

    def _call(self, method, params):
        headers = {'Content-type': 'application/json'}
        data = json.dumps({'method': method, 'params': params})
        logging.info("Calling %s with params=%s", method, params)
        reply = self.session.post(self.url, data=data, headers=headers)

        if reply.status_code != 200:
            raise ValueError("Server returned an unknown error: {} ({})".format(
                reply.status_code, reply.text))

        logging.debug("Method %s returned %r", method, reply.json())
        if 'error' in reply.json():
            raise ValueError('Error calling {}: {}'.format(
                method, reply.json()['error']))
        else:
            return reply.json()['result']

    def peers(self):
        return self._call('peers', [])

    def channels(self):
        return self._call('channels', [])

    def channel(self, cid):
        return self._call('channel', [cid])

    def allnodes(self):
        return self._call('allnodes', [])

    def help(self):
        return self._call('help', [])
