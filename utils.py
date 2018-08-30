from bitcoin.rpc import RawProxy as BitcoinProxy
from ephemeral_port_reserve import reserve

import logging
import re
import subprocess
import threading
import time
import os
import collections
import json
import base64
import requests


BITCOIND_CONFIG = collections.OrderedDict([
    ("server", 1),
    ("deprecatedrpc", "addwitnessaddress"),
    ("addresstype", "p2sh-segwit"),
    ("deprecatedrpc", "signrawtransaction"),
    ("rpcuser", "rpcuser"),
    ("rpcpassword", "rpcpass"),
    ("listen", 0)
])


def write_config(filename, opts):
    with open(filename, 'w') as f:
        write_dict(f, opts)

def write_dict(f, opts):
    for k, v in opts.items():
        if isinstance(v, dict):
            f.write("[{}]\n".format(k))
            write_dict(f, v)
        else:
            f.write("{}={}\n".format(k, v))


class TailableProc(object):
    """A monitorable process that we can start, stop and tail.

    This is the base class for the daemons. It allows us to directly
    tail the processes and react to their output.
    """

    def __init__(self, outputDir=None, prefix='proc'):
        self.logs = []
        self.logs_cond = threading.Condition(threading.RLock())
        self.cmd_line = None
        self.running = False
        self.proc = None
        self.outputDir = outputDir
        self.logger = logging.getLogger(prefix)

    def start(self):
        """Start the underlying process and start monitoring it.
        """
        self.thread = threading.Thread(target=self.tail)
        self.thread.daemon = True
        logging.debug("Starting '%s'", " ".join(self.cmd_line))
        self.proc = subprocess.Popen(self.cmd_line, stdout=subprocess.PIPE)
        self.thread.start()
        self.running = True

    def save_log(self):
        if self.outputDir:
            logpath = os.path.join(self.outputDir, 'log.' + str(int(time.time())))
            with open(logpath, 'w') as f:
                for l in self.logs:
                    f.write(l + '\n')

    def stop(self):
        self.proc.terminate()
        self.proc.kill()
        self.save_log()

    def tail(self):
        """Tail the stdout of the process and remember it.

        Stores the lines of output produced by the process in
        self.logs and signals that a new line was read so that it can
        be picked up by consumers.
        """
        for line in iter(self.proc.stdout.readline, ''):
            if len(line) == 0:
                break
            with self.logs_cond:
                self.logs.append(str(line.rstrip()))
                self.logger.debug(line.decode().rstrip())
                self.logs_cond.notifyAll()
        self.running = False

    def is_in_log(self, regex):
        """Look for `regex` in the logs."""

        ex = re.compile(regex)
        for l in self.logs:
            if ex.search(l):
                logging.debug("Found '%s' in logs", regex)
                return True

        logging.debug("Did not find '%s' in logs", regex)
        return False

    def wait_for_log(self, regex, offset=1000, timeout=60):
        """Look for `regex` in the logs.

        We tail the stdout of the process and look for `regex`,
        starting from `offset` lines in the past. We fail if the
        timeout is exceeded or if the underlying process exits before
        the `regex` was found. The reason we start `offset` lines in
        the past is so that we can issue a command and not miss its
        effects.

        """
        logging.debug("Waiting for '%s' in the logs", regex)
        ex = re.compile(regex)
        start_time = time.time()
        pos = max(len(self.logs) - offset, 0)
        initial_pos = len(self.logs)
        while True:
            if time.time() > start_time + timeout:
                print("Can't find {} in logs".format(regex))
                with self.logs_cond:
                    for i in range(initial_pos, len(self.logs)):
                        print("  " + self.logs[i])
                if self.is_in_log(regex):
                    print("(Was previously in logs!")
                raise TimeoutError(
                    'Unable to find "{}" in logs.'.format(regex))
            elif not self.running:
                print('Logs: {}'.format(self.logs))
                raise ValueError('Process died while waiting for logs')

            with self.logs_cond:
                if pos >= len(self.logs):
                    self.logs_cond.wait(1)
                    continue

                if ex.search(self.logs[pos]):
                    logging.debug("Found '%s' in logs", regex)
                    return self.logs[pos]
                pos += 1


class BitcoinRpc(object):
    def __init__(self, url=None, rpcport=8332, rpcuser=None, rpcpassword=None):
        self.url = url if url else "http://localhost:{}".format(rpcport)
        authpair = "%s:%s" % (rpcuser, rpcpassword)
        authpair = authpair.encode('utf8')
        self.auth_header = b"Basic " + base64.b64encode(authpair)
        self.__id_count = 0

    def _call(self, service_name, *args):
        self.__id_count += 1

        r = requests.post(self.url,
                          data=json.dumps({
                              'version': '1.1',
                              'method': service_name,
                              'params': args,
                              'id': self.__id_count}),
                          headers={
                              # 'Host': self.__url.hostname,
                              'Authorization': self.auth_header,
                              'Content-type': 'application/json'
                          })

        response = r.json()
        if response['error'] is not None:
            raise ValueError(response['error'])
        elif 'result' not in response:
            raise ValueError({
                'code': -343, 'message': 'missing JSON-RPC result'})
        else:
            return response['result']

    def __getattr__(self, name):
        if name in self.__dict__:
            return self.__dict__[name]

        # Create a callable to do the actual call
        f = lambda *args: self._call(name, *args)

        # Make debuggers show <function bitcoin.rpc.name> rather than <function
        # bitcoin.rpc.<lambda>>
        f.__name__ = name
        return f


class BitcoinD(TailableProc):

    CONF_NAME = 'bitcoin.conf'

    def __init__(self, bitcoin_dir="/tmp/bitcoind-test", rpcport=None):
        super().__init__(bitcoin_dir, 'bitcoind')

        if rpcport is None:
            rpcport = reserve()

        self.bitcoin_dir = bitcoin_dir

        self.prefix = 'bitcoind'
        BITCOIND_CONFIG['rpcport'] = rpcport
        self.rpcport = rpcport

        regtestdir = os.path.join(bitcoin_dir, 'regtest')
        if not os.path.exists(regtestdir):
            os.makedirs(regtestdir)

        conf_file = os.path.join(bitcoin_dir, self.CONF_NAME)

        self.cmd_line = [
            'bitcoind',
            '-datadir={}'.format(bitcoin_dir),
            '-conf={}'.format(conf_file),
            '-regtest',
            '-logtimestamps',
            '-rpcport={}'.format(rpcport),
            '-printtoconsole=1'
            '-debug',
            '-rpcuser=rpcuser',
            '-rpcpassword=rpcpass',
            '-zmqpubrawblock=tcp://127.0.0.1:29000',
            '-zmqpubrawtx=tcp://127.0.0.1:29000',
        ]
        BITCOIND_CONFIG['rpcport'] = rpcport
        write_config(
            os.path.join(bitcoin_dir, self.CONF_NAME), BITCOIND_CONFIG)
        write_config(
            os.path.join(regtestdir, self.CONF_NAME), BITCOIND_CONFIG)
        self.rpc = BitcoinRpc(rpcport=rpcport, rpcuser='rpcuser', rpcpassword='rpcpass')

    def start(self):
        super().start()
        self.wait_for_log("Done loading", timeout=10)

        logging.info("BitcoinD started")


class BtcD(TailableProc):

    def __init__(self, btcdir="/tmp/btcd-test"):
        TailableProc.__init__(self, btcdir)

        self.cmd_line = [
            'btcd',
            '--regtest',
            '--rpcuser=rpcuser',
            '--rpcpass=rpcpass',
            '--connect=127.0.0.1',
            '--rpclisten=:18334',
        ]
        self.prefix = 'btcd'

    def start(self):
        TailableProc.start(self)
        self.wait_for_log("New valid peer 127.0.0.1:18444", timeout=10)

        logging.info("BtcD started")
