#!/bin/bash -ue

#TEST_DEBUG=1 \
py.test -v test.py -s -k 'test_forwarded_payment[eclair_lightning_eclair]'

#py.test -v test.py -s -k 'test_forwarded_payment[lnd_lightning_eclair]'

#py.test -v test.py -s -k 'test_start' # PASSED
#py.test -v test.py -s -k 'test_connect' # PASSED
#py.test -v test.py -s  -k 'test_direct_payment' # PASSED
#py.test -v test.py -s -k 'test_gossip[ucoind_ucoind]' # PASSED
#py.test -v test.py -s -k 'test_reconnect'

#py.test -v test.py -s  -k 'test_forwarded_payment[eclair_lightning_ucoind]'
#py.test -v test.py -s -k 'test_forwarded_payment[ucoind_ucoind_ucoind]' --pdb

