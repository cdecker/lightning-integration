

GOPATH = $(shell pwd)/src/lnd
PWD = $(shell pwd)

src/eclair:
	git clone https://github.com/ACINQ/eclair.git src/eclair

src/lightning:
	git clone https://github.com/ElementsProject/lightning.git src/lightning

src/lnd:
	git clone https://github.com/lightningnetwork/lnd ${GOPATH}/src/github.com/lightningnetwork/lnd

update: src/eclair src/lightning src/lnd
	rm src/eclair/version src/lightning/version src/lnd/version || true

	cd src/eclair && git stash; git pull origin master
	cd src/lightning && git stash; git pull origin master
	cd ${GOPATH}/src/github.com/lightningnetwork/lnd && git stash; git pull origin master

	#cd src/eclair; git apply ${PWD}/src/eclair/*.patch

bin/eclair.jar: src/eclair
	(cd src/eclair; git rev-parse HEAD) > src/eclair/version
	(cd src/eclair/; mvn package -Dmaven.test.skip=true || true)
	cp src/eclair/eclair-node/target/eclair-node-*-$(shell git --git-dir=src/eclair/.git rev-parse HEAD | cut -b 1-7).jar bin/eclair.jar

bin/lightningd: src/lightning
	(cd src/lightning; git rev-parse HEAD) > src/lightning/version
	cd src/lightning; make
	cp src/lightning/lightningd/lightningd src/lightning/lightningd/lightning_* bin

bin/lnd: src/lnd
	(cd src/lnd; git rev-parse HEAD) > src/lnd/version
	go get -u github.com/golang/dep/cmd/dep
	cd ${GOPATH}/src/github.com/lightningnetwork/lnd; ${GOPATH}/bin/dep ensure; go install . ./cmd/...
	cp ${GOPATH}/bin/lnd ${GOPATH}/bin/lncli bin/

clean:
	rm src/lnd/version src/lightning/version src/eclair/version || true
	rm bin/* || true
	cd src/lightning; make clean
	cd src/eclair; mvn clean

clients: bin/lightningd bin/lnd bin/eclair.jar

test:
	# Failure is always an option
	py.test -v test.py --tb=short --color=yes --json=report.json || true
	python cli.py postprocess

site:
	rm -rf output; rm templates/*.json || true
	mkdir output
	cp reports/* templates/
	python cli.py html

push:
	cd output; \
	git init;\
	git config user.name "Travis CI";\
	git config user.email "decker.christian+travis@gmail.com";\
	git add .;\
	git commit --quiet -m "Deploy to GitHub Pages";\
	git push --force "git@github.com:cdecker/lightning-integration.git" master:gh-pages
