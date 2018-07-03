from bitcoin.rpc import RawProxy as BitcoinProxy

import logging
import re
import subprocess
import threading
import time
import os
import collections

BITCOIND_CONFIG = collections.OrderedDict([
    ("rpcuser", "rpcuser"),
    ("rpcpassword", "rpcpass"),
    ("printtoconsole", 1),
    ("server", 1),
    ("regtest", 1),
    ("txindex", 1),
    ("zmqpubrawblock", "tcp://127.0.0.1:29000"),
    ("zmqpubrawtx", "tcp://127.0.0.1:29000"),
    ("deprecatedrpc", "addwitnessaddress"),
    ("addresstype", "p2sh-segwit"),
    ("deprecatedrpc", "signrawtransaction"),
    ("regtest", {
        "rpcport": 28332,
    }),
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

    def stop(self):
        self.proc.terminate()
        self.proc.kill()
        if self.outputDir:
            logpath = os.path.join(self.outputDir, 'log')
            with open(logpath, 'w') as f:
                for l in self.logs:
                    f.write(l + '\n')

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


class SimpleBitcoinProxy:
    """Wrapper for BitcoinProxy to reconnect.

    Long wait times between calls to the Bitcoin RPC could result in
    `bitcoind` closing the connection, so here we just create
    throwaway connections. This is easier than to reach into the RPC
    library to close, reopen and reauth upon failure.
    """
    def __init__(self, conf_file=None):
        self.conf_file = conf_file

    def __getattr__(self, name):
        if name.startswith('__') and name.endswith('__'):
            # Python internal stuff
            raise AttributeError

        # Create a callable to do the actual call
        def callback(*args):
            return BitcoinProxy(btc_conf_file=self.conf_file)._call(name, *args)

        # Make debuggers show <function bitcoin.rpc.name> rather than <function
        # bitcoin.rpc.<lambda>>
        callback.__name__ = name
        return callback


class BitcoinD(TailableProc):

    CONF_NAME = 'bitcoin.conf'

    def __init__(self, bitcoin_dir="/tmp/bitcoind-test", rpcport=18332):
        super().__init__(bitcoin_dir, 'bitcoind')

        self.bitcoin_dir = bitcoin_dir
        self.rpcport = rpcport
        self.prefix = 'bitcoind'

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
        ]
        BITCOIND_CONFIG['rpcport'] = rpcport
        write_config(
            os.path.join(bitcoin_dir, self.CONF_NAME), BITCOIND_CONFIG)
        write_config(
            os.path.join(regtestdir, self.CONF_NAME), BITCOIND_CONFIG)
        self.rpc = SimpleBitcoinProxy(conf_file=conf_file)

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
