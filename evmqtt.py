#!/usr/bin/env python3

import os
import signal
import uuid
import threading
import sys
import time
import datetime
import subprocess
import paho.mqtt.client as mqtt
import evdev
import json
import getopt
from os.path import join, expanduser
from os import environ as env

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
        except OSError: pass

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    log("Connected with result code "+str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    #client.subscribe("topic")

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
     msgpayload = str(msg.payload)
     print(msg.topic+" "+msgpayload)

#class MQTTClient:
class MQTTClient(threading.Thread):

    def __init__(self, clientid, mqttcfg):
        super(MQTTClient, self).__init__()
        serverip = mqttcfg["mqtt"]["serverip"]
        port = mqttcfg["mqtt"]["port"]
        username = mqttcfg["mqtt"]["username"]
        password = mqttcfg["mqtt"]["password"]
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
        except getopt.GetoptError as e:
            pass

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

modifiers = ["KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_LEFTCTRL", "KEY_RIGHTCTRL"]
ignore = ["KEY_NUMLOCK"] # the number keys on the remote always set and unset numlock - this is superfluous for my use-case

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
#class InputMonitor:

    def __init__(self, mqttclient, device, topic):
        super(InputMonitor, self).__init__()
        self.mqttclient = mqttclient
        self.device = evdev.InputDevice(device)
        self.topic = topic
        log("Monitoring %s and sending to topic %s" % (device, topic))
        # TODO configuration publishing for discovery
        # self.mqttclient.publish('homeassistant/sensor/loungeremote/config', msg)
        # log("Configuration topic published")


    def run(self):
        global key_state

        # Grab the input device to avoid keypresses also going to th
        # Linux console (and attempting to login)
        #self.device.grab()

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

        mqttcfg = json.load(
                open("config.json")
                )

        myname = "EV_" + '_'.join(("%012X" % uuid.getnode())[i:i+2] for i in range(0, 12, 2))
        mq = MQTTClient(myname, mqttcfg)
        mq.start()

        # The MQTT topic, also use it in the HA sensor
        #topic = MQTTClient(topic, mqttcfg)

        # your event device, IR, whatever goes here
        im0 = InputMonitor(mq.mqttclient, "/dev/input/event3", "homeassistant/sensor/loungeremote/state")
        im0.start()

        # add more instances
        #im1 = InputMonitor(mq.mqttclient, "/dev/input/event1", "homeassistant/sensor/loungekbd/state")
        #im1.start()

    except getopt.GetoptError as e:
        log("Top level exception: %s" % str(e))

