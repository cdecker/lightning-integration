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
    git \
    golang \
    jq \
    libboost-all-dev \
    libcurl4-openssl-dev \
    libdb4.8++-dev \
    libdb4.8-dev \
    libev-dev \
    libevent-dev \
    libgmp-dev \
    libjansson-dev \
    libsecp256k1-dev \
    libsqlite3-dev \
    libssl-dev \
    libtool \
    libzmq3-dev \
    maven \
    miniupnpc \
    net-tools \
    openjdk-8-jdk \
    pkg-config \
    python3-pip \
    python3 \
    python \
    zlib1g-dev

# bitcoind
RUN curl -Lo bitcoin.tar.gz https://github.com/bitcoin/bitcoin/archive/b641f60425674d737d77abd8c49929d953ea4154.tar.gz \
  && tar -xzf bitcoin.tar.gz \
  && cd bitcoin-* \
  && ./autogen.sh \
  && ./configure \
  && make \
  && make install \
  && cd /root \
  && rm -rf bitcoin*

# lightning-integration
RUN git clone https://github.com/cdecker/lightning-integration.git /root/lightning-integration \
  && pip3 install -r /root/lightning-integration/requirements.txt

# lnd
ENV GOPATH $HOME/.go
ENV PATH $PATH:$GOPATH/bin

# eclair
RUN update-alternatives --set java /usr/lib/jvm/java-8-openjdk-amd64/jre/bin/java

WORKDIR /root/lightning-integration
CMD ["make", "update", "clients", "test"]
