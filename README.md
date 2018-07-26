# Lightning Integration Testing Framework

## Setup

The tests are written as py.test unit tests.
This facilitates the handling of fixtures and allows to instrument tests nicely, e.g., write a test once and run it against various combinations of clients.
To install the python dependencies and `bitcoind` use the following:

    apt-get install bitcoind python3 python3-pip
    pip3 install -r requirements.txt

We suggest running this in a virtualenv in order to guard against changing dependencies.

We currently do not bundle the binaries that we test against.
In order for the tests to run you'll need to compile the various clients and move/link the binaries into the directories where the tests can find them.
Please refer to the various compilation instructions and make sure that the structure matches the following diagram:

    bin
    ├── eclair-node.jar
    ├── lightningd
    ├── lnd
    └── ptarmd

The binaries for `lightningd`, `lnd` and [`ptarmd`](https://github.com/nayutaco/ptarmigan) should be pretty self-explanatory.
The binary artifact for `eclair` is the `jar`-file containing all dependencies for the `eclair-node` subproject.
It is usually called `eclair-node_X.Y.Z-SNAPSHOT-XXXXXX-capsule-fat.jar` and can be found in the `target` subdirectory.

## Running the tests

The tests rely on py.test to create fixtures, wire them into the tests and run the tests themselves.
Execute all tests by running

    py.test -v test.py

This will run through all possible combinations of the implementations and report success/failure for each one.

[![asciicast](https://asciinema.org/a/126309.png)](https://asciinema.org/a/126309)

To run only tests involving a certain implementation you can also run the following (taking `lightningd` as an example):

    py.test -v test.py -k LightningNode

Not sure where a test dies? Make the whole thing extremely verbose with this:

    TEST_DEBUG=1 py.test -v test.py -s -k 'testConnect[EclairNode_LightningNode]'

Should you want to jump into an interactive session if something is about to fail run the following:

    py.test -v test.py --pdb

This will run the tests until a failure would be recorded and start the python debugging console instead.
In the console you have a python REPL that has access to the context of the current test, and you can interact with the clients via the RPCs to gather more information about what is going wrong.

## Workarounds

The following changes to the default configuration are used to ensure compatibility. Possibly the default configurations should be compatible, but that is not always possible to do in a timely fashion.

 - c-lightning:
   - `--cltv-final` is set to 8 (while default is 6) because of the final hop delta enforcement by eclair (see issue #16)
 - lnd
   - `--bitcoin.defaultremotedelay=144` since c-lightning will not allow large `to_self_delay`s (see lightningnetwork/lnd#788 and ElementsProject/lightning#1110)
