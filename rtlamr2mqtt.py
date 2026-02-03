#!/usr/bin/env python3
"""
RTLAMR2MQTT Refactored Version
- Listen-only mode supported
- MQTT publishing encapsulated
- Config from YAML or JSON
- Graceful shutdown
"""

import os
import sys
import json
import yaml
import signal
import socket
import ssl
import logging
import subprocess
from time import sleep
from random import randrange
from typing import Dict, Optional, List
from paho.mqtt import publish, MQTTException

# --------------------------------------------------
# Logging
# --------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --------------------------------------------------
# Globals
# --------------------------------------------------
running_as_addon = bool(os.getenv("SUPERVISOR_TOKEN"))
running_in_listen_only_mode = False
external_rtl_tcp = False
mqtt_sender = None
rtltcp_proc = None
rtlamr_proc = None

# --------------------------------------------------
# MQTT Helper
# --------------------------------------------------
class MqttSender:
    def __init__(self, config: Dict):
        self.host = config.get('host', 'localhost')
        self.port = int(config.get('port', 1883))
        self.username = config.get('user', None)
        self.password = config.get('password', None)
        self.base_topic = config.get('base_topic', 'rtlamr')
        self.availability_topic = f"{self.base_topic}/status"
        tls_enabled = config.get('tls_enabled', False)
        cert_reqs = ssl.CERT_NONE if config.get('tls_insecure', True) else ssl.CERT_REQUIRED
        if tls_enabled:
            self.tls = {
                'ca_certs': config.get('tls_ca', '/etc/ssl/certs/ca-certificates.crt'),
                'certfile': config.get('tls_cert', None),
                'keyfile': config.get('tls_keyfile', None),
                'cert_reqs': cert_reqs
            }
        else:
            self.tls = None

    def publish(self, topic: str, payload: str, retain: bool = False, qos: int = 0) -> bool:
        auth = {'username': self.username, 'password': self.password} if self.username else None
        try:
            publish.single(
                topic=topic,
                payload=payload,
                retain=retain,
                qos=qos,
                hostname=self.host,
                port=self.port,
                auth=auth,
                tls=self.tls
            )
            logging.debug(f"MQTT published: {topic} -> {payload}")
            return True
        except MQTTException as e:
            logging.error(f"MQTT error: {e}")
            return False
        except Exception as e:
            logging.error(f"Unknown MQTT error: {e}")
            return False

# --------------------------------------------------
# Config loading
# --------------------------------------------------
def load_config(path: Optional[str] = None) -> Dict:
    if path is None:
        for p in ["/data/options.json", "/etc/rtlamr2mqtt.yaml"]:
            if os.path.exists(p):
                path = p
                break
        if path is None:
            logging.error("No config file found. Exiting.")
            sys.exit(1)

    logging.info(f"Using config: {path}")

    if path.endswith(('.yaml', '.yml')):
        with open(path, 'r') as f:
            cfg = yaml.safe_load(f)
    elif path.endswith(('.json', '.js')):
        with open(path, 'r') as f:
            cfg = json.load(f)
    else:
        logging.error("Unsupported config format")
        sys.exit(1)

    # Defaults
    defaults = {
        'general': {'sleep_for': 0, 'verbosity': 'info', 'listen_only': False, 'tickle_rtl_tcp': False, 'device_id': 'single', 'rtltcp_server': '127.0.0.1:1234'},
        'mqtt': {'host': None, 'user': None, 'password': None, 'tls_enabled': False, 'tls_insecure': True, 'base_topic': 'rtlamr', 'ha_autodiscovery': True, 'ha_autodiscovery_topic': 'homeassistant'},
        'custom_parameters': {'rtltcp': "-s 2048000", 'rtlamr': "-unique=true"}
    }

    # Merge
    for section, default_vals in defaults.items():
        if section not in cfg:
            cfg[section] = default_vals
        else:
            for k, v in default_vals.items():
                cfg[section].setdefault(k, v)

    return cfg

# --------------------------------------------------
# Signal / Shutdown
# --------------------------------------------------
def shutdown(signum=0, frame=0):
    logging.info("Shutting down...")
    global rtltcp_proc, rtlamr_proc, mqtt_sender
    for proc in [rtltcp_proc, rtlamr_proc]:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    if mqtt_sender:
        mqtt_sender.publish(topic=mqtt_sender.availability_topic, payload='offline', retain=True)
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

# --------------------------------------------------
# Listen-only mode
# --------------------------------------------------
def listen_mode(cfg: Dict):
    global rtlamr_proc, mqtt_sender
    logging.info("Starting in LISTEN ONLY mode...")
    mqtt_sender = MqttSender(cfg['mqtt']) if running_as_addon else None

    rtlamr_cmd = ['/usr/bin/rtlamr', '-format=json']
    if 'msgtype' in os.environ:
        rtlamr_cmd.append(f"-msgtype={os.environ['msgtype']}")

    rtlamr_cmd.extend(os.environ.get('RTLAMR_ARGS', '').split())

    rtlamr_proc = subprocess.Popen(rtlamr_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

    for line in rtlamr_proc.stdout:
        line = line.strip()
        if not line:
            continue
        logging.info(line)
        try:
            data = json.loads(line)
            if mqtt_sender:
                mqtt_sender.publish(topic=f"{cfg['mqtt']['base_topic']}/debug", payload=json.dumps(data))
        except json.JSONDecodeError:
            continue

# --------------------------------------------------
# Main
# --------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", help="Path to YAML or JSON config")
    args = parser.parse_args()

    cfg = load_config(args.config)
    running_in_listen_only_mode = cfg['general'].get('listen_only', False) or os.environ.get('LISTEN_ONLY', '').lower() in ['yes','true']

    if running_in_listen_only_mode:
        listen_mode(cfg)
    else:
        logging.info("Normal mode is not yet fully implemented in this refactor.")
        # Here you could add your meter filtering, RTL_TCP + RTLAMR subprocesses, and MQTT publishing
