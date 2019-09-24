FROM ubuntu:bionic

RUN apt-get update \
  && apt-get install -y software-properties-common \
  && add-apt-repository ppa:bitcoin/bitcoin \
  && apt-get update \
  && apt-get install -y \
    autoconf \
    automake \
    autotools-dev \
    bc \
    bsdmainutils \
    build-essential \
    clang \
    curl \
    gettext \
    git \
    jq \
    libboost-all-dev \
    wget \
    libcurl4-openssl-dev \
    libdb4.8++-dev \
    libdb4.8-dev \
    libev-dev \
    libevent-dev \
    libgmp-dev \
    libjansson-dev \
    libsecp256k1-dev \
    libsodium-dev \
    libsqlite3-dev \
    libssl-dev \
    libtool \
    libzmq3-dev \
    miniupnpc \
    net-tools \
    openjdk-11-jdk \
    pkg-config \
    python \
    python3 \
    python3-mako \
    python3-pip \
    zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

ARG BITCOIN_VERSION=0.17.1
ENV BITCOIN_TARBALL bitcoin-$BITCOIN_VERSION-x86_64-linux-gnu.tar.gz
ENV BITCOIN_URL https://bitcoincore.org/bin/bitcoin-core-$BITCOIN_VERSION/$BITCOIN_TARBALL
ENV BITCOIN_ASC_URL https://bitcoincore.org/bin/bitcoin-core-$BITCOIN_VERSION/SHA256SUMS.asc
ENV BITCOIN_PGP_KEY 01EA5486DE18A882D4C2684590C8019E36C2E964

RUN cd /tmp \
    && wget -qO $BITCOIN_TARBALL "$BITCOIN_URL" \
    && gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys "$BITCOIN_PGP_KEY" \
    && wget -qO bitcoin.asc "$BITCOIN_ASC_URL" \
    && gpg --verify bitcoin.asc \
    && grep $BITCOIN_TARBALL bitcoin.asc | tee SHA256SUMS.asc \
    && sha256sum -c SHA256SUMS.asc \
    && BD=bitcoin-$BITCOIN_VERSION/bin \
    && tar -xzvf $BITCOIN_TARBALL \
    && cp bitcoin-$BITCOIN_VERSION/bin/bitcoin* /usr/bin/ \
    && rm -rf $BITCOIN_TARBALL bitcoin-$BITCOIN_VERSION

# maven for java builds (eclair)
RUN cd /tmp \
    && wget -qO mvn.tar.gz https://www-us.apache.org/dist/maven/maven-3/3.6.2/binaries/apache-maven-3.6.2-bin.tar.gz \
    && tar -xzf mvn.tar.gz \
    && rm mvn.tar.gz \
    && mv apache-maven-3.6.2 /usr/local/maven \
    && ln -s /usr/local/maven/bin/mvn /usr/local/bin

RUN cd /tmp \
    && wget -q https://dl.google.com/go/go1.12.7.linux-amd64.tar.gz \
    && tar -xf go1.12.7.linux-amd64.tar.gz \
    && mv go /usr/local \
    && rm go1.12.7.linux-amd64.tar.gz \
    && ln -s /usr/local/go/bin/go /usr/bin/

ENV GOROOT=/usr/local/go

VOLUME /root/lightning-integration/reports
VOLUME /root/lightning-integration/output

WORKDIR /root/lightning-integration

# lightning-integration
COPY requirements.txt /root/lightning-integration/requirements.txt
RUN ln -sf /usr/bin/python3 /usr/bin/python \
  && ln -sf /usr/bin/pip3 /usr/bin/pip \
  && pip install -r /root/lightning-integration/requirements.txt

ENV LC_ALL C.UTF-8
ENV LANG C.UTF-8
ENV TEST_DEBUG=0

COPY . /root/lightning-integration/
CMD ["make", "update", "clients", "test"]
