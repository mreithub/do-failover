#!/usr/bin/env python3
#
# checks a main/standby server's availability and takes control of the DigitalOcean floatingIP if necessary
#
# uses environment variables for configuration:
# - API_KEY: digitalocean.com API key
# - FAILOVER_MODE: 'main' or 'standby' (or disabled if not set)
# - FLOATING_IP: the IP that's been shared by the main and standby server
# - FAILOVER_CHECK: local URL(s) to check to determine the health of this server (split by '|')
# - FAILOVER_MAIN: set the base URL(s) of the main server (if we're on the standby server) (URLs are split by '|')
# - FAILOVER_MAIN_HOST (optional): Set the 'Host' header for requests to FAILOVER_MAIN URLs (allows to ignore DNS while still checking the validity of SSL certs)

import json
import logging
import os
import sys
import threading
import time
import urllib.request

class Watchdog(threading.Thread):
	""" Runs the given callback function unless you call `kick()` at least once every `timeout` seconds """
	def __init__(self, timeout, callback):
		""" Watchdog constructor
		- timeout: number of seconds without receiving a `kick()` before invoking `callback`
		- callback: function to call after `timeout` seconds without a `kick()`"""
		threading.Thread.__init__(self)
		self._kick = threading.Event()
		self._stop = threading.Event()
		self.timeout = timeout
		self.callback = callback

	def kick(self):
		""" Kick the watchdog, resettings its timeout """
		self._kick.set()

	def run(self):
		""" internal Thread run() method. Use start() instead! """
		while not self._stop.is_set():
			if self._kick.wait(self.timeout):
				# no timeout -> reset flag
				self._kick.clear()
			else:
				self.callback()

	def start(self):
		""" Starts the watchdog """
		self._kick.clear()
		self._stop.clear()
		threading.Thread.start(self)

	def stop(self):
		""" Stops the watchdog (and its background Thread) """
		self._stop.set()
		self._kick.set()

def _item(dict_, *keys, default=None):
	""" Get dictionary value recursively (or 'default' if not found) """
	#print('_item({0}, {1}, {2})'.format(dict_, default, keys))
	if dict_ == None:
		return default
	if len(keys) == 0:
		return dict_
	if type(dict_) == list:
		if len(dict_) <= keys[0]:
			return default
		return _item(dict_[keys[0]], *(keys[1:]), default=default)
	elif keys[0] not in dict_:
		return default
	else:
		return _item(dict_[keys[0]], *(keys[1:]), default=default)


def _get(url, hostname=None):
	req = urllib.request.Request(url)
	if hostname != None:
		req.add_header('Host', hostname)
	resp = urllib.request.urlopen(req, timeout=20)

	if resp.status != 200:
		raise Exception("Failed to GET '{0}' (code {1})".format(url, resp.status))

	return resp.read()

def checkService(*urls, hostname=None):
	try:
		for url in urls:
			resp = _get(url, hostname=hostname)
			# TODO find a clean way to check if the service is actually healthy
		return True
	except urllib.request.HTTPError as e:
		logging.error("checkService failed (code {0}): {1}".format(e.status, url))
		return False
	except Exception as e:
		logging.error("checkService failed (url: '{0}'): {1}".format(url, str(e)))
		return False

def getDropletID():
	""" Requests this droplet's ID (or fail with an Exception) """
	return getMetadata()['droplet_id']

def getMetadata(cache=True):
	""" Queries this droplet's metadata (and caches the result) """
	global _metadata
	if _metadata != None and cache:
		return _metadata

	_metadata = json.loads(_get('http://169.254.169.254/metadata/v1.json').decode('utf8'))

	return _metadata

def hasFloatingIP(ip, apiKey):
	""" Returns True if this droplet owns the floatingIP in question """
	# even though there's a 'floating_ip' section in getMetadata() I've observed a case where
	# that returned 'active' for both droplets in question. That's why we're using the (slower, but safe)
	# api.digitalocean.com here
	dropletId = getDropletID()
	req = urllib.request.Request('https://api.digitalocean.com/v2/floating_ips/{0}'.format(ip))
	req.add_header('Authorization', 'Bearer {0}'.format(apiKey))
	resp = urllib.request.urlopen(req, timeout=20)

	data = json.loads(resp.read().decode('utf8'))
	return dropletId == _item(data, 'floating_ip', 'droplet', 'id')


def takeFloatingIP(floatingIP, apiKey):
	""" Takes control of the given floating IP """
	dropletId = getDropletID()
	req = urllib.request.Request("https://api.digitalocean.com/v2/floating_ips/{0}/actions".format(floatingIP))
	req.add_header('Content-type', 'application/json')
	req.add_header('Authorization', 'Bearer {0}'.format(apiKey))
	resp = urllib.request.urlopen(req, json.dumps({'type': 'assign', 'droplet_id': dropletId}).encode('utf8'), timeout=20)

	if resp.status not in [200, 201]:
		logging.error('response body: {0}'.format(resp.read()))
		raise Exception("Failed to acquire floating IP (code {0})".format(resp.status))
	else:
		logging.info("Acquired the floating IP {0}".format(floatingIP))


def main():
	global watchdog

	mode = _item(os.environ, 'FAILOVER_MODE')
	apiKey = _item(os.environ, 'API_KEY')
	floatingIP = _item(os.environ, 'FLOATING_IP')
	checkURLs = _item(os.environ, 'FAILOVER_CHECK')
	mainURLs = _item(os.environ, 'FAILOVER_MAIN')
	mainHost = _item(os.environ, 'FAILOVER_MAIN_HOST')

	if mode == None:
		logging.info("Not set up for automatic failover, exiting")
		return 0

	if apiKey == None:
		raise Exception("Missing 'API_KEY'!")
	if floatingIP == None:
		raise Exception("Missing 'FLOATING_IP'!")


	if mode == 'main' and mainURLs != None:
		checkURLs = mainURLs

	if checkURLs == None:
		raise Exception("Missing 'FAILOVER_CHECK' url(s)!")
	checkURLs = checkURLs.split('|')
	if type(mainURLs) == str:
		mainURLs = mainURLs.split('|')

	watchdog.start()

	try:
		while True:
			watchdog.kick()
			if checkService(*checkURLs):
				if mode == 'main':
					if not hasFloatingIP(floatingIP, apiKey):
						logging.info("Lost control of the floating IP, getting it back")

						takeFloatingIP(floatingIP, apiKey)
					else:
						logging.debug("We're in control of the floating IP {0}".format(floatingIP))

				elif mode == 'standby':
					if not hasFloatingIP(floatingIP, apiKey):
						if mainURLs == None:
							raise Exception("Missing 'FAILOVER_MAIN' url(s)!")
						if not checkService(*mainURLs, hostname=mainHost):
							logging.warn("MAIN SERVER IS DOWN, TAKING OVER!")
							takeFloatingIP(floatingIP, apiKey)
						else:
							logging.debug("Main server up and running")

					else:
						logging.debug("We're in control of the floating IP {0}".format(floatingIP))

				else:
					raise Exception("Unsupported failover mode! Expected 'main' or 'standby'")
			else:
				logging.error('Service not running!')

			time.sleep(60)
	finally:
		watchdog.stop()

def onWatchdogTimeout():
	logging.fatal("Watchdog timeout!")
	sys.exit(1)

_metadata = None
watchdog = Watchdog(180, onWatchdogTimeout)

if __name__ == '__main__':
	logging.basicConfig(level=logging.DEBUG)
	main()
