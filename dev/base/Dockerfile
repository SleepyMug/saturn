FROM debian:trixie

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        docker-cli \
        docker-compose \
        git \
        gosu \
        python3 \
        sudo \
        xz-utils \
	ripgrep \
	emacs-nox

RUN useradd --create-home --home-dir /home/guest --shell /bin/bash guest \
    && printf 'guest ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/guest \
    && chmod 0440 /etc/sudoers.d/guest

USER guest
WORKDIR /home/guest

RUN mkdir /home/guest/.local
RUN printf '%s\n' 'export PATH=/home/guest/.local/bin:$PATH' >> /home/guest/.bashrc
RUN curl -fsSL https://claude.ai/install.sh | bash

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
USER root
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["sleep", "infinity"]
