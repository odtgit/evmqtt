 Very simple Linux input event to MQTT gateway
 based on https://gist.github.com/jamesbulpin/b940e7d81e2e65158f12e59b4d6a0c3c
 To install do first pip3 install paho-mqtt evdev

Broker config goes in ~/.config/config_mqtt.json as

```
{
  "mqtt":{
    "serverip":"127.0.0.1",
    "port":1883,
    "username":"user",
    "password":"pwd",
    "topic":"homeassistant/sensor/loungeremote/state",
    "protocol":{
      "protocolId":"MQIsdp",
      "protocolVersion":3
    }
  }
}
```

Example config for HA configuration.yaml to get a sensor

```
sensor:
  - platform: mqtt
    name: loungeremote
    state_topic: "homeassistant/sensor/loungeremote/state"
```

Example automation based on MQTT Event (quickest, and registers each message if you press same key)
I've found this works best with toggle

```
- id: loungeremote MQTT KEY_1
  alias: loungeremote MQTT KEY_1
  trigger:
  - payload: KEY_1
    platform: mqtt
    topic: homeassistant/sensor/lounge
  condition: []
  action:
  - data:
      entity_id: switch.lounge_tv
    service: switch.toggle
```
