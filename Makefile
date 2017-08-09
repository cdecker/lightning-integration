

GOPATH = $(shell pwd)/src/lnd
PWD = $(shell pwd)

src/eclair:
	git clone https://github.com/ACINQ/eclair.git src/eclair

src/eclair/version: src/eclair
	cd src/eclair; git pull origin master
	if [ "x$(shell git --git-dir=src/eclair/.git rev-parse HEAD)" != "x$(shell cat src/eclair/version)" ]; then (cd src/eclair; git rev-parse HEAD) > src/eclair/version; fi

src/eclair/patched: src/eclair/version
	cd src/eclair; git pull origin master; git reset --hard; git apply ${PWD}/src/eclair/*.patch
	touch src/eclair/patched

bin/eclair.jar: src/eclair/version src/eclair/patched
	(cd src/eclair/; mvn package -pl eclair-node -am -Dmaven.test.skip=true)
	cp src/eclair/eclair-node/target/eclair-node_2.11-0.2-SNAPSHOT-$(shell cat src/eclair/version | cut -b 1-7)-capsule-fat.jar bin/eclair.jar

src/lightning:
	git clone https://github.com/ElementsProject/lightning.git src/lightning

src/lightning/version: src/lightning
	cd src/lightning; git pull origin master
	if [ "x$(shell git --git-dir=src/lightning/.git rev-parse HEAD)" != "x$(shell cat src/lightning/version)" ]; then (cd src/lightning; git rev-parse HEAD) > src/lightning/version; fi

bin/lightningd: src/lightning/version
	cd src/lightning; make
	cp src/lightning/lightningd/lightningd src/lightning/lightningd/lightningd_* bin

src/lnd:
	git clone https://github.com/lightningnetwork/lnd ${GOPATH}/src/github.com/lightningnetwork/lnd

src/lnd/version: src/lnd
	cd ${GOPATH}/src/github.com/lightningnetwork/lnd; git pull
	(cd ${GOPATH}/src/github.com/lightningnetwork/lnd; git rev-parse HEAD | cut -b 1-6) > lnd-version
	if [ "x$(shell git --git-dir=${GOPATH}/src/github.com/lightningnetwork/lnd/.git rev-parse HEAD)" != "x$(shell cat src/lnd/version)" ]; then (cd ${GOPATH}/src/github.com/lightningnetwork/lnd; git rev-parse HEAD) > src/lnd/version; fi

src/lnd/patched: src/lnd/version
	cd ${GOPATH}/src/github.com/lightningnetwork/lnd; git reset --hard; git apply ${PWD}/src/lnd/*.patch
	touch src/eclair/patched

bin/lnd: src/lnd/version src/lnd/patched
	cd ${GOPATH}/src/github.com/lightningnetwork/lnd; glide install; go install . ./cmd/...
	cp ${GOPATH}/bin/lnd ${GOPATH}/bin/lncli bin/

clean:
	rm src/lnd/version src/lightning/version src/eclair/version || true
	rm bin/* || true
	cd src/lightning; make clean
	cd src/eclair; mvn clean

all: bin/lightningd bin/lnd bin/eclair.jar

test:
	TEST_DEBUG=0 py.test -v test.py --tb=short --color=yes --json=report.json
