#!/usr/bin/env python3
"""
Linux input event to MQTT gateway
https://github.com/odtgit/evmqtt
"""

import os
import signal
import threading
import sys
import datetime
import json
from time import time
from platform import node as hostname
import evdev
import paho.mqtt.client as mqtt


def log(s):
    m = "[%s] %s\n" % (datetime.datetime.now(), s)
    sys.stderr.write(m + "\n")
    sys.stderr.flush()


class Watcher:

    def __init__(self):
        self.child = os.fork()
        if self.child == 0:
            return
        else:
            self.watch()

    def watch(self):
        try:
            os.wait()
        except KeyboardInterrupt:
            # I put the capital B in KeyBoardInterrupt so I can
            # tell when the Watcher gets the SIGINT
            print('KeyBoardInterrupt')
            self.kill()
        sys.exit()

    def kill(self):
        try:
            os.kill(self.child, signal.SIGKILL)
        except OSError:
            pass

# The callback for when the client receives a CONNACK response from the server.


def on_connect(client, userdata, flags, rc):
    log("Connected with result code " + str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("topic")

# The callback for when a PUBLISH message is received from the server.


def on_message(msg):
    msgpayload = str(msg.payload)
    print(msg.topic + " " + msgpayload)

# class MQTTClient:


class MQTTClient(threading.Thread):

    def __init__(self, clientid, mqttcfg):
        super(MQTTClient, self).__init__()
        serverip = mqttcfg["serverip"]
        port = mqttcfg["port"]
        username = mqttcfg["username"]
        password = mqttcfg["password"]
        log("MQTT connecting to %s:%u" % (serverip, port))
        self.mqttclient = mqtt.Client(clientid, protocol=mqtt.MQTTv31)
        self.mqttclient.username_pw_set(username, password)
        self.mqttclient.on_connect = on_connect
        self.mqttclient.on_message = on_message
        self.mqttclient.connect(serverip, port)
        self.connected = True
        log("MQTT connected %s:%u" % (serverip, port))

    def run(self):
        while self.connected:
            rc = self.mqttclient.loop(10)
            if rc == 7:
                log("MQTT attempting reconnect")
                self.mqttclient.reconnect()
        try:
            log("MQTT attempting disconnect")
            self.disconnect()
        except (OSError, KeyError):
            log("Exception: %s")
            return None

    def disconnect(self):
        log("Signalling disconnect to MQTT loop")
        self.connected = False


key_state = {}


def get_modifiers():
    global key_state
    ret = []
    for x in key_state.keys():
        if key_state[x] == 1:
            ret.append(x)
    ret.sort()
    if len(ret) == 0:
        return ""
    return "_" + "_".join(ret)


# the number keys on the remote always set and unset numlock - this is
# superfluous for my use-case
modifiers = [
    "KEY_LEFTSHIFT",
    "KEY_RIGHTSHIFT",
    "KEY_LEFTCTRL",
    "KEY_RIGHTCTRL"]
ignore = ["KEY_NUMLOCK"]


def set_modifier(keycode, keystate):
    global key_state, modifiers
    if keycode in modifiers:
        key_state[keycode] = keystate


def is_modifier(keycode):
    global modifiers
    if keycode in modifiers:
        return True
    return False


def is_ignore(keycode):
    global ignore
    if keycode in ignore:
        return True
    return False


class InputMonitor(threading.Thread):

    def __init__(self, mqttclient, device, topic):
        super(InputMonitor, self).__init__()
        self.mqttclient = mqttclient
        self.device = evdev.InputDevice(device)
        self.topic = topic
        log("Monitoring %s and sending to topic %s" % (device, topic))

    def run(self):
        global key_state

        # Grab the input device to avoid keypresses also going to th
        # Linux console (and attempting to login)
        self.device.grab()

        for event in self.device.read_loop():
            if event.type == evdev.ecodes.EV_KEY:
                k = evdev.categorize(event)
                set_modifier(k.keycode, k.keystate)
                if not is_modifier(k.keycode) and not is_ignore(k.keycode):
                    if k.keystate == 1:
                        msg = k.keycode + get_modifiers()
                        self.mqttclient.publish(self.topic, msg)
                        # log what we publish
                        log("Published message %s" % (msg))


if __name__ == "__main__":

    try:
        Watcher()

        MQTTCFG = json.load(
            open("config.json")
        )

        CLIENT = "evmqtt_{hostname}_{time}".format(
            hostname=hostname(), time=time()
        )

        MQ = MQTTClient(CLIENT, MQTTCFG)
        MQ.start()

        # your event key device, IR, whatever goes here
        IM0 = InputMonitor(
            MQ.mqttclient,
            "/dev/input/event4",
            "homeassistant/sensor/loungeremote/state"
        )
        IM0.start()

        # add more instances
        # IM1 = InputMonitor(
        #     MQ.mqttclient,
        #     "/dev/input/event1",
        #     "homeassistant/sensor/loungekbd/state"
        # )
        # IM1.start()

    except (OSError, KeyError):
        log("Exception: %s")
