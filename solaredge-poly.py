#!/usr/bin/env python3

import polyinterface
import sys
import http.client
import datetime
import time
import logging
import json

LOGGER = polyinterface.LOGGER
SE_API_URL = 'monitoringapi.solaredge.com'
SINGLE_PHASE = [ 'SE3000', 'SE3800', 'SE5000', 'SE6000', 'SE7600', 'SE10000', 'SE11400' ]
THREE_PHASE = [ 'SE9K', 'SE10K', 'SE14.4K', 'SE20K', 'SE33.3K' ]


class Controller(polyinterface.Controller):
    def __init__(self, polyglot):
        super().__init__(polyglot)
        self.name = 'SolarEdge Controller'
        self.address = 'sectrl'
        self.primary = self.address
        self.api_key = None
        self.conn = None
        self.site_offset = 0

    def start(self):
        # LOGGER.setLevel(logging.INFO)
        LOGGER.info('Started SolarEdge controller')
        if 'site_offset' in self.polyConfig['customParams']:
            self.site_offset = int(self.polyConfig['customParams']['site_offset'])
        if 'api_key' not in self.polyConfig['customParams']:
            LOGGER.error('Please specify api_key in the NodeServer configuration parameters');
            return False
        self.api_key = self.polyConfig['customParams']['api_key']
        data = self.api_request('/version/current?api_key='+self.api_key)
        if data is None:
            return False
        if 'version' in data:
            LOGGER.info('Successfully connected to the SolarEdge API Version {}'.format(data['version']))
            self.discover()
        else:
            LOGGER.error('API request failed: {}'.format(json.dumps(data)))
            self.api_close()
            return False
        self.api_close()

    def api_request(self, url):
        if self.conn is None:
            self.conn = http.client.HTTPSConnection(SE_API_URL)
        try:
            self.conn.request('GET', url)
            response = self.conn.getresponse()
        except Exception as ex:
            LOGGER.error('Failed to connect to SolarEdge API: {}'.format(ex))
            self.api_close()
            # retry once
            self.conn = http.client.HTTPSConnection(SE_API_URL)
            try:
                self.conn.request('GET', url)
                response = self.conn.getresponse()
            except Exception as ex:
                LOGGER.error('Retry attempt failed! {}'.format(ex))
                self.api_close()
                return None
        if response.status == 200:
            try:
                data = json.loads(response.read().decode("utf-8"))
            except Exception as ex:
                LOGGER.error('Failed to json parse API response {} {}'.format(ex, response.read().decode("utf-8")))
                self.api_close()
                return None
            return data
        else:
            LOGGER.error('Bad API response: {}'.format(response.status))
            self.api_close()
            return None

    def api_close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def stop(self):
        LOGGER.info('SolarEdge is stopping')
        self.api_close()

    def shortPoll(self):
        for node in self.nodes:
            self.nodes[node].updateInfo()
        self.api_close()

    def longPoll(self):
        for node in self.nodes:
            self.nodes[node].updateInfo(long_poll=True)
        self.api_close()

    def updateInfo(self, long_poll=False):
        pass

    def query(self):
        for node in self.nodes:
            self.nodes[node].reportDrivers()

    def _start_time(self):
        # Returns site datetime - 60 minutes
        ts = time.time()
        ts_site = ts + self.site_offset*60 - 60*60
        return datetime.datetime.fromtimestamp(ts_site).strftime('%Y-%m-%d%%20%H:%M:%S')

    def _end_time(self):
        # Returns current site time
        ts = time.time()
        ts_site = ts + self.site_offset*60
        return datetime.datetime.fromtimestamp(ts_site).strftime('%Y-%m-%d%%20%H:%M:%S')

    def discover(self, command=None):
        LOGGER.info('Discovering SolarEdge sites and equipment...')
        site_list = self.api_request('/sites/list?api_key='+self.api_key)
        if site_list is None:
            return False
        num_sites = int(site_list['sites']['count'])
        LOGGER.info('Found {} sites'.format(num_sites))
        if num_sites < 1:
            LOGGER.warning('No sites found')
            return False
        for site in site_list['sites']['site']:
            name = site['name']
            address = str(site['id'])
            LOGGER.info('Found {} site id: {}, name: {}'.format(site['status'], address, name))
            if not address in self.nodes:
                LOGGER.info('Adding site id: {}'.format(address))
                self.addNode(SESite(self, address, address, name))
            LOGGER.info('Requesting site inventory...')
            site_inv =  self.api_request('/site/'+address+'/inventory?startTime='+self._start_time()+'&endTime='+self._end_time()+'&api_key='+self.api_key)
            if site_inv is None:
                return False
            num_meter = len(site_inv['Inventory']['meters'])
            num_sens = len(site_inv['Inventory']['sensors'])
            num_gways = len(site_inv['Inventory']['gateways'])
            num_batt = len(site_inv['Inventory']['batteries'])
            num_inv = len(site_inv['Inventory']['inverters'])
            LOGGER.info('Found: {} meters, {} sensors, {} gateways, {} batteries, {} inverters'.format(num_meter, num_sens, num_gways, num_batt, num_inv))
            for inverter in site_inv['Inventory']['inverters']:
                inv_name = inverter['name']
                inv_sn = inverter['SN']
                inv_addr = inverter['SN'].replace('-','').lower()[:14]
                if not inv_addr in self.nodes:
                    LOGGER.info('Adding inverter {}'.format(inv_sn))
                    if inverter['model'] in SINGLE_PHASE:
                        self.addNode(SEInverter(self, address, inv_addr, inv_name, address, inv_sn))
                    else:
                        LOGGER.error('Model {} is not yet supported'.format(inverter['model']))

    id = 'SECTRL'
    commands = {'DISCOVER': discover}
    drivers = [{'driver': 'ST', 'value': 0, 'uom': 2}]


class SESite(polyinterface.Node):
    def __init__(self, controller, primary, address, name):
        super().__init__(controller, primary, address, name)

    def start(self):
        self.updateInfo(long_poll=True)

    def updateInfo(self, long_poll=False):
        if not long_poll:
            return True
        url = '/site/'+self.address+'/powerDetails?startTime='+self.controller._start_time()+'&endTime='+self.controller._end_time()+'&api_key='+self.controller.api_key
        power_data = self.controller.api_request(url)
        LOGGER.debug(power_data)
        if power_data is None:
            self.setDriver('ST', 0)
            return False
        for meter in power_data['powerDetails']['meters']:
            if meter['type'] == 'Production':
                datapoint = meter['values'][-1]
                if len(datapoint) == 0:
                    self.setDriver('ST', 0)
                    return False
                if 'value' in datapoint:
                    self.setDriver('ST', float(datapoint['value']))

    def query(self):
        self.reportDrivers()

    id = 'SESITE'
    commands = {'QUERY': query}
    drivers = [{'driver': 'ST', 'value': 0, 'uom': 73}]


class SEInverter(polyinterface.Node):
    def __init__(self, controller, primary, address, name, site_id, serial_num):
        super().__init__(controller, primary, address, name)
        self.serial_num = serial_num
        self.site_id = site_id

    def start(self):
        self.updateInfo()

    def updateInfo(self, long_poll=False):
        url = '/equipment/'+self.site_id+'/'+self.serial_num+'/data?startTime='+self.controller._start_time()+'&endTime='+self.controller._end_time()+'&api_key='+self.controller.api_key
        inverter_data = self.controller.api_request(url)
        LOGGER.debug(inverter_data)
        if inverter_data is None:
            return False
        datapoints = int(inverter_data['data']['count'])
        if datapoints < 1:
            LOGGER.warning('No Inverter data received, skipping...')
            return False
        # Take latest data point
        data = inverter_data['data']['telemetries'][-1]
        if not 'L1Data' in data:
            LOGGER.error('Is this a single phase inverter? {}'.format(self.serial_num))
            return False
        self.setDriver('ST', float(data['L1Data']['activePower']))
        self.setDriver('GV0', float(data['L1Data']['reactivePower']))
        self.setDriver('CPW', float(data['L1Data']['apparentPower']))
        self.setDriver('CLITEMP', float(data['temperature']))
        self.setDriver('CV', float(data['L1Data']['acVoltage']))
        self.setDriver('GV1', float(data['dcVoltage']))
        self.setDriver('GV2', round(float(data['L1Data']['acCurrent']), 1))
        self.setDriver('GV3', round(float(data['L1Data']['acFrequency']), 1))
        if data['inverterMode'] == 'MPPT':
            self.setDriver('GV4', 2)
        elif data['inverterMode'] == 'STARTING':
            self.setDriver('GV4', 1)
        else:
            self.setDriver('GV4', 0)

    def query(self):
        self.reportDrivers()

    drivers = [{'driver': 'ST', 'value': 0, 'uom': 73},
               {'driver': 'GV0', 'value': 0, 'uom': 56},
               {'driver': 'CPW', 'value': 0, 'uom': 56},
               {'driver': 'CLITEMP', 'value': 0, 'uom': 4},
               {'driver': 'CV', 'value': 0, 'uom': 72},
               {'driver': 'GV1', 'value': 0, 'uom': 72},
               {'driver': 'GV2', 'value': 0, 'uom': 1},
               {'driver': 'GV3', 'value': 0, 'uom': 90},
               {'driver': 'GV4', 'value': 0, 'uom': 25}
              ]
    id = 'SEINVERTER'
    commands = {
            'QUERY': query
               }


if __name__ == "__main__":
    try:
        polyglot = polyinterface.Interface('SolarEdge')
        polyglot.start()
        control = Controller(polyglot)
        control.runForever()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
