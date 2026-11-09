FROM sentinelops-backend:local
USER root
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends openssh-server docker-cli && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd
RUN passwd -d root
COPY sshd_config /etc/ssh/sshd_config
COPY forced_command.py /forced_command.py
CMD ["/usr/sbin/sshd", "-D", "-e"]
