# Very simple Linux input event to MQTT gateway

This program allows you to capture linux input events via [evdev][https://python-evdev.readthedocs.io/en/latest/]
and publish them to an MQTT broker.

Based on the [original][https://gist.github.com/jamesbulpin/b940e7d81e2e65158f12e59b4d6a0c3c] implementation by James Bulpin.

## Installation

Get the repo, install python3-pip, install prerequisites with

``` bash
git clone https://github.com/odtgit/evmqtt
sudo apt install python3-pip
pip3 install paho-mqtt evdev
```

## Configuration

Broker config goes in ~/.config/config_mqtt.json as shown below 

``` json
{
  "mqtt":{
    "serverip":"127.0.0.1",
    "port":1883,
    "username":"user",
    "password":"pwd",
    "protocol":{
      "protocolId":"MQIsdp",
      "protocolVersion":3
    }
  }
}
```

Modify these lines at the bottom of evmqtt.py to suit your needs and add more instances if you want

``` python
im0 = InputMonitor(mq.mqttclient, "/dev/input/event3", "homeassistant/sensor/loungeremote/state")
im0.start()
```

## Usage

In the evmqtt.service file change the path to the script and the executing user. Copy it to /etc/systemd/systemd and run with

``` bash
sudo systemctl start evmqtt
sudo systemctl enable evmqtt
```


## Integration with Home Assistant

Example config for HA configuration.yaml to get a sensor

``` yaml
sensor:
  - platform: mqtt
    name: loungeremote
    state_topic: "homeassistant/sensor/loungeremote/state"
```

Example automation based on MQTT Event (quickest, and registers each message if you press same key)
I've found this works best with toggle

``` yaml
- id: loungeremote MQTT KEY_1
  alias: loungeremote MQTT KEY_1
  trigger:
  - payload: KEY_1
    platform: mqtt
    topic: homeassistant/sensor/loungeremote/state
  condition: []
  action:
  - data:
      entity_id: switch.lounge_tv
    service: switch.toggle
```
