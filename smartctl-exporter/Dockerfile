FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends     smartmontools     ca-certificates     && rm -rf /var/lib/apt/lists/*

COPY smartctl_exporter /bin/smartctl_exporter

EXPOSE 9633
ENTRYPOINT ["/bin/smartctl_exporter"]
