FROM ubuntu:bionic
WORKDIR /root

# Install bitcoind
RUN apt-get update \
  && apt-get install -y software-properties-common \
  && add-apt-repository ppa:bitcoin/bitcoin \
  && apt-get update \
  && apt-get install -y \
    build-essential \
    libtool \
    autotools-dev \
    automake \
    pkg-config \
    libssl-dev \
    libevent-dev \
    bsdmainutils \
    python3 \
    libboost-all-dev \
    miniupnpc \
    libzmq3-dev \
    libdb4.8-dev \
    libdb4.8++-dev \
    git
RUN git clone https://github.com/bitcoin/bitcoin.git \
  && cd bitcoin \
  && git checkout b641f60425674d737d77abd8c49929d953ea4154 \
  && ./autogen.sh \
  && ./configure \
  && make \
  && make install \
  && cd /root \
  && rm -rf bitcoin

# Install lightning-integration
RUN apt-get install -y \
  python3 \
  python3-pip \
  libsecp256k1-dev \
  && git clone https://github.com/cdecker/lightning-integration.git \
  && pip3 install -r lightning-integration/requirements.txt

WORKDIR /root/lightning-integration

# Install c-lightning dependencies
RUN apt-get install -y \
  autoconf \
  automake \
  build-essential \
  git \
  libtool \
  libgmp-dev \
  libsqlite3-dev \
  python \
  python3 \
  net-tools \
  zlib1g-dev \
  clang

# Install lnd dependencies
ENV GOPATH $HOME/.go
ENV PATH $PATH:$GOPATH/bin
RUN apt-get install -y golang

# Install eclair dependencies
RUN apt-get install -y \
  openjdk-8-jdk \
  maven \
  && update-alternatives --set java /usr/lib/jvm/java-8-openjdk-amd64/jre/bin/java

# Install ptarmigan dependencies
RUN apt-get install -y \
  git \
  autoconf \
  pkg-config \
  libcurl4-openssl-dev \
  libjansson-dev \
  libev-dev \
  libboost-all-dev \
  build-essential \
  libtool \
  jq \
  bc

RUN make clients

ENTRYPOINT ["py.test", "-v", "test.py"]
