#!/usr/bin/env python3

#import debugpy.......
import udi_interface
import sys
import http.client
import requests
from datetime import datetime, timedelta
import pytz
import logging
import json
import math
import time
import re

LOGGER = udi_interface.LOGGER
Custom = udi_interface.Custom

SE_API_URL = 'monitoringapi.solaredge.com'
SINGLE_PHASE = [ 'SE3000', 'SE3000A', 'SE3800', 'SE3800A', 'SE3800H', 'SE5000', 'SE6000', 'SE6000H', 'SE7600', 'SE7600A', 'SE10000', 'SE11400', 'SE5000H', 'SE7600H', 'SE10000H', 'SE10000A' ]
THREE_PHASE = [ 'SE9K', 'SE10K', 'SE14.4K', 'SE20K', 'SE33.3K' ]

delta = timedelta(minutes=15)
last_production = -1.0
last_consumption = -1.0
last_date = datetime.now() - timedelta(minutes=60) #make sure API gets run initially

def _start_time(site_tz):
    # Returns site datetime - 60 minutes
    st_time = datetime.utcnow().replace(tzinfo=pytz.utc) - timedelta(minutes=60)
    return st_time.astimezone(pytz.timezone(site_tz)).strftime('%Y-%m-%d%%20%H:%M:%S')
    
def _end_time(site_tz):
    # Returns current site time
    utc_time = datetime.utcnow().replace(tzinfo=pytz.utc)
    LOGGER.debug("_end_time " + utc_time.astimezone(pytz.timezone(site_tz)).strftime('%Y-%m-%d%%20%H:%M:%S'))
    return utc_time.astimezone(pytz.timezone(site_tz)).strftime('%Y-%m-%d%%20%H:%M:%S')


def _start_time_midnight(site_tz):
    today = datetime.utcnow().replace(tzinfo=pytz.utc)    
    return today.astimezone(pytz.timezone(site_tz)).strftime('%Y-%m-%d%%200:0:0')

def _end_time_midnight(site_tz):
    today = datetime.utcnow().replace(tzinfo=pytz.utc)  
    tomorrow = today + timedelta(hours=24)
    return tomorrow.astimezone(pytz.timezone(site_tz)).strftime('%Y-%m-%d%%200:0:0')

'''
def floor_dt(dt, delta):
    # find floor of time, ex 18:17:00-> 18:15:00)
    return datetime.min + math.floor((dt - datetime.min) / delta) * delta

def ceil_dt(dt, delta):
     return dt + (datetime.min - dt) % delta

def _energy_start_time(site_tz):
    # Returns site datetime - 60 minutes
    st_time = ceil_dt(datetime.now() - timedelta(minutes=60),delta)
    return st_time.strftime('%Y-%m-%d%%20%H:%M:%S')

def _energy_end_time(site_tz):
    # Returns current site time
    utc_time = ceil_dt(datetime.now(),delta)
    LOGGER.debug("_energy_end_time " + utc_time.strftime('%Y-%m-%d%%20%H:%M:%S'))
    return utc_time.strftime('%Y-%m-%d%%20%H:%M:%S')
'''

def _api_request(url):
    full = 'https://' + SE_API_URL + url
    try:
        c = requests.get(full)
        jdata = c.json()
        c.close()
    except Exception as e:
        LOGGER.error('Request failed: {}'.format(e))
        jdata = None

    return jdata

class Controller(udi_interface.Node):
    def __init__(self, polyglot, primary, address, name):
        super().__init__(polyglot, primary, address, name)
        self.poly = polyglot
        self.name = 'SolarEdge Extended Controller'
        self.address = address
        self.primary = primary
        self.api_key = None
        self.conn = None
        self.batteries = []
        self.Parameters = Custom(polyglot, 'customparams')

        self.poly.subscribe(self.poly.START, self.start, address)
        self.poly.subscribe(self.poly.CUSTOMPARAMS, self.handleParameters)
        self.poly.subscribe(self.poly.ADDNODEDONE, self.node_queue)
        self.poly.ready()
        self.poly.addNode(self)
        self.n_queue = []
        
    '''
    node_queue() and wait_for_node_event() create a simple way to wait
    for a node to be created.  The nodeAdd() API call is asynchronous and
    will return before the node is fully created. Using this, we can wait
    until it is fully created before we try to use it.
    '''
    def node_queue(self,data):
      self.n_queue.append(data['address'])

    def wait_for_node_event(self):
      while len(self.n_queue) == 0:
          time.sleep(0.1)
      self.n_queue.pop()

    def handleParameters(self, params):
        validKey = False
        self.Parameters.load(params)
        self.poly.Notices.clear()

        if self.Parameters['api_key'] is not None:
            if len(self.Parameters['api_key']) > 10:
                validKey = True
            else:
                LOGGER.debug('API Key {} is invalid'.format(self.Parameters['api_key']))
        else:
            self.poly.Notices['key'] = 'Please specify api_key in NodeServer configuration parameters'

        if self.Parameters['rate_limit'] is not None:
                self.rate_limit = float(self.Parameters['rate_limit'])
                LOGGER.info('parameter rate_limit ' + str(self.rate_limit))
        else:
            self.rate_limit = 5
            LOGGER.info('parameter rate_limit ' + str(self.rate_limit))
                
        
        if validKey:
            self.api_key = self.Parameters['api_key']
            data = _api_request('/version/current?api_key='+self.api_key)
            if data is None:
                LOGGER.info('API request failed. Invalid api key?')
                return

            if 'version' in data:
                LOGGER.info(f"Successfully connected to the SolarEdge API Version {data['version']}")
                self.discover()
            else:
                LOGGER.error('API request failed: {}'.format(json.dumps(data)))
                self.api_close()
            self.api_close()


    def start(self):
        # LOGGER.setLevel(logging.INFO)
        LOGGER.info('Started SolarEdge controller')
        self.poly.updateProfile()
        self.poly.setCustomParamsDoc()

    def api_close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def stop(self):
        LOGGER.info('SolarEdge is stopping')
        self.api_close()

    def query(self, command=None):
        self.reportDrivers()

    '''
       Multiple sites:   Each site
         - multiple inverters
         - mutiple batteries
    '''
    def discover(self, command=None):
        LOGGER.info('Discovering SolarEdge sites and equipment...')
        site_list = _api_request('/sites/list?api_key='+self.api_key)
        if site_list is None:
            return False
        num_sites = int(site_list['sites']['count'])
        LOGGER.info('Found {} sites'.format(num_sites))
        if num_sites < 1:
            LOGGER.warning('No sites found')
            return False
        for site in site_list['sites']['site']:
            name = re.sub(r'[^A-Za-z0-9 ]+', '', site['name'])
            #name = site['name']
            site_tz = site['location']['timeZone']
            address = str(site['id'])
            LOGGER.info('Found {} site id: {}, name: {}, TZ: {}'.format(site['status'], address, name, site_tz))
            if self.poly.getNode(address) == None:
                LOGGER.info('Adding site id: {}'.format(address))
                self.poly.addNode(SESite(self.poly, address, address, name, site_tz, self.api_key, last_production, last_consumption, last_date, self.rate_limit))
                self.wait_for_node_event()
            LOGGER.info('Requesting site inventory...')
            site_inv =  _api_request('/site/'+address+'/inventory?startTime='+_start_time(site_tz)+'&endTime='+_end_time(site_tz)+'&api_key='+self.api_key)
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
                inv_addr = inverter['SN'].replace('-','').lower()[:14] # node names must be alphanumreric lower case
                if '-' in inverter['model']:
                    inv_model = inverter['model'].split('-')[0]
                else:
                    inv_model = inverter['model']
                if self.poly.getNode(inv_addr) == None:
                    LOGGER.info('Adding inverter {}'.format(inv_sn))
                    if inv_model in SINGLE_PHASE:
                        self.poly.addNode(SEInverter(self.poly, address, inv_addr, inv_name, address, inv_sn, site_tz, self.api_key, last_date, self.rate_limit))
                        self.wait_for_node_event()
                    else:
                        LOGGER.error('Model {} is not yet supported'.format(inverter['model']))
            for battery in site_inv['Inventory']['batteries']:
                batt_name = battery['name']
                batt_sn = battery['SN']
                batt_addr = battery['SN'].replace('-','').lower()[:14]
                if self.poly.getNode(batt_addr) == None:
                    LOGGER.info('Adding battery {}'.format(batt_sn))
                    self.poly.addNode(SEBattery(self.poly, address, batt_addr, batt_name, address, batt_sn, site_tz, battery))
                    self.wait_for_node_event()
                    #self.batteries.append(batt_sn)
                    self.poly.getNode(address).batteries.append(batt_sn)

            # Adding Energy Node
            en_name = "Energy Last 15min"
            en_addr = "en"+address
            if self.poly.getNode(en_addr) == None:
                    LOGGER.info('Adding Energy')
                    self.poly.addNode(SEEnergy(self.poly, address, en_addr, en_name, address, site_tz, self.api_key, last_date, self.rate_limit))
                    self.wait_for_node_event()

            # Adding Daily Energy Node
            en_name = "Energy Today"
            en_addr = "dy"+address
            if self.poly.getNode(en_addr) == None:
                    LOGGER.info('Adding EnergyDay')
                    self.poly.addNode(SEEnergyDay(self.poly, address, en_addr, en_name, address, site_tz, self.api_key, last_date, self.rate_limit))
                    self.wait_for_node_event()

            # Adding overview node
            ov_name = "Production Overview"
            ov_addr = "ov"+address
            if self.poly.getNode(ov_addr) == None:
                    LOGGER.info('Adding Overview')
                    self.poly.addNode(SEOverview(self.poly, address, ov_addr, ov_name, address, site_tz, self.api_key, last_date, self.rate_limit))
                    self.wait_for_node_event()

    id = 'SECTRL'
    commands = {'DISCOVER': discover}
    drivers = [{'driver': 'ST', 'value': 1, 'uom': 2}]


class SESite(udi_interface.Node):
    def __init__(self, polyglot, primary, address, name, site_tz, key, last_production, last_consumption, last_date, rate_limit):
        super().__init__(polyglot, primary, address, name)
        self.site_tz = site_tz
        self.key = key
        self.batteries = []
        self.last_production = last_production
        self.last_consumption = last_consumption
        self.last_date = last_date
        self.rate = rate_limit

        self.poly.subscribe(self.poly.START, self.start, address)
        self.poly.subscribe(self.poly.POLL, self.updateInfo)

    def start(self):
        self.updateInfo(poll_flag='shortPoll')

    def updateInfo(self, poll_flag='longPoll'):
        try:
            if poll_flag == 'longPoll':
                return True #updates every shortpoll
            
            last_minute = round(((datetime.now() - self.last_date) / timedelta(seconds=60)),1)
            LOGGER.debug('initial site rate_limit ' + str(self.rate))
            LOGGER.info('initial site last_minute ' + str(last_minute))
                                
            if ((last_minute >= self.rate) | (last_minute == 0.0)):
                
                datapoint_changed = 0
                url = '/site/'+self.address+'/powerDetails?startTime='+_start_time(self.site_tz)+'&endTime='+_end_time(self.site_tz)+'&api_key='+self.key
                
                LOGGER.info ("power  " + url)
                power_data = _api_request(url)
                
                '''
                Is this getting all the battery info (all sites)? not just
                the batteries for this site?
                '''
                if len(self.batteries) > 0:

                    url = '/site/'+self.address+'/storageData?serials='+','.join(map(str, self.batteries))+'&startTime='+_start_time(self.site_tz)+'&endTime='+_end_time(self.site_tz)+'&api_key='+self.key

                    
                    storage_data = _api_request(url)

                    LOGGER.debug(storage_data)
                    for battery in storage_data['storageData']['batteries']:
                        batt_sn = battery['serialNumber']
                        batt_addr = battery['serialNumber'].replace('-','').lower()[:14]
                        if battery['telemetryCount'] > 0:
                            self.poly.getNode(batt_addr).updateData(battery['telemetries'])
                        else:
                            LOGGER.debug('no battery telemetries received')

                LOGGER.info(power_data)
                if power_data is None:
                    self.setDriver('ST', 0)
                    self.setDriver('GV0', 0)
                    self.setDriver('GV1', 0)
                    self.setDriver('GV2', 0)
                    self.setDriver('GV3', 0)
                    self.setDriver('GV4', 0)
                else:
                    for meter in power_data['powerDetails']['meters']:
                        if meter['type'] == 'Production':
                            try:
                                datapoint = meter['values'][-1]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('ST', 0)
                            if 'value' in datapoint:
                                if round(float(datapoint['value']),3) != self.last_production:
                                    datapoint_changed = 1
                                    self.last_production = round(float(datapoint['value']),3) 
                                    LOGGER.debug("self.last_production " + str(self.last_production))
                                self.setDriver('ST', round(float(datapoint['value']),3))
                        elif meter['type'] == 'Consumption':
                            try:
                                datapoint = meter['values'][-1]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV0', 0)
                            if 'value' in datapoint:
                                if round(float(datapoint['value']),3) != self.last_consumption:
                                    datapoint_changed = 1
                                    self.last_consumption = round(float(datapoint['value']),3)
                                    LOGGER.debug("self.last_consumption " + str(self.last_consumption))
                                self.setDriver('GV0', round(float(datapoint['value']),3))
                        elif meter['type'] == 'Purchased':
                            try:
                                datapoint = meter['values'][-1]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV1', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV1', round(float(datapoint['value']),3))
                        elif meter['type'] == 'SelfConsumption':
                            try:
                                datapoint = meter['values'][-1]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV2', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV2', round(float(datapoint['value']),3))
                        elif meter['type'] == 'FeedIn':
                            try:
                                datapoint = meter['values'][-1]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV3', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV3', round(float(datapoint['value']),3))
            
                        
                        if datapoint_changed == 1:
                            self.last_date = datetime.now()
                            LOGGER.debug("updated power last date " + str(self.last_date))
                        last_minute = round(((datetime.now() - self.last_date) / timedelta(seconds=60)),1)
                        LOGGER.debug("updated power last minute " + str(last_minute))
                        self.setDriver('GV4',last_minute) #minute    
            else:
                self.setDriver('GV4',last_minute) #minute  

        except Exception as ex:
            LOGGER.error('SESite updateInfo failed! {}'.format(ex))

    def query(self, command=None):
        self.reportDrivers()

    id = 'SESITE'
    commands = {'QUERY': query}
    drivers = [{'driver': 'ST', 'value': 0, 'uom': 73},
               {'driver': 'GV0', 'value': 0, 'uom': 73},
               {'driver': 'GV1', 'value': 0, 'uom': 73},
               {'driver': 'GV2', 'value': 0, 'uom': 73},
               {'driver': 'GV3', 'value': 0, 'uom': 73},
               {'driver': 'GV4', 'value': 0, 'uom': 44}
              ]

class SEEnergy(udi_interface.Node):
    # energy last 15 minutes
    def __init__(self, polyglot, primary, address, name, site_id, site_tz, key, last_date, rate_limit):
        super().__init__(polyglot, primary, address, name)
        self.site_tz = site_tz
        self.key = key
        self.site_id = site_id
        self.en_date = last_date
        self.rate = rate_limit
        self.batteries = []

        self.poly.subscribe(self.poly.START, self.start, address)
        self.poly.subscribe(self.poly.POLL, self.updateInfo)

    def start(self):
        self.updateInfo(poll_flag='shortPoll')

    def updateInfo(self, poll_flag='longPoll'):
        try:
            if poll_flag == 'longPoll':
                return True

            last_minute = round(((datetime.now() - self.en_date) / timedelta(seconds=60)),1)
            LOGGER.info('initial energy last_minute ' + str(last_minute))

            if ((last_minute >= self.rate) | (last_minute == 0.0)):

                url = '/site/'+self.site_id+'/energyDetails?timeUnit=QUARTER_OF_AN_HOUR&startTime='+_start_time(self.site_tz)+'&endTime='+_end_time(self.site_tz)+'&api_key='+self.key
                
                LOGGER.debug ("energy  " + url)
                energy_data = _api_request(url)
                
                
                
                LOGGER.debug(energy_data)
                if energy_data is None:
                    self.setDriver('ST', 0)
                    self.setDriver('GV0', 0)
                    self.setDriver('GV1', 0)
                    self.setDriver('GV2', 0)
                    self.setDriver('GV3', 0)
                    self.setDriver('GV4', 0)
                    last_date = ""
                else:
                    for meter in energy_data['energyDetails']['meters']:
                        LOGGER.debug(meter)
                        if meter['type'] == 'Production':
                            try:
                                datapoint = meter['values'][-2]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('ST', 0)
                            if 'value' in datapoint:
                                self.setDriver('ST', float(datapoint['value']))
                        elif meter['type'] == 'Consumption':
                            try:
                                datapoint = meter['values'][-2]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV0', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV0', float(datapoint['value']))
                        elif meter['type'] == 'Purchased':
                            try:
                                datapoint = meter['values'][-2]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV1', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV1', float(datapoint['value']))
                        elif meter['type'] == 'SelfConsumption':
                            try:
                                datapoint = meter['values'][-2]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV2', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV2', float(datapoint['value']))
                        elif meter['type'] == 'FeedIn':
                            try:
                                datapoint = meter['values'][-2]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV3', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV3', float(datapoint['value']))

                        try:
                            datapoint = meter['values'][-1]
                        except:
                            continue  
                        if len(datapoint) == 0:
                            self.setDriver('GV4', 0)
                        if 'date' in datapoint:
                            last_date = datapoint['date']  
                        if len(last_date) > 0:
                            LOGGER.debug("new energy last date " + last_date)
                            last_minute = round(((datetime.now() - datetime.fromisoformat(last_date)) / timedelta(seconds=60)),1)
                            self.en_date = datetime.fromisoformat(last_date)
                            LOGGER.debug("new energy last minute " + str(last_minute))
                            self.setDriver('GV4',last_minute) #minute
            else:
                self.setDriver('GV4',last_minute) #minute

        except Exception as ex:
            LOGGER.error('SEEnergy updateInfo failed! {}'.format(ex))

    def query(self, command=None):
        self.reportDrivers()

    id = 'SEENERGY'
    commands = {'QUERY': query}
    drivers = [{'driver': 'ST', 'value': 0, 'uom': 119},
               {'driver': 'GV0', 'value': 0, 'uom': 119},
               {'driver': 'GV1', 'value': 0, 'uom': 119},
               {'driver': 'GV2', 'value': 0, 'uom': 119},
               {'driver': 'GV3', 'value': 0, 'uom': 119},
               {'driver': 'GV4', 'value': 0, 'uom': 44}
              ]

class SEEnergyDay(udi_interface.Node):
    def __init__(self, polyglot, primary, address, name, site_id, site_tz, key, last_date, rate_limit):
        super().__init__(polyglot, primary, address, name)
        self.site_tz = site_tz
        self.key = key
        self.last_date = last_date
        self.rate = rate_limit
        self.site_id = site_id
        self.batteries = []

        self.poly.subscribe(self.poly.START, self.start, address)
        self.poly.subscribe(self.poly.POLL, self.updateInfo)

    def start(self):
        self.updateInfo(poll_flag='shortPoll')

    def updateInfo(self, poll_flag='longPoll'):
        try:
            if poll_flag == 'longPoll':
                return True

            last_minute = round(((datetime.now() - self.last_date) / timedelta(seconds=60)),1)
            LOGGER.info('initial energy today last_minute ' + str(last_minute))
            
            if ((last_minute >= self.rate) | (last_minute == 0.0)):

                url = '/site/'+self.site_id+'/energyDetails?timeUnit=DAY&startTime='+_start_time_midnight(self.site_tz)+'&endTime='+_end_time_midnight(self.site_tz)+'&api_key='+self.key
                
                LOGGER.info ("energy today  " + url)
                energy_data = _api_request(url)
                
                
                
                LOGGER.debug(energy_data)
                if energy_data is None:
                    self.setDriver('ST', 0)
                    self.setDriver('GV0', 0)
                    self.setDriver('GV1', 0)
                    self.setDriver('GV2', 0)
                    self.setDriver('GV3', 0)
                else:
                    for meter in energy_data['energyDetails']['meters']:
                        LOGGER.debug(meter)
                        if meter['type'] == 'Production':
                            try:
                                datapoint = meter['values'][-2]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('ST', 0)
                            if 'value' in datapoint:
                                self.setDriver('ST', round(float(datapoint['value'])/1000,1))
                        elif meter['type'] == 'Consumption':
                            try:
                                datapoint = meter['values'][-2]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV0', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV0', round(float(datapoint['value'])/1000,1))
                        elif meter['type'] == 'Purchased':
                            try:
                                datapoint = meter['values'][-2]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV1', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV1', round(float(datapoint['value'])/1000,1))
                        elif meter['type'] == 'SelfConsumption':
                            try:
                                datapoint = meter['values'][-2]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV2', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV2', round(float(datapoint['value'])/1000,1))
                        elif meter['type'] == 'FeedIn':
                            try:
                                datapoint = meter['values'][-2]
                            except:
                                continue
                            if len(datapoint) == 0:
                                self.setDriver('GV3', 0)
                            if 'value' in datapoint:
                                self.setDriver('GV3', round(float(datapoint['value'])/1000,1))
            
                self.last_date = datetime.now()
                    
        except Exception as ex:
            LOGGER.error('SEEnergyDay updateInfo failed! {}'.format(ex))

    def query(self, command=None):
        self.reportDrivers()

    id = 'SEENERGYDAY'
    commands = {'QUERY': query}
    drivers = [{'driver': 'ST', 'value': 0, 'uom': 33},
               {'driver': 'GV0', 'value': 0, 'uom': 33},
               {'driver': 'GV1', 'value': 0, 'uom': 33},
               {'driver': 'GV2', 'value': 0, 'uom': 33},
               {'driver': 'GV3', 'value': 0, 'uom': 33}
              ]


class SEInverter(udi_interface.Node):
    def __init__(self, polyglot, primary, address, name, site_id, serial_num, site_tz, key, last_date, rate_limit):
        super().__init__(polyglot, primary, address, name)
        self.serial_num = serial_num
        self.site_id = site_id
        self.site_tz = site_tz
        self.key = key
        self.last_date = last_date
        self.rate = rate_limit

        self.poly.subscribe(self.poly.START, self.start, address)
        self.poly.subscribe(self.poly.POLL, self.updateInfo)

    def start(self):
        self.updateInfo()

    def updateInfo(self, poll_flag='shortPoll'):
        try:
            
            if poll_flag == 'longPoll':
                return True

            last_minute = round(((datetime.now() - self.last_date) / timedelta(seconds=60)),1)
            LOGGER.info('initial inverter last_minute ' + str(last_minute))
                
            if ((last_minute >= self.rate) | (last_minute == 0.0)):

                url = '/equipment/'+self.site_id+'/'+self.serial_num+'/data?startTime='+_start_time(self.site_tz)+'&endTime='+_end_time(self.site_tz)+'&api_key='+self.key
                inverter_data = _api_request(url)

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
                self.setDriver('ST', round(float(data['L1Data']['activePower']),3))
                if 'reactivePower' in data['L1Data']:
                    self.setDriver('GV0', round(float(data['L1Data']['reactivePower']),3))
                else:
                    self.setDriver('GV0', 0)
                if 'apparentPower' in data['L1Data']:
                    self.setDriver('CPW', round(float(data['L1Data']['apparentPower']),3))
                else:
                    self.setDriver('CPW', 0)
                self.setDriver('CLITEMP', round(float(data['temperature']),3))
                self.setDriver('CV', round(float(data['L1Data']['acVoltage']),3))
                if data['dcVoltage'] is not None:
                    self.setDriver('GV1', round(float(data['dcVoltage']),3))
                self.setDriver('GV2', round(float(data['L1Data']['acCurrent']), 1))
                self.setDriver('GV3', round(float(data['L1Data']['acFrequency']), 1))
                if data['inverterMode'] == 'MPPT':
                    self.setDriver('GV4', 2)
                elif data['inverterMode'] == 'STARTING':
                    self.setDriver('GV4', 1)
                else:
                    self.setDriver('GV4', 0)

                self.last_date = datetime.now()    
        
        
        except Exception as ex:
            LOGGER.error('SEInverter updateInfo failed! {}'.format(ex))

    def query(self, command=None):
        self.reportDrivers()

    drivers = [{'driver': 'ST', 'value': 0, 'uom': 73},
               {'driver': 'GV0', 'value': 0, 'uom': 136},
               {'driver': 'CPW', 'value': 0, 'uom': 135},
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

class SEBattery(udi_interface.Node):
    def __init__(self, polyglot, primary, address, name, site_id, serial_num, site_tz, battery):
        super().__init__(polyglot, primary, address, name)
        self.serial_num = serial_num
        self.site_id = site_id
        self.site_tz = site_tz
        self.battery = battery
        self.poly.subscribe(self.poly.START, self.start, address)
        #self.poly.subscribe(self.poly.POLL, self.updateData)

    def start(self):
        self.updateInfo()

    def updateInfo(self, long_poll=False):
        try:
            ''' Battery does not query anything right now but depends on the site node to supply information to save on the number of API calls '''
            self.setDriver('GPV', float(self.battery['nameplateCapacity']))
        except Exception as ex:
            LOGGER.error('SEBattery updateInfo failed! {}'.format(ex))

    def updateData(self, batt_data=None):
        LOGGER.debug(batt_data)
        if batt_data is None:
            return False
        # Take latest data point
        data = batt_data[-1]
        self.setDriver('ST', data['power'])
        self.setDriver('BATLVL', round(float(data['batteryPercentageState']), 1))

    def query(self, command=None):
        self.reportDrivers()

    drivers = [{'driver': 'ST', 'value': 0, 'uom': 73},
               {'driver': 'BATLVL', 'value': 0, 'uom': 51},
               {'driver': 'GPV', 'value': 0, 'uom': 56}
              ]

    id = 'SEBATT'
    commands = {
            'QUERY': query
               }


class SEOverview(udi_interface.Node):
    def __init__(self, polyglot, primary, address, name, site_id, site_tz, key, last_date, rate_limit):
        super().__init__(polyglot, primary, address, name)
        self.site_id = site_id
        self.site_tz = site_tz
        self.key = key
        self.last_date = last_date
        self.rate = rate_limit

        self.poly.subscribe(self.poly.START, self.start, address)
        self.poly.subscribe(self.poly.POLL, self.updateInfo)

    def start(self):
        self.updateInfo()


    def updateInfo(self, poll_flag='longPoll'):
        
        try:
            
            if poll_flag == 'ShortPoll':
                return True

            last_minute = round(((datetime.now() - self.last_date) / timedelta(seconds=60)),1)
            LOGGER.debug('initial overview last_minute ' + str(last_minute))
                
            if ((last_minute >= self.rate) | (last_minute == 0.0)):

                url = '/site/'+self.site_id+'/overview/'+'?api_key='+self.key
                LOGGER.debug("overview url = " + url)
                overview_data = _api_request(url)

                LOGGER.debug(overview_data)

                if overview_data is None:
                    return False
                data = overview_data['overview']
                LOGGER.debug("Overview Data " + str(data['currentPower']['power']))
                self.setDriver('ST', round(data['lifeTimeData']['energy'] / 1000,1))
                self.setDriver('GV0', round(data['lastYearData']['energy'] / 1000,1))
                self.setDriver('GV1', round(data['lastMonthData']['energy'] / 1000,1))
                self.setDriver('GV2', round(data['lastDayData']['energy'] / 1000,1))
                self.setDriver('GV3', data['currentPower']['power'])
                
                self.last_date = datetime.now()  

        except Exception as ex:
            LOGGER.error('SEOverview updateInfo failed! {}'.format(ex))

    def query(self, command=None):
        self.reportDrivers()

    drivers = [{'driver': 'ST', 'value': 0, 'uom': 33},
               {'driver': 'GV0', 'value': 0, 'uom': 33},
               {'driver': 'GV1', 'value': 0, 'uom': 33},
               {'driver': 'GV2', 'value': 0, 'uom': 33},
               {'driver': 'GV3', 'value': 0, 'uom': 73}
              ]
    id = 'SEOVERVIEW'
    commands = {
            'QUERY': query
               }





if __name__ == "__main__":
    try:
       
        polyglot = udi_interface.Interface([])
        polyglot.start("1.1.01")
        Controller(polyglot, 'controller', 'controller', 'SolarEdge')
        polyglot.runForever()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
