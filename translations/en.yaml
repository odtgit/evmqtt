configuration:
  mqtt_host:
    name: MQTT Host
    description: Broker hostname or IP. Leave empty to use the MQTT broker Home Assistant provides (e.g. the Mosquitto add-on); the MQTT options below then override single values.
  mqtt_port:
    name: MQTT Port
    description: Broker port. Default 1883, or 8883 with TLS, or the port provided by Home Assistant.
  mqtt_username:
    name: MQTT Username
    description: Username for the broker. Leave empty to use the provided credentials.
  mqtt_password:
    name: MQTT Password
    description: Password for the broker.
  mqtt_tls:
    name: MQTT TLS
    description: Connect with TLS ("MQTTS").
  mqtt_tls_ca:
    name: MQTT TLS CA Certificate
    description: Path to a CA certificate file for TLS. Empty uses the system CA certificates.
  name:
    name: Gateway Name
    description: Name of the gateway device in Home Assistant.
  discovery_prefix:
    name: Discovery Prefix
    description: Home Assistant MQTT discovery prefix (default homeassistant).
  base_topic:
    name: Base Topic
    description: Topic for state, events and commands. Empty means evmqtt/<hostname>. Must not be under the discovery prefix.
  topic:
    name: Topic (deprecated)
    description: The 1.x topic. Only used to find and remove 1.x discovery, or as base topic if it is outside the discovery prefix. Use Base Topic instead.
  auto_discover:
    name: Auto Discover
    description: Select keyboard-like devices automatically (no mice, power buttons or virtual devices such as keyd). When off, only listed devices are used.
  filter_keys_only:
    name: Filter Keys Only (deprecated)
    description: Ignored in 2.0. List any other device under Input Devices.
  keystates:
    name: Key States
    description: Key states reported as events (PRESS, RELEASE, REPEAT).
  devices:
    name: Input Devices
    description: Extra devices to use, by stable id, path or name. Listed devices are used even if virtual or not keyboard-like, and are grabbed while enabled (auto-discovered devices are not). See the log or "evmqtt --list-devices" for ids.
  enabled_devices:
    name: Enabled Devices
    description: Devices enabled when first seen, by stable id, path or name; they are grabbed while enabled. Empty enables all. After that the switch in Home Assistant decides, and the state is kept across restarts.
  state_file:
    name: State File
    description: Where enable/disable state is stored. Default /data/evmqtt-state.json.
  rescan_interval:
    name: Rescan Interval
    description: Seconds between scans for plugged or unplugged devices. 0 disables hotplug.
  cleanup_legacy:
    name: Clean Up 1.x Entities
    description: On start, remove retained 1.x discovery messages (unique_id evmqtt_*) so old sensor and switch entities disappear.
  log_level:
    name: Log Level
    description: Logging verbosity.
