# Very simple Linux input event to MQTT gateway

This program allows you to capture linux input events via [evdev](https://python-evdev.readthedocs.io/en/latest)
and publish them to an MQTT broker. This can for example be used to turn IR button presses to triggers in Home Assistant.

Based on the original [gist](https://gist.github.com/jamesbulpin/b940e7d81e2e65158f12e59b4d6a0c3c) by James Bulpin.

As of the latest merge the payload will be sent as JSON with 2 attributes, key and devicePath of the generator.

## Installation as a service

Get the repo, install python3-pip, install prerequisites with:

```bash
git clone https://github.com/odtgit/evmqtt
sudo apt install python3-pip
pip3 install paho-mqtt evdev
```

## Configuration

Check out config.json and modify for your requirements. The topic will be expanded automatically to send to /state for
payload and /config for autodiscovery.

## Run in a docker container

Very simple to build and run using docker as shown

```bash
docker build . -t evmqtt
docker run -d --network host --device=/dev/input/event3 --name evmqtt <image_id>
```


## Run as a service

In the evmqtt.service file change the path to the script and the executing user. Copy it to /etc/systemd/system and run with

```bash
sudo systemctl enable evmqtt
sudo systemctl start evmqtt
```


## Integration with Home Assistant

MQTT autodiscovery should take care of adding a sensor to HA. By default this is called sensor.event_gateway_mqtt
You can still create automation based on MQTT messages directly. I've found this works best with toggle service.
Or you can create a flow like this in Node-Red

![](nodered.png?raw=true)

