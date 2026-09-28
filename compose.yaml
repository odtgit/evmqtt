services:
  evmqtt:
    hostname: evmqtt
    container_name: evmqtt
    build:
      context: .
    image: odtdock/evmqtt
    restart: unless-stopped
    volumes:
      - "./config.json:/data/config.json:ro"
      - "evmqtt-state:/var/lib/evmqtt"
      - "/dev/input:/dev/input:ro"
      - "/etc/localtime:/etc/localtime:ro"
    environment:
      TZ: Europe/Stockholm
      STATE_DIRECTORY: /var/lib/evmqtt
    # Every input device, hotplug included. Only devices listed in devices or
    # enabled_devices are grabbed; see README, Device selection.
    device_cgroup_rules:
      - "c 13:* rw"
    network_mode: host

volumes:
  evmqtt-state:
